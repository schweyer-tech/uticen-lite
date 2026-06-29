from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.pipeline.procedures import (
    derived_membership,
    effective_procedures,
    tests_for_procedure,
)


def _two_proc_pipeline() -> dict:
    # Shared Import → two filtered branches, each with one Test, grouped into P1 and P2.
    return {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "je"},
            {"id": "f1", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all",
                        "conditions": [{"column": "kind", "op": "eq", "value": "manual"}]}},
            {"id": "t1", "type": "test", "inputs": ["f1"],
             "config": {"logic": "all", "item_key_column": "je_id", "procedure_id": "p1",
                        "conditions": [{"column": "preparer", "op": "eq", "value": "approver"}]}},
            {"id": "f2", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all",
                        "conditions": [{"column": "posted", "op": "eq", "value": "late"}]}},
            {"id": "t2", "type": "test", "inputs": ["f2"],
             "config": {"logic": "all", "item_key_column": "je_id", "procedure_id": "p2",
                        "conditions": [{"column": "posted", "op": "eq", "value": "late"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "Manual JE Review",
             "assertion": "Segregation of Duties",
             "failure_threshold_count": 0, "position": 0},
            {"id": "p2", "code": "P2", "name": "Late Posting", "assertion": "Cutoff",
             "failure_threshold_pct": 1.0, "position": 1},
        ],
    }


def test_parse_round_trips_procedures():
    p = parse_pipeline(_two_proc_pipeline())
    assert [pr.code for pr in p.procedures] == ["P1", "P2"]
    assert p.procedures[0].assertion == "Segregation of Duties"
    assert p.procedures[1].failure_threshold_pct == 1.0


def test_effective_procedures_uses_defined_sorted_by_position():
    p = parse_pipeline(_two_proc_pipeline())
    eff = effective_procedures(p)
    assert [pr.id for pr in eff] == ["p1", "p2"]
    assert [t.id for t in tests_for_procedure(p, "p1")] == ["t1"]
    assert [t.id for t in tests_for_procedure(p, "p2")] == ["t2"]


def test_derived_membership_marks_shared_import_in_both():
    p = parse_pipeline(_two_proc_pipeline())
    mem = derived_membership(p)
    assert mem["imp"] == {"p1", "p2"}     # shared support node feeds both
    assert mem["f1"] == {"p1"}
    assert mem["t1"] == {"p1"}
    assert mem["f2"] == {"p2"}


def test_no_procedures_defined_derives_one_per_terminal():
    raw = _two_proc_pipeline()
    raw.pop("procedures")
    for n in raw["nodes"]:
        n.get("config", {}).pop("procedure_id", None)
    p = parse_pipeline(raw)
    eff = effective_procedures(p)
    assert [pr.code for pr in eff] == ["P1", "P2"]      # auto one-per-terminal
    assert {t.id for t in tests_for_procedure(p, eff[0].id)} == {"t1"}


def test_sole_auto_procedure_gets_empty_code():
    """Single-terminal pipeline with no defined procedures → code='' (bundle byte-identity)."""
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "s"},
            {"id": "t1", "type": "test", "inputs": ["imp"],
             "config": {"logic": "all", "item_key_column": "id", "conditions": []}},
        ],
    }
    p = parse_pipeline(raw)
    eff = effective_procedures(p)
    assert len(eff) == 1
    assert eff[0].code == ""          # lone auto → empty code (matches bundle single-proc shape)


def test_two_auto_procedures_keep_positional_codes():
    """Two-terminal no-procedures pipeline → codes stay P1, P2 (not collapsed to '')."""
    raw = _two_proc_pipeline()
    raw.pop("procedures")
    for n in raw["nodes"]:
        n.get("config", {}).pop("procedure_id", None)
    p = parse_pipeline(raw)
    eff = effective_procedures(p)
    assert [pr.code for pr in eff] == ["P1", "P2"]  # ≥2 autos keep positional codes


def test_unassigned_test_falls_back_to_auto_procedure():
    raw = _two_proc_pipeline()
    # Drop t2's procedure_id but keep p1/p2 defined → t2 is unassigned.
    for n in raw["nodes"]:
        if n["id"] == "t2":
            n["config"].pop("procedure_id")
    p = parse_pipeline(raw)
    eff = effective_procedures(p)
    # p1, p2 defined + one auto procedure owning the orphan t2.
    owners = {t.id for pid in (pr.id for pr in eff) for t in tests_for_procedure(p, pid)}
    assert {"t1", "t2"}.issubset(owners)
