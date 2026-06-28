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
| **datacenter-temperature** | Facilities | datacenter_weather | PE-14 | Data-center sites running above the 27°C safe-operating ceiling | 2 |

**Note:** `mfa-enforcement` is a passing control — all active accounts have MFA enabled. Its workpaper is included to demonstrate a clean audit result.

**Public-API source:** `datacenter-temperature` is built on `datacenter_weather`, a source **snapshotted once from the public [Open-Meteo](https://open-meteo.com) API** (no API key) and frozen to `data/datacenter_weather.csv`. It demonstrates Uticen Lite's "fetch from URL" on-ramp — the local snapshot is the source of truth (one-time snapshot-to-file), so the test stays fully offline and deterministic. To reproduce the fetch in the app: **Sources → Add source → Fetch from URL** with an Open-Meteo `current=temperature_2m,wind_speed_10m` request.

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
- **9 controls** with full metadata (title, objective, narrative, NIST framework refs)
- **9 workpapers** — one per control, including the passing `mfa-enforcement`
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

**Author everything in the no-code builder.** Every control in this demo is authored with a
sidecar next to `control.yaml` (precedence: `rule.yaml` → `pipeline.yaml`), and the Python
escape hatch appears only as a *single node* inside a pipeline — never as a whole script:

1. **No-code rule** — `rule.yaml`. A declarative rule (AND/OR over typed conditions,
   severity, description template). Best for single-source checks. Match each condition
   value to the source's loaded `data_type` (a `boolean` column compares to `true`/`false`,
   a `date` column to an ISO date). See [`mfa-enforcement/rule.yaml`](controls/mfa-enforcement/rule.yaml).

2. **Visual pipeline** — `pipeline.yaml`. A small graph of `Import → Filter → Join → Test`
   nodes for cross-source joins and AND-of-OR shapes a flat rule can't express. Still no
   Python. See [`terminated-access/pipeline.yaml`](controls/terminated-access/pipeline.yaml)
   (cross-source join) and [`privileged-access-review/pipeline.yaml`](controls/privileged-access-review/pipeline.yaml)
   (filter + any-of test).

3. **Custom Python node** — a single `custom_python` node *inside* a pipeline, for the one
   irreducible step the no-code grammar can't express (row-pairwise windows, cross-column
   arithmetic, column-to-column comparisons). You still **import the data with Import nodes
   and combine it with Join nodes**; only the hard transform is Python. The node is *starved*
   — it receives just the incoming `rows` frame and can't reach other sources (pull those in
   with Import/Join, never `sources[...]`), can't read files, and may import only
   `re`/`datetime`/`decimal`. See [`three-way-match/pipeline.yaml`](controls/three-way-match/pipeline.yaml)
   (two Joins feed a node that does the 1% variance check), plus `vendor-master-sod` and
   `duplicate-payments`.

A `custom_python` `test`-flavor node receives `rows` and **returns a list** of violation
dicts (`item_key`, `description`, `severity`, `details`):

```yaml
- id: flag
  type: custom_python
  inputs: [pmt_inv_po]   # an upstream Join already brought the PO amount onto each row
  config:
    flavor: test
    code: |
      out = []
      for _, row in rows.iterrows():
          r = row.to_dict()
          # ... the one step the grammar can't express (here: a 1% variance check) ...
          out.append({
              "item_key": str(r.get("payment_id")),
              "description": "Payment amount deviates from the approved PO by >1%",
              "severity": "high",
              "details": {"reason": "amount_variance"},
          })
      return out
```

> A standalone hand-written `test.py` (a full `test(pop, sources)` control) is still
> supported by the engine for controls authored outside the builder, but the demo
> deliberately doesn't use one — prefer Import/Join nodes + a Custom Python node so the
> logic stays inspectable in the builder. The `test.py` files kept in each control directory
> are documentation of the equivalent logic, not the executed artifact.

**For authoring controls**, refer to the [ControlFlow SDK README](../../README.md) for:
- Control YAML structure (objective, narrative, framework_refs)
- The no-code rule grammar and the `test()` signature / exception format
- Execution environment (pandas, numpy, standard library)
- Workpaper and HTML generation

---

**Questions?** See `docs/` in the ControlFlow SDK or reach out to the audit team.
