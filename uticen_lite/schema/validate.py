"""JSON-Schema validation helpers for Uticen SDK documents.

Each ``validate_*`` function accepts a plain ``dict`` and returns a list of
human-readable error strings.  An empty list means the document is valid.

Schemas are loaded from the package directory via ``importlib.resources`` so
they work whether the package is installed as a wheel or run from source.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

import jsonschema

_SCHEMA_DIR = files("uticen_lite.schema")


def load_schema(name: str) -> dict[str, Any]:
    """Load a packaged JSON-Schema file by base name (e.g. ``"control.schema.json"``)."""
    data = _SCHEMA_DIR.joinpath(name).read_text(encoding="utf-8")
    return json.loads(data)  # type: ignore[no-any-return]


def _validate(schema: dict[str, Any], doc: dict[str, Any]) -> list[str]:
    """Return a list of human-readable error strings for *doc* against *schema*."""
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    return [f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]


def validate_control(doc: dict[str, Any]) -> list[str]:
    """Validate a control definition document against ``control.schema.json``."""
    return _validate(load_schema("control.schema.json"), doc)


def validate_sources(doc: dict[str, Any]) -> list[str]:
    """Validate a sources document against ``sources.schema.json``."""
    return _validate(load_schema("sources.schema.json"), doc)


def validate_bundle(doc: dict[str, Any]) -> list[str]:
    """Validate a bundle document against ``bundle.schema.json`` (stubbed until Phase 3)."""
    return _validate(load_schema("bundle.schema.json"), doc)
