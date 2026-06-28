"""Manual journal entry review — two procedures over material manual entries.

Documentation sidecar for Finance.GL.1 (the executed artifact is the visual
pipeline in ``pipeline.yaml``, which forks into two procedure terminals):

  P1 · Independent Review (Segregation of Duties) — a material manual entry
       reviewed by its own preparer (reviewed_by present and == prepared_by).
  P2 · Reviewer Assigned (Authorization)         — a material manual entry with
       no independent reviewer (reviewed_by empty).

"Material" = a manual entry with abs(amount) >= 50000.
"""

import pandas as pd


def test(pop):  # noqa: ANN001, ANN201
    df = pop.df
    violations = []

    for _, row in df.iterrows():
        if str(row.get("entry_type", "")).strip().lower() != "manual":
            continue
        try:
            amount = float(row["amount"])
        except (ValueError, TypeError):
            continue
        if abs(amount) < 50000:  # materiality on absolute value (large credits count too)
            continue

        prepared_by = str(row.get("prepared_by", "") or "").strip()
        reviewed_by = str(row.get("reviewed_by", "") or "").strip()
        reviewer_missing = pd.isna(row.get("reviewed_by")) or reviewed_by == ""
        self_reviewed = not reviewer_missing and reviewed_by == prepared_by

        if self_reviewed:  # P1 · Segregation of duties
            violations.append({
                "item_key": str(row["entry_id"]),
                "description": "Entry reviewed by preparer (self-authorization)",
                "severity": "high",
                "details": {"prepared_by": prepared_by, "reviewed_by": reviewed_by},
            })
        elif reviewer_missing:  # P2 · Reviewer assigned
            violations.append({
                "item_key": str(row["entry_id"]),
                "description": "No independent reviewer assigned",
                "severity": "high",
                "details": {"prepared_by": prepared_by},
            })

    return violations
