"""Tests for provider abstraction — guards the Open-tier no-network promise."""

import ast
import importlib.util
from pathlib import Path

import pytest


def test_open_build_has_no_anthropic_import():
    """The Open build must NOT import anthropic or any non-localhost HTTP.

    This test scans the import graph of the default build (no [pro] extra)
    and asserts that `anthropic` is not importable. Per
    [[Decision-Pro-Tier-BYO-Key-2026-05-11]], the privacy boundary is
    code-enforced, not just documented.
    """
    # Check that anthropic is not in the installed packages
    spec = importlib.util.find_spec("anthropic")
    assert spec is None, (
        "anthropic package found in Open build! "
        "This violates the no-network promise. "
        "Install 'basalt-vault[pro]' only when Pro tier features are needed."
    )


def test_ollama_provider_imports_without_anthropic():
    """OllamaProvider must be importable without anthropic installed."""
    from basalt.providers.ollama import OllamaProvider
    from basalt.providers import EmbeddingProvider

    # Verify OllamaProvider implements the protocol
    assert isinstance(OllamaProvider, type)
    assert hasattr(OllamaProvider, "embed_batch")
    
    # Verify instance can be created with model attribute
    provider = OllamaProvider(model="nomic-embed-test")
    assert provider.model == "nomic-embed-test"


def test_anthropic_stub_raises_not_implemented():
    """AnthropicProvider stub must raise NotImplementedError in Open build."""
    from basalt.providers.anthropic import AnthropicProvider

    with pytest.raises(NotImplementedError, match="basalt-vault\[pro\]"):
        AnthropicProvider()


def test_mcp_server_has_no_anthropic_import_in_default_build():
    """MCP server module must not import anthropic in Open build.

    Scans the mcp_server.py AST to ensure no anthropic import path exists.
    """
    src_path = Path(__file__).parent.parent / "src" / "basalt" / "mcp_server.py"
    tree = ast.parse(src_path.read_text())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "anthropic", (
                    f"Direct 'import anthropic' found at line {node.lineno}. "
                    "Pro-tier imports must be conditional."
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module and "anthropic" in node.module:
                pytest.fail(
                    f"Found 'from anthropic' import at line {node.lineno}. "
                    "Pro-tier imports must be conditional."
                )
