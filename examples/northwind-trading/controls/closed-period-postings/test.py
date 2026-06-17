"""Closed accounting period posting detection.

Joins journal entries to the closed-period master and flags any entry
whose period is marked as 'closed'.
"""


def test(pop, sources):  # noqa: ANN001, ANN201
    je_df = pop.df
    periods_df = sources["closed_periods"].df

    # Build a set of closed period identifiers for fast lookup
    closed = set(
        periods_df.loc[periods_df["status"].str.strip().str.lower() == "closed", "period"]
        .str.strip()
        .tolist()
    )

    violations = []
    for _, row in je_df.iterrows():
        period = str(row.get("period", "") or "").strip()
        if period in closed:
            violations.append(
                {
                    "item_key": str(row["entry_id"]),
                    "description": (f"Journal entry posted to closed period '{period}'"),
                    "severity": "high",
                    "details": {
                        "period": period,
                        "posting_date": str(row.get("posting_date", "")),
                    },
                }
            )

    return violations
