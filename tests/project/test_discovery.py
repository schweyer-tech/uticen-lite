"""Tests for uticen_lite.project.discovery — TDD (write first, implement second)."""

from __future__ import annotations

from pathlib import Path

import pytest

from uticen_lite.model.control import ControlDef
from uticen_lite.project import (
    Project,
    ProjectError,
    discover_controls,
    load_test_callable,
)

SAMPLE = Path(__file__).parent / "fixtures" / "sample"


class TestDiscoverControls:
    def test_returns_list(self):
        controls = discover_controls(SAMPLE)
        assert isinstance(controls, list)

    def test_finds_one_control(self):
        controls = discover_controls(SAMPLE)
        assert len(controls) == 1

    def test_control_id(self):
        controls = discover_controls(SAMPLE)
        assert controls[0].id == "cash_cutoff"

    def test_control_is_control_def(self):
        controls = discover_controls(SAMPLE)
        assert isinstance(controls[0], ControlDef)

    def test_sources_resolved(self):
        """Sources list should contain a resolved SourceBinding (not just an id stub)."""
        controls = discover_controls(SAMPLE)
        src = controls[0].sources[0]
        assert src.id == "gl"
        assert src.type == "file"

    def test_test_path_attached(self):
        controls = discover_controls(SAMPLE)
        # test_path is resolved to an absolute path at construction time
        p = Path(controls[0].test_path)
        assert p.is_absolute()
        assert p.name == "test.py"
        assert p.exists()

    def test_threshold_defaults_to_implicit_zero(self):
        """A control with no ``threshold:`` block gets an implicit-zero threshold."""
        controls = discover_controls(SAMPLE)
        assert controls[0].threshold.is_implicit_zero is True

    def test_threshold_block_parsed(self, tmp_path):
        """A ``threshold:`` block in control.yaml is parsed onto the ControlDef."""
        from uticen_lite.model.control import SourceBinding
        from uticen_lite.project.discovery import _parse_control

        doc = {
            "id": "thresholded",
            "title": "Thresholded Control",
            "objective": "Obj.",
            "narrative": "Narr.",
            "sources": [],
            "threshold": {"failure_threshold_pct": 5, "failure_threshold_count": 2},
        }
        sources_map: dict[str, SourceBinding] = {}
        (tmp_path / "test.py").write_text("def test(df):\n    return []\n", encoding="utf-8")
        ctrl = _parse_control(doc, sources_map, tmp_path)
        assert ctrl.threshold.failure_threshold_pct == 5.0
        assert ctrl.threshold.failure_threshold_count == 2

    def test_unknown_source_raises_project_error(self, tmp_path):
        """A control.yaml referencing an unknown source id raises ProjectError."""
        # Minimal cflow.yaml
        (tmp_path / "cflow.yaml").write_text("name: T\nsystem: {}\ndefaults: {}\n")
        # sources.yaml with only 'bank' source
        (tmp_path / "sources.yaml").write_text(
            "sources:\n"
            "  - id: bank\n"
            "    type: file\n"
            "    config:\n"
            "      path: bank.csv\n"
            "      format: csv\n"
            "    key_config:\n"
            "      mode: single\n"
            "      columns:\n"
            "        - txn_id\n"
            "    column_mappings:\n"
            "      - original_name: txn_id\n"
            "        display_name: Txn ID\n"
            "        is_key: true\n"
            "        include: true\n"
        )
        # control referencing 'gl', which doesn't exist
        ctrl_dir = tmp_path / "controls" / "bad_ctrl"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "control.yaml").write_text(
            "id: bad_ctrl\n"
            "title: Bad\n"
            "objective: Test\n"
            "narrative: Narrative\n"
            "sources:\n"
            "  - id: gl\n"
            "test_path: test.py\n"
        )
        (ctrl_dir / "test.py").write_text("def test(pop):\n    return []\n")
        with pytest.raises(ProjectError, match="gl"):
            discover_controls(tmp_path)


class TestLoadTestCallable:
    def test_returns_callable(self):
        controls = discover_controls(SAMPLE)
        fn = load_test_callable(controls[0])
        assert callable(fn)

    def test_callable_name(self):
        controls = discover_controls(SAMPLE)
        fn = load_test_callable(controls[0])
        assert fn.__name__ == "test"

    def test_does_not_execute(self):
        """load_test_callable should return the function without calling it."""
        controls = discover_controls(SAMPLE)
        # If it were called it would return [], but we just want a callable
        fn = load_test_callable(controls[0])
        assert callable(fn)

    def test_missing_test_function_raises_project_error(self, tmp_path):
        """A test.py without def test raises ProjectError."""
        (tmp_path / "cflow.yaml").write_text("name: T\nsystem: {}\ndefaults: {}\n")
        (tmp_path / "sources.yaml").write_text(
            "sources:\n"
            "  - id: gl\n"
            "    type: file\n"
            "    config:\n"
            "      path: gl.csv\n"
            "      format: csv\n"
            "    key_config:\n"
            "      mode: single\n"
            "      columns:\n"
            "        - entry_id\n"
            "    column_mappings:\n"
            "      - original_name: entry_id\n"
            "        display_name: Entry ID\n"
            "        is_key: true\n"
            "        include: true\n"
        )
        ctrl_dir = tmp_path / "controls" / "no_test"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "control.yaml").write_text(
            "id: no_test\n"
            "title: No Test\n"
            "objective: Test\n"
            "narrative: Narrative\n"
            "sources:\n"
            "  - id: gl\n"
            "test_path: test.py\n"
        )
        # test.py exists but has no 'test' function
        (ctrl_dir / "test.py").write_text("def run(pop):\n    return []\n")
        controls = discover_controls(tmp_path)
        with pytest.raises(ProjectError, match="test"):
            load_test_callable(controls[0])

    def test_non_callable_test_attr_raises_project_error(self, tmp_path):
        """A test.py where 'test' is not a callable raises ProjectError."""
        (tmp_path / "cflow.yaml").write_text("name: T\nsystem: {}\ndefaults: {}\n")
        (tmp_path / "sources.yaml").write_text(
            "sources:\n"
            "  - id: gl\n"
            "    type: file\n"
            "    config:\n"
            "      path: gl.csv\n"
            "      format: csv\n"
            "    key_config:\n"
            "      mode: single\n"
            "      columns:\n"
            "        - entry_id\n"
            "    column_mappings:\n"
            "      - original_name: entry_id\n"
            "        display_name: Entry ID\n"
            "        is_key: true\n"
            "        include: true\n"
        )
        ctrl_dir = tmp_path / "controls" / "bad_test"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "control.yaml").write_text(
            "id: bad_test\n"
            "title: Bad Test\n"
            "objective: Test\n"
            "narrative: Narrative\n"
            "sources:\n"
            "  - id: gl\n"
            "test_path: test.py\n"
        )
        # 'test' is a string, not a function
        (ctrl_dir / "test.py").write_text("test = 'not a function'\n")
        controls = discover_controls(tmp_path)
        with pytest.raises(ProjectError, match="test"):
            load_test_callable(controls[0])


class TestInlineTestCode:
    def _control(self, **kw):  # type: ignore[no-untyped-def]
        from uticen_lite.model.control import FrameworkRefs

        base = dict(
            id="c",
            title="t",
            objective="o",
            narrative="n",
            framework_refs=FrameworkRefs(),
            risk=None,
            sources=[],
        )
        base.update(kw)
        return ControlDef(**base)

    def test_load_test_callable_from_inline_code(self):
        c = self._control(
            test_code="def test(pop):\n    return [{'item_key': 'X', 'description': 'd'}]"
        )
        fn = load_test_callable(c)
        assert callable(fn)
        assert fn(None) == [{"item_key": "X", "description": "d"}]


class TestProject:
    def test_load_returns_project(self):
        project = Project.load(SAMPLE)
        assert isinstance(project, Project)

    def test_project_config(self):
        project = Project.load(SAMPLE)
        assert project.config.name == "Sample Audit Project"

    def test_project_sources(self):
        project = Project.load(SAMPLE)
        assert "gl" in project.sources

    def test_project_controls(self):
        project = Project.load(SAMPLE)
        assert len(project.controls) == 1
        assert project.controls[0].id == "cash_cutoff"
