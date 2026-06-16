"""ControlFlow SDK runner — full-population control execution.

Public API
----------
``run_control``
    Execute a control's ``test()`` over the full data population and return a
    :class:`~controlflow_sdk.model.run.RunRecord`.

``RunnerError``
    Exception raised when author code fails (exception in ``test()``, non-list
    return value, or a malformed violation element).
"""

from __future__ import annotations

from controlflow_sdk.runner.execute import RunnerError, run_control

__all__ = ["RunnerError", "run_control"]
