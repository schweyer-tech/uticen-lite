"""Uticen SDK runner — full-population control execution.

Public API
----------
``run_control``
    Execute a control's ``test()`` over the full data population and return a
    :class:`~uticen_lite.model.run.RunRecord`.

``append_run``
    Append a :class:`~uticen_lite.model.run.RunRecord` to an immutable
    JSONL run log.

``read_runs``
    Read all entries from an immutable JSONL run log.

``RunnerError``
    Exception raised when author code fails (exception in ``test()``, non-list
    return value, or a malformed violation element).
"""

from __future__ import annotations

from uticen_lite.runner.execute import RunnerError, collect_data_samples, run_control
from uticen_lite.runner.runlog import append_run, read_runs

__all__ = [
    "RunnerError",
    "run_control",
    "collect_data_samples",
    "append_run",
    "read_runs",
]
