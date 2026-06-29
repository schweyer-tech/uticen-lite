"""Unit tests for the run-history view-model helpers (issue #14).

These guard the 0004 ordering trap: ``repo.list_runs_for`` is newest-first, so the
trend must reverse to chronological (oldest->newest) while "latest" reads index 0.
"""
from uticen_lite.plane.routes.controls import _fmt_executed, _history_view


def _run(pass_rate, failed, total, executed_at):
    return {
        "pass_rate": pass_rate,
        "failed": failed,
        "total": total,
        "executed_at": executed_at,
        "run_id": f"r{pass_rate}{failed}",
    }


def test_history_view_orders_chronologically():
    # newest-first, mirroring list_runs_for output
    newest_first = [
        _run(90, 2, 20, "2026-03-31T00:00:00+00:00"),  # latest
        _run(50, 10, 20, "2026-01-01T00:00:00+00:00"),  # oldest
    ]
    view = _history_view(newest_first)
    # points are reversed to oldest->newest for left-to-right charting
    assert [p["pass_rate"] for p in view["points"]] == [50, 90]
    assert view["points"][0]["executed_at"] == "2026-01-01T00:00:00+00:00"
    assert view["points"][-1]["executed_at"] == "2026-03-31T00:00:00+00:00"
    # latest reads the NEWEST input (index 0), not the last point (0004 trap)
    assert view["latest_pass_rate"] == 90
    # max_failed across all runs
    assert view["max_failed"] == 10
    # each point carries a tooltip label
    assert all(p.get("label") for p in view["points"])


def test_history_view_empty():
    view = _history_view([])
    assert view["points"] == []
    assert view["max_failed"] == 0
    assert view["latest_pass_rate"] is None


def test_fmt_executed():
    assert _fmt_executed("2026-03-31T00:00:00+00:00") == "2026-03-31 00:00 UTC"
    assert _fmt_executed("") == "—"
    assert _fmt_executed("not-a-date") == "not-a-date"
