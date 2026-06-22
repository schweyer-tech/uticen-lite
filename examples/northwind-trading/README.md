# Northwind Trading Co.

A complete, ready-to-run audit control example demonstrating the ControlFlow SDK on realistic financial, IT access, and procurement data.

## The Company

**Northwind Trading Co.** is a fictional ~600-person wholesale distributor of specialty foods and imported goods. This demo represents their annual audit scope:

- **Financial close:** Journal entries, period-end accounting, accounts payable
- **IT access:** System user accounts, privilege management, multi-factor authentication
- **Procurement:** Purchase orders, vendor master, payment authorization and reconciliation

All data is **frozen as of 2026-03-31** — the control execution date. This ensures reproducible results across runs (controls are deterministic as of this date).

## The Controls

Eight production-ready audit controls span financial, IT access, and procurement domains:

| Control ID | Domain | Data Sources | NIST Ref | What It Flags | Exceptions |
|---|---|---|---|---|---|
| **manual-je-review** | Financial | journal_entries | AC-5 | Manual journal entries ≥$50k that are self-reviewed or lack a reviewer | 3 |
| **closed-period-postings** | Financial | journal_entries, closed_periods | — | Journal entries posted to accounting periods marked as closed | 2 |
| **three-way-match** | Financial/Procurement | payments, invoices, purchase_orders | — | Payments without a matching approved PO (±1% tolerance) | 4 |
| **terminated-access** | IT Access | access_accounts, employees | AC-2 | Active system accounts owned by terminated employees | 3 |
| **privileged-access-review** | IT Access | access_accounts | AC-6 | Privileged accounts lacking approval or stale reviews (>90 days) | 2 |
| **mfa-enforcement** | IT Access | access_accounts | IA-2 | Active accounts without multi-factor authentication enabled | 0 ✓ |
| **duplicate-payments** | Procurement | payments | — | Payments to the same vendor for the same amount within 5 days | 2 |
| **vendor-master-sod** | Procurement | payments, vendor_master | AC-5 | Payments approved by the vendor master creator or last modifier | 2 |

**Note:** `mfa-enforcement` is a passing control — all active accounts have MFA enabled. Its workpaper is included to demonstrate a clean audit result.

## Run It Locally

Execute the full population control test suite and generate HTML workpapers:

```bash
cflow run . --at 2026-03-31T00:00:00Z
```

Results appear in `target/workpapers/`:

```bash
# Open in browser
open target/workpapers/manual-je-review.html
open target/workpapers/mfa-enforcement.html  # Clean control
```

**Full-population audit:** Every row in every source is tested against every control. No sampling.

**Provenance:** Each workpaper records:
- SHA256 hash of the source data
- Row counts per source
- Execution timestamp
- Framework references (NIST 800-53, etc.)

## Import Into ControlFlow

Build an importable bundle and upload it to the ControlFlow SaaS application:

```bash
cflow build . --out import-bundle.zip --at 2026-03-31T00:00:00Z
```

Then in the app:

1. **Sign in** as an admin user
2. Navigate to **Settings → Imports** (admin only)
3. Click **Upload Bundle**
4. Select `import-bundle.zip`

The import lands:
- **8 controls** with full metadata (title, objective, narrative, NIST framework refs)
- **8 workpapers** — one per control, including the passing `mfa-enforcement`
- **18 exceptions** — flagged violations across all controls (the 3 controls with 0 exceptions show passing results)
- **Full provenance** — SHA256 hashes and row counts embedded in each workpaper

## Use as a Template

Copy this directory and swap in your own data:

```bash
cp -r examples/northwind-trading my-audit
cd my-audit

# Replace CSV files in data/
# Describe each source in sources.yaml
# Edit control.yaml files in controls/*/  (objective, narrative, framework refs)
# Author each control's logic — see "Authoring control logic" below
# Update this README
```

### Authoring control logic

Reach for the **no-code builder first** and drop to Python only when the logic genuinely
needs it. Each control directory authors its test logic with exactly one of these
sidecars (precedence: `rule.yaml` → `pipeline.yaml` → `test.py`):

1. **No-code rule** — `rule.yaml`. A declarative rule (AND/OR over typed conditions,
   severity, description template). Best for single-source checks. Match each condition
   value to the source's loaded `data_type` (a `boolean` column compares to `true`/`false`,
   a `date` column to an ISO date). See [`mfa-enforcement/rule.yaml`](controls/mfa-enforcement/rule.yaml).

2. **Visual pipeline** — `pipeline.yaml`. A small graph of `Import → Filter → Join → Test`
   nodes for cross-source joins and AND-of-OR shapes a flat rule can't express. Still no
   Python. See [`terminated-access/pipeline.yaml`](controls/terminated-access/pipeline.yaml)
   (cross-source join) and [`privileged-access-review/pipeline.yaml`](controls/privileged-access-review/pipeline.yaml)
   (filter + any-of test).

3. **Python escape hatch** — `test.py`, only for logic outside the no-code grammar
   (row-pairwise windows, cross-column arithmetic, column-to-column comparisons across a
   join). In this demo only `duplicate-payments`, `three-way-match`, and `vendor-master-sod`
   need it.

A `test.py` function takes `pop` (single-source) or `pop, sources` (multi-source) and
**returns a list** of violation dicts (`item_key`, `description`, `severity`, `details`).
See [`three-way-match/test.py`](controls/three-way-match/test.py) for a three-source example:

```python
def test(pop, sources):
    payments_df = pop.df
    invoices_df = sources["invoices"].df
    po_df = sources["purchase_orders"].df

    violations = []
    for _, pmt in payments_df.iterrows():
        # Find matching invoice, then PO, validate the amount is within 1%
        ...
        violations.append({
            "item_key": str(pmt["payment_id"]),
            "description": "Payment without a valid approved PO",
            "severity": "high",
            "details": {...},
        })
    return violations
```

**For authoring controls**, refer to the [ControlFlow SDK README](../../README.md) for:
- Control YAML structure (objective, narrative, framework_refs)
- The no-code rule grammar and the `test()` signature / exception format
- Execution environment (pandas, numpy, standard library)
- Workpaper and HTML generation

---

**Questions?** See `docs/` in the ControlFlow SDK or reach out to the audit team.
