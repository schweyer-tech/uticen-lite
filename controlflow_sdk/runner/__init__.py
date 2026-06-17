"""ControlFlow SDK runner — full-population control execution.

Public API
----------
``run_control``
    Execute a control's ``test()`` over the full data population and return a
    :class:`~controlflow_sdk.model.run.RunRecord`.

``append_run``
    Append a :class:`~controlflow_sdk.model.run.RunRecord` to an immutable
    JSONL run log.

``read_runs``
    Read all entries from an immutable JSONL run log.

``RunnerError``
    Exception raised when author code fails (exception in ``test()``, non-list
    return value, or a malformed violation element).
"""

from __future__ import annotations

from controlflow_sdk.runner.execute import RunnerError, collect_data_samples, run_control
from controlflow_sdk.runner.runlog import append_run, read_runs

__all__ = [
    "RunnerError",
    "run_control",
    "collect_data_samples",
    "append_run",
    "read_runs",
]
