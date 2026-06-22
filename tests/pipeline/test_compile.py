"""Compile-engine tests (Stage 1, issue #25).

The proof obligations:
  * pure single-source pipeline → a rule_spec dict (and matches evaluate_rule);
  * the terminated-access fully-visual exemplar compiles to a test(pop, sources)
    string that yields the SAME violations as the reference test.py over the
    Northwind demo data (compile-equivalence);
  * a hybrid pipeline with a Custom Python (rows→rows) node compiles to a
    runnable test() with a MODULE-LEVEL _node_<id> that cannot see ``sources``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from controlflow_sdk.adapters.files import source_for
from controlflow_sdk.model.population import ColumnMeta, Population
from controlflow_sdk.pipeline.compile import (
    CompileResult,
    compile_pipeline,
    compile_pipeline_procedures,
)
from controlflow_sdk.pipeline.model import parse_pipeline
from controlflow_sdk.project.loader import load_sources
from controlflow_sdk.rules.evaluate import evaluate_rule
from controlflow_sdk.rules.spec import parse_rule_spec

_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "northwind-trading"


def _pop(df: pd.DataFrame, key: str) -> Population:
    cols = [ColumnMeta(original_name=c, display_name=c, is_key=(c == key))
            for c in df.columns]
    return Population(df=df, columns=cols, source_id="s")


def _exec_test(code: str):
    """Exec a generated test() string and return the callable."""
    ns: dict = {}
    exec(compile(code, "<generated>", "exec"), ns)  # noqa: S102 (test of generated code)
    return ns["test"]


# ---------------------------------------------------------------------------
# Pure single-source → rule_spec
# ---------------------------------------------------------------------------

def test_pure_single_source_compiles_to_rule_spec():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "access_accounts"},
            {"id": "flt", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all",
                        "conditions": [{"column": "is_active", "op": "eq", "value": True}]}},
            {"id": "tst", "type": "test", "inputs": ["flt"],
             "config": {"logic": "all", "severity": "high",
                        "conditions": [{"column": "is_privileged", "op": "eq", "value": True}],
                        "item_key_column": "account_id",
                        "description_template": "Account {account_id} privileged"}},
        ]
    }
    result = compile_pipeline(parse_pipeline(raw))
    assert isinstance(result, CompileResult)
    assert result.test_kind == "rule"
    assert result.test_code is None
    spec = result.rule_spec
    assert isinstance(spec, dict)
    assert spec["logic"] == "all"
    # Filter + Test conditions are flattened into one flat all-spec.
    ops = {(c["column"], c["op"]) for c in spec["conditions"]}
    assert ("is_active", "eq") in ops
    assert ("is_privileged", "eq") in ops
    assert spec["severity"] == "high"
    assert spec["item_key_column"] == "account_id"


def test_pure_single_source_matches_evaluate_rule():
    df = pd.DataFrame({
        "account_id": ["A1", "A2", "A3"],
        "is_active": [True, True, False],
        "is_privileged": [True, False, True],
    })
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "access_accounts"},
            {"id": "flt", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all",
                        "conditions": [{"column": "is_active", "op": "eq", "value": True}]}},
            {"id": "tst", "type": "test", "inputs": ["flt"],
             "config": {"logic": "all", "severity": "high",
                        "conditions": [{"column": "is_privileged", "op": "eq", "value": True}],
                        "item_key_column": "account_id"}},
        ]
    }
    result = compile_pipeline(parse_pipeline(raw))
    out = evaluate_rule(parse_rule_spec(result.rule_spec), _pop(df, "account_id"))
    assert [v["item_key"] for v in out] == ["A1"]


# ---------------------------------------------------------------------------
# Filter + `any` test logic must NOT flatten (regression: issue #25 review)
# ---------------------------------------------------------------------------


def _filter_any_pipeline() -> dict:
    """Import → Filter[dept==IT, logic=any] → Test[amount>100, logic=any].

    A Filter is a conjunctive *narrowing* (keep dept==IT, THEN assert), never an
    alternative. Flattening it into a flat any-spec would OR the filter condition
    with the test condition over the *unfiltered* population — wrong. The pure
    path must bail to the staged Python path here.
    """
    return {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "s"},
            {"id": "flt", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "any",
                        "conditions": [{"column": "dept", "op": "eq", "value": "IT"}]}},
            {"id": "tst", "type": "test", "inputs": ["flt"],
             "config": {"logic": "any", "item_key_column": "id",
                        "conditions": [{"column": "amount", "op": "gt", "value": 100}]}},
        ]
    }


def test_filter_with_any_logic_does_not_flatten_to_rule():
    # With a Filter present and any-logic, the pure-rule flattening is unsound;
    # compile must target the staged Python path instead.
    result = compile_pipeline(parse_pipeline(_filter_any_pipeline()))
    assert result.test_kind == "python"
    assert result.rule_spec is None


def test_filter_any_python_path_uses_staged_semantics():
    # filter to dept==IT → only id 1 (amount 10) survives; then amount>100 → none.
    df = pd.DataFrame({
        "id": ["1", "2", "3"],
        "dept": ["IT", "Sales", "Sales"],
        "amount": [10, 200, 5],
    })
    result = compile_pipeline(parse_pipeline(_filter_any_pipeline()))
    out = _exec_test(result.test_code)(_pop(df, "id"), {"s": _pop(df, "id")})
    assert [v["item_key"] for v in out] == []


def test_filter_all_logic_still_flattens_to_rule():
    # The all-AND case is sound to flatten (filter AND test composes), so it
    # stays a no-code rule_spec — the metric-preserving simple case.
    raw = _filter_any_pipeline()
    raw["nodes"][1]["config"]["logic"] = "all"
    raw["nodes"][2]["config"]["logic"] = "all"
    result = compile_pipeline(parse_pipeline(raw))
    assert result.test_kind == "rule"


def test_filter_free_any_import_test_still_flattens_to_rule():
    # No Filter to narrow → an Import → Test under any logic can still flatten;
    # the unsoundness is specifically Filter-under-any.
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "s"},
            {"id": "tst", "type": "test", "inputs": ["imp"],
             "config": {"logic": "any", "item_key_column": "id",
                        "conditions": [{"column": "amount", "op": "gt", "value": 100}]}},
        ]
    }
    result = compile_pipeline(parse_pipeline(raw))
    assert result.test_kind == "rule"


def test_filter_any_rule_path_would_disagree_with_python_path():
    """The crux: a (counterfactual) flat any-spec disagrees with the staged
    pipeline. We assert the staged Python path == evaluate_rule of the CORRECT
    staged spec, and that it differs from the naive flattened spec — so the two
    compile targets can never disagree for one authored graph."""
    df = pd.DataFrame({
        "id": ["1", "2", "3"],
        "dept": ["IT", "Sales", "Sales"],
        "amount": [10, 200, 5],
    })
    pipe = parse_pipeline(_filter_any_pipeline())
    python_out = _exec_test(compile_pipeline(pipe).test_code)(
        _pop(df, "id"), {"s": _pop(df, "id")}
    )
    # Correct staged semantics: narrow to dept==IT first, then assert amount>100.
    staged = df[df["dept"] == "IT"]
    correct = evaluate_rule(
        parse_rule_spec({"logic": "any", "item_key_column": "id",
                         "conditions": [{"column": "amount", "op": "gt", "value": 100}]}),
        _pop(staged, "id"),
    )
    assert [v["item_key"] for v in python_out] == [v["item_key"] for v in correct] == []
    # The naive flattened any-spec (what the old pure path emitted) flags ['1','2'].
    naive = evaluate_rule(
        parse_rule_spec({"logic": "any", "item_key_column": "id", "conditions": [
            {"column": "dept", "op": "eq", "value": "IT"},
            {"column": "amount", "op": "gt", "value": 100}]}),
        _pop(df, "id"),
    )
    assert [v["item_key"] for v in naive] == ["1", "2"]  # the bug the fix avoids


# ---------------------------------------------------------------------------
# Compile-equivalence: terminated-access (fully-visual cross-source exemplar)
# ---------------------------------------------------------------------------

def _terminated_access_pipeline() -> dict:
    """Import(access_accounts) → Filter[is_active==true] → Join(employee_id,
    mode=inner, against employees filtered to status==terminated) → Test."""
    return {
        "nodes": [
            {"id": "acc", "type": "import", "source_id": "access_accounts",
             "narrative": "All system access accounts"},
            {"id": "active", "type": "filter", "inputs": ["acc"],
             "narrative": "Keep only currently-active accounts",
             "config": {"logic": "all",
                        "conditions": [{"column": "is_active", "op": "eq", "value": True}]}},
            {"id": "emp", "type": "import", "source_id": "employees",
             "narrative": "HR employee roster"},
            {"id": "term", "type": "filter", "inputs": ["emp"],
             "narrative": "Keep only terminated employees",
             "config": {"logic": "all",
                        "conditions": [{"column": "status", "op": "eq",
                                        "value": "terminated"}]}},
            {"id": "join", "type": "join", "inputs": ["active", "term"],
             "narrative": "Active accounts whose employee is terminated",
             "config": {"left_key": "employee_id", "right_key": "employee_id",
                        "mode": "inner", "bring_columns": ["status"]}},
            {"id": "tst", "type": "test", "inputs": ["join"],
             "narrative": "Flag every surviving account",
             "config": {"logic": "any", "severity": "critical",
                        "item_key_column": "account_id",
                        "conditions": [{"column": "account_id", "op": "not_empty"}],
                        "description_template": (
                            "Account '{account_id}' is active but linked employee "
                            "'{employee_id}' has terminated status")}},
        ]
    }


def _load_northwind_sources() -> dict[str, Population]:
    bindings = load_sources(_EXAMPLE)
    return {sid: source_for(b, _EXAMPLE).load() for sid, b in bindings.items()}


def test_terminated_access_compile_equivalence():
    sources = _load_northwind_sources()
    primary = sources["access_accounts"]

    result = compile_pipeline(parse_pipeline(_terminated_access_pipeline()))
    assert result.test_kind == "python"
    assert result.test_code is not None
    assert "def test(pop, sources):" in result.test_code

    test_fn = _exec_test(result.test_code)
    compiled_out = test_fn(primary, sources)

    # Reference: the canonical hand-written test.py for terminated-access.
    ref_ns: dict = {}
    ref_src = (_EXAMPLE / "controls" / "terminated-access" / "test.py").read_text()
    exec(compile(ref_src, "<ref>", "exec"), ref_ns)  # noqa: S102
    ref_out = ref_ns["test"](primary, sources)

    assert {v["item_key"] for v in compiled_out} == {v["item_key"] for v in ref_out}
    assert {v["item_key"] for v in compiled_out} == {"ACC-T01", "ACC-T02", "ACC-T03"}
    # Same shape as evaluate_rule / Violation.from_raw expects.
    v0 = next(v for v in compiled_out if v["item_key"] == "ACC-T01")
    assert set(v0) == {"item_key", "description", "severity", "details"}
    assert v0["severity"] == "critical"


def test_terminated_access_narratives_become_comments():
    result = compile_pipeline(parse_pipeline(_terminated_access_pipeline()))
    assert "# HR employee roster" in result.test_code
    assert "# Active accounts whose employee is terminated" in result.test_code


# ---------------------------------------------------------------------------
# Hybrid pipeline with a Custom Python (rows→rows) node
# ---------------------------------------------------------------------------

def _hybrid_pipeline() -> dict:
    return {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "journal_entries",
             "narrative": "All journal entries"},
            {"id": "cust", "type": "custom_python", "inputs": ["imp"],
             "narrative": "Keep only large manual entries",
             "config": {"flavor": "transform", "code": (
                 "rows = rows[rows['entry_type'].astype(str).str.lower() == 'manual']\n"
                 "rows = rows[rows['amount'].astype(float).abs() >= 50000]")}},
            {"id": "tst", "type": "test", "inputs": ["cust"],
             "narrative": "Flag self-reviewed entries",
             "config": {"logic": "any", "severity": "high",
                        "item_key_column": "entry_id",
                        "conditions": [{"column": "reviewed_by", "op": "is_empty"}]}},
        ]
    }


def test_hybrid_compiles_module_level_node_function():
    result = compile_pipeline(parse_pipeline(_hybrid_pipeline()))
    assert result.test_kind == "python"
    code = result.test_code
    # Module-level helper, defined BEFORE test().
    assert "def _node_cust(rows):" in code
    assert code.index("def _node_cust(rows):") < code.index("def test(pop, sources):")
    # The orchestrator calls it.
    assert "_node_cust(" in code


def test_custom_node_function_cannot_see_sources():
    """A module-level _node_<id>(rows) structurally cannot reference ``sources``
    — verified by exec'ing the module and inspecting the function's globals."""
    result = compile_pipeline(parse_pipeline(_hybrid_pipeline()))
    ns: dict = {}
    exec(compile(result.test_code, "<gen>", "exec"), ns)  # noqa: S102
    node_fn = ns["_node_cust"]
    # 'sources' is a parameter of test(), never a global; the node fn's globals
    # are the module namespace, which has no 'sources' binding.
    assert "sources" not in node_fn.__globals__
    assert "sources" not in node_fn.__code__.co_varnames


def test_hybrid_runs_and_flags_expected_entries():
    df = pd.DataFrame({
        "entry_id": ["E1", "E2", "E3", "E4"],
        "entry_type": ["manual", "manual", "auto", "manual"],
        "amount": [60000, 10000, 99999, 75000],
        "reviewed_by": ["", "boss", "boss", "boss"],
    })
    result = compile_pipeline(parse_pipeline(_hybrid_pipeline()))
    test_fn = _exec_test(result.test_code)
    out = test_fn(_pop(df, "entry_id"), {})
    # E1: manual, 60k, empty reviewer → flagged. E2 too small. E3 auto. E4 reviewed.
    assert [v["item_key"] for v in out] == ["E1"]


# ---------------------------------------------------------------------------
# Join modes: exists / not_exists / left / bring_columns
# ---------------------------------------------------------------------------

def _join_pipeline(mode: str, bring: list[str] | None = None) -> dict:
    join_cfg = {"left_key": "user_id", "right_key": "employee_id", "mode": mode}
    if bring is not None:
        join_cfg["bring_columns"] = bring
    return {
        "nodes": [
            {"id": "a", "type": "import", "source_id": "accounts"},
            {"id": "b", "type": "import", "source_id": "hr"},
            {"id": "j", "type": "join", "inputs": ["a", "b"], "config": join_cfg},
            {"id": "t", "type": "test", "inputs": ["j"],
             "config": {"logic": "any", "item_key_column": "user_id",
                        "conditions": [{"column": "user_id", "op": "not_empty"}]}},
        ]
    }


def _join_frames():
    a = _pop(pd.DataFrame({"user_id": ["U1", "U2", "U3"], "dept": ["IT", "Sales", "IT"]}),
             "user_id")
    b = _pop(pd.DataFrame({"employee_id": ["U1", "U3"], "name": ["Ann", "Cara"]}),
             "employee_id")
    return a, b


def test_join_exists_keeps_matching_left_rows():
    a, b = _join_frames()
    result = compile_pipeline(parse_pipeline(_join_pipeline("exists")))
    out = _exec_test(result.test_code)(a, {"accounts": a, "hr": b})
    assert [v["item_key"] for v in out] == ["U1", "U3"]


def test_join_not_exists_keeps_unmatched_left_rows():
    a, b = _join_frames()
    result = compile_pipeline(parse_pipeline(_join_pipeline("not_exists")))
    out = _exec_test(result.test_code)(a, {"accounts": a, "hr": b})
    assert [v["item_key"] for v in out] == ["U2"]


def test_join_inner_brings_only_named_columns():
    a, b = _join_frames()
    result = compile_pipeline(parse_pipeline(_join_pipeline("inner", bring=["name"])))
    code = result.test_code
    assert "merge(" in code
    assert "how='inner'" in code
    out = _exec_test(code)(a, {"accounts": a, "hr": b})
    assert [v["item_key"] for v in out] == ["U1", "U3"]


def test_join_left_keeps_all_left_rows():
    a, b = _join_frames()
    result = compile_pipeline(parse_pipeline(_join_pipeline("left")))
    out = _exec_test(result.test_code)(a, {"accounts": a, "hr": b})
    assert [v["item_key"] for v in out] == ["U1", "U2", "U3"]


# ---------------------------------------------------------------------------
# Custom Python test-flavor as the terminal (duplicate-payments shape)
# ---------------------------------------------------------------------------

def test_custom_test_flavor_node_is_a_valid_terminal():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "payments"},
            {"id": "dup", "type": "custom_python", "inputs": ["imp"],
             "narrative": "Detect duplicate payments",
             "config": {"flavor": "test", "code": (
                 "out = []\n"
                 "for _, r in rows.iterrows():\n"
                 "    if str(r['amount']) == '100':\n"
                 "        out.append({'item_key': str(r['payment_id']),\n"
                 "                    'description': 'dup', 'severity': 'high',\n"
                 "                    'details': {}})\n"
                 "return out")}},
        ]
    }
    result = compile_pipeline(parse_pipeline(raw))
    assert result.test_kind == "python"
    code = result.test_code
    # The test-flavor helper has NO appended `return rows`.
    assert "def _node_dup(rows):" in code
    df = pd.DataFrame({"payment_id": ["P1", "P2"], "amount": [100, 50]})
    out = _exec_test(code)(_pop(df, "payment_id"), {})
    assert [v["item_key"] for v in out] == ["P1"]


def test_custom_test_flavor_terminal_cannot_see_sources():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "payments"},
            {"id": "dup", "type": "custom_python", "inputs": ["imp"],
             "config": {"flavor": "test", "code": "return []"}},
        ]
    }
    result = compile_pipeline(parse_pipeline(raw))
    ns: dict = {}
    exec(compile(result.test_code, "<g>", "exec"), ns)  # noqa: S102
    assert "sources" not in ns["_node_dup"].__globals__


# ---------------------------------------------------------------------------
# Multi-terminal: per-procedure compile + union test()
# ---------------------------------------------------------------------------

def _forked():
    return parse_pipeline({"nodes": [
        {"id": "imp", "type": "import", "source_id": "inv"},
        {"id": "flt", "type": "filter", "inputs": ["imp"],
         "config": {"logic": "all",
                    "conditions": [{"column": "status", "op": "eq", "value": "posted"}]}},
        {"id": "a", "type": "test", "inputs": ["flt"], "narrative": "approver",
         "config": {"logic": "all", "item_key_column": "id",
                    "conditions": [{"column": "approver", "op": "is_empty"}]}},
        {"id": "b", "type": "test", "inputs": ["flt"], "narrative": "po",
         "config": {"logic": "all", "item_key_column": "id",
                    "conditions": [{"column": "po", "op": "is_empty"}]}},
    ]})


def test_compile_one_procedure_per_terminal():
    procs = compile_pipeline_procedures(_forked())
    assert [p.procedure_id for p in procs] == ["a", "b"]
    # Procedure "a" is a pure single-source chain → rule_spec referencing approver only.
    a = next(p for p in procs if p.procedure_id == "a")
    assert a.result.test_kind == "rule"
    cols = [c["column"] for c in a.result.rule_spec["conditions"]]
    assert "approver" in cols and "po" not in cols  # b's condition does NOT leak into a


def test_union_test_code_runs_both_branches_and_concatenates(tmp_path):
    # equivalence: exec the union test() over a fixture; violations == branch a + branch b
    union = compile_pipeline(_forked())
    assert union.test_kind == "python"
    ns = {}
    exec(union.test_code, ns)
    df = pd.DataFrame([
        {"id": "1", "status": "posted", "approver": "", "po": "PO1"},   # fails a only
        {"id": "2", "status": "posted", "approver": "X", "po": ""},     # fails b only
        {"id": "3", "status": "draft",  "approver": "", "po": ""},      # filtered out
    ])
    pop = _pop(df, "id")
    out = ns["test"](pop, {"inv": pop})
    keys = sorted(v["item_key"] for v in out)
    assert keys == ["1", "2"]  # both branches' violations, trunk computed once
