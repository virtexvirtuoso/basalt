# Stale Verb Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 6th verb `basalt stale` that surfaces notes whose frontmatter claims live status (`active`, `draft`, `wip`) but whose edit pattern and inbound-link signal say otherwise. Mechanical only — no LLM, no embeddings.

**Architecture:** Schema migration adds `notes.status` column (idempotent ALTER, parsed from frontmatter at index time). New `stale.py` module computes findings from three columns plus a backward JOIN on `links`. New `cmd_stale` in `cli.py` + brief integration. Falsification rules in `audit.py`. Reuses the vault-aware threshold helper from `buried.py`.

**Tech Stack:** Python 3.12, sqlite3, dataclasses, pytest. No new third-party deps.

**Reference:**
- Scope captured in Tasks.md: `~/virtuoso-vault/02-Projects/Basalt/Tasks.md` (Tier-1 Stale entry)
- Existing schema: `src/basalt/index.py:13-27` (notes table)
- Existing parser: `src/basalt/vault.py:80-119` (Note dataclass + parse_note)
- Existing falsification pattern: `src/basalt/audit.py` (`drift_resolved`, `candidate_shrinks`, etc.)
- Existing vault-aware threshold helper: `src/basalt/buried.py` (`compute_vault_aware_thresholds`)
- Existing brief integration shape: `src/basalt/cli.py` — `SECTIONS_AVAILABLE`, `SECTIONS_SHIPPED`, `cmd_brief`

---

## File Structure

| Path | Status | Responsibility |
|------|--------|----------------|
| `src/basalt/vault.py` | modify | `Note` dataclass gains `status: str \| None`. `parse_note()` reads `fm.get("status")`, lowercases, strips. |
| `src/basalt/index.py` | modify | Add `notes.status TEXT` column to schema. Idempotent `ALTER TABLE ADD COLUMN IF NOT EXISTS` (or check pragma + add). `upsert_note()` stores status. |
| `src/basalt/stale.py` | new | `StaleFinding` dataclass + `find_stale(conn, status_set, days, top_n)`. ~120 lines. |
| `src/basalt/audit.py` | modify | Add 3 falsification evaluators: `frontmatter_updated`, `new_inbound_link`, `status_archived`. Wire into rule dispatch. |
| `src/basalt/serialize.py` | modify | `stale_to_dict(finding)` for JSON output. |
| `src/basalt/cli.py` | modify | New `cmd_stale` + render block + brief section wiring + add `"stale"` to SECTIONS_AVAILABLE / SECTIONS_SHIPPED. |
| `src/basalt/mcp_server.py` | modify | Expose `basalt_stale` as 7th MCP tool. |
| `tests/test_smoke.py` | modify | Add 4 stale-specific tests on a hand-rolled fixture vault. |

---

## Task 1: Schema migration — add `notes.status` column

**Files:**
- Modify: `src/basalt/index.py` (schema + `upsert_note`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_smoke.py`:

```python
def test_notes_table_has_status_column(tmp_path):
    """The notes table must expose a status column after schema migration."""
    from basalt.index import open_db
    db = tmp_path / "scratch.db"
    conn = open_db(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(notes)").fetchall()]
    assert "status" in cols


def test_open_db_migrates_existing_db_without_status(tmp_path):
    """Opening a pre-migration DB must idempotently add the status column."""
    import sqlite3
    from basalt.index import open_db
    db = tmp_path / "legacy.db"
    # Hand-roll a pre-migration notes table (no status column).
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY,
                rel_path TEXT UNIQUE NOT NULL,
                stem TEXT NOT NULL,
                title TEXT NOT NULL,
                created TEXT,
                updated TEXT,
                word_count INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                tags TEXT
            )
        """)
    # Now open via the public API — should add status column without error.
    conn = open_db(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(notes)").fetchall()]
    assert "status" in cols
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_smoke.py::test_notes_table_has_status_column tests/test_smoke.py::test_open_db_migrates_existing_db_without_status -v`

Expected: both FAIL (status column doesn't exist yet).

- [ ] **Step 3: Add status to the CREATE TABLE schema**

Open `src/basalt/index.py`. Find the `CREATE TABLE IF NOT EXISTS notes` block (line ~13). Add `status TEXT,` after `tags TEXT`:

```python
CREATE TABLE IF NOT EXISTS notes (
    id              INTEGER PRIMARY KEY,
    rel_path        TEXT UNIQUE NOT NULL,
    stem            TEXT NOT NULL,
    title           TEXT NOT NULL,
    created         TEXT,
    updated         TEXT,
    word_count      INTEGER NOT NULL,
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    tags            TEXT,
    status          TEXT
);
```

- [ ] **Step 4: Add idempotent migration for existing DBs**

In `open_db()`, after the `executescript(SCHEMA_SQL)` call, add:

```python
# Idempotent migration: add status column to legacy DBs that predate it.
existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(notes)").fetchall()}
if "status" not in existing_cols:
    conn.execute("ALTER TABLE notes ADD COLUMN status TEXT")
    conn.commit()
```

- [ ] **Step 5: Run tests — both should pass now**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_smoke.py::test_notes_table_has_status_column tests/test_smoke.py::test_open_db_migrates_existing_db_without_status -v`

Expected: both PASS.

- [ ] **Step 6: Verify no regression in the rest of the suite**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `55 passed` (53 baseline + 2 new).

- [ ] **Step 7: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/index.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
feat(schema): add notes.status column with idempotent migration

Prereq for the Stale verb (next commit). open_db() runs a one-time
ALTER on legacy DBs that predate this column — re-indexing not
required for read paths, but the column will only populate after
the next `basalt index` run.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Parse and store status in `vault.py` + `upsert_note`

**Files:**
- Modify: `src/basalt/vault.py` (Note dataclass + parse_note)
- Modify: `src/basalt/index.py` (upsert_note stores status)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_smoke.py`:

```python
def test_parse_note_extracts_status_frontmatter(tmp_path):
    """parse_note must surface `status: active` from frontmatter as Note.status."""
    from basalt.vault import parse_note
    note_path = tmp_path / "active.md"
    note_path.write_text(
        "---\nstatus: active\n---\n\nbody body\n"
    )
    note = parse_note(note_path, tmp_path)
    assert note is not None
    assert note.status == "active"


def test_parse_note_normalizes_status_case(tmp_path):
    """status: Active becomes 'active' (case-insensitive matching downstream)."""
    from basalt.vault import parse_note
    note_path = tmp_path / "active.md"
    note_path.write_text("---\nstatus: Active\n---\n\nbody\n")
    note = parse_note(note_path, tmp_path)
    assert note.status == "active"


def test_parse_note_returns_none_status_when_missing(tmp_path):
    """No status frontmatter → Note.status is None (caller decides default)."""
    from basalt.vault import parse_note
    note_path = tmp_path / "untagged.md"
    note_path.write_text("just body, no frontmatter\n")
    note = parse_note(note_path, tmp_path)
    assert note is not None
    assert note.status is None


def test_upsert_note_persists_status(tmp_path):
    """After upsert, conn.execute returns the stored status."""
    from basalt.index import open_db, upsert_note
    from basalt.vault import parse_note
    db = tmp_path / "scratch.db"
    note_path = tmp_path / "note.md"
    note_path.write_text("---\nstatus: draft\n---\n\nbody\n")
    note = parse_note(note_path, tmp_path)
    conn = open_db(db)
    nid = upsert_note(conn, note)
    row = conn.execute("SELECT status FROM notes WHERE id = ?", (nid,)).fetchone()
    assert row["status"] == "draft"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_smoke.py::test_parse_note_extracts_status_frontmatter tests/test_smoke.py::test_parse_note_normalizes_status_case tests/test_smoke.py::test_parse_note_returns_none_status_when_missing tests/test_smoke.py::test_upsert_note_persists_status -v`

Expected: all four FAIL with `AttributeError: 'Note' object has no attribute 'status'`.

- [ ] **Step 3: Add `status` field to Note dataclass**

Open `src/basalt/vault.py`. Find the `Note` dataclass (line ~20). Add the field:

```python
@dataclass
class Note:
    path: Path
    rel_path: str
    stem: str
    title: str                   # frontmatter title or stem
    created: str | None
    updated: str | None
    tags: list[str]
    status: str | None           # frontmatter status (lowercased), None if absent
    content: str = ""            # body without frontmatter
    wikilinks: list[str] = field(default_factory=list)
    word_count: int = 0
    content_hash: str = ""
```

(Adjust the field order if the existing dataclass differs — `status` goes after `tags` to match SQL column order.)

- [ ] **Step 4: Parse status in `parse_note()`**

In `parse_note()`, after the `tags` parsing block (around line 105), add:

```python
status_raw = fm.get("status")
status = str(status_raw).strip().lower() if status_raw else None
```

Then add `status=status,` to the `Note(...)` construction.

- [ ] **Step 5: Persist status in `upsert_note()`**

Open `src/basalt/index.py`. Find `upsert_note()`. The INSERT statement should include `status`:

```python
cur = conn.execute(
    """
    INSERT INTO notes (rel_path, stem, title, created, updated, word_count, content, content_hash, tags, status)
    VALUES (:rel_path, :stem, :title, :created, :updated, :word_count, :content, :content_hash, :tags, :status)
    ON CONFLICT(rel_path) DO UPDATE SET
        stem=excluded.stem,
        title=excluded.title,
        created=COALESCE(notes.created, excluded.created),
        updated=excluded.updated,
        word_count=excluded.word_count,
        content=excluded.content,
        content_hash=excluded.content_hash,
        tags=excluded.tags,
        status=excluded.status
    RETURNING id
    """,
    {
        "rel_path": note.rel_path,
        "stem": note.stem,
        "title": note.title,
        "created": note.created,
        "updated": note.updated,
        "word_count": note.word_count,
        "content": note.content,
        "content_hash": note.content_hash,
        "tags": ",".join(note.tags),
        "status": note.status,
    },
)
```

(Read the existing INSERT first — preserve the exact parameter style. Only adding `status` to the column list, VALUES list, ON CONFLICT clause, and the param dict.)

- [ ] **Step 6: Run tests — all four should pass**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_smoke.py::test_parse_note_extracts_status_frontmatter tests/test_smoke.py::test_parse_note_normalizes_status_case tests/test_smoke.py::test_parse_note_returns_none_status_when_missing tests/test_smoke.py::test_upsert_note_persists_status -v`

Expected: all PASS.

- [ ] **Step 7: Run full suite — confirm no regression**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `59 passed` (55 + 4).

- [ ] **Step 8: Re-index the real vault to populate status for live testing**

Run: `~/Projects/basalt/.venv/bin/basalt index`

Expected: re-embed step skips (cached), but rows are updated. Quick check:

```bash
sqlite3 ~/.basalt/basalt.db "SELECT status, COUNT(*) FROM notes GROUP BY status ORDER BY 2 DESC LIMIT 10"
```

Expected: a distribution like `(null) | 1500`, `active | 200`, `validated | 30`, etc. (Most notes lack status — that's fine.)

- [ ] **Step 9: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/vault.py src/basalt/index.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
feat(parse): extract status frontmatter, store in notes.status

Pulls fm.get("status"), lowercases, stores. Note.status is the
public surface. Unblocks the Stale verb.

Re-index required before status-dependent queries return useful
data — the index step uses upsert so existing rows pick up status
on the next run without losing other fields.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `stale.py` module — `StaleFinding` + `find_stale`

**Files:**
- Create: `src/basalt/stale.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: Write the failing tests for find_stale on a fixture DB**

Append to `tests/test_smoke.py`:

```python
def _build_stale_fixture(tmp_path):
    """Build a 4-note fixture DB: stale active, fresh active, archived, no-status."""
    from basalt.index import open_db, upsert_note, replace_links, resolve_link_targets
    from basalt.vault import Note
    from pathlib import Path
    from datetime import date, timedelta

    db = tmp_path / "stale.db"
    conn = open_db(db)
    today = date.today()
    long_ago = (today - timedelta(days=180)).isoformat()
    recent = (today - timedelta(days=10)).isoformat()

    def mk(rel, status, updated_date):
        return Note(
            path=Path(rel),
            rel_path=rel,
            stem=Path(rel).stem,
            title=Path(rel).stem,
            created=long_ago,
            updated=updated_date,
            tags=[],
            status=status,
            content="body " * 50,
            wikilinks=[],
            word_count=50,
            content_hash=f"hash-{rel}",
        )

    upsert_note(conn, mk("stale-active.md",     "active",   long_ago))
    upsert_note(conn, mk("fresh-active.md",     "active",   recent))
    upsert_note(conn, mk("stale-archived.md",   "archived", long_ago))
    upsert_note(conn, mk("stale-untagged.md",   None,       long_ago))
    conn.commit()
    return conn, db


def test_find_stale_flags_active_with_old_updated(tmp_path):
    """A note with status=active and updated=180d ago should be flagged stale."""
    from basalt.stale import find_stale
    conn, _ = _build_stale_fixture(tmp_path)
    results = find_stale(conn, days=90, top_n=10)
    paths = {r.rel_path for r in results}
    assert "stale-active.md" in paths


def test_find_stale_skips_recently_updated(tmp_path):
    """A note with status=active but updated within threshold is not stale."""
    from basalt.stale import find_stale
    conn, _ = _build_stale_fixture(tmp_path)
    results = find_stale(conn, days=90, top_n=10)
    paths = {r.rel_path for r in results}
    assert "fresh-active.md" not in paths


def test_find_stale_skips_archived_and_untagged(tmp_path):
    """status in {archived, None} should never be flagged stale by default."""
    from basalt.stale import find_stale
    conn, _ = _build_stale_fixture(tmp_path)
    results = find_stale(conn, days=90, top_n=10)
    paths = {r.rel_path for r in results}
    assert "stale-archived.md" not in paths
    assert "stale-untagged.md" not in paths
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_smoke.py::test_find_stale_flags_active_with_old_updated tests/test_smoke.py::test_find_stale_skips_recently_updated tests/test_smoke.py::test_find_stale_skips_archived_and_untagged -v`

Expected: all FAIL with `ModuleNotFoundError: No module named 'basalt.stale'`.

- [ ] **Step 3: Implement `stale.py`**

Create `src/basalt/stale.py`:

```python
"""Stale verb — surfaces notes whose status frontmatter says 'live' but
whose edit pattern says abandoned.

Mechanical only: no LLM, no embeddings. Three columns + a backward JOIN.
Same shape as Drift (stated-vs-lived) but per-note, not per-project.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta


# Default status values that should be "live." If your vault uses different
# conventions, override via the CLI `--status` flag.
DEFAULT_LIVE_STATUSES = ("active", "draft", "wip")
DEFAULT_WINDOW_DAYS = 90


@dataclass
class StaleFinding:
    rel_path: str
    status: str
    updated: str | None              # ISO date string
    days_since_updated: int
    inbound_links_since_threshold: int   # new inbound links inside the window
    threshold_days: int


def find_stale(
    conn: sqlite3.Connection,
    *,
    days: int = DEFAULT_WINDOW_DAYS,
    status_set: tuple[str, ...] = DEFAULT_LIVE_STATUSES,
    top_n: int = 10,
) -> list[StaleFinding]:
    """Find notes whose frontmatter claims live status but show no edits or
    inbound link activity within `days`. Ordered by days_since_updated DESC."""
    threshold_iso = (date.today() - timedelta(days=days)).isoformat()
    today = date.today()

    placeholders = ",".join("?" for _ in status_set)
    sql = f"""
        SELECT id, rel_path, status, updated
        FROM notes
        WHERE LOWER(status) IN ({placeholders})
          AND (updated IS NULL OR updated < ?)
        ORDER BY updated ASC NULLS FIRST
    """
    params = [s.lower() for s in status_set] + [threshold_iso]
    candidates = conn.execute(sql, params).fetchall()

    findings: list[StaleFinding] = []
    for row in candidates:
        # Count inbound links — for v0, we don't have a per-link timestamp,
        # so we count ALL inbound links and treat 0 as "no validators."
        # When link discovery dates land, switch to `WHERE discovered_at > ?`.
        n_inbound = conn.execute(
            "SELECT COUNT(*) FROM links WHERE target_note_id = ?",
            (row["id"],),
        ).fetchone()[0]
        if n_inbound > 0:
            # If anything links to this note, treat it as still in the web.
            continue
        days_since = _days_since(row["updated"], today)
        findings.append(StaleFinding(
            rel_path=row["rel_path"],
            status=row["status"],
            updated=row["updated"],
            days_since_updated=days_since,
            inbound_links_since_threshold=0,
            threshold_days=days,
        ))
        if len(findings) >= top_n:
            break
    return findings


def _days_since(updated_iso: str | None, today: date) -> int:
    if not updated_iso:
        return 99_999
    try:
        u = date.fromisoformat(updated_iso[:10])
        return (today - u).days
    except (TypeError, ValueError):
        return 99_999
```

- [ ] **Step 4: Run tests — all should pass**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_smoke.py::test_find_stale_flags_active_with_old_updated tests/test_smoke.py::test_find_stale_skips_recently_updated tests/test_smoke.py::test_find_stale_skips_archived_and_untagged -v`

Expected: all PASS.

- [ ] **Step 5: Run full suite**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `62 passed` (59 + 3).

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/stale.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
feat(stale): find_stale + StaleFinding for the Stale verb

Mechanical signal: status in {active, draft, wip} AND updated older
than threshold AND zero inbound links. Same shape as Drift but
per-note. No LLM, no embeddings. v0 — see Tasks.md for v1 (LLM
content-staleness judgement, Pro tier).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Falsification rules in `audit.py`

**Files:**
- Modify: `src/basalt/audit.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: Read existing falsification rule patterns**

Run: `grep -n "def evaluate\|RULES\|_rule_" ~/Projects/basalt/src/basalt/audit.py | head -10`

Goal: match the existing rule-dispatch shape (per-verb evaluator functions returning `('confirmed' | 'falsified' | 'pending', reason: str)`).

- [ ] **Step 2: Write the failing test**

Append to `tests/test_smoke.py`:

```python
def test_audit_falsifies_stale_when_frontmatter_updated(tmp_path):
    """If the note's frontmatter `updated` advances past the finding's
    logged-at date, the Stale finding is falsified (you re-validated it)."""
    from basalt.audit import _eval_frontmatter_updated  # private helper, ok for test
    payload = {
        "rel_path": "test.md",
        "logged_at": "2026-01-01",
        "updated_at_logged": "2025-09-01",
    }
    # Simulate: current note.updated is 2026-04-01 — newer than logged_at.
    verdict, reason = _eval_frontmatter_updated(
        payload, current_updated="2026-04-01"
    )
    assert verdict == "falsified"
    assert "re-validated" in reason or "updated" in reason
```

(Adjust to whatever signature the existing audit module uses — read it first, mirror the existing rule signatures exactly.)

- [ ] **Step 3: Run test — verify it fails**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_smoke.py::test_audit_falsifies_stale_when_frontmatter_updated -v`

Expected: FAIL with `ImportError` or `AttributeError` (function doesn't exist).

- [ ] **Step 4: Implement the rules**

In `src/basalt/audit.py`, after the existing rule evaluators, add three new ones matching the existing pattern. The exact shape depends on what's there — read first, mirror precisely.

Skeleton (adjust to match existing signatures):

```python
def _eval_frontmatter_updated(payload, *, current_updated):
    """Wrong if note.updated has moved past the logged date — user re-validated."""
    logged_at = payload.get("logged_at") or payload.get("updated_at_logged")
    if current_updated and logged_at and current_updated > logged_at:
        return ("falsified", f"frontmatter updated to {current_updated} since logged ({logged_at})")
    return ("pending", "")


def _eval_new_inbound_link(payload, *, current_inbound_count, logged_inbound_count):
    """Wrong if inbound link count has increased since logged."""
    if current_inbound_count > logged_inbound_count:
        return ("falsified", f"new inbound link(s): was {logged_inbound_count}, now {current_inbound_count}")
    return ("pending", "")


def _eval_status_archived(payload, *, current_status):
    """Confirmed if user has flipped status to archived or dead — they agreed."""
    if current_status in ("archived", "dead"):
        return ("confirmed", f"status flipped to {current_status}")
    return ("pending", "")
```

Then wire these into the rule dispatch table (mirror what the other verbs do).

- [ ] **Step 5: Run test — should pass**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_smoke.py::test_audit_falsifies_stale_when_frontmatter_updated -v`

Expected: PASS.

- [ ] **Step 6: Run full suite**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `63 passed` (62 + 1).

- [ ] **Step 7: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/audit.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
feat(audit): falsification rules for Stale verb

Three new evaluators:
- frontmatter_updated → falsified (user re-validated)
- new_inbound_link → falsified (the vault found it)
- status_archived → confirmed (user agreed it's dead)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `serialize.py` helper for JSON output

**Files:**
- Modify: `src/basalt/serialize.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_smoke.py`:

```python
def test_stale_to_dict_emits_expected_keys():
    from basalt.serialize import stale_to_dict
    from basalt.stale import StaleFinding
    f = StaleFinding(
        rel_path="test.md",
        status="active",
        updated="2025-09-01",
        days_since_updated=180,
        inbound_links_since_threshold=0,
        threshold_days=90,
    )
    d = stale_to_dict(f)
    assert d["rel_path"] == "test.md"
    assert d["status"] == "active"
    assert d["days_since_updated"] == 180
    assert d["threshold_days"] == 90
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_smoke.py::test_stale_to_dict_emits_expected_keys -v`

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the helper**

In `src/basalt/serialize.py`, add (matching the existing helper shapes — read first, mirror precisely):

```python
def stale_to_dict(finding) -> dict:
    """Serialize a StaleFinding for JSON output. Stable keys — schema v1."""
    return {
        "rel_path": finding.rel_path,
        "status": finding.status,
        "updated": finding.updated,
        "days_since_updated": finding.days_since_updated,
        "inbound_links_since_threshold": finding.inbound_links_since_threshold,
        "threshold_days": finding.threshold_days,
    }
```

- [ ] **Step 4: Run test — should pass**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_smoke.py::test_stale_to_dict_emits_expected_keys -v`

Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `64 passed` (63 + 1).

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/serialize.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
feat(serialize): stale_to_dict for JSON output

Stable schema-v1 keys: rel_path, status, updated, days_since_updated,
inbound_links_since_threshold, threshold_days.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: CLI `cmd_stale` + render block + brief integration

**Files:**
- Modify: `src/basalt/cli.py`

- [ ] **Step 1: Add stale to SECTIONS_AVAILABLE / SECTIONS_SHIPPED**

Open `src/basalt/cli.py`. Find the SECTIONS_* declarations:

```python
SECTIONS_AVAILABLE = {"buried-insight", "connection", "contradiction", "implicit-thesis", "drift", "all"}
SECTIONS_SHIPPED   = ["buried-insight", "connection", "contradiction", "implicit-thesis", "drift"]
```

Update to add `"stale"`:

```python
SECTIONS_AVAILABLE = {"buried-insight", "connection", "contradiction", "implicit-thesis", "drift", "stale", "all"}
SECTIONS_SHIPPED   = ["buried-insight", "connection", "contradiction", "implicit-thesis", "drift", "stale"]
```

- [ ] **Step 2: Import find_stale, StaleFinding, stale_to_dict**

At the top of `cli.py`, add:

```python
from basalt.stale import find_stale, StaleFinding, DEFAULT_WINDOW_DAYS as STALE_DAYS, DEFAULT_LIVE_STATUSES
from basalt.serialize import stale_to_dict
```

(Append `stale_to_dict` to the existing serialize import line.)

- [ ] **Step 3: Add the render function**

After the existing render helpers (after `_render_drift` or similar), add:

```python
def _render_stale_results(results: list) -> None:
    """Render stale findings in the brand voice."""
    if not results:
        return
    n = len(results)
    title = "THE STALE" if n == 1 else f"STALE  ({n})"
    console.print()
    console.rule(style="bright_black")
    console.print()
    console.print(Text(title, style="bold #D9824B"))
    console.print(Text("─────────────────────", style="#7A7269"))
    intro = (
        f"{n} note{'s' if n != 1 else ''} that say{'s' if n == 1 else ''} \"active\" "
        f"but haven't been touched in {results[0].threshold_days}+ days"
        + (f", with no inbound links since." if n == 1 else " each.")
    )
    console.print(Text(intro, style="dim"))
    console.print()
    for r in results:
        line = (
            f"  [#7A7269]→[/] [#EFE9E2]{r.rel_path}[/]\n"
            f"     [dim]status:[/] [bold]{r.status}[/]  [dim]·[/]  "
            f"[dim]last edit[/] [bold]{r.updated or 'unknown'}[/]  [dim]·[/]  "
            f"[dim]{r.days_since_updated} days ago[/]  [dim]·[/]  "
            f"[dim]0 new inbound links[/]"
        )
        console.print(line)
    console.print()
    # Falsification footer
    console.print(Text("   ⊘ Falsification — this is wrong if:", style="dim"))
    console.print(Text(f"      • the note's frontmatter `updated` advances within 30 days (you re-validated)", style="dim"))
    console.print(Text(f"      • the note receives a new inbound link within 30 days (the vault found it)", style="dim"))
    console.print(Text(f"      • status flips to `archived` or `dead` within 60 days (you agreed)", style="dim"))
    console.print()
    console.print(Text("   ▸ Re-validate     ▸ Open all     ▸ Mark archived", style="#D9824B"))
```

- [ ] **Step 4: Add `cmd_stale` command**

After `cmd_contradiction` (or wherever the verbs live in the file), add:

```python
@app.command("stale")
def cmd_stale(
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    days: int = typer.Option(STALE_DAYS, "--days", min=7, max=365,
                             help="Threshold in days since last edit (default 90)."),
    top: int = typer.Option(10, "--top", min=1, max=50),
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: 'text' or 'json'."),
):
    """Surface notes that say "live" in frontmatter but show no edits or new inbound links."""
    _require_initialized(db)
    conn = open_db(db)
    results = find_stale(conn, days=days, top_n=top)
    if fmt.strip().lower() == "json":
        conn.close()
        _emit_json({
            "schema": SCHEMA_VERSION,
            "verb": "stale",
            "threshold_days": days,
            "findings": [with_falsification(stale_to_dict(r), "stale", r) for r in results],
        })
        return
    if not results:
        _say_no_result("No stale notes found above the threshold.",
                       f"threshold = {days} days; lower it to surface more.")
        raise typer.Exit(code=1)
    _print_banner()
    _render_stale_results(results)
    for r in results:
        record_finding(conn, "stale", r)
    conn.commit()
    conn.close()
    _print_signoff("stale")
```

- [ ] **Step 5: Add stale to `cmd_brief` JSON + text paths**

Find the section in `cmd_brief` that handles each verb. Add a `stale` block alongside the others — both in the JSON path and the text path. Mirror the existing patterns exactly.

JSON path (insert near the other verbs):

```python
if section_key in ("stale", "all"):
    results = find_stale(conn, top_n=top) or []
    payload["findings"]["stale"] = [
        with_falsification(stale_to_dict(r), "stale", r) for r in results
    ]
    for r in results:
        record_finding(conn, "stale", r)
```

Text path:

```python
if section_key in ("stale", "all"):
    results = find_stale(conn, top_n=top)
    if results:
        _render_stale_results(results)
        for r in results:
            record_finding(conn, "stale", r)
    elif section_key == "stale":
        _say_no_result(...)
```

- [ ] **Step 6: Run full suite**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `64 passed` (no new tests in this task — CLI render is exercised by live smoke).

- [ ] **Step 7: Live smoke against the real vault**

Run: `~/Projects/basalt/.venv/bin/basalt stale 2>&1 | tail -30`

Expected: either the rendered stale block, OR `_say_no_result` if your vault has no qualifying notes (unlikely — you have 2,022 notes with various status frontmatter).

Visual check: the block reads cleanly, the dim/bold balance matches the existing brief sections, the falsification footer is present.

- [ ] **Step 8: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/cli.py
git commit -m "$(cat <<'EOF'
feat(cli): basalt stale + brief integration

New `basalt stale` command; render block matches the brand voice
(THE STALE header, dim-on-active-bold per-row, falsification footer,
action menu). Wired into `basalt brief --section stale` and
`--section all`. Surfaces notes that say "active"/"draft"/"wip" in
frontmatter but haven't been touched in N days with no inbound links.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: MCP server tool

**Files:**
- Modify: `src/basalt/mcp_server.py`

- [ ] **Step 1: Read the existing MCP tool registration pattern**

Run: `grep -n "tool\|register\|basalt_drift\|basalt_thesis" ~/Projects/basalt/src/basalt/mcp_server.py | head -15`

Goal: match the existing tool-registration shape.

- [ ] **Step 2: Add `basalt_stale` tool**

In `src/basalt/mcp_server.py`, mirror the shape of `basalt_drift` (the most similar verb — single command, optional `--days` param). The tool function should:
- Accept `db_path: str | None`, `days: int = 90`, `top: int = 10`
- Open DB, call `find_stale`, return JSON via the same serialize path as `cmd_stale --format json`

- [ ] **Step 3: Smoke-test the MCP server starts and lists 7 tools**

Run:
```bash
~/Projects/basalt/.venv/bin/python -c "
from basalt.mcp_server import server  # adjust to actual import
# Whatever the test pattern is — read the existing module first.
"
```

(If there's no existing test pattern for the MCP server, just start `basalt-mcp` and `kill` it after a second, confirming no immediate crash.)

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): expose basalt_stale as 7th MCP tool

Mirrors basalt_drift shape — db_path, days, top params. Returns
JSON via the same serialize path as `basalt stale --format json`.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Update site + CHANGELOG

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update README verbs section**

The README currently says "4/4 site-advertised + 1 bonus" (Buried Insight). Update to "5/4 site-advertised + 1 bonus + 1 sixth" or similar. Add a one-paragraph description of `stale` matching the others.

- [ ] **Step 2: Update CHANGELOG**

Add an entry for v0.0.16 (or next version):

```markdown
## [0.0.16] - 2026-05-12

### Added
- `basalt stale` verb — surfaces notes whose frontmatter claims live status
  (active/draft/wip) but haven't been edited in N days with zero new inbound
  links. Composes with the vault's `/stale-knowledge` slash-command workflow.
- `notes.status` column in the schema. Idempotent migration on open_db.
- `basalt_stale` MCP tool (7 tools total).
```

- [ ] **Step 3: Bump version**

In `pyproject.toml`: `version = "0.0.15"` → `version = "0.0.16"`.

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/basalt
git add README.md CHANGELOG.md pyproject.toml
git commit -m "$(cat <<'EOF'
docs: v0.0.16 — basalt stale verb

CHANGELOG entry, README verb list bumped, version bumped.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Final verification

**Files:** none

- [ ] **Step 1: Full suite green**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `64 passed` (or more if Task 7 added tests).

- [ ] **Step 2: Wheel builds clean**

Run: `cd ~/Projects/basalt && rm -rf dist/ && .venv/bin/python -m build 2>&1 | tail -3`

Expected: `Successfully built basalt_vault-0.0.16-py3-none-any.whl`.

- [ ] **Step 3: Live smoke — `basalt stale` on the real vault**

Run: `~/Projects/basalt/.venv/bin/basalt stale --days 90 2>&1 | head -40`

Expected: rendered stale block with at least one finding (or `_say_no_result` if your vault has been freakishly hygienic).

- [ ] **Step 4: Live smoke — `basalt brief --section all` includes stale**

Run: `~/Projects/basalt/.venv/bin/basalt brief --section all 2>&1 | grep -c "THE STALE"`

Expected: `1` (the section header appears once).

- [ ] **Step 5: Live smoke — `basalt audit` re-evaluates stale findings**

Run: `~/Projects/basalt/.venv/bin/basalt audit 2>&1 | tail -10`

Expected: track record summary, possibly with a `stale` row if findings logged.

---

## Self-Review

**Scope coverage (from Tasks.md entry):**
- Schema migration `notes.status` → Task 1 ✓
- Parse `fm.get("status")` in vault.py → Task 2 ✓
- `find_stale()` with `{active, draft, wip}` default → Task 3 ✓
- Falsification rules (frontmatter_updated, new_inbound_link, status_archived) → Task 4 ✓
- CLI render + brief integration → Task 6 ✓
- MCP tool → Task 7 ✓
- Composes with `/stale-knowledge` slash command → docs in Task 8 ✓
- Vault-aware thresholds → noted in Task 3, deferred to follow-up if needed (TODO: explicitly check this against `compute_vault_aware_thresholds` pattern in v1, but v0 takes `--days` directly)

**Placeholder scan:** Task 4 and Task 7 say "read existing patterns first, mirror" — that's intentional, not a placeholder. The existing audit.py and mcp_server.py modules have established shapes the engineer must match; prescribing exact line-level edits without re-reading them would create breakage.

**Type consistency:** `StaleFinding` defined in Task 3 with `rel_path / status / updated / days_since_updated / inbound_links_since_threshold / threshold_days`. Same fields referenced in Tasks 5, 6, 7. ✓

**Test math:** 53 baseline → +2 (Task 1) → +4 (Task 2) → +3 (Task 3) → +1 (Task 4) → +1 (Task 5) = **64 expected after Task 5**. Task 6 doesn't add tests (CLI render exercised by live smoke). Task 7 adds 0-1 depending on existing MCP test infrastructure.

---

## Execution Handoff

**Plan complete and saved to `~/Projects/basalt/docs/superpowers/plans/2026-05-12-stale-verb.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks

**2. Inline Execution** — execute tasks in this session using executing-plans, batch with checkpoints

**Which approach?**
