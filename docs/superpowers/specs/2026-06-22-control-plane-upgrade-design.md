# Design — Upgrade & update-awareness for the control plane

> Status: approved design (brainstorming). Date: 2026-06-22. Slots under issue **#11**
> (distribution + first-run onboarding). Author surface: `controlplane` web app + `uticen-lite` CLI.

## Problem

There is no in-product way to move from one version of `uticen-lite` to the next. The
maintainer upgrades a git checkout by hand (`git pull` + reinstall + restart); an end user on a
`pipx`/`pip` install has no signal that a newer version exists and no affordance to take it. These
are *mechanically different* upgrades (a git checkout has no package index; a `pipx` install has no
`.git`), so a single hardcoded command can't serve both. We want one routine that **adapts to how
the app was installed**, surfaced as a dashboard button and a CLI command.

## Goals

- A control-plane **dashboard button** that, when the install is out of date, upgrades in one click.
- A **`uticen-lite upgrade` CLI** that runs the same routine headless — this *is* the maintainer's
  "git pull + reinstall" script, made first-class.
- **Opt-in** update awareness that does not break the product's documented **zero-network-egress**
  guarantee.
- Adapt the upgrade command to the detected install method (git-editable / pipx / pip / unknown).

## Non-goals (explicit)

- **No auto-upgrade.** Every upgrade is an explicit, confirmed user action. The app never mutates
  itself in the background.
- **No telemetry.** The (opt-in) update check reads a version number from the configured package
  index; it sends no client data and no usage payload.
- **No bundle-contract impact.** This feature is orthogonal to `contract/bundle.schema.json`. No
  `schema_version` change, no new bundle shape (cardinal rule, learning 0001).
- **No background daemon / hot-reload.** The app does not hot-restart itself; it shuts down and the
  user (or a future opt-in flag) re-runs `controlplane`.

## Decisions (resolved in brainstorming)

1. **Upgrade model:** detect-and-then **one-click self-upgrade** (a real button), not merely
   detect-and-instruct.
2. **Network egress:** an **opt-in** "check for updates on launch" toggle, **default OFF**. OFF =
   zero egress, exactly as documented. A manual "Check now" is always available on demand.
3. **Execution:** one **install-aware** routine (git-editable → `git pull` + reinstall; pipx →
   `pipx upgrade`; pip → `pip install -U`), exposed as both the dashboard button and a
   `uticen-lite upgrade` CLI command sharing the same code path.
4. **Restart:** after a web-triggered upgrade, **manual re-run** of `controlplane` (no
   auto-relaunch — fragile cross-platform; can be added later).
5. **What's new:** introduce a small `CHANGELOG.md` as the link target.

## Architecture

New package `uticen_lite/upgrade/`, imported **only** by `plane/` and `cli` — never by the
Pyodide-safe core (`model/`, `runner/`, `rules/`). Stdlib + the package's own metadata only; no new
runtime dependency.

```
uticen_lite/upgrade/
  __init__.py      # public API: detect_install, latest_version, current_version,
                   #             build_upgrade_command, spawn_detached_upgrade, read_status
  detect.py        # detect_install() -> InstallMethod
  check.py         # current_version(), latest_version(method, timeout) -> str | None
  version.py       # tiny tuple-based semver compare (no `packaging` dependency)
  run.py           # build_upgrade_command(method) -> list[str]; spawn_detached_upgrade(...)
  _helper.py       # template for the self-contained, stdlib-only detached helper script
```

Each unit has one job and is independently testable:

- **`detect.py` — `detect_install() -> InstallMethod`** (enum: `GIT_EDITABLE`, `PIPX`, `PIP`,
  `UNKNOWN`).
  - `GIT_EDITABLE`: read the dist-info `direct_url.json` for `uticen-lite` via
    `importlib.metadata`; if `dir_info.editable is True` **and** a `.git` directory exists at the
    recorded source tree → `GIT_EDITABLE`.
  - `PIPX`: the package install path is under `**/pipx/venvs/uticen-lite/**`.
  - `PIP`: installed (non-editable) into a normal site-packages.
  - `UNKNOWN`: none of the above (or detection raises) → no self-upgrade; show instructions.

- **`check.py`**
  - `current_version()` → `__version__` / `importlib.metadata.version("uticen-lite")`.
  - `latest_version(method, timeout)` → `str | None`, method-aware:
    - `PIP` / `PIPX`: query the **configured index** (respect `PIP_INDEX_URL` / pip config — a
      corporate mirror, *not* a hardcoded PyPI URL). Primary path: the index JSON API
      (`<index>/uticen-lite/json`); fallback: parse `python -m pip index versions
      uticen-lite`. Short timeout; any failure → `None` ("couldn't check").
    - `GIT_EDITABLE`: `git fetch` then compare `HEAD` to `@{u}`; "latest" is expressed as
      "N commits behind" (+ short SHA), not a PyPI version.
    - `UNKNOWN`: `None` (never reached for the badge).
  - Returns only a version/behind-count. Sends no payload. Never raises into the request path.

- **`version.py`** — a ~10-line `is_newer(latest, current) -> bool` over dotted-integer tuples, so
  we add **no `packaging` dependency** (keeps the core dep-free, learning 0003).

- **`run.py`**
  - `build_upgrade_command(method) -> list[str]`:
    - `GIT_EDITABLE`: a two-step (`git -C <src> pull --ff-only`, then
      `<python> -m pip install -e <src>`); the editable reinstall keeps existing extras.
    - `PIPX`: `pipx upgrade uticen-lite`.
    - `PIP`: `<python> -m pip install -U uticen-lite` (index from the environment/pip config).
  - `spawn_detached_upgrade(method, project_dir, parent_pid)`:
    1. Render `_helper.py` to a **temp file** (self-contained, imports nothing from
       `uticen_lite`, so the package can be replaced under it).
    2. Spawn it **detached** — POSIX `start_new_session=True`; Windows
       `creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` — with stdout/stderr to the log.

- **`_helper.py`** — the detached upgrade runner (written to temp, run with the app's interpreter):
  1. **Wait for `parent_pid` to fully exit** (portable `pid_alive()`: `os.kill(pid, 0)` on POSIX;
     `OpenProcess` via `ctypes` on Windows) with a timeout fallback — so no file the upgrade
     replaces is held open by the running app (the Windows-safe ordering).
  2. Run the upgrade command, streaming output to `<project>/.controlplane-upgrade.log`.
  3. Write a **status file** (`<project>/.controlplane-upgrade.status`: JSON `{ok, from, to, ts}`)
     so the next launch can report the outcome.
  4. **Default: no auto-relaunch** — log "re-run `controlplane` to use the new version."

## Web surface (plane/)

- **`GET /settings/updates`** — a new **Updates** tab in the existing Settings hub
  (`/settings`, alongside `/settings/ai`). Server-rendered sub-route sharing the settings tab nav
  (learning 0007); colors via design tokens (learning 0005). Contents:
  - Current version + detected install method.
  - Toggle **"Check for updates when the app starts"** — **OFF by default**, persisted in the
    existing settings store.
  - A **"Check now"** button (works regardless of the toggle).
- **`POST /settings/updates/check`** — performs the network check now; returns the badge/result
  partial (HTMX swap).
- **Dashboard badge** — an HTMX include in the dashboard header rendering an "Update available:
  vX.Y.Z" callout when (a) the launch check is ON and found a newer version, or (b) a manual check
  just did. The launch check runs **once** (lazily, on first dashboard load after start) and caches
  the result in-memory with a timestamp — never a network call per page load. For `UNKNOWN` /
  air-gapped installs the callout shows the **manual command as text**, no button.
- **`POST /upgrade`** — preceded by a confirm interstitial ("This closes the app and upgrades to
  vX.Y.Z"). On confirm: `spawn_detached_upgrade(...)`, flush the "Upgrading… re-run `controlplane`"
  page, then **shut the app down**. Shutdown is cross-platform: flush the response, then a short
  delayed signal to the app process (graceful `SIGINT` to uvicorn where supported; `os._exit(0)`
  fallback on Windows) — the detached helper is already waiting on this PID.
- **"What's new"** link → the GitHub release for the latest tag (opens in the browser).
- **Post-upgrade notice** — on launch, if a `.controlplane-upgrade.status` exists, the dashboard
  shows "Upgraded to vX.Y.Z" or "Upgrade failed — see `.controlplane-upgrade.log`," then clears it.

## CLI surface

- **`uticen-lite upgrade [--check] [--yes]`** — runs **inline** (no detached dance; there is no server to
  outlive):
  - `--check`: report installed-vs-latest (or "N commits behind" for git) and exit; no changes.
  - bare: detect method, print the command, confirm (skipped with `--yes`), run it, exit.
  - For `GIT_EDITABLE` this is `git pull --ff-only && pip install -e .` — the maintainer's script.

## Safety & security

- **Explicit action + confirm on every path.** No silent or scheduled upgrades.
- **Egress only when opted in or on an explicit "Check now"/`--check`.** The README/`INSTALL.md`
  "zero network egress" line is reworded honestly to "zero network egress *except an opt-in update
  check you can leave off*." Default behavior is unchanged: no calls.
- **Respect the configured index** (corporate mirror) for both the check and the upgrade — never
  hardcode PyPI. The upgrade itself delegates to `pip`/`pipx`, which already honor the user's
  configured indexes; we introduce no new trust path (no new dependency-confusion surface).
- **Short timeouts, graceful degradation** — a slow/unreachable index shows "couldn't check," never
  blocks the dashboard.

## Testing

- `detect_install()` — unit tests per method with faked dist-info `direct_url.json` / install paths
  (monkeypatched `importlib.metadata` + path probes), including `UNKNOWN`.
- `version.is_newer()` — newer / equal / older / malformed.
- `build_upgrade_command()` — assert the exact argv per method.
- `latest_version()` — with an **injected fake fetcher** (no real network in the suite): newer /
  equal / unreachable → badge-logic branches.
- Web: the check + badge partials with the fetcher faked; the `/upgrade` handler's decision branch
  with `spawn_detached_upgrade` mocked (assert it spawns + schedules shutdown, asserts nothing on
  `UNKNOWN`). Optionally a mocked-network "Check now" e2e click (learning 0012 — re-run the browser
  smoke if an HTMX swap restructures a form).
- The **actual detached spawn + restart** is covered by a documented manual verification (honest
  cap, like learning 0012), not an automated test — no test mutates the runtime environment.

## Docs / map / follow-ups

- `docs/INSTALL.md`: new **"Upgrading"** section (button + `uticen-lite upgrade` + the opt-in check + the
  egress reword).
- `PRODUCT-MAP.md`: add a row for the Updates settings + upgrade affordance once shipped.
- `CHANGELOG.md`: introduce it (target for "What's new"; makes version bumps legible).
- After the cycle: capture any durable rule via the compounding-learnings step (e.g. the
  self-replacing-process ordering, or the egress-reword discipline).

## Risks & open implementation details (for the plan to nail)

- **Cross-platform shutdown** from inside a request (graceful `SIGINT` vs `os._exit` fallback) and
  **parent-PID wait** in the helper — the fiddliest parts; the plan pins exact mechanics per OS.
- **`pip index versions`** is marked experimental; the JSON-API path is primary, that parse is the
  fallback. If neither is reliable on a target mirror, the check degrades to "couldn't check" and
  the button still works (the upgrade delegates to pip/pipx regardless).
- **Editable reinstall extras** — `pip install -e .` drops previously-selected extras; the git path
  reinstalls plain `-e .`. Acceptable for the maintainer loop; documented.

## Manual verification (one-time, by hand)

Self-upgrade mutates the environment, so it is verified manually, not in CI:

1. **pip path** — in a throwaway venv, `pip install -e '.[plane]'` is detected as `git-editable`;
   confirm `uticen-lite upgrade --check` reports the version + method. (For a true `pip` test, install a
   built wheel into a plain venv and confirm `pip install -U` is the chosen command.)
2. **Web button** — launch `controlplane`, enable the launch check, force the badge (or use
   "Check now" with a fake newer version), click **Update now**: the app shows the "Upgrading…"
   page and exits; `.controlplane-upgrade.log` records the command; re-running `controlplane`
   shows the one-shot "Upgraded from …" notice, which clears on the next load.
3. **Air-gapped** — with no index reachable, "Check now" degrades to "couldn't check" and the
   dashboard never blocks; `UNKNOWN` installs show manual instructions, never a dead button.
