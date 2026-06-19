"""Tests for the ``cflow build`` subcommand (store-backed)."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from controlflow_sdk.bundle import read_bundle
from controlflow_sdk.cli import main
from controlflow_sdk.cli.build_cmd import build_cmd
from controlflow_sdk.cli.import_cmd import import_cmd
from controlflow_sdk.cli.run_cmd import run_cmd
from controlflow_sdk.schema.validate import validate_bundle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "northwind-trading"

FIXED_AT = "2026-03-31T00:00:00+00:00"


def _engagement(tmp_path: Path) -> Path:
    """Import the northwind example into an engagement dir and copy data files."""
    into = tmp_path / "eng"
    import_cmd(argparse.Namespace(src=str(EXAMPLE_DIR), into=str(into)))
    # copy data files the imported sources point at
    shutil.copytree(str(EXAMPLE_DIR / "data"), str(into / "data"))
    return into


# ---------------------------------------------------------------------------
# Primary store-backed test (per brief)
# ---------------------------------------------------------------------------


def test_run_then_build_from_store(tmp_path: Path) -> None:
    """End-to-end: import → run → build → valid bundle."""
    root = _engagement(tmp_path)
    assert run_cmd(argparse.Namespace(dir=str(root), control=None, at=FIXED_AT)) == 0
    out = root / "import-bundle.zip"
    assert build_cmd(argparse.Namespace(dir=str(root), out=str(out), at=FIXED_AT)) == 0
    manifest = read_bundle(out)
    assert manifest["schema_version"] == "1.0"
    assert len(manifest["controls"]) == 8
    # contract conformance is asserted by tests/test_contract_export.py against the schema


# ---------------------------------------------------------------------------
# Happy path via main()
# ---------------------------------------------------------------------------


class TestBuildHappyPath:
    def test_returns_0(self, tmp_path: Path) -> None:
        """build exits 0 after a successful run."""
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        out_zip = tmp_path / "bundle.zip"
        rc = main(["build", str(root), "--out", str(out_zip), "--at", FIXED_AT])
        assert rc == 0

    def test_creates_zip(self, tmp_path: Path) -> None:
        """build writes a zip file at the --out path."""
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        out_zip = tmp_path / "bundle.zip"
        main(["build", str(root), "--out", str(out_zip), "--at", FIXED_AT])
        assert out_zip.exists(), f"Expected {out_zip} to be created"

    def test_bundle_passes_validation(self, tmp_path: Path) -> None:
        """The manifest inside the zip must pass validate_bundle with no errors."""
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        out_zip = tmp_path / "bundle.zip"
        main(["build", str(root), "--out", str(out_zip), "--at", FIXED_AT])
        manifest = read_bundle(out_zip)
        errors = validate_bundle(manifest)
        assert errors == [], f"Bundle failed validation: {errors}"

    def test_prints_bundle_path(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """build prints the output path on success."""
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        out_zip = tmp_path / "bundle.zip"
        main(["build", str(root), "--out", str(out_zip), "--at", FIXED_AT])
        out = capsys.readouterr().out
        assert str(out_zip) in out

    def test_prints_control_and_run_counts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """build prints control count and run count on success."""
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        out_zip = tmp_path / "bundle.zip"
        main(["build", str(root), "--out", str(out_zip), "--at", FIXED_AT])
        out = capsys.readouterr().out
        # Summary line: "  BUNDLE  <path>  8 controls / 8 runs"
        assert "8 controls" in out
        assert "8 runs" in out


# ---------------------------------------------------------------------------
# No runs: must exit 1 with a helpful message
# ---------------------------------------------------------------------------


class TestBuildNoRuns:
    def test_returns_1_when_no_runs(self, tmp_path: Path) -> None:
        """build exits 1 when store has no runs (i.e. cflow run has not been run)."""
        root = _engagement(tmp_path)
        out_zip = tmp_path / "bundle.zip"
        rc = main(["build", str(root), "--out", str(out_zip), "--at", FIXED_AT])
        assert rc == 1

    def test_prints_run_before_build_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """build prints a guidance message when no runs are in the store."""
        root = _engagement(tmp_path)
        out_zip = tmp_path / "bundle.zip"
        main(["build", str(root), "--out", str(out_zip), "--at", FIXED_AT])
        combined = capsys.readouterr()
        output = combined.out + combined.err
        assert "errors" in output.lower()


# ---------------------------------------------------------------------------
# --out default
# ---------------------------------------------------------------------------


class TestBuildOutDefault:
    def test_default_out_in_project_dir(self, tmp_path: Path) -> None:
        """When --out is omitted, build writes import-bundle.zip in the project dir."""
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        rc = main(["build", str(root), "--at", FIXED_AT])
        assert rc == 0
        assert (root / "import-bundle.zip").exists()


# ---------------------------------------------------------------------------
# --at default (clock boundary)
# ---------------------------------------------------------------------------


class TestBuildAtDefault:
    def test_build_without_at_exits_0(self, tmp_path: Path) -> None:
        """--at is optional; the CLI injects now() when omitted."""
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        out_zip = tmp_path / "bundle.zip"
        rc = main(["build", str(root), "--out", str(out_zip)])
        assert rc == 0
