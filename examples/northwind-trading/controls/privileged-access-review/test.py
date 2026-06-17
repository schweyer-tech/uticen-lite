"""Privileged access periodic review.

Flags privileged accounts that either:
  (a) have no recorded approver (approved_by is blank/NaN), or
  (b) have a last_review_date before the 90-day cutoff.

The 90-day cutoff is computed from the fixed demo as-of date 2026-03-31:
  cutoff = 2026-03-31 - 90 days = 2025-12-31.
A future enhancement could derive the cutoff from the run's executed_at
timestamp instead of the hard-coded demo date.
"""

import pandas as pd

# Fixed demo as-of date; cutoff = as-of minus 90 days = 2025-12-31.
# ISO 8601 strings sort lexically, so string comparison works for YYYY-MM-DD.
_REVIEW_CUTOFF = "2025-12-31"


def test(pop):  # noqa: ANN001, ANN201
    df = pop.df
    violations = []

    for _, row in df.iterrows():
        # Only evaluate privileged accounts
        is_privileged_raw = str(row.get("is_privileged", "") or "").strip().lower()
        if is_privileged_raw != "true":
            continue

        account_id = str(row["account_id"]).strip()

        # ── Check 1: approved_by must be present ─────────────────────────────
        approved_by = str(row.get("approved_by", "") or "").strip()
        approver_missing = pd.isna(row.get("approved_by")) or approved_by == ""

        # ── Check 2: last_review_date must be >= cutoff ──────────────────────
        last_review_raw = str(row.get("last_review_date", "") or "").strip()
        review_stale = (not last_review_raw) or (last_review_raw < _REVIEW_CUTOFF)

        if approver_missing or review_stale:
            reasons = []
            if approver_missing:
                reasons.append("no approver recorded")
            if review_stale:
                reasons.append(
                    f"last review date '{last_review_raw}' is before cutoff '{_REVIEW_CUTOFF}'"
                )

            violations.append(
                {
                    "item_key": account_id,
                    "description": (
                        f"Privileged account '{account_id}' failed review: " + "; ".join(reasons)
                    ),
                    "severity": "high",
                    "details": {
                        "approved_by": approved_by if not approver_missing else None,
                        "last_review_date": last_review_raw or None,
                        "cutoff": _REVIEW_CUTOFF,
                    },
                }
            )

    return violations
