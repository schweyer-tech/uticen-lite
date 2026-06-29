# Phase 2 — Northwind Trading Demo Engagement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

---

## EXECUTION RULES (read first)

- **Never ask the user for permission to continue between tasks.** Execute the full plan start to finish without interruption.
- On an unresolvable error after 2–3 attempts: note it in the task and **skip to the next task**.
- **Commit per task; do NOT push** (the controller pushes once the phase gate is green — SDK repo `main`).
- Work in the SDK repo `/Users/dom/repos/uticen-lite`. The CLI is the installed `uticen-lite` (editable → reflects repo state, incl. Phase 1's multi-source `test(pop, sources)`). Toolchain: `ruff`, `mypy`, `python3 -m pytest`.
- **Depends on Phase 1** (multi-source `test()`). Verify it's present first: `python3 -c "import inspect, uticen_lite.runner.execute as e; print(hasattr(e, '_accepts_sources'))"` → `True`.

---

**Goal:** A committed, runnable sample engagement — **Northwind Trading Co.**, 8 audit controls across financial close / IT access / procurement over 8 seeded CSV sources — under `examples/northwind-trading/`, that `uticen-lite run` turns into audit-grade workpapers and `uticen-lite build` into an import bundle, guarded by a CI fixture test.

**Architecture:** A normal `uticen-lite` project (`cflow.yaml`, `sources.yaml`, `controls/*/{control.yaml,test.py}`, `data/*.csv`). Controls 2/3/4/8 use multi-source `def test(pop, sources)`. The dataset is seeded so each control yields its specified exception count (one control, `mfa-enforcement`, passes clean). A CI fixture (`tests/examples/test_northwind.py`) runs it end-to-end with a fixed `--at` and asserts the outcomes.

**Tech Stack:** SDK `uticen-lite` CLI, pandas (in `test.py`), CSV data, pytest fixture.

## Global Constraints

- **Demo "as-of" date is fixed: `2026-03-31T00:00:00Z`** — run/build/fixture all use `--at 2026-03-31T00:00:00Z` so the date-relative controls (privileged-review 90-day window → cutoff `2025-12-31`; duplicate-payment 5-day window) are deterministic. Document this date in the README.
- **`item_key` = the natural record id** per control (entry_id / payment_id / account_id …).
- **Keys use `original_name`** (learning 0014); each source's key column is its natural id; `key_config.mode` `single`.
- **Framework refs** map to NIST 800-53 per the spec table (controls 1,4,5,6,8); financial/proc-only controls (2,3,7) omit `nist` or use an empty list.
- **Exactly one clean control:** `mfa-enforcement` yields **0** exceptions; every other control yields the count in its task.
- **No generated output committed:** `examples/northwind-trading/target/` must be gitignored / never staged.
- **`test.py` returns** `list[dict]` of `{item_key, description, severity, details}` (severity ∈ low|medium|high|critical).

---

### Task 1: Project skeleton + seeded dataset (8 CSVs) + sources.yaml

**Files:**
- Create: `examples/northwind-trading/cflow.yaml`, `examples/northwind-trading/sources.yaml`, `examples/northwind-trading/.gitignore` (`target/`), `examples/northwind-trading/data/{journal_entries,closed_periods,purchase_orders,invoices,payments,employees,access_accounts,vendor_master}.csv`

**Interfaces:**
- Produces: a valid project (`uticen-lite validate` passes — controls added later, so an empty `controls/` is fine) and the 8 datasets every later task's control logic asserts against.

- [ ] **Step 1: Scaffold + cflow.yaml.** `uticen-lite init examples/northwind-trading` (or hand-create). Set `cflow.yaml` to a Northwind project: `name: northwind-trading`, `framework: nist-800-53`, `system: { name: "Northwind Trading Co." }`. Add `.gitignore` with `target/`.

- [ ] **Step 2: Author the 8 data CSVs to these exact seeding targets.** (Author plausible business data; the columns + the *specific violation rows* below are what matters. Keep joins consistent across files — vendor_id / po_id / invoice_id / employee_id must line up.)

  - **`journal_entries.csv`** (~40 rows): `entry_id,period,posting_date,account,amount,entry_type,prepared_by,reviewed_by`.
    - For `manual-je-review` (control 1) → seed **exactly 3** rows that are `entry_type=manual` AND `amount>=50000` AND (`reviewed_by` empty OR `reviewed_by==prepared_by`). Also seed ≥2 manual rows ≥50000 WITH a different `reviewed_by` (compliant), and many automated / small-amount rows.
    - For `closed-period-postings` (control 2) → seed **exactly 2** rows whose `period` is a CLOSED period (see closed_periods). All other rows in OPEN periods.
  - **`closed_periods.csv`** (6 rows): `period,status`. e.g. `2025-Q1,closed` and `2025-Q4,closed`; `2026-Q1,open` etc. The 2 control-2 violators sit in a closed period.
  - **`purchase_orders.csv`** (~18 rows): `po_id,vendor_id,amount,approved_by,status` (`approved`/`pending`). Most `approved`.
  - **`invoices.csv`** (~22 rows): `invoice_id,po_id,vendor_id,amount,invoice_date`. Each references a real `po_id` (except where control 3 needs a gap).
  - **`payments.csv`** (~30 rows): `payment_id,invoice_id,vendor_id,amount,paid_date,approved_by,entered_by`.
    - For `three-way-match` (control 3) → seed **exactly 4** problem payments: (a) one with `invoice_id` that does not exist in invoices; (b) one whose invoice → PO has `status=pending` (not approved); (c) one whose amount is >1% off its PO amount; (d) one whose invoice has a `po_id` not present in purchase_orders. All other payments trace cleanly within tolerance to an approved PO.
    - For `duplicate-payments` (control 7) → seed **exactly 2** duplicate pairs (same `vendor_id`+`amount`, `paid_date` within 5 days) → control flags the *later* of each pair = 2 exceptions. Ensure no OTHER payments accidentally collide on vendor+amount within 5 days.
    - For `vendor-master-sod` (control 8) → seed **exactly 2** payments whose `approved_by` equals the paid vendor's `created_by` or `last_modified_by` (see vendor_master). Keep these distinct from the control-3/7 violators where possible.
  - **`employees.csv`** (~28 rows): `employee_id,name,status,termination_date,department` (`active`/`terminated`).
  - **`access_accounts.csv`** (~38 rows): `account_id,employee_id,system,role,is_privileged,mfa_enabled,is_active,approved_by,last_review_date` (booleans as `true`/`false`).
    - For `terminated-access` (control 4) → seed **exactly 3** accounts with `is_active=true` whose `employee_id` is `terminated` in employees.csv.
    - For `privileged-access-review` (control 5) → seed **exactly 2** accounts with `is_privileged=true` AND (`approved_by` empty OR `last_review_date` < `2025-12-31`, i.e. >90 days before the 2026-03-31 as-of). Other privileged accounts: approved + reviewed after 2025-12-31.
    - For `mfa-enforcement` (control 6, **CLEAN**) → EVERY `is_active=true` account has `mfa_enabled=true`. (Inactive/terminated accounts may have `mfa_enabled=false`, but they are not `is_active`.) → 0 exceptions.
  - **`vendor_master.csv`** (~14 rows): `vendor_id,vendor_name,created_by,last_modified_by`. The 2 control-8 violators' vendors have `created_by`/`last_modified_by` matching those payments' `approved_by`.

- [ ] **Step 3: Author `sources.yaml`** with all 8 sources (id = the names above), `type: file`, `config: {path: data/<file>.csv, format: csv}`, `key_config: {mode: single, columns: [<natural id>]}`, and `column_mappings` for each declared column (`original_name`, `display_name`, `data_type` ∈ text/number/date/boolean, `is_key: true` on the id column). Read `uticen_lite/schema/sources.schema.json` to match the required shape.

- [ ] **Step 4: Validate.** Run: `uticen-lite validate examples/northwind-trading`
  Expected: exit 0 (no controls yet, or controls added later — empty controls/ validates fine).

- [ ] **Step 5: Commit** (do NOT push). `git add examples/northwind-trading && git commit -m "feat(example): Northwind Trading project skeleton + seeded datasets"` — confirm no `target/` is staged.

---

### Task 2: Financial controls (manual-je-review, closed-period-postings, three-way-match)

**Files:**
- Create: `examples/northwind-trading/controls/{manual-je-review,closed-period-postings,three-way-match}/{control.yaml,test.py}`

**Interfaces:**
- Consumes: the Task 1 datasets + Phase 1 `def test(pop, sources)`.
- Produces: 3 controls; `uticen-lite run` flags 3 / 2 / 4 exceptions respectively.

- [ ] **Step 1: Author `manual-je-review`** (primary `journal_entries`). `control.yaml`: id `manual-je-review`, a real title/objective/narrative, `framework_refs: {nist: [AC-5]}`, `risk`, `sources: [- id: journal_entries]`. `test.py` (`def test(pop)`): flag rows where `entry_type=="manual"` and `float(amount)>=50000` and (`reviewed_by` blank/NaN or `reviewed_by==prepared_by`); each violation `item_key=entry_id`, severity `high`, details `{amount, prepared_by}`.

- [ ] **Step 2: Author `closed-period-postings`** (multi-source: primary `journal_entries`, also `closed_periods`). `control.yaml`: `framework_refs: {nist: []}` (cutoff control), `sources: [- id: journal_entries, - id: closed_periods]`. `test.py` (`def test(pop, sources)`): join JEs to `sources["closed_periods"].df` on `period`; flag JEs whose period `status=="closed"`; `item_key=entry_id`, severity `high`.

- [ ] **Step 3: Author `three-way-match`** (multi-source: primary `payments`, also `invoices`, `purchase_orders`). `test.py` (`def test(pop, sources)`): for each payment, look up its invoice (by `invoice_id`) then PO (by `po_id`); flag if invoice missing, PO missing, PO `status!="approved"`, or `abs(payment.amount - po.amount)/po.amount > 0.01`; `item_key=payment_id`, severity `high`, details the reason. `framework_refs: {nist: []}`.

- [ ] **Step 4: Run + assert counts.** Run: `uticen-lite run examples/northwind-trading --control manual-je-review --at 2026-03-31T00:00:00Z` (and the same for the other two, or run all). Confirm the RUN lines show **3**, **2**, **4** violations respectively. If a count is off, FIX the seeded data in `examples/northwind-trading/data/*.csv` (the data is the source of truth for the demo story) — minimally, and re-verify. Clean up any generated `target/` (do not commit it).

- [ ] **Step 5: Commit** (do NOT push). `git add examples/northwind-trading/controls examples/northwind-trading/data && git commit -m "feat(example): financial controls (manual JE review, closed-period postings, 3-way match)"`

---

### Task 3: IT access controls (terminated-access, privileged-access-review, mfa-enforcement)

**Files:**
- Create: `examples/northwind-trading/controls/{terminated-access,privileged-access-review,mfa-enforcement}/{control.yaml,test.py}`

**Interfaces:**
- Consumes: Task 1 datasets (`access_accounts`, `employees`) + Phase 1 multi-source.
- Produces: 3 controls; `uticen-lite run` flags 3 / 2 / **0** exceptions respectively.

- [ ] **Step 1: Author `terminated-access`** (multi-source: primary `access_accounts`, also `employees`). `control.yaml`: `framework_refs: {nist: [AC-2]}`. `test.py` (`def test(pop, sources)`): join accounts to `sources["employees"].df` on `employee_id`; flag accounts where `is_active` is true and the employee `status=="terminated"`; `item_key=account_id`, severity `critical`, details `{employee_id, system}`.

- [ ] **Step 2: Author `privileged-access-review`** (primary `access_accounts`). `control.yaml`: `framework_refs: {nist: [AC-6]}`. `test.py` (`def test(pop)`): flag where `is_privileged` is true and (`approved_by` blank OR `last_review_date < "2025-12-31"`); `item_key=account_id`, severity `high`. (Note in a code comment: the 90-day cutoff is computed from the fixed demo as-of date 2026-03-31; a future enhancement could derive it from the run's executed_at.)

- [ ] **Step 3: Author `mfa-enforcement`** (primary `access_accounts`, the **clean** control). `control.yaml`: `framework_refs: {nist: [IA-2]}`. `test.py` (`def test(pop)`): flag where `is_active` is true and `mfa_enabled` is false; `item_key=account_id`, severity `high`. With the Task-1 data this returns `[]` (0 exceptions) — that is the intended passing-workpaper demo.

- [ ] **Step 4: Run + assert counts.** Run `uticen-lite run ... --control <id> --at 2026-03-31T00:00:00Z` for each. Confirm **3**, **2**, **0**. The `mfa-enforcement` workpaper should show a clean pass. Fix the seeded data minimally if a count is off; re-verify. Don't commit `target/`.

- [ ] **Step 5: Commit** (do NOT push). `git add examples/northwind-trading/controls examples/northwind-trading/data && git commit -m "feat(example): IT-access controls (terminated access, privileged review, MFA — clean)"`

---

### Task 4: Procurement controls (duplicate-payments, vendor-master-sod)

**Files:**
- Create: `examples/northwind-trading/controls/{duplicate-payments,vendor-master-sod}/{control.yaml,test.py}`

**Interfaces:**
- Consumes: Task 1 datasets (`payments`, `vendor_master`) + Phase 1 multi-source.
- Produces: 2 controls; `uticen-lite run` flags 2 / 2 exceptions. **Re-verify control 3 (`three-way-match`) still flags 4** since `payments` is shared.

- [ ] **Step 1: Author `duplicate-payments`** (primary `payments`). `control.yaml`: `framework_refs: {nist: []}`. `test.py` (`def test(pop)`): parse `paid_date`; group/scan for payments sharing `vendor_id`+`amount` with another payment within 5 days; flag the LATER one of each pair; `item_key=payment_id`, severity `high`, details `{vendor_id, amount, paid_date}`.

- [ ] **Step 2: Author `vendor-master-sod`** (multi-source: primary `payments`, also `vendor_master`). `control.yaml`: `framework_refs: {nist: [AC-5]}`. `test.py` (`def test(pop, sources)`): join payments to `sources["vendor_master"].df` on `vendor_id`; flag payments whose `approved_by` equals the vendor's `created_by` or `last_modified_by`; `item_key=payment_id`, severity `high`.

- [ ] **Step 3: Run + assert counts (incl. the cross-check).** Run `uticen-lite run ... --control duplicate-payments` → **2**; `... --control vendor-master-sod` → **2**; AND re-run `... --control three-way-match` → still **4** (payments edits in Task 1/here must not change the 3-way-match story). Adjust data minimally + re-verify all three if needed. No `target/` committed.

- [ ] **Step 4: Full run sanity.** Run `uticen-lite run examples/northwind-trading --at 2026-03-31T00:00:00Z` (all controls). Confirm the per-control RUN lines: manual-je-review 3, closed-period-postings 2, three-way-match 4, terminated-access 3, privileged-access-review 2, mfa-enforcement 0, duplicate-payments 2, vendor-master-sod 2.

- [ ] **Step 5: Commit** (do NOT push). `git add examples/northwind-trading/controls examples/northwind-trading/data && git commit -m "feat(example): procurement controls (duplicate payments, vendor-master SoD)"`

---

### Task 5: Example README

**Files:**
- Create: `examples/northwind-trading/README.md`

- [ ] **Step 1: Write the README.** Sections: (a) **The company** — Northwind Trading Co., a ~600-person wholesale distributor; the audit scope; the fixed demo as-of date **2026-03-31**. (b) **The controls** — a table of all 8 (title, domain, sources, NIST ref, what it flags) noting `mfa-enforcement` is the clean/passing one. (c) **Run it** — `uticen-lite run . --at 2026-03-31T00:00:00Z` then open `target/workpapers/<id>.html`; emphasize full-population, provenance. (d) **Import into Uticen** — `uticen-lite build . --out import-bundle.zip --at 2026-03-31T00:00:00Z`, then upload at the app's **Settings → Imports** (admin), and what lands (controls + workpapers + exceptions, with the NIST refs). (e) note it doubles as a template to copy.

- [ ] **Step 2: Commit** (do NOT push). `git add examples/northwind-trading/README.md && git commit -m "docs(example): Northwind Trading README"`

---

### Task 6: CI fixture test

**Files:**
- Create: `tests/examples/__init__.py`, `tests/examples/test_northwind.py`

**Interfaces:**
- Consumes: the committed example + the public CLI (`uticen-lite`) + `uticen_lite.schema.validate.validate_bundle`.
- Produces: a test that runs the example end-to-end and asserts the seeded outcomes + bundle validity, so the example stays correct.

- [ ] **Step 1: Write the test.** `tests/examples/test_northwind.py`: copy `examples/northwind-trading` into a `tmp_path` (so the repo tree isn't dirtied), then drive the CLI via `uticen_lite.cli.main([...])` (preferred — in-process, no subprocess) OR `subprocess` to `uticen-lite`:

```python
EXPECTED = {
    "manual-je-review": 3, "closed-period-postings": 2, "three-way-match": 4,
    "terminated-access": 3, "privileged-access-review": 2, "mfa-enforcement": 0,
    "duplicate-payments": 2, "vendor-master-sod": 2,
}
AT = "2026-03-31T00:00:00Z"

def test_northwind_runs_and_builds(tmp_path):
    proj = tmp_path / "northwind"
    shutil.copytree(EXAMPLE_DIR, proj)
    assert main(["validate", str(proj)]) == 0
    assert main(["run", str(proj), "--at", AT]) == 0
    # assert each control's exception count from target/run-log.json (or evidence/*.json)
    runs = read_runs(proj / "target")            # use uticen_lite.runner.runlog.read_runs
    by_control = {r["control_id"]: r["failed"] for r in runs}
    for cid, n in EXPECTED.items():
        assert by_control[cid] == n, f"{cid}: expected {n}, got {by_control.get(cid)}"
    out = proj / "bundle.zip"
    assert main(["build", str(proj), "--out", str(out), "--at", AT]) == 0
    manifest = json.loads(zipfile.ZipFile(out).read("manifest.json"))
    assert validate_bundle(manifest) == []
    assert len(manifest["controls"]) == 8
```

  (Resolve `EXAMPLE_DIR` relative to the repo root; `read_runs` is `uticen_lite.runner.runlog.read_runs`. Confirm the run-log entry field for the per-control id + failed count — read `model/run.py` `RunRecord.to_dict()` / `runlog` to use the exact keys; adjust the accessors to match.)

- [ ] **Step 2: Run it.** Run: `python3 -m pytest tests/examples/test_northwind.py -q` → PASS. Then the full gate: `ruff check --fix --unsafe-fixes . && ruff format . && mypy uticen_lite && python3 -m pytest -q` → all green.

- [ ] **Step 3: Commit** (do NOT push). `git add tests/examples && git commit -m "test(example): Northwind end-to-end fixture (run + build + validate_bundle + seeded counts)"`

---

## Self-Review (controller, before handing off)

1. **Spec coverage:** §3 all 8 controls with exact logic ✓ Tasks 2–4; 8 sources ✓ Task 1; seeded outcomes incl. the clean control ✓ Tasks 1–4 + Global Constraints; 800-53 mapping ✓ per-control `framework_refs`; §4 location/form + README ✓ Tasks 1/5; CI fixture ✓ Task 6; §6 determinism (fixed `--at`) ✓ Global Constraints + Task 6.
2. **Placeholder scan:** the CSV row *contents* are authored by the implementer to meet explicit per-file seeding targets (counts + characteristics) — not a vague "add data"; every control's logic + expected count is spelled out.
3. **Type consistency:** control ids, source ids, the fixed `--at` (2026-03-31), and the expected counts (3/2/4/3/2/0/2/2) are identical across Tasks 1–6 and the fixture's `EXPECTED` map.
