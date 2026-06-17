"""Full-population control execution — runner core.

This module is Pyodide-safe: it imports adapters only via ``source_for``
(which is CPython/pandas-aware) and never touches pandas directly.
The DataFrame is accessed solely through the :class:`~controlflow_sdk.model.population.Population`
abstraction that ``source_for(...).load()`` returns.
"""

from __future__ import annotations

import traceback
import types
from pathlib import Path
from typing import Any

from controlflow_sdk.adapters.files import source_for
from controlflow_sdk.model.control import ControlDef, SourceBinding
from controlflow_sdk.model.population import Population
from controlflow_sdk.model.run import RunRecord, SourceProvenance
from controlflow_sdk.model.violation import Violation
from controlflow_sdk.project.discovery import load_test_callable


class RunnerError(Exception):
    """Wraps author-code failures with the control id and an original traceback summary."""


def _is_sdk_internal_frame(frame: types.FrameType | None) -> bool:
    """Return True if the frame belongs to SDK internals or installed packages."""
    if frame is None:
        return False
    filename = frame.f_code.co_filename
    return "controlflow_sdk" in filename or "site-packages" in filename


def _clean_traceback_summary(exc: BaseException) -> str:
    """Build a traceback summary that contains only user (non-SDK) frames.

    SDK-internal frames (any frame whose filename contains 'controlflow_sdk'
    or 'site-packages') are stripped. If no user frames remain the summary
    falls back to just the exception type and message.
    """
    tb = exc.__traceback__
    # Collect only user frames
    user_frames: list[traceback.FrameSummary] = []
    current = tb
    while current is not None:
        frame = current.tb_frame
        filename = frame.f_code.co_filename
        if "controlflow_sdk" not in filename and "site-packages" not in filename:
            user_frames.append(
                traceback.FrameSummary(
                    filename=filename,
                    lineno=current.tb_lineno,
                    name=frame.f_code.co_name,
                    lookup_line=True,
                )
            )
        current = current.tb_next

    exc_line = f"{type(exc).__name__}: {exc}"
    if not user_frames:
        return exc_line

    frame_lines = "".join(traceback.StackSummary.from_list(user_frames).format())
    return f"{frame_lines}{exc_line}"


def run_control(
    control: ControlDef,
    sources: dict[str, SourceBinding],
    root: Path,
    executed_at: str,
) -> RunRecord:
    """Execute a control's ``test()`` over the full population and return a :class:`RunRecord`.

    Parameters
    ----------
    control:
        The parsed :class:`~controlflow_sdk.model.control.ControlDef` whose
        ``sources`` list declares the data bindings and whose ``test_path``
        points to the author's ``test.py``.
    sources:
        All project source bindings keyed by source id (e.g. from
        :func:`~controlflow_sdk.project.loader.load_sources`).  The runner
        resolves each id named in ``control.sources`` against this dict.
    root:
        Project root directory; file paths inside source configs are resolved
        relative to this.
    executed_at:
        ISO-8601 timestamp string to embed in the :class:`RunRecord`.  The
        runner never calls ``datetime.now()`` — callers own the clock.

    Returns
    -------
    RunRecord
        A fully populated :class:`RunRecord` with ``population_size``,
        ``violations`` (every element validated via
        :meth:`~controlflow_sdk.model.violation.Violation.from_raw`), and
        ``provenance`` for every loaded source.

    Raises
    ------
    RunnerError
        - If the author callable raises any exception.
        - If the callable returns a non-list value.
        - If any element of the returned list fails
          :meth:`~controlflow_sdk.model.violation.Violation.from_raw` validation.
    """
    # ── 1. Load every bound source ────────────────────────────────────────────
    populations: list[Population] = []
    prov_records: list[SourceProvenance] = []

    for binding in control.sources:
        src_binding = sources[binding.id]
        adapter = source_for(src_binding, root)
        pop = adapter.load()
        raw_prov: dict[str, Any] = adapter.provenance()
        prov_records.append(
            SourceProvenance(
                source_id=src_binding.id,
                path=raw_prov["path"],
                sha256=raw_prov["sha256"],
                row_count=raw_prov["row_count"],
            )
        )
        populations.append(pop)

    # ── 2. Select the primary population (first bound source) ─────────────────
    primary: Population = populations[0]

    # ── 3. Load and execute the author callable ───────────────────────────────
    test_fn = load_test_callable(control)

    try:
        raw_result: Any = test_fn(primary)
    except Exception as exc:
        tb_summary = _clean_traceback_summary(exc)
        raise RunnerError(
            f"Control '{control.id}': test() raised an exception:\n{tb_summary}"
        ) from exc

    # ── 4. Validate return type ───────────────────────────────────────────────
    if not isinstance(raw_result, list):
        raise RunnerError(
            f"Control '{control.id}': test() must return a list, got {type(raw_result).__name__!r}"
        )

    # ── 5. Coerce each element via Violation.from_raw ─────────────────────────
    violations: list[Violation] = []
    for i, raw_viol in enumerate(raw_result):
        try:
            violations.append(Violation.from_raw(raw_viol))
        except (ValueError, KeyError, TypeError) as exc:
            raise RunnerError(
                f"Control '{control.id}': violation at index {i} is malformed: {exc}"
            ) from exc

    # ── 6. Assemble and return RunRecord ─────────────────────────────────────
    return RunRecord(
        control_id=control.id,
        executed_at=executed_at,
        population_size=primary.size,
        violations=violations,
        provenance=prov_records,
    )
