"""Workpaper assembly from a ControlDef + RunRecord."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from uticen_lite.model.control import Threshold

if TYPE_CHECKING:
    from uticen_lite.model.control import ControlDef
    from uticen_lite.model.run import RunRecord


def _fmt_num(value: float) -> str:
    """Format a number for prose: drop a trailing ``.0`` (5.0 → "5", 13.33 → "13.33")."""
    if value == int(value):
        return str(int(value))
    return str(value)


@dataclass
class ProcedureSpec:
    """Metadata for a single procedure in a multi-procedure assemble call.

    Pairs with a :class:`~uticen_lite.model.run.RunRecord` to build one
    :class:`Procedure`.  The ``threshold`` is per-procedure — each procedure is
    evaluated against its own pass/fail rule.
    """

    title: str
    narrative: str
    test_code: str
    code: str = ""
    assertion: str = ""
    threshold: Threshold = field(default_factory=Threshold)


@dataclass
class Procedure:
    """A single test procedure within a workpaper.

    Each procedure carries its own :class:`~uticen_lite.model.control.Threshold`
    and exposes a ``determination`` property computed against that threshold.
    The bundle-facing :meth:`to_dict` intentionally excludes both — per-procedure
    threshold and determination are render/store concerns only.
    """

    title: str
    narrative: str
    test_code: str
    result: RunRecord
    code: str = ""
    assertion: str = ""
    threshold: Threshold = field(default_factory=Threshold)

    @property
    def determination(self) -> Determination:
        """Pass/fail determination for this procedure against its own threshold."""
        return Determination(
            threshold=self.threshold,
            exception_count=len(self.result.violations),
            records_tested=self.result.population_size,
        )

    def to_dict(self) -> dict[str, Any]:
        """Bundle-facing dict — threshold/determination excluded (0015); code/assertion additive."""
        return {
            "code": self.code,
            "title": self.title,
            "assertion": self.assertion,
            "narrative": self.narrative,
            "test_code": self.test_code,
            "result": self.result.to_dict(),
        }


# Max rows embedded per data source in the rendered workpaper's data table.
MAX_SAMPLE_ROWS = 500


@dataclass
class DataSample:
    """A capped, render-only sample of a bound source's rows.

    Carries the included columns' display names and up to :data:`MAX_SAMPLE_ROWS`
    rows so the HTML renderer can embed an interactive data table.  ``total_rows``
    is the *full* row count, so the renderer can show "first 500 of N" when capped.

    ``description`` and ``completeness_accuracy`` carry the author-supplied
    Data Sources prose (threaded from the bound source) so the renderer can show
    a Description line and a Completeness & Accuracy assertion per source.

    ``extract_date`` is the optional author-supplied as-of date of the extract
    (the date the data is current as of). When absent the renderer falls back to
    the run's execution/as-of date.

    This is **render-only**: it is never serialised into the import bundle (the
    bundle keeps its no-raw-rows trust boundary).
    """

    source_id: str
    path: str
    columns: list[str]
    rows: list[list[str]] = field(default_factory=list)
    total_rows: int = 0
    description: str | None = None
    completeness_accuracy: str | None = None
    extract_date: str | None = None

    @property
    def capped(self) -> bool:
        return self.total_rows > len(self.rows)


@dataclass(frozen=True)
class Determination:
    """The threshold-based pass/fail determination — single source of truth.

    Both the Results-bar verdict pill and the Conclusion section derive from
    this, so they can never disagree.
    """

    threshold: Threshold
    exception_count: int
    records_tested: int

    @property
    def exception_rate(self) -> float:
        if self.records_tested == 0:
            return 0.0
        return round(self.exception_count / self.records_tested * 100, 2)

    @property
    def passed(self) -> bool:
        return self.threshold.passes(self.exception_count, self.records_tested)

    @property
    def verdict(self) -> str:
        return "Operated effectively" if self.passed else "Operated with deficiencies"

    def conclusion_text(self) -> tuple[str, str]:
        """Return ``(threshold_text, result_text)`` prose for the Conclusion.

        The threshold sentence states the pass rule; the result sentence states
        the measured outcome and the determination.  Renderers wrap these in
        their own markup (the result outcome is bolded / colour-coded).
        """
        n = self.exception_count
        records = self.records_tested
        rate = self.exception_rate
        outcome = (
            "within threshold → control operated effectively."
            if self.passed
            else "exceeds threshold → control did not operate effectively."
        )

        if self.threshold.is_implicit_zero:
            threshold_text = "Threshold: zero exceptions tolerated."
            if n == 0:
                result_text = "Result: 0 exceptions → operated effectively."
            else:
                result_text = f"Result: {n} exception(s) → did not operate effectively."
            return threshold_text, result_text

        bounds: list[str] = []
        if self.threshold.failure_threshold_pct is not None:
            pct_str = _fmt_num(self.threshold.failure_threshold_pct)
            bounds.append(f"the exception rate is at or below {pct_str}%")
        if self.threshold.failure_threshold_count is not None:
            bounds.append(f"no more than {self.threshold.failure_threshold_count} exception(s)")
        threshold_text = "Threshold: control passes when " + " and ".join(bounds) + "."
        result_text = f"Result: {_fmt_num(rate)}% ({n} / {records} records) → {outcome}"
        return threshold_text, result_text


@dataclass(frozen=True)
class _RollUpDetermination(Determination):
    """Internal subclass that overrides ``passed`` with an explicit any-fails flag.

    Used only for N>1 procedures.  Keeps the aggregate headline counts (for
    display) while correctly reporting the multi-procedure roll-up verdict.
    ``_all_pass`` is set by :meth:`Workpaper.determination` after evaluating
    every procedure against its own threshold.
    """

    _all_pass: bool = False

    @property
    def passed(self) -> bool:  # type: ignore[override]
        return self._all_pass


@dataclass
class Workpaper:
    """Structured audit workpaper assembled from a control definition and run record.

    This is the single source consumed by both the renderer and the bundle — all
    derived/generated fields are present in the dataclass so neither consumer has
    to re-derive them.
    """

    control_id: str
    title: str
    objective: str
    narrative: str
    framework_refs: dict[str, Any]
    procedures: list[Procedure] = field(default_factory=list)
    generated_at: str = ""
    threshold: Threshold = field(default_factory=Threshold)
    # Render-only capped row samples per bound source (never serialised to bundle).
    data_samples: list[DataSample] = field(default_factory=list)

    # ── derived ──────────────────────────────────────────────────────────────

    @property
    def records_tested(self) -> int:
        return sum(p.result.population_size for p in self.procedures)

    @property
    def exception_count(self) -> int:
        return sum(len(p.result.violations) for p in self.procedures)

    @property
    def determination(self) -> Determination:
        """Any-fails roll-up: passes iff every procedure passes its own threshold.

        The headline ``exception_count`` and ``records_tested`` always aggregate
        across all procedures (for display).

        For N<=1: uses the workpaper-level threshold against aggregate counts —
        identical to the original single-procedure behaviour, so all existing
        workpapers built via ``assemble`` or direct construction are unaffected.

        For N>1 (multi-procedure): each procedure is evaluated against its own
        per-procedure threshold; the control passes iff every procedure passes.
        """
        if len(self.procedures) <= 1:
            # N==0 or N==1: original aggregate behaviour, workpaper-level threshold.
            return Determination(
                threshold=self.threshold,
                exception_count=self.exception_count,
                records_tested=self.records_tested,
            )
        # N>1: any-fails roll-up — override passed/verdict while keeping aggregate counts.
        all_pass = all(p.determination.passed for p in self.procedures)
        return _RollUpDetermination(
            threshold=self.threshold,
            exception_count=self.exception_count,
            records_tested=self.records_tested,
            _all_pass=all_pass,
        )

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def assemble(
        cls,
        control: ControlDef,
        run: RunRecord,
        generated_at: str,
        data_samples: list[DataSample] | None = None,
        test_code: str | None = None,
    ) -> Workpaper:
        """Build a Workpaper from a ControlDef and a RunRecord.

        Reads the test source from ``control.test_path`` and wraps the run in a
        single :class:`Procedure`.  ``generated_at`` must be supplied by the
        caller (ISO-8601 string) so this stays deterministic and testable.
        ``data_samples`` (optional) carries capped per-source row samples for the
        HTML renderer's interactive data table.
        ``test_code`` (optional) overrides reading from ``control.test_path``; pass
        this when the control has no on-disk test file (e.g. rule controls or
        store-backed controls with inline code).
        """
        if test_code is None:
            test_code = pathlib.Path(control.test_path).read_text(encoding="utf-8")

        # Serialise FrameworkRefs to a plain dict (mirrors ControlDef.to_dict()).
        framework_refs: dict[str, Any] = control.framework_refs.to_dict()

        procedure = Procedure(
            title=control.title,
            narrative=control.narrative,
            test_code=test_code,
            result=run,
            threshold=control.threshold,
        )

        return cls(
            control_id=control.id,
            title=control.title,
            objective=control.objective,
            narrative=control.narrative,
            framework_refs=framework_refs,
            procedures=[procedure],
            generated_at=generated_at,
            threshold=control.threshold,
            data_samples=list(data_samples or []),
        )

    @classmethod
    def assemble_procedures(
        cls,
        control: ControlDef,
        procedures: list[tuple[ProcedureSpec, RunRecord]],
        generated_at: str,
        data_samples: list[DataSample] | None = None,
    ) -> Workpaper:
        """Build a Workpaper from a ControlDef and multiple (ProcedureSpec, RunRecord) pairs.

        Each pair becomes one :class:`Procedure` with its own threshold and
        per-procedure determination.  :attr:`Workpaper.determination` rolls up
        across all procedures: the control passes iff every procedure passes.

        ``control`` supplies the top-level metadata (id, title, objective,
        narrative, framework_refs) and the *control-level* threshold (used as the
        roll-up fallback / headline display threshold).  Per-procedure thresholds
        are carried in each :class:`ProcedureSpec`.

        ``generated_at`` is an ISO-8601 string supplied by the caller so this
        stays deterministic and testable.
        """
        # Serialise FrameworkRefs to a plain dict (mirrors ControlDef.to_dict()).
        framework_refs: dict[str, Any] = control.framework_refs.to_dict()

        built: list[Procedure] = [
            Procedure(
                code=spec.code,
                title=spec.title,
                assertion=spec.assertion,
                narrative=spec.narrative,
                test_code=spec.test_code,
                result=run,
                threshold=spec.threshold,
            )
            for spec, run in procedures
        ]

        return cls(
            control_id=control.id,
            title=control.title,
            objective=control.objective,
            narrative=control.narrative,
            framework_refs=framework_refs,
            procedures=built,
            generated_at=generated_at,
            threshold=control.threshold,
            data_samples=list(data_samples or []),
        )

    # ── serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Return the structured, import-ready representation.

        ``data_samples`` are intentionally **excluded** — raw rows never cross
        the import bundle's trust boundary; they exist only for the HTML render.
        """
        return {
            "control_id": self.control_id,
            "title": self.title,
            "objective": self.objective,
            "narrative": self.narrative,
            "framework_refs": self.framework_refs,
            "procedures": [p.to_dict() for p in self.procedures],
            "generated_at": self.generated_at,
            "threshold": self.threshold.to_dict(),
        }
