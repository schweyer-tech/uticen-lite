"""Immutable JSONL run log for appending control test results."""

import json
from pathlib import Path

from controlflow_sdk.model.run import RunRecord


def append_run(target_dir: Path, run: RunRecord) -> None:
    """
    Append one run as a JSON line to <target_dir>/run-log.json.

    Creates target_dir if it doesn't exist. Opens in append mode so prior
    lines are never rewritten (immutability guarantee).

    Args:
        target_dir: Directory where run-log.json will be stored.
        run: RunRecord to append.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    runlog_path = target_dir / "run-log.json"

    # Convert to dict and serialize to JSON line
    run_dict = run.to_dict()
    json_line = json.dumps(run_dict)

    # Append mode ensures we never rewrite prior lines
    with open(runlog_path, "a") as f:
        f.write(json_line + "\n")


def read_runs(target_dir: Path) -> list[dict]:
    """
    Read all JSONL entries from <target_dir>/run-log.json.

    Returns entries in order. Returns an empty list if the file doesn't exist.

    Args:
        target_dir: Directory containing run-log.json.

    Returns:
        List of dicts, one per JSON line, in order.
    """
    target_dir = Path(target_dir)
    runlog_path = target_dir / "run-log.json"

    if not runlog_path.exists():
        return []

    runs = []
    with open(runlog_path) as f:
        for line in f:
            line = line.strip()
            if line:  # Skip empty lines
                runs.append(json.loads(line))

    return runs
