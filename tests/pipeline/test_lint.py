"""AST allowlist deny-scan for Custom Python nodes (issue #25, Stage 2 §8).

The lint is a GUARDRAIL against accidental bypass, not a sandbox: a malicious
local user already has the full Python escape hatch + a shell. It is light,
pure-Python, and layered — it parses a Custom Python node's ``code`` with
``ast`` and rejects anything that could read a file or otherwise reach outside
``rows`` (open / read_csv / __import__ / eval / exec / compile / globals /
dunder attribute access), allowing only a tiny pure set (``re`` / ``datetime``
/ ``decimal`` + the provided helper module).

The proof obligations:
  * a node containing ``open(...)`` / ``pd.read_csv`` / ``__import__`` / ``eval``
    / dunder access is REJECTED, and the message names the "Convert to Python
    test" offramp;
  * a clean ``rows → rows`` transform and a clean ``rows → violations`` test
    pass;
  * the allowed pure imports (``re``/``datetime``/``decimal``) pass and a
    disallowed import (``os``/``pandas``) is rejected;
  * scanning a whole :class:`Pipeline` flags the offending node by id and is a
    no-op when no custom node trips.
"""

from __future__ import annotations

import pytest

from uticen_lite.pipeline.lint import (
    OFFRAMP_MESSAGE,
    LintError,
    lint_custom_code,
    lint_pipeline,
)
from uticen_lite.pipeline.model import parse_pipeline

# ---------------------------------------------------------------------------
# Code-level deny-scan
# ---------------------------------------------------------------------------


def test_clean_transform_passes():
    code = "rows = rows[rows['amount'].astype(float) >= 1000]\nrows = rows.sort_values('amount')"
    # Returns no errors → clean.
    assert lint_custom_code(code) == []


def test_clean_test_flavor_passes():
    code = (
        "out = []\n"
        "for _, r in rows.iterrows():\n"
        "    if str(r['amount']) == '100':\n"
        "        out.append({'item_key': str(r['id']), 'description': 'dup',\n"
        "                    'severity': 'high', 'details': {}})\n"
        "return out"
    )
    assert lint_custom_code(code) == []


def test_allowed_pure_imports_pass():
    for mod in ("re", "datetime", "decimal"):
        code = f"import {mod}\nrows = rows.head(1)"
        assert lint_custom_code(code) == [], f"{mod} should be allowed"
    # from-import of an allowed module is fine too.
    assert lint_custom_code("from decimal import Decimal\nrows = rows.head(1)") == []


def test_open_is_rejected_with_offramp():
    errs = lint_custom_code("data = open('/etc/passwd').read()\nrows = rows")
    assert errs, "open(...) must be rejected"
    assert any(OFFRAMP_MESSAGE in e for e in errs)
    assert any("open" in e for e in errs)


def test_read_csv_is_rejected():
    errs = lint_custom_code("rows = pd.read_csv('/secret.csv')")
    assert errs
    assert any("read_csv" in e for e in errs)
    assert any(OFFRAMP_MESSAGE in e for e in errs)


def test_read_excel_is_rejected():
    errs = lint_custom_code("rows = pd.read_excel('/secret.xlsx')")
    assert errs
    assert any("read_excel" in e for e in errs)


def test_dunder_import_is_rejected():
    errs = lint_custom_code("m = __import__('os')\nrows = rows")
    assert errs
    assert any("__import__" in e for e in errs)


def test_eval_exec_compile_are_rejected():
    for fn in ("eval", "exec", "compile"):
        errs = lint_custom_code(f"{fn}('rows')\nrows = rows")
        assert errs, f"{fn} must be rejected"
        assert any(fn in e for e in errs)


def test_globals_is_rejected():
    errs = lint_custom_code("g = globals()\nrows = rows")
    assert errs
    assert any("globals" in e for e in errs)


# ---------------------------------------------------------------------------
# Allowlist bypasses (regression: issue #25 review)
# ---------------------------------------------------------------------------


def test_builtins_subscript_bypass_is_rejected():
    # __builtins__['open'](...) reaches open via a dict-key string literal — the
    # receiver __builtins__ is a denied bare Name and must trip.
    errs = lint_custom_code("leaked = __builtins__['open']('/etc/passwd').read()\nrows = rows")
    assert errs, "__builtins__['open'] must be rejected"
    assert any("__builtins__" in e or "open" in e for e in errs)
    assert any(OFFRAMP_MESSAGE in e for e in errs)


def test_builtins_bare_name_is_rejected():
    errs = lint_custom_code("b = __builtins__\nrows = rows")
    assert errs
    assert any("__builtins__" in e for e in errs)


def test_getattr_setattr_delattr_are_rejected():
    # getattr defeats _DENIED_ATTRS and the dunder guard by reaching any
    # attr/builtin by string; setattr/delattr are the same family.
    for fn in ("getattr", "setattr", "delattr"):
        errs = lint_custom_code(f"x = {fn}(rows, 'read_csv')\nrows = rows")
        assert errs, f"{fn} must be rejected"
        assert any(fn in e for e in errs)
        assert any(OFFRAMP_MESSAGE in e for e in errs)


def test_getattr_open_bypass_is_rejected():
    errs = lint_custom_code("x = getattr(__builtins__, 'open')('/p').read()\nrows = rows")
    assert errs
    # Both getattr and __builtins__ are denied; either reason is sufficient.
    assert any("getattr" in e or "__builtins__" in e for e in errs)


def test_getattr_subclasses_dunder_chain_is_rejected():
    # getattr(rows,'__class__') is the classic sandbox-escape vector by string.
    errs = lint_custom_code("c = getattr(rows, '__class__')\nrows = rows")
    assert errs
    assert any("getattr" in e for e in errs)


def test_string_literal_subscript_of_denied_attr_is_rejected():
    # A string-key subscript naming a file-reading attr is rejected even when the
    # receiver is an innocuous name (no Name/Attribute would otherwise see it).
    errs = lint_custom_code("x = ns['read_csv']('/p')\nrows = rows")
    assert errs
    assert any("read_csv" in e for e in errs)


def test_string_literal_subscript_of_dunder_is_rejected():
    errs = lint_custom_code("x = ns['__class__']\nrows = rows")
    assert errs
    assert any("__class__" in e or "dunder" in e.lower() for e in errs)


def test_innocuous_string_subscript_still_passes():
    # Normal column/dict access by string key (not a denied name) stays clean.
    assert lint_custom_code("v = rows['amount']\nrows = rows") == []
    assert lint_custom_code("x = {'a': 1}['a']\nrows = rows") == []


def test_dunder_attribute_access_is_rejected():
    # Reaching through dunders is the classic sandbox-escape vector.
    errs = lint_custom_code("cls = rows.__class__\nrows = rows")
    assert errs
    assert any("__class__" in e or "dunder" in e.lower() for e in errs)


def test_disallowed_import_is_rejected():
    for mod in ("os", "sys", "pandas", "subprocess", "pathlib"):
        errs = lint_custom_code(f"import {mod}\nrows = rows")
        assert errs, f"import {mod} must be rejected"
        assert any(mod in e for e in errs)


def test_disallowed_from_import_is_rejected():
    errs = lint_custom_code("from os import path\nrows = rows")
    assert errs
    assert any("os" in e for e in errs)


def test_syntax_error_is_rejected_not_raised():
    # Unparseable code is a lint failure, not a crash.
    errs = lint_custom_code("rows = rows[\n")
    assert errs
    assert any("syntax" in e.lower() for e in errs)


def test_offramp_message_names_the_one_way_door():
    # The teaching message must point at both escape hatches.
    assert "Import node" in OFFRAMP_MESSAGE
    assert "Python test" in OFFRAMP_MESSAGE


def test_helper_module_alias_is_allowed():
    # The provided helper module (imported under its known name) is on the
    # allowlist so custom nodes can use shared pure helpers.
    from uticen_lite.pipeline.lint import HELPER_MODULE

    code = f"import {HELPER_MODULE}\nrows = rows.head(1)"
    assert lint_custom_code(code) == []


# ---------------------------------------------------------------------------
# Pipeline-level deny-scan
# ---------------------------------------------------------------------------


def _pipeline_with_custom(code: str, flavor: str = "transform") -> dict:
    return {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "payments"},
            {
                "id": "cust",
                "type": "custom_python",
                "inputs": ["imp"],
                "config": {"flavor": flavor, "code": code},
            },
            {
                "id": "tst",
                "type": "test",
                "inputs": ["cust"],
                "config": {
                    "logic": "any",
                    "item_key_column": "id",
                    "conditions": [{"column": "id", "op": "not_empty"}],
                },
            },
        ]
    }


def test_lint_pipeline_clean_is_noop():
    pipe = parse_pipeline(_pipeline_with_custom("rows = rows.head(5)"))
    # No raise, no errors.
    assert lint_pipeline(pipe) == []


def test_lint_pipeline_flags_offending_node_by_id():
    pipe = parse_pipeline(_pipeline_with_custom("rows = open('x').read()"))
    errs = lint_pipeline(pipe)
    assert errs
    # The error names the node id so the UI can pin the inline error.
    assert any("cust" in e for e in errs)
    assert any(OFFRAMP_MESSAGE in e for e in errs)


def test_lint_pipeline_ignores_non_custom_nodes():
    # A pure visual pipeline (no custom node) is always clean.
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "payments"},
            {
                "id": "tst",
                "type": "test",
                "inputs": ["imp"],
                "config": {
                    "logic": "any",
                    "item_key_column": "id",
                    "conditions": [{"column": "id", "op": "not_empty"}],
                },
            },
        ]
    }
    assert lint_pipeline(parse_pipeline(raw)) == []


def test_lint_error_is_raisable():
    # LintError carries the message(s) for callers that prefer to raise.
    with pytest.raises(LintError) as ei:
        raise LintError(["cust: " + OFFRAMP_MESSAGE])
    assert OFFRAMP_MESSAGE in str(ei.value)
