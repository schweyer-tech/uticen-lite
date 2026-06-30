"""Per-column coercion-health check for the source Data tab.

Uses the SAME coercion the runner uses (``adapters.files.coerce_series``) so the
preview's verdict matches run-time reality: a column declared as a non-text type
whose every non-empty value fails to coerce is the silent-wrong-result smell the
no-code analyst would otherwise hit only at run time.
"""

from __future__ import annotations

import pandas as pd

from uticen_lite.adapters.files import coerce_series


def coercion_report(
    header: list[str], data_rows: list[list[str]], columns: list[dict]
) -> list[dict]:
    """For each declared column, coerce its parsed string series to its declared
    ``data_type`` and flag total coercion failure for non-text types.

    Returns one row per declared column::

        {original_name, display_name, data_type, total, bad, all_bad}

    where ``total`` is the count of non-empty source values, ``bad`` is the count
    of those that became NaN/NaT after coercion, and ``all_bad`` is ``True`` when
    every non-empty value failed (the flag). ``text`` and ``boolean`` never coerce
    to empty, so they are reported with ``bad=0``/``all_bad=False`` (informational
    only — no false alarms). An all-empty column is excluded from the denominator,
    so a legitimately empty column never raises a false alarm.
    """
    index = {h: i for i, h in enumerate(header)}
    report: list[dict] = []
    for col in columns:
        name = col["original_name"]
        data_type = col.get("data_type", "text")
        col_idx = index.get(name)
        raw_values = [
            (row[col_idx] if col_idx is not None and col_idx < len(row) else "")
            for row in data_rows
        ]
        series = pd.Series(raw_values, dtype=str)
        non_empty_mask = series.str.strip() != ""
        non_empty_count = int(non_empty_mask.sum())

        if data_type in ("number", "date"):
            coerced = coerce_series(series, data_type)
            bad = int((coerced.isna() & non_empty_mask).sum())
        else:
            # text / boolean never coerce to empty — never a coercion failure.
            bad = 0

        all_bad = non_empty_count > 0 and bad == non_empty_count
        report.append(
            {
                "original_name": name,
                "display_name": col.get("display_name", name),
                "data_type": data_type,
                "total": non_empty_count,
                "bad": bad,
                "all_bad": all_bad,
            }
        )
    return report
