"""Tests for immutable JSONL run log."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from uticen_lite.model.run import RunRecord, SourceProvenance
from uticen_lite.model.violation import Severity, Violation
from uticen_lite.runner.runlog import append_run, read_runs


@pytest.fixture
def sample_run() -> RunRecord:
    """Create a sample RunRecord for testing."""
    return RunRecord(
        control_id="ctrl-123",
        executed_at="2025-01-15T10:30:00Z",
        population_size=100,
        violations=[
            Violation(
                item_key="INV-001",
                description="Missing required field",
                severity=Severity.HIGH,
                details={"field": "email"},
            ),
        ],
        provenance=[
            SourceProvenance(
                source_id="src-1",
                path="/data/source1.csv",
                sha256="abc123def456",
                row_count=100,
            ),
        ],
    )


def test_append_run_creates_file(sample_run: RunRecord) -> None:
    """Appending a run creates the JSONL file if it doesn't exist."""
    with TemporaryDirectory() as tmpdir:
        target_dir = Path(tmpdir)
        runlog_path = target_dir / "run-log.json"

        assert not runlog_path.exists()
        append_run(target_dir, sample_run)

        assert runlog_path.exists()


def test_append_run_writes_json_line(sample_run: RunRecord) -> None:
    """Appending a run writes a single JSON line."""
    with TemporaryDirectory() as tmpdir:
        target_dir = Path(tmpdir)
        runlog_path = target_dir / "run-log.json"

        append_run(target_dir, sample_run)

        # Read the raw file
        content = runlog_path.read_text()
        lines = content.strip().split("\n")

        assert len(lines) == 1
        # Verify it's valid JSON
        data = json.loads(lines[0])
        assert data["control_id"] == "ctrl-123"
        assert data["failed"] == 1
        assert data["passed"] == 99


def test_two_appends_creates_two_lines(sample_run: RunRecord) -> None:
    """Two append_run calls produce two JSONL lines."""
    with TemporaryDirectory() as tmpdir:
        target_dir = Path(tmpdir)
        runlog_path = target_dir / "run-log.json"

        # First append
        append_run(target_dir, sample_run)

        # Save the first line's bytes for later comparison
        runlog_path.read_bytes()

        # Create a second run with different data
        second_run = RunRecord(
            control_id="ctrl-456",
            executed_at="2025-01-15T11:00:00Z",
            population_size=50,
            violations=[
                Violation(
                    item_key="INV-002",
                    description="Invalid format",
                    severity=Severity.MEDIUM,
                )
            ],
            provenance=[],
        )

        # Second append
        append_run(target_dir, second_run)

        # Read all lines
        content = runlog_path.read_text()
        lines = content.strip().split("\n")

        assert len(lines) == 2
        data1 = json.loads(lines[0])
        data2 = json.loads(lines[1])

        assert data1["control_id"] == "ctrl-123"
        assert data2["control_id"] == "ctrl-456"


def test_first_line_immutable_after_second_append(
    sample_run: RunRecord,
) -> None:
    """The first line remains byte-identical after a second append."""
    with TemporaryDirectory() as tmpdir:
        target_dir = Path(tmpdir)
        runlog_path = target_dir / "run-log.json"

        # First append
        append_run(target_dir, sample_run)
        first_append_content = runlog_path.read_bytes()
        first_line = first_append_content.split(b"\n")[0]

        # Second append
        second_run = RunRecord(
            control_id="ctrl-999",
            executed_at="2025-01-15T12:00:00Z",
            population_size=25,
            violations=[],
            provenance=[],
        )
        append_run(target_dir, second_run)

        # Read all content
        all_content = runlog_path.read_bytes()
        all_lines = all_content.split(b"\n")

        # First line should be identical
        assert all_lines[0] == first_line


def test_read_runs_empty_file_missing() -> None:
    """read_runs returns an empty list if the file doesn't exist."""
    with TemporaryDirectory() as tmpdir:
        target_dir = Path(tmpdir)
        result = read_runs(target_dir)
        assert result == []


def test_read_runs_returns_all_entries(sample_run: RunRecord) -> None:
    """read_runs returns all JSONL entries as dicts in order."""
    with TemporaryDirectory() as tmpdir:
        target_dir = Path(tmpdir)

        # Append two runs
        append_run(target_dir, sample_run)

        second_run = RunRecord(
            control_id="ctrl-xyz",
            executed_at="2025-01-15T13:00:00Z",
            population_size=75,
            violations=[],
            provenance=[],
        )
        append_run(target_dir, second_run)

        # Read all
        runs = read_runs(target_dir)

        assert len(runs) == 2
        assert runs[0]["control_id"] == "ctrl-123"
        assert runs[1]["control_id"] == "ctrl-xyz"
        assert isinstance(runs[0], dict)
        assert isinstance(runs[1], dict)
