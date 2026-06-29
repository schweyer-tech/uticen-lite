"""Tests for the ``uticen-lite build`` subcommand (store-backed)."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from uticen_lite.bundle import read_bundle
from uticen_lite.cli import main
from uticen_lite.cli.build_cmd import build_cmd
from uticen_lite.cli.import_cmd import import_cmd
from uticen_lite.cli.run_cmd import run_cmd
from uticen_lite.schema.validate import validate_bundle

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
    assert len(manifest["controls"]) == 9
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
        # Summary line: "  BUNDLE  <path>  9 controls / 11 runs"
        # Finance.GL.1 fans out to 2 per-procedure + 1 aggregate = 3 runs; other 8 = 1 each → 11.
        assert "9 controls" in out
        assert "11 runs" in out


# ---------------------------------------------------------------------------
# No runs: must exit 1 with a helpful message
# ---------------------------------------------------------------------------


class TestBuildNoRuns:
    def test_returns_1_when_no_runs(self, tmp_path: Path) -> None:
        """build exits 1 when store has no runs (i.e. uticen-lite run has not been run)."""
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


# ---------------------------------------------------------------------------
# Missing / empty engagement store → friendly error, non-zero exit
# ---------------------------------------------------------------------------


class TestBuildMissingStore:
    def test_build_on_dir_without_store_exits_1(self, tmp_path: Path) -> None:
        """A directory that was never imported has no store — build must exit 1,
        not die with a cryptic 'no such table: project'."""
        empty = tmp_path / "no-store"
        empty.mkdir()
        out_zip = tmp_path / "bundle.zip"
        rc = main(["build", str(empty), "--out", str(out_zip), "--at", FIXED_AT])
        assert rc == 1

    def test_build_on_dir_without_store_prints_actionable_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """The error must name the dir and point the user at `uticen-lite import`,
        not leak the raw sqlite3 'no such table' text."""
        empty = tmp_path / "no-store"
        empty.mkdir()
        out_zip = tmp_path / "bundle.zip"
        main(["build", str(empty), "--out", str(out_zip), "--at", FIXED_AT])
        err = capsys.readouterr().err
        assert "No engagement store found" in err
        assert "uticen-lite import" in err
        assert "no such table" not in err
