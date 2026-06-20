# Spec: Distribution — corporate-install docs + verified release workflow (issue #11)
**Issue:** #11 · **Date:** 2026-06-20 · **Status:** approved-design

## Problem (1–3 sentences)
The control-plane pivot was motivated by "I want a straight `pip install`" in a corporate environment, and first-run onboarding already shipped — but install is still only from a repo path, which corporate/air-gapped networks frequently cannot reach. This round delivers the corporate-install story (pipx, internal index, pinned-wheel/air-gapped) and finalizes a ready-to-fire `release.yml` that builds, verifies, and publishes on a manual gate — **without actually publishing** (no PyPI account/token used here).

## Locked decisions
- **Docs + verified ready-to-fire release workflow ONLY this round; NO actual publish** (no PyPI account/token used). Nothing in this spec calls `twine upload`, runs the `publish` job, or pushes a `v*` tag.
- **Write corporate-install docs** as a new `docs/INSTALL.md` plus a short pointer/link from `README.md` (do not duplicate the long-form content in both). Cover: `pipx install controlflow-sdk`, internal-index usage (`--index-url` / `--extra-index-url`), and a pinned-wheel / air-gapped flow (build wheel → transfer → `pip install controlflow_sdk-<v>-py3-none-any.whl`). Note the `[plane]` extra, the `[adapters]` extra, and the `[ai]` extra (#10) where relevant. Mention console scripts `cflow` and `controlplane`.
- **Verify the wheel actually BUILDS and ships web assets + the bundled demo** (learning 0003: build the wheel and inspect it; prove a clean-venv install OUTSIDE the repo runs `controlplane`). Document the exact verification commands and encode the inspect step as an automated test.
- **Review `.github/workflows/release.yml`**: confirm it is correct and ready to fire on `workflow_dispatch` with `PYPI_API_TOKEN`. Describe what's there (token-based publish via `pypa/gh-action-pypi-publish`, not trusted publishing); recommend, don't change ownership. Fix any gap that would make a *future* publish fail (build / `twine check` / artifact / dispatch guard), WITHOUT publishing.
- **`PRODUCT-MAP.md`**: update the distribution row when this lands.
- **Honor learning 0003 above all** (build the wheel and inspect it; never trust a pyproject-parse test alone; prove the repo-path fallback can't mask a packaging gap by installing the wheel into a clean venv outside the repo).

## Design

### Overview
Three workstreams, all docs/CI/test — **zero runtime Python behavior changes** and **zero bundle-shape changes**:

1. **`docs/INSTALL.md`** — the canonical corporate-install guide; `README.md` gets a one-paragraph "Corporate / offline install" pointer linking to it.
2. **Packaging verification** — a new `tests/plane/test_wheel_build.py` that actually builds the wheel and asserts web assets + the bundled demo are inside it (the missing half of learning 0003; the existing `tests/plane/test_packaging.py` only parses `pyproject.toml`). Plus a documented manual clean-venv-outside-repo verification recipe in `docs/INSTALL.md`.
3. **`release.yml` hardening** — small, safe fixes that make a future manual publish reliable, keeping the publish job manual + token-gated.

### Verified substrate (already confirmed by building, this session — do NOT re-derive)
- `python -m build --wheel` succeeds; the wheel is `controlflow_sdk-0.1.0-py3-none-any.whl`.
- The wheel ships **5** `plane/static/` assets (`app.css`, `htmx.min.js`, `codemirror.min.css`, `codemirror.min.js`, `codemirror-python.min.js`), **15** `plane/templates/*` files (incl. `partials/`), and **26** `_demo/northwind-trading/` files (**8** CSVs + **8** `control.yaml`). `target/` is correctly EXCLUDED (gitignored in the example).
- In a clean venv OUTSIDE the repo, `pip install '<whl>[plane,adapters]'` installs both console scripts (`cflow`, `controlplane`); `demo_source_dir()` resolves to the packaged `_demo/` (proving the force-include path, not the repo fallback); `create_app(tmp)` builds, mounts `/static`, and creates `controlplane.db`; `uvicorn/fastapi/jinja2/multipart` and `openpyxl/pyarrow` import. `twine check dist/*.whl` → PASSED.
- **Gotcha discovered:** `httpx` is a **dev-only** dependency (in `[dev]`, not `[plane]`), so `fastapi.testclient.TestClient` is NOT importable in a clean `[plane]` venv. The clean-venv runtime check MUST use `create_app()` directly (or boot uvicorn with `--no-browser`) — NOT `TestClient`. The in-repo automated wheel test runs under `[dev]` so it may use either, but to stay faithful it should assert via `zipfile` inspection + a subprocess `pip install` into a fresh venv, not `TestClient`.
- **Version drift point:** `version` lives in BOTH `pyproject.toml` (`0.1.0`) and `controlflow_sdk/__init__.py` (`__version__ = "0.1.0"`), and is echoed in `CHANGELOG.md` ("Current version: `0.1.0`"). The release workflow should guard that the tag matches the packaged version so a future publish can't ship a mislabeled wheel.

### Files to CREATE

**`docs/INSTALL.md`** — corporate-install guide. Sections (exact headings):
- `# Installing ControlFlow SDK` — one-line intro; pure-Python, no compiled deps (cross-link the wheel's Pyodide-safe guarantee).
- `## Extras` — table of `plane` (web app: fastapi/uvicorn/jinja2/python-multipart), `adapters` (openpyxl/pyarrow for Excel/Parquet), `ai` (#10 — AI-assisted authoring; **mark "planned — not yet shipped" and only document the `pip install 'controlflow-sdk[ai]'` form, do not claim it works**). Note `[plane]` is what you want for the control plane; combine like `'controlflow-sdk[plane,adapters]'`.
- `## Console scripts` — after install, `cflow` (CLI) and `controlplane` (web app) are on PATH; the module-equivalent `python -m controlflow_sdk.plane`.
- `## Option 1 — pipx (isolated, recommended for a workstation)` — `pipx install 'controlflow-sdk[plane]'`; note pipx puts the two scripts on PATH in an isolated venv; `pipx upgrade controlflow-sdk`.
- `## Option 2 — internal package index (corporate mirror / Artifactory / Nexus / devpi)` — `pip install --index-url https://pypi.internal.example/simple/ 'controlflow-sdk[plane]'`; and the additive form `pip install --extra-index-url https://pypi.internal.example/simple/ 'controlflow-sdk[plane]'` (keeps public PyPI for deps); a `pip.conf`/`PIP_INDEX_URL` note for site-wide config; a one-line note that `--extra-index-url` can resolve deps from either index (recommend a single trusted index in locked-down sites).
- `## Option 3 — pinned wheel (air-gapped / no index reachable)` — build on a connected machine: `python -m build --wheel` (also `pip download 'controlflow-sdk[plane]' -d wheelhouse/` to pull deps); transfer the `wheelhouse/`; install offline: `pip install --no-index --find-links wheelhouse/ 'controlflow-sdk[plane]'` or, for just the one file, `pip install controlflow_sdk-<version>-py3-none-any.whl`. Note that air-gapped sites must also transfer the dependency wheels (hence `pip download`/`--find-links`), and that the wheel is `py3-none-any` (one wheel for all OS/Python ≥3.11).
- `## Verifying a build (maintainers)` — the exact, copy-pasteable verification recipe used this session (see "Documented manual verification recipe" below). This is the human-facing companion to the automated `test_wheel_build.py`.
- `## Launching` — `controlplane --project my-audit` → `http://127.0.0.1:8765`; localhost-only, zero egress (cross-link README).

**`tests/plane/test_wheel_build.py`** — the automated packaging proof (learning 0003's "build, don't parse" half). Marked slow but kept in the default suite (build ~ a few seconds). Structure:

```python
# module-level: build the wheel ONCE into a tmp dir, reuse across tests
import subprocess, sys, zipfile
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[2]  # repo root

@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
        cwd=REPO, check=True, capture_output=True, text=True,
    )
    whl = next(out.glob("*.whl"))
    return whl

def _names(whl: Path) -> list[str]:
    with zipfile.ZipFile(whl) as z:
        return z.namelist()

def test_wheel_ships_web_assets(built_wheel):
    names = _names(built_wheel)
    for asset in (
        "controlflow_sdk/plane/static/app.css",
        "controlflow_sdk/plane/static/htmx.min.js",
        "controlflow_sdk/plane/templates/base.html",
        "controlflow_sdk/plane/templates/setup.html",
        "controlflow_sdk/plane/templates/partials/rule_builder.html",
    ):
        assert asset in names

def test_wheel_ships_bundled_demo(built_wheel):
    names = _names(built_wheel)
    csvs = [n for n in names if n.startswith("controlflow_sdk/_demo/northwind-trading/data/") and n.endswith(".csv")]
    ctrls = [n for n in names if n.startswith("controlflow_sdk/_demo/northwind-trading/controls/") and n.endswith("control.yaml")]
    assert len(csvs) == 8
    assert len(ctrls) == 8
    assert "controlflow_sdk/_demo/northwind-trading/cflow.yaml" in names
    assert "controlflow_sdk/_demo/northwind-trading/sources.yaml" in names

def test_wheel_excludes_example_target_dir(built_wheel):
    # target/ workpapers/evidence are gitignored in the example and must not ship.
    assert not any("/target/" in n for n in _names(built_wheel))

def test_wheel_has_no_compiled_deps_in_record(built_wheel):
    # Mirror the release.yml Pyodide-safe guard at unit level.
    with zipfile.ZipFile(built_wheel) as z:
        record = z.read(next(n for n in z.namelist() if n.endswith("/RECORD"))).decode()
    assert not [l for l in record.splitlines() if "pydantic" in l.lower()]
```

- **Guard `build` availability:** wrap the fixture with `pytest.importorskip("build")` so the test skips cleanly if someone runs the suite without the `[dev]` extra (`build` is already in `[dev]`). CI installs `[adapters,dev]`, so it runs there.
- **Keep output pristine** (CLAUDE.md gate): `capture_output=True`; on failure, attach `proc.stderr` to the assertion message. No prints.
- **Optional (recommended) clean-venv subprocess test** — gated behind an env flag (e.g. `CFLOW_WHEEL_VENV_TEST=1`) and `pytest.mark.skipif`, because `python -m venv` + install is slow and network-touching (deps). When enabled it: creates a venv in tmp, `pip install '<whl>[plane]'`, then runs `python -c "from controlflow_sdk.store.import_service import demo_source_dir; assert '_demo' in str(demo_source_dir())"` from a cwd OUTSIDE the repo to prove the packaged path (not the repo fallback) resolves. Default-skipped to keep CI fast/offline; the manual recipe in `docs/INSTALL.md` is the always-available equivalent.

### Files to MODIFY

**`README.md`** — add a short subsection under `## Installation` (after the existing pipx line):
> **Corporate / offline install.** Behind a firewall, air-gapped, or on an internal package index? See **[docs/INSTALL.md](docs/INSTALL.md)** for pipx, internal-index (`--index-url`/`--extra-index-url`), and pinned-wheel flows.

Do not move the existing quick-start install commands; keep README the "happy path" and INSTALL.md the deep reference.

**`PRODUCT-MAP.md`** — update the distribution story. The map has no explicit "distribution" row today; the `controlplane` row says install is `pip install 'controlflow-sdk[plane]'`. Add ONE row near the top:
> `| Distribution / install | docs + release CI | Corporate-ready install paths — pipx, internal index (\`--index-url\`/\`--extra-index-url\`), and pinned-wheel/air-gapped — documented in \`docs/INSTALL.md\`. Wheel is pure-Python \`py3-none-any\`, ships the web assets + bundled Northwind demo (verified by \`tests/plane/test_wheel_build.py\`). A manual, token-gated \`release.yml\` (\`workflow_dispatch\`, \`PYPI_API_TOKEN\`) builds + \`twine check\`s the dist; **not yet published to PyPI**. |`
Bump the "Last updated" date to 2026-06-20.

**`.github/workflows/release.yml`** — hardening fixes (publish stays manual + token-gated; do NOT enable trusted publishing or change repo ownership):
1. **Add a tag↔version guard to the `publish` job** (prevents shipping a mislabeled wheel given the version is duplicated in `pyproject.toml` + `__init__.py`). After "Require an exact v* tag", add a step that asserts the tag equals the built package version:
   ```yaml
   - name: Tag matches package version
     run: |
       PKG=$(python -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")
       TAG="${GITHUB_REF_NAME#v}"
       [ "$PKG" = "$TAG" ] || { echo "::error::tag v$TAG != pyproject version $PKG"; exit 1; }
   ```
   (Runs only inside the already-gated `publish` job, so it never affects ordinary CI.)
2. **`build-and-check` already validates the dist** (build + `twine check` + import smoke + Pyodide-safe RECORD check + artifact upload) — confirm and KEEP as-is; it is correct and ready to fire.
3. **Reuse the verified artifact in publish (avoid rebuild drift):** the `publish` job currently re-checks-out and rebuilds. Lower-risk improvement: have `publish` `download-artifact@v7` (name `dist-${{ github.ref_name }}`) the exact dist that `build-and-check` verified and `twine check` it again before upload, instead of an independent rebuild. If keeping the rebuild is preferred for simplicity, leave it — but document the choice in a code comment. (Pick the download-artifact approach; it guarantees the published bytes are the verified bytes.)
4. **Do not change** the dispatch guard (`github.event_name == 'workflow_dispatch' && github.event.inputs.publish == 'true'`), the `environment: pypi`, or the `password: ${{ secrets.PYPI_API_TOKEN }}` credential path — all correct. Add a top-of-file comment recommending (for a future round, out of scope now) migrating to PyPI **trusted publishing** (OIDC, no long-lived token) once the project owner creates the PyPI project + a `pending publisher`; note that requires `permissions: id-token: write` and dropping the `password:` input — explicitly flagged as a recommendation, not a change here.

### Documented manual verification recipe (goes verbatim into `docs/INSTALL.md` "Verifying a build")
```bash
# 1. Build the wheel
python -m build --wheel --outdir dist/

# 2. Inspect it: web assets + bundled demo present, no example target/
python - <<'PY'
import zipfile, pathlib
n = zipfile.ZipFile(next(pathlib.Path('dist').glob('*.whl'))).namelist()
assert 'controlflow_sdk/plane/static/app.css' in n
assert 'controlflow_sdk/plane/templates/base.html' in n
assert sum(x.endswith('.csv') for x in n if '_demo/' in x) == 8
assert not any('/target/' in x for x in n)
print('wheel contents OK')
PY

# 3. twine metadata check
python -m twine check dist/*.whl

# 4. Clean-venv install OUTSIDE the repo, prove controlplane + packaged demo work
python -m venv /tmp/cfsdk-venv
/tmp/cfsdk-venv/bin/pip install "$(ls dist/*.whl)[plane,adapters]"
cd /tmp   # leave the repo so the repo-path fallback can't mask a packaging gap
/tmp/cfsdk-venv/bin/python -c "from controlflow_sdk.store.import_service import demo_source_dir; p=str(demo_source_dir()); assert '_demo' in p, p; print('packaged demo resolves:', p)"
/tmp/cfsdk-venv/bin/python -c "import pathlib,tempfile; from controlflow_sdk.plane.app import create_app; create_app(pathlib.Path(tempfile.mkdtemp())); print('control plane builds OK')"
ls /tmp/cfsdk-venv/bin/cflow /tmp/cfsdk-venv/bin/controlplane  # console scripts on PATH
```
(Note in the doc: use `create_app()` not `fastapi.testclient.TestClient` — `httpx` is a dev-only dep, absent from `[plane]`.)

## Bundle / contract impact
**UNCHANGED.** This round touches only docs, a CI workflow, a packaging test, and the product map. No code in `bundle/assemble.py`, `bundle/archive.py`, `contract/bundle.schema.json`, or any producer is modified; `schema_version` is untouched. `test_wheel_build.py` inspects the *distribution wheel*, never the export bundle, and explicitly asserts the example's `target/` (which could contain run output) does not ship — reinforcing the trust boundary at the packaging layer too. The contract gates (`tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`) remain authoritative and unaffected.

## Testing
TDD order — write the wheel-build test FIRST (it encodes the learning-0003 requirement and currently has no coverage), watch it pass against the real build, then write docs/CI.

- **New: `tests/plane/test_wheel_build.py`** (unit/integration over the built artifact):
  - `test_wheel_ships_web_assets` — `plane/static/app.css`, `plane/static/htmx.min.js`, `plane/templates/base.html`, `setup.html`, `partials/rule_builder.html` present.
  - `test_wheel_ships_bundled_demo` — 8 demo CSVs + 8 `control.yaml` + `cflow.yaml` + `sources.yaml` present.
  - `test_wheel_excludes_example_target_dir` — no `/target/` paths.
  - `test_wheel_has_no_compiled_deps_in_record` — RECORD has no `pydantic*` (Pyodide-safe; mirrors release.yml).
  - (optional, env-gated, default-skip) `test_clean_venv_install_resolves_packaged_demo` — venv + wheel install + out-of-repo `demo_source_dir()` resolves under `_demo/`.
- **Existing, KEEP green:** `tests/plane/test_packaging.py` (pyproject parse + `controlplane` entry + `__main__.main` importable) — the two tests are complementary (declared vs built); do not delete it.
- **Existing, must stay green/pristine:** `tests/store/test_import_service.py` (`demo_source_dir` packaged-first + repo fallback, `load_demo` copies + runnable) — unchanged but it's the runtime side of the same packaging contract; re-run to confirm no regression.
- **No new fixtures needed** beyond the module-scoped `built_wheel` tmp build inside the new test; reuse `tmp_path_factory`. No network in the default suite (the venv-install test is opt-in).
- **CI sanity:** `.github/workflows/ci.yml` runs `pytest -q` under `[adapters,dev]` where `build` is present, so `test_wheel_build.py` executes; `release.yml`'s `build-and-check` independently proves the wheel on the release path. Lint/type gates (`ruff check .`, `mypy controlflow_sdk`) must stay green — the test file targets py311, line-length 100, fully typed signatures.

## Non-goals / out of scope
- **No actual PyPI publish** — no token used, `publish` job not run, no `v*` tag pushed, no PyPI project/account created.
- **No trusted-publishing migration** — only recommended in a comment for a future round (requires owner action on PyPI).
- **No `[ai]` extra implementation** (#10) — docs mention it as "planned"; this round does not add the extra to `pyproject.toml`.
- **No version bump / release** — version stays `0.1.0`; consolidating the duplicated version (`pyproject.toml` vs `__init__.py` vs `CHANGELOG.md`) into a single source is noted as a risk but not done here (the tag-vs-pyproject guard mitigates the publish risk).
- **No runtime/app behavior changes**, no new dependencies, no bundle/contract changes, no Windows/macOS-specific install branches (the wheel is `py3-none-any`).

## Risks & mitigations
- **`test_wheel_build.py` slows the suite / flakes without `build`.** Mitigation: module-scoped single build (~seconds), `capture_output=True` for pristine output, `pytest.importorskip("build")` to skip gracefully; `build` is already in `[dev]` and CI installs it.
- **Version drift (`pyproject.toml` ≠ `__init__.py` ≠ `CHANGELOG.md`) could ship a mislabeled wheel.** Mitigation: the new tag↔pyproject-version guard in the `publish` job fails the publish before upload. (Full single-source-of-version consolidation is deferred.)
- **Air-gapped users miss dependency wheels if they only copy the one `.whl`.** Mitigation: `docs/INSTALL.md` explicitly shows `pip download ... -d wheelhouse/` + `pip install --no-index --find-links wheelhouse/` so transitive deps travel too.
- **`--extra-index-url` dependency-confusion footgun** (public index can shadow internal packages). Mitigation: doc note recommending a single trusted `--index-url` (mirror) for locked-down sites and explaining the difference from `--extra-index-url`.
- **Doc/recipe drift if `[plane]` deps change.** Mitigation: the automated wheel test asserts the *shipped contents*; the manual recipe references extras by name, and CLAUDE.md's grounding loop keeps PRODUCT-MAP/README in sync when a surface changes.
- **`release.yml` artifact reuse vs rebuild.** Mitigation: switch `publish` to download the verified `dist-${{ github.ref_name }}` artifact and re-`twine check` it, so published bytes are exactly the verified bytes (no second-build drift).

## Resolved open questions (2026-06-20)
- **release.yml publish job:** use `download-artifact` of the verified `dist/` built earlier in the workflow (guarantees the published bytes are the verified ones) rather than an independent rebuild.
- **Version source consolidation:** deferred — add only a tag-vs-`pyproject` publish guard now; do not consolidate the three version locations this round.
- **Clean-venv install test:** include it env-gated (default-skipped) AND keep the fast-lane zipfile-inspection tests AND document the manual recipe. Belt and suspenders.
- **`[ai]` extra in docs:** because #10 ships before #11 (and #11 is stacked on it), docs present `[ai]` as a real, working extra.
