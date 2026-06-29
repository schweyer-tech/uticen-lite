"""AI-assisted authoring seam (opt-in, behind the ``[ai]`` extra).

The public surface is import-safe without the optional ``anthropic`` / ``openai``
SDKs — those are imported lazily inside each backend's ``draft_rule_spec`` only
when a draft is actually requested. The control plane runs fully offline by
default; no provider is ever called unless the author selects one and its key/env
is present.
"""

from __future__ import annotations

from uticen_lite.ai.draft import RULE_SPEC_JSON_SCHEMA, DraftError, draft_and_validate
from uticen_lite.ai.providers import (
    Provider,
    available_providers,
    provider_key_present,
)

__all__ = [
    "RULE_SPEC_JSON_SCHEMA",
    "DraftError",
    "Provider",
    "available_providers",
    "draft_and_validate",
    "provider_key_present",
]
