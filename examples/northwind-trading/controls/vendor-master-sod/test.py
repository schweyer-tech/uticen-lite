"""Vendor master segregation-of-duties check.

Flags payments whose approved_by employee also created or last-modified the
vendor record in the vendor master, indicating a segregation-of-duties
violation in the procure-to-pay cycle.
"""


def test(pop, sources):  # noqa: ANN001, ANN201
    payments_df = pop.df
    vendor_df = sources["vendor_master"].df

    # Index vendor master by vendor_id for O(1) lookup; drop duplicates
    # defensively so each .loc returns a single row.
    vendors = vendor_df.drop_duplicates(subset="vendor_id", keep="first").set_index("vendor_id")

    violations = []

    for _, pmt in payments_df.iterrows():
        payment_id = str(pmt["payment_id"]).strip()
        vendor_id = str(pmt.get("vendor_id", "") or "").strip()
        approved_by = str(pmt.get("approved_by", "") or "").strip()

        # Skip payments with no vendor reference or no approver
        if not vendor_id or not approved_by:
            continue

        if vendor_id not in vendors.index:
            # Vendor not in master — not a SoD violation (missing vendor is a
            # three-way-match concern, not a SoD concern)
            continue

        vendor_row = vendors.loc[vendor_id]
        created_by = str(vendor_row.get("created_by", "") or "").strip()
        last_modified_by = str(vendor_row.get("last_modified_by", "") or "").strip()

        sod_match = None
        if approved_by == created_by and approved_by:
            sod_match = "created_by"
        elif approved_by == last_modified_by and approved_by:
            sod_match = "last_modified_by"

        if sod_match:
            vendor_name = str(vendor_row.get("vendor_name", "") or "").strip()
            violations.append(
                {
                    "item_key": payment_id,
                    "description": (
                        f"Payment '{payment_id}' to vendor '{vendor_id}' "
                        f"({vendor_name}) was approved by '{approved_by}' who is "
                        f"also the {sod_match.replace('_', ' ')} of the vendor "
                        "master record — segregation of duties violation"
                    ),
                    "severity": "high",
                    "details": {
                        "vendor_id": vendor_id,
                        "vendor_name": vendor_name,
                        "approved_by": approved_by,
                        "sod_field": sod_match,
                    },
                }
            )

    return violations
