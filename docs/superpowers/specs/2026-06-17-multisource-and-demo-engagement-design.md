# SDK Multi-Source `test()` + "Northwind Trading" Demo Engagement — Design

> Status: **Approved design (2026-06-17)** — pending implementation plans.
> Repo: `uticen-lite`. Two coordinated pieces: a small SDK capability (multi-source `test()`)
> and a rich sample engagement that uses it. Decomposed into two build phases (§5).

## 1. Summary & motivation

Build a **fake mid-size company ("Northwind Trading Co.") with ~8 decently-complex audit controls**, committed to the SDK repo under `examples/northwind-trading/`, that does **triple duty**: a runnable **demo** (`uticen-lite run` → audit-grade workpapers; `uticen-lite build` → an import bundle for the Uticen app), **cold-user onboarding** (a real project to copy/adapt — far better than the bare scaffold), and **rich test fixtures** (a CI test runs it end-to-end so it stays green).

Designing the controls surfaced a real SDK gap: **`test(pop)` currently receives only the first bound source.** `run_control` loads every bound source into `populations` but passes only `populations[0]` to the test callable, and `Population` exposes no siblings — so a single control cannot join across sources. The most realistic blended-org controls (3-way PO/invoice/payment match, terminated-user-access, vendor-master SoD) are exactly cross-source joins. So this design **first extends the SDK** to expose all bound sources to `test()`, then builds the demo on top.

## 2. Part A — SDK enhancement: multi-source `test()`

### API (backward-compatible)
`test()` gains an **optional** second parameter:

```python
def test(pop):                 # unchanged: pop = first bound source (Population)
    ...

def test(pop, sources):        # new: sources = dict[str, Population] for ALL bound sources
    payments = pop.df                     # primary
    invoices = sources["invoices"].df     # any other bound source, by its source id
    pos      = sources["purchase_orders"].df
    ...
```

- `sources` is a `dict[str, Population]` keyed by **source id** (the ids declared in `sources.yaml` / the control's `sources:` list), and **includes the primary** (so `sources[primary_id] is pop`).
- `pop` remains the primary (first bound source) for back-compat.

### Runner change (`runner/execute.py`)
`run_control` already builds `populations: list[Population]` for every `control.sources` binding. The change:
1. Build `sources_by_id: dict[str, Population]` alongside `populations` (key = `binding.id`).
2. Inspect the test callable's signature with `inspect.signature(test_fn)`. If it accepts **≥ 2 positional parameters** (or `*args`), call `test_fn(primary, sources_by_id)`; otherwise call `test_fn(primary)`.
3. Everything else (violation validation, RunRecord assembly, provenance from all sources) is unchanged.

`load_test_callable` is already typed `Callable[..., list[Any]]`, so no signature-type change is needed. Keep the existing `RunnerError` wrapping (a multi-source test that raises still surfaces a clean, SDK-frame-stripped error).

### Tests & docs
- `tests/runner/test_execute.py`: a 1-arg test still works (back-compat); a 2-arg test receives a `sources` dict containing all bound sources keyed by id, with `sources[primary_id] is pop`; a 2-arg test that joins two sources flags the correct rows; a malformed multi-source test raises `RunnerError`.
- `README.md` "Authoring a control" gains a short multi-source example (the 3-way-match shape) and documents that `sources` is keyed by the ids in `sources.yaml`.
- Ships as its own commit/PR to SDK `main` **before** the example (the example depends on it).

## 3. Part B — The demo org & control set: Northwind Trading Co.

**Northwind Trading Co.** — a fictional ~600-employee wholesale distributor (the name winks at the classic sample DB; clearly fictional). Its control universe spans financial close, IT access, and procurement, mapped to **NIST 800-53** where it fits so imported controls carry recognized framework refs in the app.

### Sources (8 CSVs; a couple are tiny dimension tables)

| Source id | File | Key columns |
|---|---|---|
| `journal_entries` | `data/journal_entries.csv` | entry_id, period, posting_date, account, amount, entry_type (`manual`\|`automated`), prepared_by, reviewed_by |
| `closed_periods` | `data/closed_periods.csv` | period, status (`open`\|`closed`) |
| `purchase_orders` | `data/purchase_orders.csv` | po_id, vendor_id, amount, approved_by, status |
| `invoices` | `data/invoices.csv` | invoice_id, po_id, vendor_id, amount, invoice_date |
| `payments` | `data/payments.csv` | payment_id, invoice_id, vendor_id, amount, paid_date, approved_by, entered_by |
| `employees` | `data/employees.csv` | employee_id, name, status (`active`\|`terminated`), termination_date, department |
| `access_accounts` | `data/access_accounts.csv` | account_id, employee_id, system, role, is_privileged (bool), mfa_enabled (bool), is_active (bool), approved_by, last_review_date |
| `vendor_master` | `data/vendor_master.csv` | vendor_id, vendor_name, created_by, last_modified_by |

Key configs use `original_name` keys (per learning 0014); the primary key column per source is its natural id.

### Controls (8) — exact logic

| # | id / title | Domain | Primary source | Other sources | Logic | 800-53 | Expected |
|---|---|---|---|---|---|---|---|
| 1 | `manual-je-review` — Manual JEs ≥ $50k require independent review | Financial | journal_entries | — | flag where `entry_type=="manual"` AND `amount >= 50000` AND (`reviewed_by` blank OR `reviewed_by == prepared_by`) | AC-5 | ~3 exceptions |
| 2 | `closed-period-postings` — No postings to closed periods | Financial | journal_entries | closed_periods | flag JEs whose `period` has `status=="closed"` (join on period) | — | ~2 exceptions |
| 3 | `three-way-match` — Payment ↔ Invoice ↔ approved PO | Financial/Proc | payments | invoices, purchase_orders | for each payment: must map to an invoice (by invoice_id) that maps to a PO (by po_id) with `status=="approved"`, and `|payment.amount − po.amount| / po.amount ≤ 0.01`; flag missing links or out-of-tolerance | — | ~4 exceptions |
| 4 | `terminated-access` — Terminated employees have no active accounts | IT access | access_accounts | employees | flag accounts where `is_active` AND the joined employee (by employee_id) has `status=="terminated"` | AC-2 | ~3 exceptions |
| 5 | `privileged-access-review` — Privileged accounts approved & reviewed ≤ 90d | IT access | access_accounts | — | flag where `is_privileged` AND (`approved_by` blank OR `last_review_date` older than 90 days before the run's `executed_at`) | AC-6 | ~2 exceptions |
| 6 | `mfa-enforcement` — MFA on all active accounts | IT access | access_accounts | — | flag where `is_active` AND NOT `mfa_enabled` | IA-2 | **0 (clean)** — the designated passing control |
| 7 | `duplicate-payments` — No duplicate vendor payments | Procurement | payments | — | flag payments sharing `vendor_id` + `amount` with another payment within 5 days (`paid_date`); report the later one | — | ~2 exceptions |
| 8 | `vendor-master-sod` — Vendor creator ≠ payment approver | Procurement | payments | vendor_master | flag payments where the payment's `approved_by` equals the `created_by` or `last_modified_by` of the paid vendor (join on vendor_id) | AC-5 | ~2 exceptions |

Controls 2, 3, 4, 8 are genuine cross-source joins (use `def test(pop, sources)`). Each `test.py` is plain pandas over `pop.df` / `sources[...].df`, returning the standard `[{item_key, description, severity, details}, ...]`. Each `control.yaml` carries a real objective + narrative + `framework_refs` + `risk`.

### Data & outcomes
~25–50 readable rows per substantial source (dimension tables smaller). Issues are **intentionally seeded** so each control's expected exceptions above actually occur, producing real workpapers — a **realistic mix**: most controls flag a few exceptions, and **1–2 controls pass clean** (e.g. seed `mfa-enforcement` so only active accounts have MFA → 0 exceptions, demonstrating a passing workpaper alongside the failing ones). `item_key`s are the natural record ids (entry_id, payment_id, account_id…).

## 4. Part C — Location, form & CI

```
examples/northwind-trading/
  README.md            # the org, the 8 controls, how to run/build/import
  cflow.yaml           # project config (system: Northwind Trading Co., framework: nist-800-53)
  sources.yaml         # the 8 sources
  controls/<id>/control.yaml + test.py     # 8 controls
  data/*.csv           # 8 seeded datasets
  (target/ is gitignored — never commit generated output)
```

- **README**: a short narrative (the company, the audit scope), a per-control table, and the run/build/import walkthrough (`uticen-lite run` → open a workpaper; `uticen-lite build` → import into Uticen at Settings → Imports).
- **CI fixture test** (`tests/examples/test_northwind.py`): runs the example end-to-end — `uticen-lite validate`, `uticen-lite run` (asserts the expected per-control exception counts + that the clean control is clean), `uticen-lite build`, and `validate_bundle` on the produced manifest. This keeps the example correct as the SDK evolves and gives the importer a realistic multi-control, multi-source fixture. (Use a tmp copy + `--at` a fixed timestamp so the privileged-review 90-day logic is deterministic.)

## 5. Part D — Decomposition (build phases)

- **Phase 1 — SDK multi-source `test()`** (`runner/execute.py` + tests + README). Small, backward-compatible; lands first (the example depends on it). Own commit(s); SDK gate green; push to `main`.
- **Phase 2 — Northwind Trading example** (`examples/northwind-trading/` data + 8 controls + sources + README + the CI fixture test). Built on Phase 1. Authored so `uticen-lite validate/run/build` succeed and the fixture test asserts the seeded outcomes.

Both land in the SDK repo. Each phase gets its own implementation plan.

## 6. Risks & notes

- **Determinism:** control 5 (privileged review ≤ 90 days) and control 7 (within-5-days duplicates) depend on dates relative to the run time. Author the data with absolute dates and run the fixture with a **fixed `--at`** so results are stable across time. Document the fixed demo "as-of" date in the README.
- **Back-compat:** the runner arity-inspection must not break existing 1-arg tests or the QA scenarios — the test suite covers both forms.
- **Trust boundary unchanged:** the example flows through the existing bundle path; no raw rows / `test_path` leak (the importer + schema already enforce this; the fixture's `validate_bundle` reconfirms).
- **Naming:** "Northwind Trading Co." is a placeholder the owner approved; trivially renamable (one `cflow.yaml` field + README).
- **Scope discipline (YAGNI):** no new source formats, no per-control config beyond what the 8 controls need; the multi-source change is the only SDK API addition.

## 7. References
- Strategy: full-population CCM; "any domain — that generality is the point"; NIST RMF/800-53 spine in scope.
- SDK contract: `def test(pop) -> list[dict]` (item_key/description/severity/details); `uticen_lite/runner/execute.py`, `model/population.py`, `project/discovery.py`.
- App importer: bundle → controls/workpapers/control-owned scripts/exceptions (framework_refs carry through).
