from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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
            details=dict(obj.get("details") or {}),
        )
