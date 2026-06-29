"""Provider seam for AI-assisted rule drafting.

Three backends — Anthropic (default), OpenAI, and local Ollama — each returning a
raw ``rule_spec`` dict that the validation gate in :mod:`uticen_lite.ai.draft`
re-validates. **The heavy SDKs (``anthropic`` / ``openai``) are imported lazily,
inside each backend's ``draft_rule_spec``** — never at module import — so the
control plane runs without the optional ``[ai]`` extra (learning 0003) and the
Pyodide-safe core gains no hard dependency.

Secrets live only in environment variables read by the SDK at call time; nothing
here stores a key. ``provider_key_present`` and ``available_providers`` decide
*offline-by-default* eligibility without importing any SDK.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Protocol, runtime_checkable

# Provider registry. ``models`` are editable defaults surfaced in the picker;
# ``env`` is the environment variable that gates "enabled".
PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Anthropic",
        "default_model": "claude-opus-4-8",
        "models": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
        "env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "label": "OpenAI",
        "default_model": "gpt-4o",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "env": "OPENAI_API_KEY",
    },
    "ollama": {
        "label": "Local (Ollama)",
        "default_model": "llama3.1",
        "models": ["llama3.1", "mistral"],
        "env": "OLLAMA_HOST",
    },
}

_OLLAMA_DEFAULT_HOST = "http://localhost:11434"


@runtime_checkable
class Provider(Protocol):
    """A backend that drafts a raw ``rule_spec`` dict from objective + data.

    The returned dict is never trusted — :func:`uticen_lite.ai.draft.draft_and_validate`
    re-validates it with ``parse_rule_spec`` + a run on the sample before it
    reaches the rule builder.
    """

    def draft_rule_spec(
        self, objective: str, source_schema: dict, data_sample: dict, *, model: str
    ) -> dict: ...


def provider_key_present(provider: str) -> bool:
    """True if *provider* is eligible to be called (its key/env is present).

    Ollama is local and defaults to ``http://localhost:11434`` when ``OLLAMA_HOST``
    is unset, so it is always eligible. For the cloud providers, the env var named
    in :data:`PROVIDERS` must be set to a non-empty value. Unknown providers are
    never eligible. **This never imports the [ai] SDKs.**
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        return False
    if provider == "ollama":
        return True
    return bool(os.environ.get(spec["env"], "").strip())


def available_providers() -> list[dict[str, Any]]:
    """Picker rows: ``[{id, label, models, default_model, env, needs_key, enabled}]``.

    ``enabled`` is :func:`provider_key_present`. ``env`` is the exact environment
    variable that gates the provider (so the UI can name it) and ``needs_key`` is
    ``True`` for the cloud providers (the var holds an API key) and ``False`` for
    local Ollama (always eligible; ``OLLAMA_HOST`` only *overrides* the localhost
    default). **Never imports the [ai] SDKs.**
    """
    return [
        {
            "id": pid,
            "label": spec["label"],
            "models": list(spec["models"]),
            "default_model": spec["default_model"],
            "env": spec["env"],
            "needs_key": pid != "ollama",
            "enabled": provider_key_present(pid),
        }
        for pid, spec in PROVIDERS.items()
    ]


# --------------------------------------------------------------------------- #
# Backends — SDK imports happen INSIDE draft_rule_spec, never at module import.
# --------------------------------------------------------------------------- #
class _AnthropicProvider:
    def draft_rule_spec(
        self, objective: str, source_schema: dict, data_sample: dict, *, model: str
    ) -> dict:
        import anthropic  # type: ignore[import-not-found]  # lazy; only with [ai] extra

        from uticen_lite.ai.draft import (
            RULE_SPEC_JSON_SCHEMA,
            system_prompt,
            user_prompt,
        )

        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the env
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            system=system_prompt(),
            messages=[{"role": "user", "content": user_prompt(objective, source_schema,
                                                               data_sample)}],
            output_config={
                "format": {"type": "json_schema", "schema": RULE_SPEC_JSON_SCHEMA}
            },
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return _loads_dict(text)


class _OpenAIProvider:
    def draft_rule_spec(
        self, objective: str, source_schema: dict, data_sample: dict, *, model: str
    ) -> dict:
        import openai  # type: ignore[import-not-found]  # lazy; only with [ai] extra

        from uticen_lite.ai.draft import (
            RULE_SPEC_JSON_SCHEMA,
            system_prompt,
            user_prompt,
        )

        client = openai.OpenAI()  # reads OPENAI_API_KEY from the env
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": user_prompt(objective, source_schema, data_sample)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "rule_spec",
                    "schema": RULE_SPEC_JSON_SCHEMA,
                    "strict": True,
                },
            },
        )
        return _loads_dict(resp.choices[0].message.content or "")


class _OllamaProvider:
    def draft_rule_spec(
        self, objective: str, source_schema: dict, data_sample: dict, *, model: str
    ) -> dict:
        # Stdlib only — keeps Ollama dependency-free. The schema is embedded in
        # the prompt text and JSON mode is requested.
        from uticen_lite.ai.draft import (
            RULE_SPEC_JSON_SCHEMA,
            system_prompt,
            user_prompt,
        )

        host = os.environ.get("OLLAMA_HOST", "").strip() or _OLLAMA_DEFAULT_HOST
        prompt = (
            f"{system_prompt()}\n\n"
            f"Output JSON matching this schema exactly:\n"
            f"{json.dumps(RULE_SPEC_JSON_SCHEMA)}\n\n"
            f"{user_prompt(objective, source_schema, data_sample)}"
        )
        payload = json.dumps(
            {"model": model, "prompt": prompt, "format": "json", "stream": False}
        ).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 (host is operator-configured, localhost default)
            f"{host.rstrip('/')}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as r:  # noqa: S310
            body = json.loads(r.read().decode("utf-8"))
        return _loads_dict(body.get("response", ""))


_BACKENDS: dict[str, type] = {
    "anthropic": _AnthropicProvider,
    "openai": _OpenAIProvider,
    "ollama": _OllamaProvider,
}


def get_provider(provider: str) -> Provider:
    """Lazy-construct a backend. The SDK import happens inside ``draft_rule_spec``,
    so constructing the backend object does not require the ``[ai]`` extra."""
    try:
        return _BACKENDS[provider]()
    except KeyError as exc:
        raise ValueError(f"unknown provider {provider!r}") from exc


def _loads_dict(text: str) -> dict:
    """Parse a model's text response as a JSON object (raises on non-object)."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("provider did not return a JSON object")
    return data
