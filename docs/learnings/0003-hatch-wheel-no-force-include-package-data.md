---
id: 0003
date: 2026-06-19
area: packaging
tags: [hatch, hatchling, wheel, packaging, pyproject, build]
status: active
supersedes: null
superseded_by: null
---

# Don't `force-include` package-internal paths in the hatchling wheel — `packages` already ships them; verify packaging by building the wheel, not by reading pyproject

## What went wrong

To ship `plane/templates` + `plane/static` (HTML, CSS, vendored JS) in the wheel, a
`[tool.hatch.build.targets.wheel.force-include]` mapping was added pointing those paths back at
themselves. The wheel build then failed: `ValueError: A second file is being added to the wheel
archive at the same path: 'controlflow_sdk/plane/static/app.css'`. Root cause: hatchling's
`packages = ["controlflow_sdk"]` already includes **every** file under the package directory (data
files too, not just `.py`), so force-including paths already inside the package duplicates them. The
packaging unit test stayed green the whole time — it only parsed `pyproject.toml` and never built a
wheel.

## The rule

- With hatchling, `[tool.hatch.build.targets.wheel] packages = ["pkg"]` already ships all non-Python
  data files under the package. Use `force-include` (or `artifacts`) ONLY for files that live
  **outside** the selected packages or are VCS-ignored. Never force-include a path already inside a
  packaged directory — it fails the build with a duplicate-archive-path `ValueError`.
- A packaging test that only reads `pyproject.toml` does not prove the wheel works. Verify by
  building: `python -m build --wheel`, then open the `.whl` (it's a zip) and assert the expected data
  files (templates/static) are present. A green pyproject-parse test can sit on top of a wheel that
  does not build.

### Corollary — shipping out-of-package data to pip users (the legitimate force-include case)

To ship data that lives **outside** the package (e.g. `examples/` — the demo a
pip-installed feature needs but which is not under `controlflow_sdk/`), force-include it
INTO the package namespace and keep the original as the single source of truth:
`[tool.hatch.build.targets.wheel.force-include]` → `"examples/x" = "controlflow_sdk/_demo/x"`.
Map only the files the feature needs, not the whole dir.

- **force-include runs for the BUILT wheel, not for editable/source installs.** So any
  runtime code that reads the bundled data MUST resolve with a fallback: packaged location
  first (`<pkg>/_demo/...`), repo path second (`<repo>/examples/...`). A resolver that only
  looks in the packaged location works from a wheel but breaks in editable dev installs and
  the test suite (which run from the source tree where `_demo/` does not exist).
- After adding a force-include, prove BOTH paths: build the wheel and assert the mapped
  files are inside it, AND `pip install` that wheel into a clean venv **outside the repo**
  and exercise the runtime path — so the repo-path fallback can't mask a packaging gap.

## Reference

- `pyproject.toml` (`[tool.hatch.build.targets.wheel] packages = ["controlflow_sdk"]` ships
  `plane/templates` + `plane/static`; a later `force-include` block maps
  `examples/northwind-trading` → `controlflow_sdk/_demo/...` for the demo loader).
- `controlflow_sdk/store/import_service.py` (`demo_source_dir()` — packaged-first, repo-path
  fallback resolver).
- `tests/plane/test_packaging.py` (asserts the `plane` extra + `controlplane` script — a
  pyproject-only check; pair it with a real `python -m build` when touching packaging).
