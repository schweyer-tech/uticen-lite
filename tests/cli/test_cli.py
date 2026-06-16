"""Tests for the cflow CLI (init / new / validate subcommands)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from controlflow_sdk.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PROJECT = Path(__file__).parent.parent / "project" / "fixtures" / "sample"


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# cflow init
# ---------------------------------------------------------------------------


class TestInit:
    def test_returns_0_and_creates_cflow_yaml(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        rc = main(["init", str(proj)])
        assert rc == 0
        assert (proj / "cflow.yaml").exists()

    def test_creates_sources_yaml(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        assert (proj / "sources.yaml").exists()

    def test_creates_controls_dir(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        assert (proj / "controls").is_dir()

    def test_creates_gitignore_with_target(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        gitignore = proj / ".gitignore"
        assert gitignore.exists()
        assert "target/" in gitignore.read_text()

    def test_scaffolded_sources_yaml_is_valid(self, tmp_path: Path) -> None:
        """sources.yaml produced by init must pass validate_sources."""
        from controlflow_sdk.schema.validate import validate_sources

        proj = tmp_path / "proj"
        main(["init", str(proj)])
        doc = _read_yaml(proj / "sources.yaml")
        errors = validate_sources(doc)
        assert errors == [], f"sources.yaml invalid: {errors}"

    def test_idempotent_on_existing_dir(self, tmp_path: Path) -> None:
        """Running init on an existing project dir exits 0 without overwriting."""
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        # Modify cflow.yaml and re-run — should not overwrite
        (proj / "cflow.yaml").write_text("name: modified\n")
        rc = main(["init", str(proj)])
        assert rc == 0
        assert "modified" in (proj / "cflow.yaml").read_text()


# ---------------------------------------------------------------------------
# cflow validate (round-trip with init)
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate_sample_fixture_exits_0(self, tmp_path: Path) -> None:
        """The fixture project in tests/project/fixtures/sample should be valid."""
        rc = main(["validate", str(SAMPLE_PROJECT)])
        assert rc == 0

    def test_validate_newly_inited_project_exits_0(self, tmp_path: Path) -> None:
        """An init'd project with no controls should validate cleanly."""
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        rc = main(["validate", str(proj)])
        assert rc == 0

    def test_validate_defaults_to_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cflow validate with no dir arg uses the current directory."""
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        monkeypatch.chdir(proj)
        rc = main(["validate"])
        assert rc == 0

    def test_validate_returns_1_on_missing_id(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Removing 'id' from control.yaml makes validate exit 1 and mention 'id'."""
        # Copy fixture project into tmp so we can mutate it
        proj = tmp_path / "proj"
        shutil.copytree(SAMPLE_PROJECT, proj)

        control_yaml = proj / "controls" / "cash_cutoff" / "control.yaml"
        doc = _read_yaml(control_yaml)
        del doc["id"]
        with control_yaml.open("w", encoding="utf-8") as fh:
            yaml.dump(doc, fh)

        rc = main(["validate", str(proj)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "id" in captured.out.lower() or "id" in captured.err.lower()

    def test_validate_prints_control_status_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """validate prints at least one line per control."""
        rc = main(["validate", str(SAMPLE_PROJECT)])
        assert rc == 0
        out = capsys.readouterr().out
        # Should mention the control slug or 'ok'
        assert "cash_cutoff" in out.lower() or "ok" in out.lower()


# ---------------------------------------------------------------------------
# cflow new control
# ---------------------------------------------------------------------------


class TestNew:
    def test_new_control_creates_control_yaml(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        rc = main(["new", "control", "vendor_payments", "--dir", str(proj)])
        assert rc == 0
        assert (proj / "controls" / "vendor_payments" / "control.yaml").exists()

    def test_new_control_creates_test_py(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        main(["new", "control", "vendor_payments", "--dir", str(proj)])
        assert (proj / "controls" / "vendor_payments" / "test.py").exists()

    def test_new_control_yaml_is_schema_valid(self, tmp_path: Path) -> None:
        from controlflow_sdk.schema.validate import validate_control

        proj = tmp_path / "proj"
        main(["init", str(proj)])
        main(["new", "control", "vendor_payments", "--dir", str(proj)])
        doc = _read_yaml(proj / "controls" / "vendor_payments" / "control.yaml")
        errors = validate_control(doc)
        assert errors == [], f"control.yaml invalid: {errors}"

    def test_new_control_test_py_has_test_function(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        main(["new", "control", "vendor_payments", "--dir", str(proj)])
        test_src = (proj / "controls" / "vendor_payments" / "test.py").read_text()
        assert "def test(" in test_src

    def test_new_control_slug_embedded_in_yaml(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        main(["new", "control", "vendor_payments", "--dir", str(proj)])
        doc = _read_yaml(proj / "controls" / "vendor_payments" / "control.yaml")
        assert doc["id"] == "vendor_payments"

    def test_new_control_validates_after_init(self, tmp_path: Path) -> None:
        """After init + new control, validate should still exit 0."""
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        main(["new", "control", "vendor_payments", "--dir", str(proj)])
        rc = main(["validate", str(proj)])
        assert rc == 0

    def test_new_control_uses_cwd_as_default_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        monkeypatch.chdir(proj)
        rc = main(["new", "control", "ap_aging"])
        assert rc == 0
        assert (proj / "controls" / "ap_aging" / "control.yaml").exists()

    def test_new_control_idempotent(self, tmp_path: Path) -> None:
        """Re-running new control on an existing slug does not overwrite."""
        proj = tmp_path / "proj"
        main(["init", str(proj)])
        main(["new", "control", "vendor_payments", "--dir", str(proj)])
        # Modify the file, then re-run
        cy = proj / "controls" / "vendor_payments" / "control.yaml"
        cy.write_text(
            "id: vendor_payments\ntitle: Modified\nobjective: x\nnarrative: y\nsources: []\n"
        )
        rc = main(["new", "control", "vendor_payments", "--dir", str(proj)])
        assert rc == 0
        assert "Modified" in cy.read_text()
