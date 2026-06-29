from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from uticen_lite.model.violation import Violation


@dataclass(frozen=True)
class SourceProvenance:
    """Immutable record of a data source snapshot used in a run."""

    source_id: str
    path: str
    sha256: str
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "path": self.path,
            "sha256": self.sha256,
            "row_count": self.row_count,
        }


def _derive_run_id(control_id: str, executed_at: str, provenance: list[SourceProvenance]) -> str:
    """Deterministic 16-char hex id: sha256(control_id + executed_at + prov_hashes)[:16]."""
    prov_hashes = "".join(p.sha256 for p in provenance)
    raw = control_id + executed_at + prov_hashes
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class RunRecord:
    """
    Result of executing a control test against a population.

    ``executed_at`` must be supplied by the caller (ISO-8601 string) so that
    this pure model never calls ``datetime.now()`` internally — keeping runs
    fully reproducible and testable.

    ``run_id`` is derived deterministically from ``control_id``, ``executed_at``,
    and the concatenated provenance sha256 hashes, so the same inputs always
    yield the same identifier.
    """

    control_id: str
    executed_at: str
    population_size: int
    violations: list[Violation] = field(default_factory=list)
    provenance: list[SourceProvenance] = field(default_factory=list)
    # Store-only: which terminal procedure produced this run. Default '' = sole/legacy procedure.
    # NOT included in to_dict() — the bundle contract ($defs/run) does not carry this field.
    procedure_id: str = ""

    # Derived on first access; stored to avoid recomputing on repeated calls.
    run_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "run_id",
            _derive_run_id(self.control_id, self.executed_at, self.provenance),
        )

    # ── computed properties ───────────────────────────────────────────────────

    @property
    def failed(self) -> int:
        return len(self.violations)

    @property
    def passed(self) -> int:
        return max(self.population_size - self.failed, 0)

    @property
    def pass_rate(self) -> float:
        if self.population_size == 0:
            return 0.0
        return round(self.passed / self.population_size * 100, 2)

    @property
    def summary(self) -> str:
        return f"{self.failed} violation(s) across {self.population_size} record(s)"

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Return a dict matching the app's ``test_results`` columns."""
        return {
            # test_results columns
            "passed": self.passed,
            "failed": self.failed,
            "total": self.population_size,
            "pass_rate": self.pass_rate,
            "summary": self.summary,
            "details": {
                "violations": [v.to_dict() for v in self.violations],
            },
            # identity / traceability
            "control_id": self.control_id,
            "run_id": self.run_id,
            "executed_at": self.executed_at,
            "provenance": [p.to_dict() for p in self.provenance],
        }
