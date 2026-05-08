"""Buried Insight algorithm.

Finds: an old note (>= MIN_AGE_DAYS) that the user wrote once and never
returned to (no self-update in >= MIN_DORMANT_DAYS), but that recent notes
(within RECENT_WINDOW_DAYS) keep validating — either by linking to it or by
expressing semantically similar claims.

Output: the strongest such candidate, with a quoted line, validating notes,
and a one-paragraph framing.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from basalt.embed import _blob_to_vec


DEFAULT_MIN_AGE_DAYS         = 180     # candidate created at least 6 months ago
DEFAULT_MIN_DORMANT_DAYS     = 90      # not self-updated in 3+ months
DEFAULT_RECENT_WINDOW_DAYS   = 180     # validators must be within last 6 months
MIN_VALIDATORS               = 3       # need at least N validating recent notes
MIN_SIM                      = 0.62    # cosine threshold for "semantically validates"
TOP_K_VALIDATORS             = 5       # cap how many to surface
MIN_WORD_COUNT               = 30      # ignore stub notes
MIN_BODY_FOR_QUOTE           = 80      # min chars to bother extracting a quote

# Vault-age-aware bounds: thresholds derived from the actual span of the corpus.
# Cap the upward end so a 10-year vault doesn't demand 5-year-old notes.
VAULT_AWARE_MIN_AGE_FLOOR    = 60
VAULT_AWARE_MIN_AGE_CEIL     = 365
VAULT_AWARE_DORMANT_FLOOR    = 30
VAULT_AWARE_DORMANT_CEIL     = 180
VAULT_AWARE_RECENT_FLOOR     = 60
VAULT_AWARE_RECENT_CEIL      = 365

# Hub-note penalty: MOCs/index pages have many outgoing wikilinks per word.
# Empirically (Fernando's vault, 2026-05-08): real insights ≤ 0.50; hubs ≥ 1.81.
# Hard-exclude above HARD; soft-penalize from SOFT upward via inverse-square.
HUB_DENSITY_HARD     = 1.5     # outgoing-links-per-100-words above this → not a candidate
HUB_DENSITY_SOFT     = 0.5     # below this no penalty; above, score ÷ (1 + (d - SOFT)²)


@dataclass
class Candidate:
    note_id: int
    rel_path: str
    title: str
    stem: str
    created: date
    updated: date
    content: str
    word_count: int
    inbound_recent: int                # explicit wikilinks from recent notes
    semantic_validators: list[tuple[int, float]]  # (note_id, sim)
    hub_density: float                 # outgoing unique wikilinks per 100 words
    hub_penalty: float                 # multiplicative score factor in [0, 1]
    score: float


@dataclass
class Validator:
    note_id: int
    rel_path: str
    title: str
    updated: date
    sim: float
    explicit_link: bool


@dataclass
class BuriedInsight:
    candidate: Candidate
    quote: str
    quote_provenance: str       # "first claim line" / "first quoted line" / etc.
    validators: list[Validator]
    thresholds: dict            # the (min_age_days, min_dormant_days, recent_window_days) actually used
    vault_age_days: int         # oldest note's age — derived for transparency


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def compute_vault_age_days(conn: sqlite3.Connection, today: date | None = None) -> int:
    """Return the age in days of the oldest note in the vault (by frontmatter created date).
    Returns 0 if no dated notes exist."""
    today = today or date.today()
    rows = conn.execute(
        "SELECT created FROM notes WHERE created IS NOT NULL"
    ).fetchall()
    ages = [(today - _parse_date(r[0])).days for r in rows if _parse_date(r[0])]
    return max(ages) if ages else 0


def compute_vault_aware_thresholds(
    conn: sqlite3.Connection,
    today: date | None = None,
) -> dict:
    """Derive (min_age, min_dormant, recent_window) from the vault's own time scale.

    Logic: candidates must be at least *half* the vault's age. Dormancy at least
    a third of that. Recent window matches the candidate-age scale, never longer
    than the vault itself. All clamped to floors/ceilings so very young or very
    old vaults still produce sensible numbers.
    """
    vault_age = compute_vault_age_days(conn, today)
    if vault_age <= 0:
        return {
            "min_age_days": DEFAULT_MIN_AGE_DAYS,
            "min_dormant_days": DEFAULT_MIN_DORMANT_DAYS,
            "recent_window_days": DEFAULT_RECENT_WINDOW_DAYS,
            "vault_age_days": 0,
        }
    min_age = _clamp(vault_age // 2, VAULT_AWARE_MIN_AGE_FLOOR, VAULT_AWARE_MIN_AGE_CEIL)
    min_dormant = _clamp(min_age // 3, VAULT_AWARE_DORMANT_FLOOR, VAULT_AWARE_DORMANT_CEIL)
    # Recent window: same scale as candidate age, but never longer than the vault itself.
    recent = _clamp(min(min_age, max(vault_age - 1, 1)), VAULT_AWARE_RECENT_FLOOR, VAULT_AWARE_RECENT_CEIL)
    return {
        "min_age_days": min_age,
        "min_dormant_days": min_dormant,
        "recent_window_days": recent,
        "vault_age_days": vault_age,
    }


_MIN_QUOTE_CHARS = 40
_MAX_QUOTE_CHARS = 320

# Load-bearing-sentence signals — which complete sentence in a passage carries the claim?
# Conclusion openers: "the moat is", "ultimately", "in short", "the takeaway", etc.
_CONCLUSION_OPENERS = re.compile(
    r"^\s*("
    r"the\s+(\w+\s+){0,3}(is|isn't|are|aren't|means|comes\s+down\s+to)|"
    r"ultimately|in\s+short|in\s+the\s+end|bottom\s+line|"
    r"the\s+(takeaway|lesson|point|moat|real|truth|reality|verdict)|"
    r"so\s+(the|what)|that\s+is\s+why|net[\s-]net"
    r")\b",
    re.IGNORECASE,
)
# Negation+assertion: "isn't speed alone — it's …", "not just X but Y", "doesn't mean …"
_NEGATION_ASSERTION = re.compile(
    r"\b(isn't|aren't|wasn't|weren't|doesn't|don't|won't|can't|"
    r"shouldn't|wouldn't|hasn't|haven't|"
    r"not\s+(just|merely|only|simply|enough)|no\s+longer)\b",
    re.IGNORECASE,
)

# Markdown noise strippers — applied to passages before sentence-splitting
_MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_ITAL = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_MD_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\([^)\n]+\)")
_MD_WIKILINK = re.compile(r"\[\[([^\]\n|]+)(?:\|([^\]\n]+))?\]\]")
_MD_IMG = re.compile(r"!\[[^\]\n]*\]\([^)\n]+\)")
_MD_HIGHLIGHT = re.compile(r"==([^=\n]+)==")
_MD_STRIKE = re.compile(r"~~([^~\n]+)~~")

# Sentence boundary: . ! ? followed by whitespace and a capital/quote/digit/paren.
# Naive but good enough for the prose we extract.
_SENT_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(\[])')

# A "complete" sentence terminator — colons, commas, semicolons don't count.
_COMPLETE_END = (".", "!", "?", "”", "’", "\"", "'")


def _strip_md(s: str) -> str:
    s = _MD_IMG.sub("", s)
    s = _MD_BOLD.sub(r"\1", s)
    s = _MD_ITAL.sub(r"\1", s)
    s = _MD_INLINE_CODE.sub(r"\1", s)
    s = _MD_HIGHLIGHT.sub(r"\1", s)
    s = _MD_STRIKE.sub(r"\1", s)
    s = _MD_LINK.sub(r"\1", s)
    s = _MD_WIKILINK.sub(lambda m: m.group(2) or m.group(1), s)
    return re.sub(r"\s+", " ", s).strip()


def _split_sentences(s: str) -> list[str]:
    """Naive sentence splitter — good enough for prose claim extraction."""
    return [p.strip() for p in _SENT_END.split(s) if p.strip()]


def _score_load_bearing(
    sentence: str,
    position: int,
    total: int,
    prefer_last: bool,
) -> float:
    """Heuristic score: how likely is this sentence to be the load-bearing claim?

    Higher = more punchline-shaped. Combines position (last in callouts, first
    in prose), claim-shape signals (em-dash, negation, conclusion-opener), and
    a sweet-spot length bonus. See module-level constants for tunables.
    """
    score = 0.0

    # Positional weight — callouts conclude at the end; vault-style prose leads with verdict
    if prefer_last:
        if position == total - 1:
            score += 1.0
        elif total >= 3 and position == total - 2:
            score += 0.4
    else:
        if position == 0:
            score += 0.6
        elif position == 1 and total >= 3:
            score += 0.2

    # Em-dash claim-shape ("not X — Y", "X — Y", attribution dashes)
    if "—" in sentence or " – " in sentence:
        score += 0.6

    # Negation+assertion ("isn't speed alone", "not just X but Y")
    if _NEGATION_ASSERTION.search(sentence):
        score += 0.5

    # Explicit conclusion opener ("the moat is", "ultimately", "in short")
    if _CONCLUSION_OPENERS.search(sentence):
        score += 0.7

    # Length sweet spot: 60–150 chars is punchy; >220 is hedged; <40 is a label
    L = len(sentence)
    if 60 <= L <= 150:
        score += 0.2
    elif L > 220:
        score -= 0.3
    elif L < 40:
        score -= 0.5

    return score


def _score_passage_sentences(
    passage: str,
    prefer_last: bool,
) -> list[tuple[float, str]]:
    """Return (score, sentence) for every COMPLETE sentence in `passage`.
    A complete sentence ends in a real terminator and meets MIN/MAX bounds."""
    passage = _strip_md(passage)
    sents = _split_sentences(passage)
    if not sents:
        return []
    out: list[tuple[float, str]] = []
    for i, sent in enumerate(sents):
        sent = sent.strip()
        if not (_MIN_QUOTE_CHARS <= len(sent) <= _MAX_QUOTE_CHARS):
            continue
        if not sent.rstrip().endswith(_COMPLETE_END):
            continue
        out.append((_score_load_bearing(sent, i, len(sents), prefer_last), sent))
    return out


def _pick_complete_quote(passage: str) -> str | None:
    """Return the shortest substantive run of complete sentences from `passage`.

    "Complete" means the chosen tail ends in a real sentence terminator —
    not a colon, semicolon, or comma. Aggregates sentences until at least
    MIN chars are accumulated AND the running tail is a complete ending.
    """
    passage = _strip_md(passage)
    if len(passage) < _MIN_QUOTE_CHARS:
        return None
    sents = _split_sentences(passage)
    if not sents:
        return None
    out: list[str] = []
    total = 0
    for sent in sents:
        # Don't blow past max — stop here even if we haven't hit a clean ending.
        if total and total + 1 + len(sent) > _MAX_QUOTE_CHARS:
            break
        out.append(sent)
        total = sum(len(s) for s in out) + len(out) - 1  # joined length
        tail = out[-1].rstrip()
        if total >= _MIN_QUOTE_CHARS and tail.endswith(_COMPLETE_END):
            return " ".join(out)
    # Didn't hit a clean ending — return what we have only if substantive.
    joined = " ".join(out).strip()
    return joined if len(joined) >= _MIN_QUOTE_CHARS else None


def _aggregate_blockquote_passages(lines: list[str]) -> list[tuple[str, bool]]:
    """Walk lines, return (passage, is_callout) tuples for each contiguous
    blockquote group. An empty `>` line terminates the group; bullet/list lines
    inside a callout also terminate it (they aren't part of the prose claim)."""
    out: list[tuple[str, bool]] = []
    cur: list[str] = []
    is_callout = False
    for line in lines:
        s = line.strip()
        if s.startswith("> [!"):
            if cur:
                out.append((" ".join(cur), is_callout))
                cur = []
            is_callout = True
        elif s.startswith("> "):
            inner = s[2:].strip()
            # Inside a callout, bullets/headers aren't claim prose — flush
            if inner.startswith(("-", "*", "#", "|", ">")) or not inner:
                if cur:
                    out.append((" ".join(cur), is_callout))
                    cur = []
                # for blank `>` lines, stay in the callout but reset buffer
                continue
            cur.append(inner)
        elif s == ">":
            if cur:
                out.append((" ".join(cur), is_callout))
                cur = []
        else:
            if cur:
                out.append((" ".join(cur), is_callout))
                cur = []
            is_callout = False
    if cur:
        out.append((" ".join(cur), is_callout))
    return out


def _aggregate_prose_paragraphs(lines: list[str]) -> list[str]:
    """Walk lines, return contiguous prose paragraphs (skip code, headings,
    lists, blockquotes, frontmatter remnants)."""
    out: list[str] = []
    cur: list[str] = []
    in_code = False
    for line in lines:
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            if cur:
                out.append(" ".join(cur)); cur = []
            continue
        if in_code:
            continue
        if not s or s.startswith(("#", "-", "*", "|", ">", "<!--", "---", "+++", "|", "    ")):
            if cur:
                out.append(" ".join(cur)); cur = []
            continue
        cur.append(s)
    if cur:
        out.append(" ".join(cur))
    return out


def _extract_claim_quote(body: str) -> tuple[str, str]:
    """Extract the load-bearing claim from the note body. Returns (quote, provenance).

    Strategy:
      1. Score every complete sentence across all blockquote/callout passages
         AND prose paragraphs, then pick the highest-scoring one.
      2. If no complete sentence anywhere, fall back to multi-sentence
         aggregation (preserves the older _pick_complete_quote behavior for
         notes that have only short sentences).
      3. Last resort: opening passage of body, ended at the last sentence
         terminator within the cap.
    """
    body = body.strip()
    if not body:
        return "", "empty"

    lines = [ln.rstrip() for ln in body.splitlines()]

    # ── 1. Score-based pass across all candidate passages ──
    candidates: list[tuple[float, str, str]] = []  # (score, sentence, provenance)

    bq_passages = _aggregate_blockquote_passages(lines)
    for passage, is_callout in bq_passages:
        prov = "callout body" if is_callout else "blockquote summary"
        # Slight boost — blockquote/callout content is usually editorialized
        boost = 0.3 if is_callout else 0.2
        for sc, sent in _score_passage_sentences(passage, prefer_last=is_callout):
            candidates.append((sc + boost, sent, prov))

    prose_paras = _aggregate_prose_paragraphs(lines)
    for para in prose_paras:
        for sc, sent in _score_passage_sentences(para, prefer_last=False):
            candidates.append((sc, sent, "first prose sentence"))

    if candidates:
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1], candidates[0][2]

    # ── 2. Multi-sentence aggregation fallback (when individual sentences are short) ──
    for passage, is_callout in bq_passages:
        q = _pick_complete_quote(passage)
        if q:
            return q, "callout body" if is_callout else "blockquote summary"
    for para in prose_paras:
        q = _pick_complete_quote(para)
        if q:
            return q, "first prose sentence"

    # ── 3. Final fallback: opening passage trimmed to a sentence boundary ──
    flat = _strip_md(body)
    if len(flat) >= _MIN_QUOTE_CHARS:
        capped = flat[:_MAX_QUOTE_CHARS]
        last_end = max(capped.rfind(c) for c in _COMPLETE_END)
        if last_end >= _MIN_QUOTE_CHARS:
            return capped[: last_end + 1].strip(), "opening passage"
        return capped.strip(), "opening passage"

    return "", "empty"


def find_buried_insights(
    conn: sqlite3.Connection,
    today: date | None = None,
    min_age_days: int | None = None,
    min_dormant_days: int | None = None,
    recent_window_days: int | None = None,
    vault_aware: bool = True,
    top_n: int = 1,
) -> list[BuriedInsight]:
    """Run the Buried Insight algorithm. Returns the top N strongest candidates.

    Thresholds default to vault-age-aware values; explicit overrides win.
    Pass `vault_aware=False` to fall back to fixed defaults.
    """
    today = today or date.today()

    if vault_aware and (min_age_days is None or min_dormant_days is None or recent_window_days is None):
        derived = compute_vault_aware_thresholds(conn, today)
        if min_age_days is None:        min_age_days = derived["min_age_days"]
        if min_dormant_days is None:    min_dormant_days = derived["min_dormant_days"]
        if recent_window_days is None:  recent_window_days = derived["recent_window_days"]
        vault_age = derived["vault_age_days"]
    else:
        if min_age_days is None:        min_age_days = DEFAULT_MIN_AGE_DAYS
        if min_dormant_days is None:    min_dormant_days = DEFAULT_MIN_DORMANT_DAYS
        if recent_window_days is None:  recent_window_days = DEFAULT_RECENT_WINDOW_DAYS
        vault_age = compute_vault_age_days(conn, today)

    age_cutoff      = today - timedelta(days=min_age_days)
    dormant_cutoff  = today - timedelta(days=min_dormant_days)
    recent_cutoff   = today - timedelta(days=recent_window_days)
    thresholds_used = {
        "min_age_days": min_age_days,
        "min_dormant_days": min_dormant_days,
        "recent_window_days": recent_window_days,
    }

    # Pull all notes with their embeddings (if any)
    rows = conn.execute(
        """
        SELECT n.id, n.rel_path, n.stem, n.title, n.created, n.updated,
               n.word_count, n.content,
               e.vec
        FROM notes n
        LEFT JOIN embeddings e ON e.note_id = n.id
        """
    ).fetchall()

    # Outgoing unique-link counts (for hub-density detection)
    out_link_counts = dict(conn.execute(
        "SELECT from_note_id, COUNT(DISTINCT target) FROM links GROUP BY from_note_id"
    ).fetchall())

    notes_by_id = {}
    vec_by_id   = {}
    for r in rows:
        nid = r["id"]
        notes_by_id[nid] = {
            "id": nid,
            "rel_path": r["rel_path"],
            "stem": r["stem"],
            "title": r["title"],
            "created": _parse_date(r["created"]),
            "updated": _parse_date(r["updated"]),
            "word_count": r["word_count"],
            "content": r["content"],
        }
        if r["vec"]:
            vec_by_id[nid] = _blob_to_vec(r["vec"])

    # Identify "recent notes" and "candidate (buried) notes"
    recent_ids = [
        nid for nid, n in notes_by_id.items()
        if n["updated"] and n["updated"] >= recent_cutoff
        and n["word_count"] >= MIN_WORD_COUNT
    ]
    def _hub_density(nid: int, wc: int) -> float:
        if wc <= 0:
            return 0.0
        return out_link_counts.get(nid, 0) / max(wc / 100.0, 1.0)

    candidate_ids = [
        nid for nid, n in notes_by_id.items()
        if n["created"] and n["created"] <= age_cutoff
        and n["updated"] and n["updated"] <= dormant_cutoff
        and n["word_count"] >= MIN_WORD_COUNT
        and _hub_density(nid, n["word_count"]) <= HUB_DENSITY_HARD
    ]

    if not candidate_ids or not recent_ids:
        return None

    # Build inbound-recent-link counts via the explicit graph
    inbound_recent = {nid: 0 for nid in candidate_ids}
    inbound_recent_ids: dict[int, set[int]] = {nid: set() for nid in candidate_ids}
    cur = conn.execute(
        f"""
        SELECT l.target_note_id AS to_id, l.from_note_id AS from_id
        FROM links l
        WHERE l.target_note_id IS NOT NULL
          AND l.target_note_id IN ({",".join("?"*len(candidate_ids))})
          AND l.from_note_id   IN ({",".join("?"*len(recent_ids))})
        """,
        (*candidate_ids, *recent_ids),
    )
    for row in cur.fetchall():
        inbound_recent[row["to_id"]] += 1
        inbound_recent_ids[row["to_id"]].add(row["from_id"])

    # Semantic validation: for each candidate with an embedding, find recent notes
    # whose embeddings cross the similarity threshold.
    semantic = {nid: [] for nid in candidate_ids}
    if vec_by_id:
        recent_with_vec = [(rid, vec_by_id[rid]) for rid in recent_ids if rid in vec_by_id]
        if recent_with_vec:
            recent_ids_arr  = np.array([rid for rid, _ in recent_with_vec])
            recent_mat      = np.stack([v for _, v in recent_with_vec])
            for nid in candidate_ids:
                v = vec_by_id.get(nid)
                if v is None:
                    continue
                sims = recent_mat @ v
                mask = sims >= MIN_SIM
                if not mask.any():
                    continue
                hits = list(zip(recent_ids_arr[mask].tolist(), sims[mask].tolist()))
                hits.sort(key=lambda x: -x[1])
                semantic[nid] = hits[:TOP_K_VALIDATORS]

    # Score each candidate
    cands: list[Candidate] = []
    for nid in candidate_ids:
        n = notes_by_id[nid]
        explicit = inbound_recent[nid]
        sem      = semantic[nid]
        # Combined validators = explicit links + semantic hits (dedup by note id)
        explicit_validators = inbound_recent_ids[nid]
        all_validator_ids = set(explicit_validators) | {sid for sid, _ in sem}
        if len(all_validator_ids) < MIN_VALIDATORS:
            continue
        # Score: explicit links count double; semantic similarity averaged.
        # Apply soft hub-density penalty: notes with high outgoing-link density
        # are likely MOCs/indexes, not buried insights.
        sem_score = sum(s for _, s in sem) if sem else 0.0
        raw_score = (explicit * 2.0) + sem_score + (0.05 * (today - n["updated"]).days / 30)
        density   = _hub_density(nid, n["word_count"])
        excess    = max(0.0, density - HUB_DENSITY_SOFT)
        # Inverse-square penalty with 2× excess scaling — bites in 0.5–1.5 gray zone:
        # density 0.5 → 1.00 (no penalty)
        # density 0.7 → 0.86
        # density 1.0 → 0.50
        # density 1.3 → 0.28
        penalty   = 1.0 / (1.0 + (2.0 * excess) ** 2)
        score     = raw_score * penalty
        cands.append(Candidate(
            note_id=nid,
            rel_path=n["rel_path"],
            title=n["title"],
            stem=n["stem"],
            created=n["created"],
            updated=n["updated"],
            content=n["content"],
            word_count=n["word_count"],
            inbound_recent=explicit,
            semantic_validators=sem,
            hub_density=density,
            hub_penalty=penalty,
            score=score,
        ))

    if not cands:
        return []
    cands.sort(key=lambda c: -c.score)

    results: list[BuriedInsight] = []
    for cand in cands:
        if len(results) >= top_n:
            break
        if len(cand.content) < MIN_BODY_FOR_QUOTE:
            continue
        quote, prov = _extract_claim_quote(cand.content)
        if not quote:
            continue

        # Compose validator list (explicit + semantic, dedup, ordered by recency)
        validators: list[Validator] = []
        seen: set[int] = set()
        for vid in inbound_recent_ids[cand.note_id]:
            if vid in seen:
                continue
            seen.add(vid)
            n = notes_by_id[vid]
            validators.append(Validator(
                note_id=vid, rel_path=n["rel_path"], title=n["title"],
                updated=n["updated"], sim=1.0, explicit_link=True,
            ))
        for vid, sim in cand.semantic_validators:
            if vid in seen:
                continue
            seen.add(vid)
            n = notes_by_id[vid]
            validators.append(Validator(
                note_id=vid, rel_path=n["rel_path"], title=n["title"],
                updated=n["updated"], sim=float(sim), explicit_link=False,
            ))
        validators.sort(key=lambda v: (-int(v.explicit_link), -v.sim, v.updated or date.min), reverse=False)
        validators = validators[:TOP_K_VALIDATORS]

        results.append(BuriedInsight(
            candidate=cand,
            quote=quote,
            quote_provenance=prov,
            validators=validators,
            thresholds=thresholds_used,
            vault_age_days=vault_age,
        ))

    return results


def find_buried_insight(
    conn: sqlite3.Connection,
    today: date | None = None,
    min_age_days: int | None = None,
    min_dormant_days: int | None = None,
    recent_window_days: int | None = None,
    vault_aware: bool = True,
) -> BuriedInsight | None:
    """Backwards-compatible single-result wrapper around find_buried_insights."""
    results = find_buried_insights(
        conn,
        today=today,
        min_age_days=min_age_days,
        min_dormant_days=min_dormant_days,
        recent_window_days=recent_window_days,
        vault_aware=vault_aware,
        top_n=1,
    )
    return results[0] if results else None
