import pandas as pd

from uticen_lite.model.population import ColumnMeta, Population
from uticen_lite.pipeline.compile import (
    _subpipeline_for,
    compile_pipeline,
    compile_pipeline_procedures,
)
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.rules.evaluate import evaluate_rule  # canonical interpreter
from uticen_lite.rules.spec import parse_rule_spec


def _multi_check_one_procedure() -> dict:
    # ONE procedure (p1) owning TWO checks (t1, t2) over the same filtered trunk.
    return {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "je"},
            {
                "id": "flt",
                "type": "filter",
                "inputs": ["imp"],
                "config": {
                    "logic": "all",
                    "conditions": [{"column": "kind", "op": "eq", "value": "manual"}],
                },
            },
            {
                "id": "t1",
                "type": "test",
                "inputs": ["flt"],
                "title": "preparer=approver",
                "config": {
                    "logic": "all",
                    "item_key_column": "je_id",
                    "procedure_id": "p1",
                    "conditions": [{"column": "preparer", "op": "eq", "value": "approver"}],
                },
            },
            {
                "id": "t2",
                "type": "test",
                "inputs": ["flt"],
                "title": "no approval",
                "config": {
                    "logic": "all",
                    "item_key_column": "je_id",
                    "procedure_id": "p1",
                    "conditions": [{"column": "approval", "op": "is_empty"}],
                },
            },
        ],
        "procedures": [
            {
                "id": "p1",
                "code": "P1",
                "name": "Manual JE Review",
                "assertion": "Segregation of Duties",
                "position": 0,
            },
        ],
    }


def test_one_compiled_procedure_carries_metadata():
    p = parse_pipeline(_multi_check_one_procedure())
    procs = compile_pipeline_procedures(p)
    assert len(procs) == 1
    assert procs[0].procedure_id == "p1"
    assert procs[0].code == "P1"
    assert procs[0].title == "Manual JE Review"
    assert procs[0].assertion == "Segregation of Duties"


def test_compiled_union_matches_interpreter(tmp_path):
    """The generated union test() must equal the union of the interpreter over the
    procedure's checks (learning 0009)."""
    p = parse_pipeline(_multi_check_one_procedure())
    procs = compile_pipeline_procedures(p)
    result = procs[0].result  # CompileResult: union test() string for the 2 checks

    df = pd.DataFrame(
        [
            {"je_id": "A", "kind": "manual", "preparer": "approver", "approval": ""},  # fails both
            {"je_id": "B", "kind": "manual", "preparer": "alice", "approval": ""},  # fails t2
            {"je_id": "C", "kind": "manual", "preparer": "approver", "approval": "yes"},  # fails t1
            {"je_id": "D", "kind": "auto", "preparer": "approver", "approval": ""},  # filtered out
        ]
    )
    pop = Population(
        df=df,
        columns=[ColumnMeta(original_name="je_id", display_name="je_id", is_key=True)],
        source_id="je",
    )

    ns: dict = {}
    exec(result.test_code, ns)  # noqa: S102 — generated, guardrailed
    generated = ns["test"](pop, {})
    keys = sorted(v["item_key"] for v in generated)
    # A (both), B (t2), C (t1) ⇒ keys A,A,B,C before dedupe (concat of both checks)
    assert keys == ["A", "A", "B", "C"]

    # Cross-validate against the canonical interpreter (learning 0009): each check's
    # single-terminal sub-pipeline compiles to a rule_spec; running evaluate_rule over
    # the SAME population and unioning the violations must equal the generated union, so
    # a future silent divergence between emit and interpreter is caught here.
    interp_keys: list[str] = []
    for node in (p.node("t1"), p.node("t2")):
        single = compile_pipeline(_subpipeline_for(p, node))
        assert single.rule_spec is not None  # filter + test flatten to a pure rule_spec
        spec = parse_rule_spec(single.rule_spec)
        interp_keys.extend(v["item_key"] for v in evaluate_rule(spec, pop))
    assert sorted(interp_keys) == keys
