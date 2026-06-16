"""Tests for the ``cflow run`` subcommand."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from controlflow_sdk.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PROJECT = Path(__file__).parent.parent / "project" / "fixtures" / "sample"

FIXED_AT = "2026-06-16T00:00:00Z"

# The fixture control id (from controls/cash_cutoff/control.yaml).
CONTROL_ID = "cash_cutoff"


def _copy_project(src: Path, dest: Path) -> Path:
    """Recursively copy the fixture project into dest (excluding __pycache__)."""
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


# ---------------------------------------------------------------------------
# Happy-path: run all controls
# ---------------------------------------------------------------------------


class TestRunAll:
    def test_returns_0(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        rc = main(["run", str(proj), "--at", FIXED_AT])
        assert rc == 0

    def test_creates_markdown_workpaper(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        wp = proj / "target" / "workpapers" / f"{CONTROL_ID}.md"
        assert wp.exists(), f"Expected {wp} to be created"
        assert wp.stat().st_size > 0

    def test_creates_html_workpaper(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        wp = proj / "target" / "workpapers" / f"{CONTROL_ID}.html"
        assert wp.exists(), f"Expected {wp} to be created"
        assert wp.stat().st_size > 0

    def test_html_starts_with_doctype(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        html = (proj / "target" / "workpapers" / f"{CONTROL_ID}.html").read_text()
        assert html.strip().lower().startswith("<!doctype html>")

    def test_creates_violations_json(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        ev = proj / "target" / "evidence" / f"{CONTROL_ID}-violations.json"
        assert ev.exists(), f"Expected {ev} to be created"

    def test_violations_json_is_list(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        ev = proj / "target" / "evidence" / f"{CONTROL_ID}-violations.json"
        data = json.loads(ev.read_text())
        assert isinstance(data, list)

    def test_creates_run_log(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        log = proj / "target" / "run-log.json"
        assert log.exists(), "Expected target/run-log.json to be created"

    def test_run_log_is_non_empty(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        log = proj / "target" / "run-log.json"
        assert log.stat().st_size > 0

    def test_run_log_contains_control_id(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        log = proj / "target" / "run-log.json"
        line = log.read_text().strip().splitlines()[0]
        record = json.loads(line)
        assert record["control_id"] == CONTROL_ID

    def test_run_log_contains_executed_at(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        log = proj / "target" / "run-log.json"
        line = log.read_text().strip().splitlines()[0]
        record = json.loads(line)
        assert record["executed_at"] == FIXED_AT

    def test_prints_summary_line(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        out = capsys.readouterr().out
        # Summary line must mention the control id
        assert CONTROL_ID in out


# ---------------------------------------------------------------------------
# --control filter
# ---------------------------------------------------------------------------


class TestRunSingleControl:
    def test_returns_0_for_known_control(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        rc = main(["run", str(proj), "--control", CONTROL_ID, "--at", FIXED_AT])
        assert rc == 0

    def test_creates_workpaper_for_selected_control(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--control", CONTROL_ID, "--at", FIXED_AT])
        wp = proj / "target" / "workpapers" / f"{CONTROL_ID}.md"
        assert wp.exists()

    def test_returns_1_for_unknown_control(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        rc = main(["run", str(proj), "--control", "does_not_exist", "--at", FIXED_AT])
        assert rc == 1

    def test_unknown_control_prints_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--control", "does_not_exist", "--at", FIXED_AT])
        out = capsys.readouterr()
        combined = out.out + out.err
        assert "does_not_exist" in combined


# ---------------------------------------------------------------------------
# --at default (clock boundary)
# ---------------------------------------------------------------------------


class TestAtDefault:
    def test_run_without_at_still_exits_0(self, tmp_path: Path) -> None:
        """--at is optional; the CLI injects now() when omitted."""
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        rc = main(["run", str(proj)])
        assert rc == 0

    def test_run_without_at_creates_workpaper(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj)])
        wp = proj / "target" / "workpapers" / f"{CONTROL_ID}.md"
        assert wp.exists()


# ---------------------------------------------------------------------------
# Partial-failure: one bad control must not abort others; exit code == 1
# ---------------------------------------------------------------------------


class TestPartialFailure:
    """A project with one good and one bad control.

    The bad control's test.py raises at runtime, which causes run_control to
    raise RunnerError.  The CLI must:
      - continue running the good control (not abort on first error),
      - return exit code 1 (because at least one control errored),
      - report the bad control id / "error" to stderr.
    """

    GOOD_ID = "good_ctrl"
    BAD_ID = "bad_ctrl"
    FIXED_AT = "2026-06-16T00:00:00Z"

    def _build_project(self, root: Path) -> Path:
        """Construct a minimal two-control project under *root* and return its path."""
        proj = root / "proj"

        # --- cflow.yaml ---
        (proj).mkdir(parents=True)
        (proj / "cflow.yaml").write_text(
            "name: Partial Failure Test\n"
            "framework: NIST SP 800-53\n"
            "system:\n"
            "  name: Test System\n"
            "  description: Partial failure test harness\n"
            "defaults:\n"
            "  severity: medium\n",
            encoding="utf-8",
        )

        # --- sources.yaml (reuse gl.csv structure) ---
        (proj / "sources.yaml").write_text(
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
            "      - original_name: amount\n"
            "        display_name: Amount\n"
            "        is_key: false\n"
            "        include: true\n"
            "      - original_name: posting_date\n"
            "        display_name: Posting Date\n"
            "        is_key: false\n"
            "        include: true\n",
            encoding="utf-8",
        )

        # --- gl.csv (same minimal fixture as sample project) ---
        (proj / "gl.csv").write_text(
            "entry_id,amount,posting_date\n"
            "GL-001,150.00,2024-01-15\n"
            "GL-002,-75.50,2024-01-16\n"
            "GL-003,300.00,2024-01-17\n",
            encoding="utf-8",
        )

        # --- good_ctrl ---
        good_dir = proj / "controls" / self.GOOD_ID
        good_dir.mkdir(parents=True)
        (good_dir / "control.yaml").write_text(
            f"id: {self.GOOD_ID}\n"
            "title: Good Control\n"
            "objective: Always passes.\n"
            "narrative: This control always returns an empty violation list.\n"
            "sources:\n"
            "  - id: gl\n"
            "framework_refs:\n"
            "  nist:\n"
            "    - AC-2\n"
            "test_path: test.py\n",
            encoding="utf-8",
        )
        (good_dir / "test.py").write_text(
            "def test(pop):\n    return []\n",
            encoding="utf-8",
        )

        # --- bad_ctrl (raises at runtime → RunnerError) ---
        bad_dir = proj / "controls" / self.BAD_ID
        bad_dir.mkdir(parents=True)
        (bad_dir / "control.yaml").write_text(
            f"id: {self.BAD_ID}\n"
            "title: Bad Control\n"
            "objective: Always crashes.\n"
            "narrative: This control raises to simulate an author-code failure.\n"
            "sources:\n"
            "  - id: gl\n"
            "framework_refs:\n"
            "  nist:\n"
            "    - AC-3\n"
            "test_path: test.py\n",
            encoding="utf-8",
        )
        (bad_dir / "test.py").write_text(
            'def test(pop):\n    raise ValueError("boom")\n',
            encoding="utf-8",
        )

        return proj

    def test_returns_exit_1(self, tmp_path: Path) -> None:
        """Exit code must be 1 when any control errored."""
        proj = self._build_project(tmp_path)
        rc = main(["run", str(proj), "--at", self.FIXED_AT])
        assert rc == 1

    def test_good_control_workpaper_created(self, tmp_path: Path) -> None:
        """The good control's workpaper must still be written (no abort-on-first-error)."""
        proj = self._build_project(tmp_path)
        main(["run", str(proj), "--at", self.FIXED_AT])
        wp = proj / "target" / "workpapers" / f"{self.GOOD_ID}.md"
        assert wp.exists(), f"Expected {wp} to be created despite the other control failing"

    def test_bad_control_workpaper_absent(self, tmp_path: Path) -> None:
        """The bad control must NOT produce a workpaper (it raised before assembly)."""
        proj = self._build_project(tmp_path)
        main(["run", str(proj), "--at", self.FIXED_AT])
        wp = proj / "target" / "workpapers" / f"{self.BAD_ID}.md"
        assert not wp.exists(), "Bad control should not have produced a workpaper"

    def test_error_reported_for_bad_control(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """The bad control id and an error indicator must appear in stderr output."""
        proj = self._build_project(tmp_path)
        main(["run", str(proj), "--at", self.FIXED_AT])
        combined = capsys.readouterr().err
        assert self.BAD_ID in combined
        assert "ERROR" in combined.upper()
