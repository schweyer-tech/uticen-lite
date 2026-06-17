"""Control metadata models parsed from control.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FrameworkRefs:
    """Framework reference tags for a control (NIST, ISO, etc.)."""

    nist: list[str] = field(default_factory=list)
    extra: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Threshold:
    """Pass/fail tolerance for a control (mirrors the app's ``control-thresholds``).

    A control **passes** when the exception rate is ``<= failure_threshold_pct``
    **AND** the exception count is ``<= failure_threshold_count`` (each ignored
    when ``None``).  When **both** are ``None`` the implicit threshold is ``0``
    (any exception is a deficiency) — preserving the SDK's original behaviour.
    """

    failure_threshold_pct: float | None = None
    failure_threshold_count: int | None = None

    @property
    def is_implicit_zero(self) -> bool:
        """True when neither bound is set (implicit zero-tolerance)."""
        return self.failure_threshold_pct is None and self.failure_threshold_count is None

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> Threshold:
        """Parse a ``threshold:`` block; tolerant of missing keys.

        ``failure_threshold_pct`` is coerced to ``float`` and clamped to ``[0, 100]``
        is NOT performed here (validation lives in the schema) — but out-of-range or
        non-numeric values raise ``ValueError`` so authoring mistakes surface early.
        """
        if not raw:
            return cls()

        pct_raw = raw.get("failure_threshold_pct")
        count_raw = raw.get("failure_threshold_count")

        pct: float | None = None
        if pct_raw is not None:
            pct = float(pct_raw)
            if pct < 0 or pct > 100:
                raise ValueError("failure_threshold_pct must be in [0, 100]")

        count: int | None = None
        if count_raw is not None:
            count = int(count_raw)
            if count < 0:
                raise ValueError("failure_threshold_count must be >= 0")

        return cls(failure_threshold_pct=pct, failure_threshold_count=count)

    def passes(self, exception_count: int, records_tested: int) -> bool:
        """Return True when *exception_count* satisfies this threshold.

        Implicit-zero (both bounds ``None``) → passes only with zero exceptions.
        """
        if self.is_implicit_zero:
            return exception_count == 0

        if (
            self.failure_threshold_count is not None
            and exception_count > self.failure_threshold_count
        ):
            return False

        if self.failure_threshold_pct is not None:
            rate = (exception_count / records_tested * 100) if records_tested else 0.0
            if rate > self.failure_threshold_pct:
                return False

        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_threshold_pct": self.failure_threshold_pct,
            "failure_threshold_count": self.failure_threshold_count,
        }


@dataclass
class RiskRef:
    """A risk linked to a control."""

    name: str
    description: str = ""
    inherent_rating: str | None = None


@dataclass
class SourceBinding:
    """A data source bound to a control, as declared in control.yaml.

    ``to_data_source()`` maps to the app's ``data_sources`` shape (the subset
    the app cares about): ``{type, key_config, column_mappings}``.

    ``description`` and ``completeness_accuracy`` are optional, author-supplied
    audit prose — a one-line description of the extract and a Completeness &
    Accuracy assertion. They surface in the rendered workpaper's Data Sources
    section; when absent the renderer derives a default C&A line from the tie-out.

    ``extract_date`` is the optional author-supplied as-of date of the extract
    (the date the data is current as of). It surfaces as the Extract Date in the
    rendered workpaper's Data Sources section; when absent the renderer falls
    back to the run's execution/as-of date.
    """

    id: str
    type: str
    config: dict[str, Any]
    key_config: dict[str, Any]
    column_mappings: list[dict[str, Any]]
    description: str | None = None
    completeness_accuracy: str | None = None
    extract_date: str | None = None

    def to_data_source(self) -> dict[str, Any]:
        """Return the app ``data_sources`` shape (type + key_config + column_mappings only)."""
        return {
            "type": self.type,
            "key_config": dict(self.key_config),
            "column_mappings": [dict(cm) for cm in self.column_mappings],
        }


@dataclass
class ControlDef:
    """A parsed control definition from control.yaml.

    Plain data holder — no validation logic lives here (that belongs to the
    YAML parser in task 7).  Only behaviour: ``to_dict()`` for serialisation.
    """

    id: str
    title: str
    objective: str
    narrative: str
    framework_refs: FrameworkRefs
    risk: RiskRef | None
    sources: list[SourceBinding]
    test_path: str
    severity_policy: dict[str, Any] = field(default_factory=dict)
    threshold: Threshold = field(default_factory=Threshold)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON / API payloads."""
        return {
            "id": self.id,
            "title": self.title,
            "objective": self.objective,
            "narrative": self.narrative,
            "framework_refs": {
                "nist": list(self.framework_refs.nist),
                "extra": {k: list(v) for k, v in self.framework_refs.extra.items()},
            },
            "risk": (
                {
                    "name": self.risk.name,
                    "description": self.risk.description,
                    "inherent_rating": self.risk.inherent_rating,
                }
                if self.risk is not None
                else None
            ),
            "sources": [s.to_data_source() for s in self.sources],
            "severity_policy": dict(self.severity_policy),
            "threshold": self.threshold.to_dict(),
            "test_path": self.test_path,
        }
