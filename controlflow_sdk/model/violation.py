from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


def _json_safe(value: Any) -> Any:
    """Coerce a violation-``details`` value to a JSON-native type.

    Details come straight off a DataFrame row (``row.to_dict()``), so a no-code
    rule that references a ``date``/``number``/``boolean`` column carries pandas
    and numpy scalars — ``Timestamp``, ``NaT``, ``np.bool_``, ``np.int64`` — none
    of which ``json.dumps`` can serialize. Coerce them here (the single funnel is
    :meth:`Violation.from_raw`) so the run persists and renders regardless of the
    authoring surface. Uses duck typing only, so the model layer keeps its
    pandas/numpy-free import surface.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # before int (bool ⊂ int); already JSON-native
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):  # also catches np.float64 (a float subclass)
        return None if value != value else value  # NaN → None
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    # NaT and other "not-equal-to-itself" sentinels → None (before isoformat,
    # because NaT.isoformat() would yield the string "NaT").
    try:
        if value != value:
            return None
    except (TypeError, ValueError):  # pragma: no cover - non-comparable scalar
        pass
    # datetime / date / pandas.Timestamp
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    # numpy / pandas scalar wrappers expose .item() → a native Python scalar
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):  # pragma: no cover - defensive
            pass
    return str(value)


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def coerce(cls, value: str | Severity | None) -> Severity:
        if isinstance(value, Severity):
            return value
        if value is None:
            return cls.MEDIUM
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.MEDIUM


@dataclass(frozen=True)
class Violation:
    item_key: str
    description: str
    severity: Severity = Severity.MEDIUM
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_key": self.item_key,
            "description": self.description,
            "severity": self.severity.value,
            "details": dict(self.details),
        }

    @classmethod
    def from_raw(cls, obj: Mapping[str, Any]) -> Violation:
        for required in ("item_key", "description"):
            if not obj.get(required):
                raise ValueError(f"Violation missing required field: {required}")
        return cls(
            item_key=str(obj["item_key"]),
            description=str(obj["description"]),
            severity=Severity.coerce(obj.get("severity")),
            details={str(k): _json_safe(v) for k, v in (obj.get("details") or {}).items()},
        )
