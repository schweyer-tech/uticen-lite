"""Workpaper assembly from a ControlDef + RunRecord."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from controlflow_sdk.model.control import ControlDef
    from controlflow_sdk.model.run import RunRecord


@dataclass
class Procedure:
    """A single test procedure within a workpaper.

    In v1 there is exactly one procedure per control — one automated test mapped
    to one run result.
    """

    title: str
    narrative: str
    test_code: str
    result: RunRecord

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "narrative": self.narrative,
            "test_code": self.test_code,
            "result": self.result.to_dict(),
        }


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

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def assemble(
        cls,
        control: ControlDef,
        run: RunRecord,
        generated_at: str,
    ) -> Workpaper:
        """Build a Workpaper from a ControlDef and a RunRecord.

        Reads the test source from ``control.test_path`` and wraps the run in a
        single :class:`Procedure`.  ``generated_at`` must be supplied by the
        caller (ISO-8601 string) so this stays deterministic and testable.
        """
        test_code = pathlib.Path(control.test_path).read_text(encoding="utf-8")

        # Serialise FrameworkRefs to a plain dict (mirrors ControlDef.to_dict()).
        framework_refs: dict[str, Any] = {
            "nist": list(control.framework_refs.nist),
            "extra": {k: list(v) for k, v in control.framework_refs.extra.items()},
        }

        procedure = Procedure(
            title=control.title,
            narrative=control.narrative,
            test_code=test_code,
            result=run,
        )

        return cls(
            control_id=control.id,
            title=control.title,
            objective=control.objective,
            narrative=control.narrative,
            framework_refs=framework_refs,
            procedures=[procedure],
            generated_at=generated_at,
        )

    # ── serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Return the structured, import-ready representation."""
        return {
            "control_id": self.control_id,
            "title": self.title,
            "objective": self.objective,
            "narrative": self.narrative,
            "framework_refs": self.framework_refs,
            "procedures": [p.to_dict() for p in self.procedures],
            "generated_at": self.generated_at,
        }
