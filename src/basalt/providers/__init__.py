"""LLM provider abstraction — preserves the Open-tier no-network promise.

The Open build bundles only `OllamaProvider` (localhost embeddings).
Anthropic and other cloud providers are in the `[pro]` extra and
raise `NotImplementedError` until explicitly installed + configured.

This interface is the code-enforced privacy boundary per
[[Decision-Pro-Tier-BYO-Key-2026-05-11]].
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Protocol

import numpy as np


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers.

    Implementations must support batch embedding with progress callbacks.
    """

    model: str
    """The embedding model identifier (e.g., 'nomic-embed-text')."""

    @abstractmethod
    def embed_batch(
        self,
        texts: Iterable[str],
        on_progress: callable | None = None,
        progress_every: int = 50,
    ) -> list[np.ndarray]:
        """Embed a batch of texts.

        Args:
            texts: Iterable of text strings to embed.
            on_progress: Optional callback for progress updates.
            progress_every: Call on_progress every N embeddings.

        Returns:
            List of normalized float32 numpy arrays (one per input text).
        """
        ...


class LLMProvider(Protocol):
    """Protocol for LLM providers (Pro tier only).

    Not implemented in the Open build. Pro tier users supply their own
    API keys; Basalt never hosts inference.
    """

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
    ) -> str:
        """Generate a completion.

        Args:
            system: System prompt.
            user: User prompt.
            max_tokens: Maximum tokens to generate.

        Returns:
            Generated completion text.
        """
        ...


__all__ = ["EmbeddingProvider", "LLMProvider"]
