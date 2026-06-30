---
id: 0046
date: 2026-06-29
area: ci
tags: [sonarqube, coverage, self-hosted-runner, python, ci]
status: active
supersedes:
superseded_by:
---

# Feed coverage to the Sonar scan with a generated `coverage.xml`; on a self-hosted macOS runner fetch a **relocatable** CPython because `setup-python` can't run there

## Context
The SonarQube dashboard showed **0% coverage** with 4929 "uncovered" lines despite 940+
passing tests. `sonar-scanner` does **not** run tests — it only ingests a coverage report
named by `sonar.python.coverage.reportPaths`. None was produced, so every line read as
uncovered. Wiring `pytest --cov-report=xml` into `sonarqube.yml` then hit a second wall:
the self-hosted scan runner (macOS, runner user is **not** `runner`) has only system
Python 3.9, below the repo's `>=3.11` floor.

Dead-ends (each wrong because):
- Bare `python3 -m venv` — wrong: resolves to macOS system 3.9, fails the `>=3.11` floor.
- `actions/setup-python@v6` alone — wrong: its actions/python-versions macOS build is
  **not relocatable** (framework hardcodes `/Users/runner/hostedtoolcache`); dies
  `mkdir: /Users/runner: Permission denied`.
- Redirecting `RUNNER_TOOL_CACHE` / `AGENT_TOOLSDIRECTORY` to a writable dir — wrong: the
  hardcoded `/Users/runner` path is baked into the framework, not read from those vars.

## What worked
A `coverage.xml` produced by the scan job + a Python interpreter that satisfies the floor
without admin or a fixed path: probe for any local `python3.1x`, else download a pinned
**relocatable** CPython (astral `python-build-standalone`,
`cpython-<ver>+<tag>-<arch>-apple-darwin-install_only.tar.gz`, which extracts to
`python/bin/python3`) into `$RUNNER_TEMP` and build the venv from it. Result: dashboard
coverage 0% → 92.4%.

## The rule
- **A 0% coverage tile on SonarQube means no report is uploaded, NOT untested code.** The
  scanner never runs tests. Generate the report in the scan workflow (`pytest
  --cov=<pkg> --cov-report=xml`) and point `sonar.python.coverage.reportPaths=coverage.xml`
  (path relative to the project base dir) at it. Declare `pytest-cov` in the `dev` extra.
- **Build the coverage venv from an `pip install -e ".[adapters,dev]"`** (editable) so the
  package metadata resolves — otherwise the CLI version test
  (`importlib.metadata.version("uticen-lite")`) fails with `PackageNotFoundError` on the
  runner. Use an isolated venv so the self-hosted runner's global site-packages stay clean.
- **`actions/setup-python` cannot provision Python on a self-hosted macOS runner whose
  user is not `runner`** — its macOS build is not relocatable and no env var fixes the
  hardcoded `/Users/runner` path. To meet a `>=3.x` floor there: probe for a local
  `python3.1x` first, then fall back to a pinned **relocatable** `python-build-standalone`
  `install_only` tarball downloaded into `$RUNNER_TEMP`; select the asset arch from
  `uname -m` (`arm64`/`aarch64` → `aarch64`, `x86_64` → `x86_64`). Pin the version + release
  tag so the build is reproducible.

## Reference
- `.github/workflows/sonarqube.yml` (the "Run tests with coverage" step), `sonar-project.properties`,
  `pyproject.toml` `[project.optional-dependencies] dev`.
- PRs #123 (wire-up) and #127 (relocatable CPython); commit landing the green scan: 7c4c42a.
