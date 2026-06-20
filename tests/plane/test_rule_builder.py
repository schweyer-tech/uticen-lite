import io

from controlflow_sdk.plane.routes.controls import _rule_spec_from_form, _typed
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


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
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)


def test_rule_builder_builds_spec_from_conditions(client):
    _src(client)
    client.post("/controls", data={
        "id": "sod", "title": "SoD", "objective": "o", "narrative": "n",
        "test_kind": "rule", "rule_logic": "all", "rule_severity": "high",
        "rule_description": "User {user_id} can create and approve",
        "rule_item_key": "user_id",
        "cond_column": ["can_create", "can_approve"],
        "cond_op": ["eq", "eq"],
        "cond_value": ["true", "true"],
        "source_ids": ["users"],
    }, follow_redirects=False)
    c = repo.get_control(connect(client.app.state.project_root), "sod")
    assert c["test_kind"] == "rule"
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
    form = FakeForm({
        "cond_column": ["__other__"],
        "cond_column_freetext": ["custom_col"],
        "cond_op": ["eq"],
        "cond_value": ["x"],
        "rule_logic": "all", "rule_severity": "medium",
        "rule_description": "", "rule_item_key": "",
    })
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{"column": "custom_col", "op": "eq", "value": "x"}]


def test_rule_spec_dropdown_column_used_directly(client):
    form = FakeForm({
        "cond_column": ["can_create"],
        "cond_column_freetext": [""],
        "cond_op": ["eq"],
        "cond_value": ["true"],
        "rule_logic": "all", "rule_severity": "medium",
        "rule_description": "", "rule_item_key": "",
    })
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{"column": "can_create", "op": "eq", "value": True}]


# ---------------------------------------------------------------------------
# Part C — cross-source primitive (issue #9)
# ---------------------------------------------------------------------------

def test_rule_spec_builds_cross_source_condition():
    form = FakeForm({
        "cond_column": ["user_id"],
        "cond_op": ["not_exists_in"],
        "cond_value": [""],
        "cond_other_source": ["hr_roster"],
        "cond_this_key": ["user_id"],
        "cond_other_key": ["employee_id"],
        "rule_logic": "all", "rule_severity": "high",
        "rule_description": "", "rule_item_key": "user_id",
    })
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{
        "op": "not_exists_in", "column": "user_id", "other_source": "hr_roster",
        "this_key": "user_id", "other_key": "employee_id",
    }]


def test_save_auto_binds_cross_source_b(client):
    # two sources so source B (hr_roster) exists
    client.post("/sources", data={"source_id": "access", "format": "csv"},
                files={"file": ("access.csv", io.BytesIO(b"user_id\nU1\n"), "text/csv")},
                follow_redirects=False)
    client.post("/sources", data={"source_id": "hr_roster", "format": "csv"},
                files={"file": ("hr_roster.csv",
                                io.BytesIO(b"employee_id\nU1\n"), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={
        "id": "term", "title": "T", "objective": "o", "narrative": "n",
        "test_kind": "rule", "rule_logic": "all", "rule_severity": "high",
        "rule_description": "", "rule_item_key": "user_id",
        "cond_column": ["user_id"], "cond_op": ["not_exists_in"], "cond_value": [""],
        "cond_other_source": ["hr_roster"], "cond_this_key": ["user_id"],
        "cond_other_key": ["employee_id"],
        "source_ids": ["access"],  # only A ticked; B must be auto-bound
    }, follow_redirects=False)
    c = repo.get_control(connect(client.app.state.project_root), "term")
    # B was unioned into the control's sources so the runner can load it
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
    form = FakeForm({
        "cond_column": ["amount"],
        "cond_op": ["gt"],
        "cond_value": ["1"],
        "rule_logic": "all",
        "rule_severity": "medium",
        "rule_description": "",
        "rule_item_key": "",
    })
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{"column": "amount", "op": "gt", "value": 1}]
    assert isinstance(spec["conditions"][0]["value"], int)


def test_rule_spec_float_coercion():
    form = FakeForm({
        "cond_column": ["score"],
        "cond_op": ["gt"],
        "cond_value": ["2.5"],
        "rule_logic": "all",
        "rule_severity": "medium",
        "rule_description": "",
        "rule_item_key": "",
    })
    spec = _rule_spec_from_form(form)
    assert spec["conditions"][0]["value"] == 2.5
    assert isinstance(spec["conditions"][0]["value"], float)


def test_rule_spec_in_split_string():
    form = FakeForm({
        "cond_column": ["status"],
        "cond_op": ["in"],
        "cond_value": ["a|b|c"],
        "rule_logic": "any",
        "rule_severity": "low",
        "rule_description": "",
        "rule_item_key": "",
    })
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{"column": "status", "op": "in", "value": ["a", "b", "c"]}]


def test_rule_spec_in_split_numeric():
    form = FakeForm({
        "cond_column": ["code"],
        "cond_op": ["in"],
        "cond_value": ["1|2"],
        "rule_logic": "all",
        "rule_severity": "medium",
        "rule_description": "",
        "rule_item_key": "",
    })
    spec = _rule_spec_from_form(form)
    assert spec["conditions"] == [{"column": "code", "op": "in", "value": [1, 2]}]


def test_rule_spec_unary_op_no_value_key():
    form = FakeForm({
        "cond_column": ["notes"],
        "cond_op": ["is_empty"],
        "cond_value": [""],
        "rule_logic": "all",
        "rule_severity": "high",
        "rule_description": "",
        "rule_item_key": "",
    })
    spec = _rule_spec_from_form(form)
    cond = spec["conditions"][0]
    assert cond["op"] == "is_empty"
    assert "value" not in cond


def test_rule_spec_whitespace_column_skipped():
    form = FakeForm({
        "cond_column": ["   ", "status"],
        "cond_op": ["eq", "eq"],
        "cond_value": ["x", "active"],
        "rule_logic": "all",
        "rule_severity": "medium",
        "rule_description": "",
        "rule_item_key": "",
    })
    spec = _rule_spec_from_form(form)
    # The whitespace-only column must be skipped; only "status" remains
    assert len(spec["conditions"]) == 1
    assert spec["conditions"][0]["column"] == "status"


def test_rule_spec_column_stripped():
    form = FakeForm({
        "cond_column": ["  amount  "],
        "cond_op": ["eq"],
        "cond_value": ["100"],
        "rule_logic": "all",
        "rule_severity": "medium",
        "rule_description": "",
        "rule_item_key": "",
    })
    spec = _rule_spec_from_form(form)
    assert spec["conditions"][0]["column"] == "amount"
