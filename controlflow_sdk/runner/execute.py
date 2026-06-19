"""Full-population control execution — runner core.

This module is Pyodide-safe: it imports adapters only via ``source_for``
(which is CPython/pandas-aware) and never touches pandas directly.
The DataFrame is accessed solely through the :class:`~controlflow_sdk.model.population.Population`
abstraction that ``source_for(...).load()`` returns.
"""

from __future__ import annotations

import inspect
import traceback
from pathlib import Path
from typing import Any

from controlflow_sdk.adapters.files import source_for
from controlflow_sdk.model.control import ControlDef, SourceBinding
from controlflow_sdk.model.population import Population
from controlflow_sdk.model.run import RunRecord, SourceProvenance
from controlflow_sdk.model.violation import Violation
from controlflow_sdk.model.workpaper import MAX_SAMPLE_ROWS, DataSample
from controlflow_sdk.project.discovery import load_test_callable


class RunnerError(Exception):
    """Wraps author-code failures with the control id and an original traceback summary."""


def _sample_from_population(pop: Population, path: str, binding: SourceBinding) -> DataSample:
    """Build a capped, render-only :class:`DataSample` from a loaded population.

    Columns are the population's *included* columns' display names; rows are the
    first :data:`MAX_SAMPLE_ROWS` rows, every cell stringified (the renderer
    HTML-escapes them).  ``total_rows`` records the full population size so the
    renderer can show "first 500 of N" when capped.  The bound source's optional
    ``description`` / ``completeness_accuracy`` prose is threaded through for the
    renderer's Data Sources section.
    """
    cols = [c for c in pop.columns if c.include]
    display_names = [c.display_name for c in cols]
    original_names = [c.original_name for c in cols]

    rows: list[list[str]] = []
    head = pop.df.head(MAX_SAMPLE_ROWS)
    for record in head.to_dict(orient="records"):
        rows.append([_cell_str(record.get(name, "")) for name in original_names])

    return DataSample(
        source_id=pop.source_id,
        path=path,
        columns=display_names,
        rows=rows,
        total_rows=pop.size,
        description=binding.description,
        completeness_accuracy=binding.completeness_accuracy,
        extract_date=binding.extract_date,
    )


def _cell_str(value: object) -> str:
    """Stringify a cell value for the data table (renderer escapes it)."""
    if value is None:
        return ""
    # pandas NaN / NaT compare unequal to themselves.
    if value != value:  # noqa: PLR0124
        return ""
    return str(value)


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


def _accepts_sources(test_fn: object) -> bool:
    """True if the author's test() declares a second positional parameter (the
    sources dict) or accepts *args — i.e. wants multi-source access."""
    try:
        params = list(inspect.signature(test_fn).parameters.values())  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    positional = [
        p
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_var_positional = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params)
    return len(positional) >= 2 or has_var_positional


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
    sources_by_id: dict[str, Population] = {}

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
        sources_by_id[binding.id] = pop

    # ── 2. Select the primary population (first bound source) ─────────────────
    primary: Population = populations[0]

    # ── 3. Load and execute the author callable OR evaluate the rule spec ────
    if control.rule_spec is not None:
        from controlflow_sdk.rules.evaluate import evaluate_rule
        from controlflow_sdk.rules.spec import parse_rule_spec

        raw_result: Any = evaluate_rule(parse_rule_spec(control.rule_spec), primary)
    else:
        test_fn = load_test_callable(control)

        try:
            if _accepts_sources(test_fn):
                raw_result = test_fn(primary, sources_by_id)
            else:
                raw_result = test_fn(primary)
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


def collect_data_samples(
    control: ControlDef,
    sources: dict[str, SourceBinding],
    root: Path,
) -> list[DataSample]:
    """Load each bound source and return capped, render-only row samples.

    Used by ``cflow run`` to embed an interactive data table in the HTML
    workpaper.  Each source contributes at most :data:`MAX_SAMPLE_ROWS` rows.
    Sources are deduped by id (first binding wins) so a source bound to several
    procedures is sampled once.  This is **render-only** — samples never enter
    the import bundle.
    """
    samples: list[DataSample] = []
    seen: set[str] = set()
    for binding in control.sources:
        if binding.id in seen:
            continue
        seen.add(binding.id)
        src_binding = sources[binding.id]
        adapter = source_for(src_binding, root)
        pop = adapter.load()
        path = adapter.provenance()["path"]
        samples.append(_sample_from_population(pop, path, src_binding))
    return samples
