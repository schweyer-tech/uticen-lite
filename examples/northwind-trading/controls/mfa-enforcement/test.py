"""MFA enforcement.

Flags active accounts that do not have multi-factor authentication enabled.
With the current Northwind demo dataset this control returns zero exceptions —
all active accounts have mfa_enabled=true. Inactive accounts are excluded
because they cannot be used for login and are covered by the terminated-access
and account-lifecycle controls.
"""


def test(pop):  # noqa: ANN001, ANN201
    df = pop.df
    violations = []

    for _, row in df.iterrows():
        # Only evaluate accounts that are currently active
        is_active_raw = str(row.get("is_active", "") or "").strip().lower()
        if is_active_raw != "true":
            continue

        # Flag if MFA is not enabled
        mfa_enabled_raw = str(row.get("mfa_enabled", "") or "").strip().lower()
        if mfa_enabled_raw != "true":
            account_id = str(row["account_id"]).strip()
            system = str(row.get("system", "") or "").strip()
            role = str(row.get("role", "") or "").strip()

            violations.append(
                {
                    "item_key": account_id,
                    "description": (f"Active account '{account_id}' does not have MFA enabled"),
                    "severity": "high",
                    "details": {
                        "system": system,
                        "role": role,
                    },
                }
            )

    return violations
