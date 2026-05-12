"""Embed notes via Ollama. Cache in SQLite keyed on content_hash."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Iterable

import httpx
import numpy as np


OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"
EMBED_MAX_CHARS = 4000     # truncate aggressively — speed > marginal quality
EMBED_CONCURRENCY = 6      # parallel single-input calls — empirically faster than batched on M1 / nomic-embed-text


def _normalize(vec: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(vec)
    return vec / n if n > 0 else vec


async def _embed_one_async(
    client: httpx.AsyncClient, model: str, text: str, ollama_url: str = OLLAMA_URL
) -> np.ndarray:
    """Embed one text asynchronously."""
    if len(text) > EMBED_MAX_CHARS:
        text = text[:EMBED_MAX_CHARS]
    r = await client.post(
        f"{ollama_url.rstrip('/')}/api/embed",
        json={"model": model, "input": text},
        timeout=60.0,
    )
    r.raise_for_status()
    payload = r.json()
    raw = payload["embeddings"][0] if "embeddings" in payload else payload["embedding"]
    return _normalize(np.asarray(raw, dtype=np.float32))


def _embed_one(client: httpx.Client, model: str, text: str, ollama_url: str = OLLAMA_URL) -> np.ndarray:
    """Sync fallback. Returns float32 numpy array."""
    if len(text) > EMBED_MAX_CHARS:
        text = text[:EMBED_MAX_CHARS]
    r = client.post(
        f"{ollama_url.rstrip('/')}/api/embed",
        json={"model": model, "input": text},
        timeout=60.0,
    )
    r.raise_for_status()
    payload = r.json()
    raw = payload["embeddings"][0] if "embeddings" in payload else payload["embedding"]
    return _normalize(np.asarray(raw, dtype=np.float32))


def _vec_to_blob(v: np.ndarray) -> bytes:
    return v.astype(np.float32).tobytes()


def _blob_to_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


async def _embed_async(
    todo: list,
    model: str,
    on_result,
    on_progress,
    progress_every: int,
    ollama_url: str = OLLAMA_URL,
) -> int:
    """Embed `todo` concurrently (single-input calls fanned out via semaphore)."""
    sem = asyncio.Semaphore(EMBED_CONCURRENCY)
    done = 0
    total = len(todo)

    async with httpx.AsyncClient(
        http2=False, limits=httpx.Limits(max_connections=EMBED_CONCURRENCY * 2)
    ) as client:

        async def one(row):
            nonlocal done
            text = (row["title"] + "\n\n" + row["content"]).strip()
            if not text:
                done += 1
                return
            async with sem:
                try:
                    vec = await _embed_one_async(client, model, text, ollama_url)
                except Exception as e:
                    if on_progress:
                        on_progress(f"  ! embed failed for {row['rel_path']}: {e}")
                    done += 1
                    return
                on_result(row, vec)
                done += 1
                if on_progress and done % progress_every == 0:
                    on_progress(f"  embedded {done}/{total}…")

        await asyncio.gather(*(one(r) for r in todo))
    return done


def ensure_embeddings(
    conn: sqlite3.Connection,
    model: str = DEFAULT_MODEL,
    progress_every: int = 50,
    on_progress=None,
    ollama_url: str = OLLAMA_URL,
) -> tuple[int, int]:
    """For every note whose embedding is missing or stale, compute via Ollama.
    Returns (computed, skipped)."""
    rows = conn.execute(
        """
        SELECT n.id, n.rel_path, n.title, n.content, n.content_hash,
               e.content_hash AS cached_hash, e.model AS cached_model
        FROM notes n
        LEFT JOIN embeddings e ON e.note_id = n.id
        """
    ).fetchall()

    todo = [
        r for r in rows
        if r["cached_hash"] != r["content_hash"] or r["cached_model"] != model
    ]
    skipped = len(rows) - len(todo)
    if not todo:
        return 0, skipped

    results: list[tuple[int, str, str, int, bytes]] = []

    def on_result(row, vec):
        results.append((row["id"], model, row["content_hash"], len(vec), _vec_to_blob(vec)))
        # Persist in chunks to avoid losing all work on crash
        if len(results) >= 100:
            conn.executemany(
                """
                INSERT INTO embeddings (note_id, model, content_hash, dim, vec)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(note_id) DO UPDATE SET
                    model=excluded.model,
                    content_hash=excluded.content_hash,
                    dim=excluded.dim,
                    vec=excluded.vec
                """,
                results,
            )
            conn.commit()
            results.clear()

    asyncio.run(_embed_async(todo, model, on_result, on_progress, progress_every, ollama_url))

    if results:
        conn.executemany(
            """
            INSERT INTO embeddings (note_id, model, content_hash, dim, vec)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                model=excluded.model,
                content_hash=excluded.content_hash,
                dim=excluded.dim,
                vec=excluded.vec
            """,
            results,
        )
    conn.commit()

    computed_total = conn.execute(
        "SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)
    ).fetchone()[0] - skipped
    return max(computed_total, len(todo)), skipped


def load_embeddings(conn: sqlite3.Connection) -> tuple[list[int], np.ndarray]:
    """Load all embeddings into a numpy matrix. Returns (note_ids, matrix)."""
    rows = conn.execute("SELECT note_id, vec FROM embeddings ORDER BY note_id").fetchall()
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32)
    ids = [r["note_id"] for r in rows]
    vecs = np.stack([_blob_to_vec(r["vec"]) for r in rows])
    return ids, vecs
