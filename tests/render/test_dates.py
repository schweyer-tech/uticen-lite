"""Tests for the shared display-date formatter (controlflow_sdk.render.dates)."""

from __future__ import annotations

from datetime import UTC, datetime

from controlflow_sdk.render.dates import format_display_date


def test_date_only_string_is_not_timezone_shifted() -> None:
    # A calendar date (no time component) must format as-is — converting it
    # through a timezone would shift it across midnight and show the wrong day.
    assert format_display_date("2026-03-31") == "03/31/2026"
    assert format_display_date("2026-01-01") == "01/01/2026"


def test_aware_datetime_is_converted_to_target_tz() -> None:
    # Midnight UTC on the 17th is the evening of the 16th in US Eastern.
    midnight_utc = datetime(2026, 6, 17, 0, 0, tzinfo=UTC)
    assert format_display_date(midnight_utc, tz="America/New_York") == "06/16/2026"
    # Same instant, formatted in UTC, stays the 17th.
    assert format_display_date(midnight_utc, tz="UTC") == "06/17/2026"


def test_iso_string_with_time_is_treated_as_a_moment() -> None:
    # A full timestamp (has a time component) IS tz-converted.
    assert format_display_date("2026-06-17T00:00:00Z", tz="America/New_York") == "06/16/2026"


def test_custom_format_is_respected() -> None:
    assert format_display_date("2026-03-31", date_format="%Y-%m-%d") == "2026-03-31"


def test_none_and_unparseable_values_degrade_gracefully() -> None:
    assert format_display_date(None) == ""
    assert format_display_date("not a date") == "not a date"


def test_unknown_timezone_falls_back_without_crashing() -> None:
    midnight_utc = datetime(2026, 6, 17, 0, 0, tzinfo=UTC)
    # An unknown zone name must not raise — it falls back to UTC.
    assert format_display_date(midnight_utc, tz="Not/AZone") == "06/17/2026"
