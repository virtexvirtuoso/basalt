"""Walk a Markdown vault, parse frontmatter, extract wikilinks."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

import frontmatter

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
EXCLUDE_DIRS = {".git", ".obsidian", ".stversions", ".stfolder", ".trash", "node_modules", ".claude"}


@dataclass
class Note:
    path: Path                   # absolute path on disk
    rel_path: str                # path relative to vault root
    stem: str                    # filename without .md
    title: str                   # frontmatter title or stem
    created: date | None
    updated: date | None
    tags: list[str] = field(default_factory=list)
    content: str = ""            # body without frontmatter
    wikilinks: list[str] = field(default_factory=list)   # raw link targets
    word_count: int = 0
    content_hash: str = ""


def _coerce_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(v[:19], fmt).date()
            except ValueError:
                continue
    return None


def _extract_wikilinks(text: str) -> list[str]:
    """Pull [[targets]] (without alias suffixes or anchors)."""
    out = []
    for raw in WIKILINK_RE.findall(text):
        target = raw.split("|")[0].split("#")[0].strip()
        if target:
            out.append(target)
    return out


def _file_dates_fallback(path: Path) -> tuple[date, date]:
    st = path.stat()
    return (
        datetime.fromtimestamp(st.st_birthtime if hasattr(st, "st_birthtime") else st.st_ctime).date(),
        datetime.fromtimestamp(st.st_mtime).date(),
    )


def _is_excluded(p: Path) -> bool:
    return any(part in EXCLUDE_DIRS or part.startswith(".") and part not in {".", ".."} and part != p.parts[-1]
               for part in p.parts)


def parse_note(path: Path, vault_root: Path) -> Note | None:
    """Parse a single .md file. Returns None if unparseable."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    try:
        post = frontmatter.loads(raw)
    except Exception:
        post = frontmatter.Post(raw, **{})

    fm = post.metadata or {}
    body = post.content or ""

    created = _coerce_date(fm.get("created"))
    updated = _coerce_date(fm.get("updated"))
    fb_created, fb_updated = _file_dates_fallback(path)
    if created is None:
        created = fb_created
    if updated is None:
        updated = fb_updated

    rel_path = str(path.relative_to(vault_root))
    stem = path.stem
    title = str(fm.get("title") or stem)

    tags_raw = fm.get("tags", [])
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    elif isinstance(tags_raw, list):
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    else:
        tags = []

    return Note(
        path=path,
        rel_path=rel_path,
        stem=stem,
        title=title,
        created=created,
        updated=updated,
        tags=tags,
        content=body,
        wikilinks=_extract_wikilinks(body),
        word_count=len(body.split()),
        content_hash=hashlib.sha256(body.encode("utf-8", "replace")).hexdigest(),
    )


def walk_vault(vault_root: Path) -> Iterator[Note]:
    """Yield every parseable .md file in the vault."""
    vault_root = vault_root.resolve()
    for path in vault_root.rglob("*.md"):
        # Skip excluded directories
        rel_parts = path.relative_to(vault_root).parts
        if any(p in EXCLUDE_DIRS for p in rel_parts):
            continue
        note = parse_note(path, vault_root)
        if note is not None and note.word_count > 0:
            yield note
