"""Duplicate payment detection.

Flags the LATER payment in any pair where the same vendor received the same
dollar amount within a five-calendar-day window.  Each distinct pair produces
exactly one violation (the later payment).
"""

import pandas as pd


def test(pop):  # noqa: ANN001, ANN201
    df = pop.df.copy()

    # ── Normalise columns ────────────────────────────────────────────────────
    df["payment_id"] = df["payment_id"].astype(str).str.strip()
    df["vendor_id"] = df["vendor_id"].astype(str).str.strip()

    # Parse amounts — coerce unreadable values to NaN and drop them
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["amount"])

    # Parse dates — coerce unparseable to NaT and drop them
    df["paid_date"] = pd.to_datetime(df["paid_date"], errors="coerce")
    df = df.dropna(subset=["paid_date"])

    # Sort ascending so we can always compare earlier vs later within each pair
    df = df.sort_values("paid_date").reset_index(drop=True)

    # ── Find duplicate pairs ─────────────────────────────────────────────────
    # Track which payment_ids are already flagged so each pair only yields one
    # violation (the later payment).
    flagged: set[str] = set()
    violations = []

    rows = df.to_dict("records")

    for i, pmt_a in enumerate(rows):
        pid_a = pmt_a["payment_id"]
        if pid_a in flagged:
            continue

        for j in range(i + 1, len(rows)):
            pmt_b = rows[j]

            # Once the date gap exceeds 5 days there can be no further matches
            # for pmt_a (rows are sorted ascending).
            delta_days = (pmt_b["paid_date"] - pmt_a["paid_date"]).days
            if delta_days > 5:
                break

            pid_b = pmt_b["payment_id"]
            if pid_b in flagged:
                continue

            # Same vendor + same amount within the window → duplicate pair
            if pmt_b["vendor_id"] == pmt_a["vendor_id"] and pmt_b["amount"] == pmt_a["amount"]:
                # Flag the later payment (pmt_b, since rows are sorted ascending)
                flagged.add(pid_b)
                violations.append(
                    {
                        "item_key": pid_b,
                        "description": (
                            f"Payment '{pid_b}' ({pmt_b['paid_date'].date()}) appears to be a "
                            f"duplicate of '{pid_a}' ({pmt_a['paid_date'].date()}) — "
                            f"same vendor '{pmt_b['vendor_id']}' and amount "
                            f"{pmt_b['amount']:,.2f} within {delta_days} day(s)"
                        ),
                        "severity": "high",
                        "details": {
                            "vendor_id": pmt_b["vendor_id"],
                            "amount": pmt_b["amount"],
                            "paid_date": str(pmt_b["paid_date"].date()),
                            "earlier_payment_id": pid_a,
                            "days_apart": delta_days,
                        },
                    }
                )

    return violations
