import io
import json

from uticen_lite.plane.routes.controls import (
    _conditions_view_from_form,
    _rule_spec_from_form,
    _typed,
)
from uticen_lite.store import repo
from uticen_lite.store.db import connect


class FakeForm:
    """Minimal form-like object matching the getlist/get API used by _rule_spec_from_form."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def getlist(self, name: str) -> list:
        v = self._data.get(name, [])
        return v if isinstance(v, list) else [v]

    def get(self, name: str, default: str = "") -> str:
        return self._data.get(name, default)  # type: ignore[return-value]


def _src(client):
    csv = b"user_id,can_create,can_approve\nU1,true,true\n"
    client.post(
        "/sources",
        data={"source_id": "users", "format": "csv"},
        files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
        follow_redirects=False,
    )


def test_rule_builder_builds_spec_from_conditions(client):
    _src(client)
    # Step 1: create metadata shell via Definition form.
    client.post(
        "/controls",
        data={
            "id": "sod",
            "title": "SoD",
            "objective": "o",
            "narrative": "n",
            "source_ids": ["users"],
        },
        follow_redirects=False,
    )
    # Step 2: author logic via Builder (Import→Test pipeline that compiles to rule_spec).
    graph = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "users"},
            {
                "id": "tst",
                "type": "test",
                "inputs": ["imp"],
                "config": {
                    "logic": "all",
                    "severity": "high",
                    "item_key_column": "user_id",
                    "description_template": "User {user_id} can create and approve",
                    "conditions": [
                        {"column": "can_create", "op": "eq", "value": True},
                        {"column": "can_approve", "op": "eq", "value": True},
                    ],
                },
            },
        ]
    }
    client.post(
        "/controls/sod/logic/builder",
        data={"pipeline_json": json.dumps(graph)},
        follow_redirects=False,
    )
    c = repo.get_control(connect(client.app.state.project_root), "sod")
    # Single-source pipeline compiles to a rule_spec artifact (test_kind="pipeline").
    assert c["test_kind"] == "pipeline"
    spec = c["rule_spec"]
    assert spec["logic"] == "all" and spec["severity"] == "high"
    assert spec["conditions"] == [
        {"column": "can_create", "op": "eq", "value": True},
        {"column": "can_approve", "op": "eq", "value": True},
    ]
    assert spec["item_key_column"] == "user_id"


def test_add_condition_row_partial(client):
    resp = client.get("/controls/_condition_row")
    assert resp.status_code == 200
    assert 'name="cond_column"' in resp.text
    assert 'name="cond_op"' in resp.text


# ---------------------------------------------------------------------------
# Part A — column dropdown (issue #9)
# ---------------------------------------------------------------------------


def test_condition_row_with_source_renders_column_select(client):
    _src(client)
    resp = client.get("/controls/_condition_row?source_id=users")
    assert resp.status_code == 200
    assert '<select name="cond_column"' in resp.text
    # the source's columns appear as options
    assert "can_create" in resp.text and "can_approve" in resp.text
    # an "Other" escape hatch is offered for power users
    assert "__other__" in resp.text


def test_condition_row_without_source_falls_back_to_freetext(client):
    resp = client.get("/controls/_condition_row")
    assert resp.status_code == 200
    # fallback: a free-text cond_column input, no <select>
    assert 'name="cond_column"' in resp.text
    assert '<select name="cond_column"' not in resp.text


def test_rule_spec_resolves_other_freetext_column(client):
    form = FakeForm(
        {
            "cond_column": ["__other__"],
            "cond_column_freetext": ["custom_col"],
            "cond_op": ["eq"],
            "cond_value": ["x"],
            "rule_logic": "all",
            "rule_severity": "medium",
            "rule_description": "",
            "rule_item_key": "",
        }
    )
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{"column": "custom_col", "op": "eq", "value": "x"}]


def test_rule_spec_dropdown_column_used_directly(client):
    form = FakeForm(
        {
            "cond_column": ["can_create"],
            "cond_column_freetext": [""],
            "cond_op": ["eq"],
            "cond_value": ["true"],
            "rule_logic": "all",
            "rule_severity": "medium",
            "rule_description": "",
            "rule_item_key": "",
        }
    )
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{"column": "can_create", "op": "eq", "value": True}]


# ---------------------------------------------------------------------------
# Part B — checkbox-driven condition refresh (U1, issue #9)
#
# The bug: on /controls/new the column input was free-text and only became a
# dropdown after Save+reopen (columns were derived from PERSISTED bindings).
# GET /controls/_conditions re-renders the rows for the currently-checked
# source WITHOUT writing the store, preserving uncommitted op/value/column.
# ---------------------------------------------------------------------------


def test_conditions_refresh_offers_checked_source_columns(client):
    _src(client)
    # No control exists yet (mirrors /controls/new): the checked source's
    # columns must still come back as a dropdown, not free text.
    resp = client.get("/controls/_conditions", params={"source_ids": "users"})
    assert resp.status_code == 200
    assert '<select name="cond_column"' in resp.text
    assert "can_create" in resp.text and "can_approve" in resp.text
    # the "Other (type a name)…" escape hatch is preserved
    assert "__other__" in resp.text


def test_conditions_refresh_without_source_falls_back_to_freetext(client):
    # Un-ticking every source (no source_ids) reverts to the free-text column.
    resp = client.get("/controls/_conditions")
    assert resp.status_code == 200
    assert 'name="cond_column"' in resp.text
    assert '<select name="cond_column"' not in resp.text


def test_conditions_refresh_preserves_uncommitted_state(client):
    _src(client)
    # The author already picked an op + typed a value and a free-text column;
    # ticking the source must keep those, not blow them away.
    resp = client.get(
        "/controls/_conditions",
        params=[
            ("source_ids", "users"),
            ("cond_column", "can_create"),  # a real column → matches the dropdown
            ("cond_column_freetext", ""),
            ("cond_op", "ne"),
            ("cond_value", "true"),
        ],
    )
    assert resp.status_code == 200
    # the picked column is selected in the now-populated dropdown
    assert '<option value="can_create" selected' in resp.text
    # the chosen op survives
    assert '<option value="ne" selected' in resp.text
    # the typed value survives
    assert 'value="true"' in resp.text


def test_conditions_refresh_keeps_freetext_for_unknown_column(client):
    _src(client)
    # A column the checked source doesn't have stays in the free-text box via
    # the "__other__" escape, so binding a source never discards the author's
    # custom column name.
    resp = client.get(
        "/controls/_conditions",
        params=[
            ("source_ids", "users"),
            ("cond_column", "__other__"),
            ("cond_column_freetext", "made_up_col"),
            ("cond_op", "eq"),
            ("cond_value", "x"),
        ],
    )
    assert resp.status_code == 200
    assert '<option value="__other__" selected' in resp.text
    assert 'value="made_up_col"' in resp.text


def test_conditions_view_from_form_resolves_other_freetext():
    rows = _conditions_view_from_form(
        FakeForm(
            {
                "cond_column": ["__other__"],
                "cond_column_freetext": ["custom_col"],
                "cond_op": ["ne"],
                "cond_value": ["a|b"],
            }
        )
    )
    # raw value preserved verbatim (no |-split / type coercion in the view model)
    assert rows == [{"op": "ne", "column": "custom_col", "value": "a|b"}]


def test_conditions_view_from_form_preserves_cross_source_row():
    rows = _conditions_view_from_form(
        FakeForm(
            {
                "cond_column": ["user_id"],
                "cond_op": ["not_exists_in"],
                "cond_value": [""],
                "cond_other_source": ["hr_roster"],
                "cond_this_key": ["user_id"],
                "cond_other_key": ["employee_id"],
            }
        )
    )
    assert rows == [
        {
            "op": "not_exists_in",
            "column": "user_id",
            "other_source": "hr_roster",
            "this_key": "user_id",
            "other_key": "employee_id",
        }
    ]


def test_conditions_view_from_form_empty_yields_blank_row(client):
    # No posted conditions → empty list → the partial renders one blank row.
    assert _conditions_view_from_form(FakeForm({})) == []
    resp = client.get("/controls/_conditions")
    assert resp.status_code == 200
    assert 'name="cond_op"' in resp.text  # a row was rendered


# ---------------------------------------------------------------------------
# Part C — cross-source primitive (issue #9)
# ---------------------------------------------------------------------------


def test_rule_spec_builds_cross_source_condition():
    form = FakeForm(
        {
            "cond_column": ["user_id"],
            "cond_op": ["not_exists_in"],
            "cond_value": [""],
            "cond_other_source": ["hr_roster"],
            "cond_this_key": ["user_id"],
            "cond_other_key": ["employee_id"],
            "rule_logic": "all",
            "rule_severity": "high",
            "rule_description": "",
            "rule_item_key": "user_id",
        }
    )
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [
        {
            "op": "not_exists_in",
            "column": "user_id",
            "other_source": "hr_roster",
            "this_key": "user_id",
            "other_key": "employee_id",
        }
    ]


def test_save_auto_binds_cross_source_b(client):
    # Two sources; both appear as Import nodes so the Builder auto-binds both.
    client.post(
        "/sources",
        data={"source_id": "access", "format": "csv"},
        files={"file": ("access.csv", io.BytesIO(b"user_id\nU1\n"), "text/csv")},
        follow_redirects=False,
    )
    client.post(
        "/sources",
        data={"source_id": "hr_roster", "format": "csv"},
        files={"file": ("hr_roster.csv", io.BytesIO(b"employee_id\nU1\n"), "text/csv")},
        follow_redirects=False,
    )
    # Step 1: create metadata shell (only A in the definition form).
    client.post(
        "/controls",
        data={
            "id": "term",
            "title": "T",
            "objective": "o",
            "narrative": "n",
            "source_ids": ["access"],
        },
        follow_redirects=False,
    )
    # Step 2: author logic via Builder using Import nodes for both sources;
    # the Builder derives source binding from Import nodes → both A and B are bound.
    graph = {
        "nodes": [
            {"id": "imp_a", "type": "import", "source_id": "access"},
            {"id": "imp_b", "type": "import", "source_id": "hr_roster"},
            {
                "id": "jn",
                "type": "join",
                "inputs": ["imp_a", "imp_b"],
                "config": {"left_key": "user_id", "right_key": "employee_id", "mode": "exists"},
            },
            {
                "id": "tst",
                "type": "test",
                "inputs": ["jn"],
                "config": {
                    "logic": "any",
                    "severity": "high",
                    "item_key_column": "user_id",
                    "description_template": "",
                    "conditions": [{"column": "user_id", "op": "not_empty"}],
                },
            },
        ]
    }
    client.post(
        "/controls/term/logic/builder",
        data={"pipeline_json": json.dumps(graph)},
        follow_redirects=False,
    )
    c = repo.get_control(connect(client.app.state.project_root), "term")
    # Both Import sources are bound so the runner can load them.
    assert "access" in c["source_ids"] and "hr_roster" in c["source_ids"]


# ---------------------------------------------------------------------------
# Unit tests for _typed
# ---------------------------------------------------------------------------


def test_typed_integer():
    assert _typed("1") == 1
    assert isinstance(_typed("1"), int)


def test_typed_float():
    assert _typed("2.5") == 2.5
    assert isinstance(_typed("2.5"), float)


def test_typed_bool():
    assert _typed("true") is True
    assert _typed("false") is False


def test_typed_string():
    assert _typed("hello") == "hello"


# ---------------------------------------------------------------------------
# Unit tests for _rule_spec_from_form
# ---------------------------------------------------------------------------


def test_rule_spec_numeric_coercion():
    form = FakeForm(
        {
            "cond_column": ["amount"],
            "cond_op": ["gt"],
            "cond_value": ["1"],
            "rule_logic": "all",
            "rule_severity": "medium",
            "rule_description": "",
            "rule_item_key": "",
        }
    )
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{"column": "amount", "op": "gt", "value": 1}]
    assert isinstance(spec["conditions"][0]["value"], int)


def test_rule_spec_float_coercion():
    form = FakeForm(
        {
            "cond_column": ["score"],
            "cond_op": ["gt"],
            "cond_value": ["2.5"],
            "rule_logic": "all",
            "rule_severity": "medium",
            "rule_description": "",
            "rule_item_key": "",
        }
    )
    spec = _rule_spec_from_form(form)
    assert spec["conditions"][0]["value"] == 2.5
    assert isinstance(spec["conditions"][0]["value"], float)


def test_rule_spec_in_split_string():
    form = FakeForm(
        {
            "cond_column": ["status"],
            "cond_op": ["in"],
            "cond_value": ["a|b|c"],
            "rule_logic": "any",
            "rule_severity": "low",
            "rule_description": "",
            "rule_item_key": "",
        }
    )
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{"column": "status", "op": "in", "value": ["a", "b", "c"]}]


def test_rule_spec_in_split_numeric():
    form = FakeForm(
        {
            "cond_column": ["code"],
            "cond_op": ["in"],
            "cond_value": ["1|2"],
            "rule_logic": "all",
            "rule_severity": "medium",
            "rule_description": "",
            "rule_item_key": "",
        }
    )
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{"column": "code", "op": "in", "value": [1, 2]}]


def test_rule_spec_unary_op_no_value_key():
    form = FakeForm(
        {
            "cond_column": ["notes"],
            "cond_op": ["is_empty"],
            "cond_value": [""],
            "rule_logic": "all",
            "rule_severity": "high",
            "rule_description": "",
            "rule_item_key": "",
        }
    )
    spec = _rule_spec_from_form(form)
    cond = spec["conditions"][0]
    assert cond["op"] == "is_empty"
    assert "value" not in cond


def test_rule_spec_whitespace_column_skipped():
    form = FakeForm(
        {
            "cond_column": ["   ", "status"],
            "cond_op": ["eq", "eq"],
            "cond_value": ["x", "active"],
            "rule_logic": "all",
            "rule_severity": "medium",
            "rule_description": "",
            "rule_item_key": "",
        }
    )
    spec = _rule_spec_from_form(form)
    # The whitespace-only column must be skipped; only "status" remains
    assert len(spec["conditions"]) == 1
    assert spec["conditions"][0]["column"] == "status"


def test_rule_spec_column_stripped():
    form = FakeForm(
        {
            "cond_column": ["  amount  "],
            "cond_op": ["eq"],
            "cond_value": ["100"],
            "rule_logic": "all",
            "rule_severity": "medium",
            "rule_description": "",
            "rule_item_key": "",
        }
    )
    spec = _rule_spec_from_form(form)
    assert spec["conditions"][0]["column"] == "amount"
