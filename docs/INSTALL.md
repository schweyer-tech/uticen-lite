# Installing Uticen SDK

Uticen SDK is **pure Python** — the wheel is `py3-none-any` (one wheel for every OS and
Python ≥ 3.11) with **no compiled dependencies**, which keeps it Pyodide-safe and trivial to
install behind a firewall, on an internal index, or fully air-gapped. The happy-path install is in
the [README](../README.md#installation); this guide covers corporate / offline installs in depth.

## Extras

| Extra | Installs | Use it for |
| --- | --- | --- |
| `plane` | `fastapi`, `uvicorn`, `jinja2`, `python-multipart` | The local control-plane web app. **This is what you want** to author in the browser. |
| `adapters` | `openpyxl`, `pyarrow` | Reading Excel (`.xlsx`) and Parquet source files (CSV needs no extra). |
| `ai` | `anthropic`, `openai` | AI-assisted authoring (draft a `rule_spec` from an objective + sample). The SDKs import lazily, and Ollama uses the stdlib only, so the package runs without this extra. |

`[plane]` is the one to install for the control plane. Combine extras with a comma, e.g.
`'uticen-lite[plane,adapters]'` or `'uticen-lite[plane,adapters,ai]'`.

## Console scripts

After install, two commands are on your `PATH`:

- `uticen-lite` — the CLI (`import` / `run` / `build` / `validate`).
- `controlplane` — the local web app.

If you'd rather not rely on a script on `PATH`, the module equivalent is
`python -m uticen_lite.plane` (same flags as `controlplane`).

## Option 1 — pipx (isolated, recommended for a workstation)

[pipx](https://pipx.pypa.io/) installs the package into its own isolated virtual environment and
puts the `uticen-lite` and `controlplane` scripts on your `PATH` without touching your system or project
Python:

```bash
pipx install 'uticen-lite[plane]'
```

Upgrade in place with:

```bash
pipx upgrade uticen-lite
```

## Option 2 — internal package index (corporate mirror / Artifactory / Nexus / devpi)

If your organization mirrors PyPI on an internal index, point pip at it. The **`--index-url`** form
resolves *everything* (the package and all its dependencies) from the internal index only:

```bash
pip install --index-url https://pypi.internal.example/simple/ 'uticen-lite[plane]'
```

The additive **`--extra-index-url`** form keeps public PyPI in the search path and adds the internal
index alongside it (useful when only some packages live on the mirror):

```bash
pip install --extra-index-url https://pypi.internal.example/simple/ 'uticen-lite[plane]'
```

To make an index site-wide, set it once in `pip.conf` (`~/.config/pip/pip.conf` on Linux/macOS,
`%APPDATA%\pip\pip.ini` on Windows) or via the `PIP_INDEX_URL` environment variable:

```ini
[global]
index-url = https://pypi.internal.example/simple/
```

> **Security note.** `--extra-index-url` lets pip resolve a dependency from *either* index, which
> opens a dependency-confusion footgun (a public package can shadow an internal one of the same
> name). On locked-down sites prefer a **single trusted `--index-url`** that mirrors everything you
> need.

## Option 3 — pinned wheel (air-gapped / no index reachable)

On a connected machine, build the wheel and download every dependency wheel into a local
`wheelhouse/`:

```bash
python -m build --wheel                                  # builds dist/uticen_lite-<version>-py3-none-any.whl
pip download 'uticen-lite[plane]' -d wheelhouse/      # also pulls all dependency wheels
```

Transfer the `wheelhouse/` directory to the air-gapped machine, then install fully offline:

```bash
pip install --no-index --find-links wheelhouse/ 'uticen-lite[plane]'
```

Or, if you only need the single project wheel (and its deps are already present):

```bash
pip install uticen_lite-<version>-py3-none-any.whl
```

> Air-gapped sites must transfer the **dependency** wheels too, not just the project wheel — that is
> why `pip download ... -d wheelhouse/` + `--find-links wheelhouse/` is the reliable recipe. The
> wheel itself is `py3-none-any`, so a single file works for every OS and Python ≥ 3.11.

## Verifying a build (maintainers)

This is the human-facing companion to the automated `tests/plane/test_wheel_build.py`. It builds the
wheel, inspects its contents, runs the metadata check, and proves a clean-venv install **outside the
repo** can resolve the packaged demo and boot the app (learning 0003 — a repo checkout can mask a
packaging gap, so we leave the repo before exercising the runtime path):

```bash
# 1. Build the wheel
python -m build --wheel --outdir dist/

# 2. Inspect it: web assets + bundled demo present, no example target/
python - <<'PY'
import zipfile, pathlib
n = zipfile.ZipFile(next(pathlib.Path('dist').glob('*.whl'))).namelist()
assert 'uticen_lite/plane/static/app.css' in n
assert 'uticen_lite/plane/templates/base.html' in n
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
/tmp/cfsdk-venv/bin/python -c "from uticen_lite.store.import_service import demo_source_dir; p=str(demo_source_dir()); assert '_demo' in p, p; print('packaged demo resolves:', p)"
/tmp/cfsdk-venv/bin/python -c "import pathlib,tempfile; from uticen_lite.plane.app import create_app; create_app(pathlib.Path(tempfile.mkdtemp())); print('control plane builds OK')"
ls /tmp/cfsdk-venv/bin/uticen-lite /tmp/cfsdk-venv/bin/controlplane  # console scripts on PATH
```

> Use `create_app()` for the runtime check, **not** `fastapi.testclient.TestClient` — `httpx` is a
> dev-only dependency and is absent from a clean `[plane]` install, so `TestClient` won't import
> there.

## Upgrading

The control plane is **install-aware** — it upgrades itself the right way for how you installed it.

**From the app.** Open **Settings ▸ Updates**. *"Check for updates when the app starts"* is **on by
default** — it shows the header update indicator and a banner when a newer version is available; turn
it **off** for zero network egress. You can also click **Check for updates** any time. Click **Update now** to upgrade: the app runs
the upgrade in a detached helper and shuts down — re-run `controlplane` when it finishes (progress is
logged to `.controlplane-upgrade.log` in the engagement folder).

**From the terminal.** The same routine is available headless:

```bash
uticen-lite upgrade --check     # report installed vs latest, change nothing
uticen-lite upgrade             # detect the install method and upgrade (asks to confirm)
uticen-lite upgrade --yes       # upgrade without the prompt
```

`uticen-lite upgrade` picks the command for your install: a git checkout does
`git pull --ff-only` + an editable reinstall; a `pipx` install does `pipx upgrade uticen-lite`;
a `pip` install does `pip install -U uticen-lite` (honouring your configured index). Air-gapped /
pinned-wheel installs can't self-upgrade — re-run the [pinned-wheel](#option-3--pinned-wheel-air-gapped--no-index-reachable)
steps with the new wheel.

## Launching

Once installed, start the control plane in any engagement directory (created if missing):

```bash
controlplane --project my-audit
# → opens http://127.0.0.1:8765
```

The control plane is **localhost-only** — it listens on `127.0.0.1:8765`. Its one outbound
connection is a launch update check (**on by default**; see [Upgrading](#upgrading)) that fetches only
the latest version number — never client data. Turn the check off in **Settings ▸ Updates** for zero
network egress (see the [README](../README.md#design-principles)).
