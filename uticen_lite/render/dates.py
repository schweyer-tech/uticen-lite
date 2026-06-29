"""Shared date-formatting helper for the workpaper renderers.

Formats a date/datetime (or an ISO-8601 string) for display — by default
``mm/dd/yyyy`` in US Eastern time.  Uses the stdlib :mod:`zoneinfo`; if the
tz database is unavailable (e.g. a stripped-down Pyodide build) it degrades
gracefully to the supplied value's own offset rather than crashing.

Pure stdlib; Pyodide-safe (no third-party tz packages).
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone

__all__ = ["format_display_date"]


def _resolve_tz(tz: str) -> timezone | object:
    """Return a tzinfo for *tz*, or UTC if the tz database is unavailable.

    Defensive: a missing/broken ``zoneinfo`` (or an unknown zone name) must
    never crash the renderer — we fall back to UTC so a date still renders.
    """
    try:
        from zoneinfo import ZoneInfo  # local import keeps the failure contained

        return ZoneInfo(tz)
    except Exception:  # noqa: BLE001 — any tzdb/import failure → safe UTC fallback
        return UTC


def format_display_date(
    value: datetime | str | None,
    *,
    date_format: str = "%m/%d/%Y",
    tz: str = "America/New_York",
) -> str:
    """Format *value* for display using *date_format* in timezone *tz*.

    ``value`` may be a :class:`~datetime.datetime`, an ISO-8601 string (e.g.
    ``"2026-03-31T00:00:00Z"``), or ``None``.  A naive datetime/string is
    assumed to be UTC, then converted to *tz* before formatting.  Anything that
    cannot be parsed as a datetime is returned unchanged (escaped by the caller),
    so the renderer never crashes on an odd value.
    """
    if value is None:
        return ""

    dt: datetime | None
    date_only = False
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        dt = _parse_iso(text)
        if dt is None:
            # Not a parseable datetime — return the original string as-is.
            return str(value)
        # A date-only value (e.g. "2026-03-31") is a calendar date, not a moment
        # in time. Format it directly — converting it through a timezone would
        # shift it across midnight and show the wrong day.
        date_only = "T" not in text and ":" not in text

    if date_only:
        return dt.strftime(date_format)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    target = _resolve_tz(tz)
    try:
        localised = dt.astimezone(target)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 — never let conversion crash the render
        localised = dt
    return localised.strftime(date_format)


def _parse_iso(text: str) -> datetime | None:
    """Parse an ISO-8601 string into a datetime, or return ``None``.

    Accepts a trailing ``Z`` (UTC) which :meth:`datetime.fromisoformat` only
    handles natively on newer Pythons — normalise it to ``+00:00`` first.
    """
    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None
