"""Terminated employee access revocation.

Flags active system accounts whose linked employee has a 'terminated' status
in the HR employee roster. Any is_active account belonging to a terminated
employee indicates access was not revoked after separation.
"""


def test(pop, sources):  # noqa: ANN001, ANN201
    accounts_df = pop.df
    employees_df = sources["employees"].df

    # Index employees by employee_id for O(1) lookup; drop duplicates to ensure
    # a single row is returned per id (defensive guard against data quality issues).
    emp_index = employees_df.drop_duplicates(subset="employee_id", keep="first").set_index(
        "employee_id"
    )

    violations = []

    for _, row in accounts_df.iterrows():
        # Only flag accounts that are currently active
        is_active_raw = str(row.get("is_active", "") or "").strip().lower()
        if is_active_raw != "true":
            continue

        account_id = str(row["account_id"]).strip()
        employee_id = str(row.get("employee_id", "") or "").strip()

        if not employee_id or employee_id not in emp_index.index:
            # Cannot determine employee status — skip (no employee record linked)
            continue

        emp_row = emp_index.loc[employee_id]
        emp_status = str(emp_row.get("status", "") or "").strip().lower()

        if emp_status == "terminated":
            system = str(row.get("system", "") or "").strip()
            violations.append(
                {
                    "item_key": account_id,
                    "description": (
                        f"Account '{account_id}' is active but linked employee "
                        f"'{employee_id}' has terminated status"
                    ),
                    "severity": "critical",
                    "details": {
                        "employee_id": employee_id,
                        "system": system,
                    },
                }
            )

    return violations
