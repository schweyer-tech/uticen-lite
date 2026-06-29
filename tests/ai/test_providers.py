"""Unit tests for the provider seam (no network, SDKs not required).

Anthropic/OpenAI need an env var to be "enabled"; Ollama defaults to localhost.
The public functions must never import the SDKs — only ``draft_rule_spec`` does.
"""

from __future__ import annotations

import builtins

from uticen_lite.ai import providers


def test_provider_key_present_reads_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert providers.provider_key_present("anthropic") is True
    assert providers.provider_key_present("openai") is False

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert providers.provider_key_present("anthropic") is False


def test_ollama_defaults_enabled_and_respects_host(monkeypatch):
    # Ollama is local — enabled even without OLLAMA_HOST (defaults to localhost).
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert providers.provider_key_present("ollama") is True
    monkeypatch.setenv("OLLAMA_HOST", "http://elsewhere:11434")
    assert providers.provider_key_present("ollama") is True


def test_unknown_provider_not_present():
    assert providers.provider_key_present("nope") is False


def test_available_providers_marks_enabled(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    by_id = {p["id"]: p for p in providers.available_providers()}
    assert by_id["anthropic"]["enabled"] is True
    assert by_id["openai"]["enabled"] is False
    assert by_id["ollama"]["enabled"] is True  # localhost default
    # Each carries label + models + default_model for the picker.
    assert by_id["anthropic"]["default_model"] == "claude-opus-4-8"
    assert "claude-opus-4-8" in by_id["anthropic"]["models"]
    assert by_id["anthropic"]["label"]


def test_public_functions_never_import_sdks(monkeypatch):
    # Simulate the [ai] SDKs being absent: any import of anthropic/openai blows up.
    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name in ("anthropic", "openai") or name.startswith(("anthropic.", "openai.")):
            raise ImportError(f"{name} is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    # These must work with the SDKs unimportable — they never touch the SDK.
    assert providers.provider_key_present("anthropic") is True
    out = providers.available_providers()
    assert any(p["id"] == "anthropic" for p in out)
