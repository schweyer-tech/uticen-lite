"""Single source of truth for resolving a control's test_code for output.

Priority: inline (test_code) → rule (rule_spec) → file (test_path) → "".
Used by bundle.assemble (bundle output) and store.run_service (workpaper text)
so the two producers cannot drift. See issue #12 / learning 0001.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from uticen_lite.rules.render_rule import rule_to_text
from uticen_lite.rules.spec import parse_rule_spec

if TYPE_CHECKING:
    from uticen_lite.model.control import ControlDef


def resolve_test_code(control: ControlDef) -> str:
    """Resolve a control's test_code with priority inline → rule → file → "".

    1. ``control.test_code`` — already-inlined Python source.
    2. ``control.rule_spec`` — declarative rule; rendered to human-readable text.
    3. ``control.test_path`` — path to a .py file; content read from disk.
    4. Empty string fallback.
    """
    if control.test_code is not None:
        return control.test_code
    rule_spec = getattr(control, "rule_spec", None)
    if rule_spec is not None:
        return rule_to_text(parse_rule_spec(rule_spec))
    if control.test_path:
        return pathlib.Path(control.test_path).read_text(encoding="utf-8")
    return ""
