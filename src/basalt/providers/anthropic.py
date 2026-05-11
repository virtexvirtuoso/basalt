"""Anthropic LLM provider stub — Pro tier only.

This provider is NOT bundled in the Open build. It raises
`NotImplementedError` until the user installs `basalt-vault[pro]`
and supplies their own Anthropic API key.

Per [[Decision-Pro-Tier-BYO-Key-2026-05-11]]: Basalt never hosts
inference. Pro tier = BYO-key only.
"""

from __future__ import annotations

import os
from typing import Iterable

import numpy as np

from . import EmbeddingProvider, LLMProvider


class AnthropicProvider:
    """Anthropic LLM provider — Pro tier, BYO-key.

    This provider is NOT available in the Open build. Attempting to
    use it without installing `basalt-vault[pro]` will raise
    `NotImplementedError`.

    Requires ANTHROPIC_API_KEY environment variable.
    """

    def __init__(self, model: str = "claude-sonnet-4-5-20250929", api_key: str | None = None):
        """Initialize Anthropic provider.

        Args:
            model: Anthropic model to use (default: claude-sonnet-4-5-20250929).
            api_key: Anthropic API key. If None, reads ANTHROPIC_API_KEY env.

        Raises:
            NotImplementedError: Always — this is a stub until [pro] is installed.
        """
        # Always raise — this is the stub. The real implementation
        # lives in the [pro] extra and is imported only when that
        # extra is installed.
        raise NotImplementedError(
            "AnthropicProvider requires basalt-vault[pro]. "
            "Install with: pip install 'basalt-vault[pro]'. "
            "See [[Decision-Pro-Tier-BYO-Key-2026-05-11]]."
        )


class AnthropicEmbeddingProvider:
    """Anthropic embedding provider stub — Pro tier only.

    Placeholder for future Claude Embeddings support.
    """

    model: str = "claude-embed-20240924"

    def __init__(self, api_key: str | None = None):
        raise NotImplementedError(
            "AnthropicEmbeddingProvider requires basalt-vault[pro]. "
            "Install with: pip install 'basalt-vault[pro]'."
        )

    def embed_batch(
        self,
        texts: Iterable[str],
        on_progress: callable | None = None,
        progress_every: int = 50,
    ) -> list[np.ndarray]:
        """Stub — always raises NotImplementedError."""
        raise NotImplementedError(
            "Anthropic embedding requires basalt-vault[pro]."
        )


__all__ = ["AnthropicProvider", "AnthropicEmbeddingProvider"]
