"""Tiny dependency-free version comparison.

Avoids adding ``packaging`` as a dependency (the core stays dep-free — see
docs/learnings/0003). Compares dotted numeric releases like ``1.2.3``; any
non-numeric tail on a segment is ignored so a malformed string never raises.
"""

from __future__ import annotations


def _parts(value: str) -> tuple[int, ...]:
    out: list[int] = []
    for segment in value.strip().lstrip("vV").split("."):
        digits = ""
        for char in segment:
            if char.isdigit():
                digits += char
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(candidate: str, current: str) -> bool:
    """Return True if *candidate* is a strictly newer release than *current*."""
    a, b = _parts(candidate), _parts(current)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return a > b
