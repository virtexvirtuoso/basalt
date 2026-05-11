"""Ollama embedding provider — localhost-only, no network calls.

This is the ONLY embedding provider bundled in the Open tier.
All HTTP calls are confined to localhost:11434.
"""

from __future__ import annotations

import asyncio
from typing import Iterable

import httpx
import numpy as np

from ..embed import EMBED_MAX_CHARS, EMBED_CONCURRENCY
from . import EmbeddingProvider


OLLAMA_URL = "http://localhost:11434"


class OllamaProvider:
    """Ollama embedding provider for localhost-only embeddings.

    This provider makes HTTP calls ONLY to localhost:11434.
    No external network calls are possible.
    """

    def __init__(self, model: str = "nomic-embed-text", ollama_url: str = OLLAMA_URL):
        self.model = model
        self._ollama_url = ollama_url

    async def _embed_one_async(
        self,
        client: httpx.AsyncClient,
        text: str,
    ) -> np.ndarray:
        """Embed a single text asynchronously."""
        if len(text) > EMBED_MAX_CHARS:
            text = text[:EMBED_MAX_CHARS]
        r = await client.post(
            f"{self._ollama_url}/api/embeddings",
            json={"model": self.model, "prompt": text},
            timeout=60.0,
        )
        r.raise_for_status()
        vec = np.asarray(r.json()["embedding"], dtype=np.float32)
        n = np.linalg.norm(vec)
        if n > 0:
            vec = vec / n
        return vec

    def embed_batch(
        self,
        texts: Iterable[str],
        on_progress: callable | None = None,
        progress_every: int = 50,
    ) -> list[np.ndarray]:
        """Embed a batch of texts via Ollama.

        Args:
            texts: Iterable of text strings to embed.
            on_progress: Optional callback for progress updates.
            progress_every: Call on_progress every N embeddings.

        Returns:
            List of normalized float32 numpy arrays.
        """
        texts_list = list(texts)
        results: list[np.ndarray] = [None] * len(texts_list)
        sem = asyncio.Semaphore(EMBED_CONCURRENCY)
        done = 0

        async def embed_one(index: int, text: str):
            nonlocal done
            if len(text) > EMBED_MAX_CHARS:
                text = text[:EMBED_MAX_CHARS]
            async with sem:
                try:
                    async with httpx.AsyncClient(
                        http2=False,
                        limits=httpx.Limits(max_connections=EMBED_CONCURRENCY * 2),
                    ) as client:
                        vec = await self._embed_one_async(client, text)
                    results[index] = vec
                except Exception as e:
                    if on_progress:
                        on_progress(f"  ! embed failed for text {index}: {e}")
                    results[index] = np.zeros(0, dtype=np.float32)
                finally:
                    done += 1
                    if on_progress and done % progress_every == 0:
                        on_progress(f"  embedded {done}/{len(texts_list)}…")

        asyncio.gather(*(embed_one(i, t) for i, t in enumerate(texts_list)))
        return results


__all__ = ["OllamaProvider"]
