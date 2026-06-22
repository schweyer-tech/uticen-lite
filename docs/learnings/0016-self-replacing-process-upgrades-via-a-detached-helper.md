---
id: 0016
date: 2026-06-22
area: backend
tags: [upgrade, subprocess, self-update, packaging, plane, cli]
status: active
supersedes: null
superseded_by: null
---

# A long-running process upgrading its own package must do it from a detached, self-contained helper that waits for the process to exit before mutating files

## Context

The control plane (#11) gained a one-click self-upgrade: a localhost FastAPI app that reinstalls its
own package (`pip`/`pipx`/editable) and restarts. A process cannot reinstall the code it is currently
running: the interpreter holds package files open, the running app imports the modules being replaced,
and on Windows replacing in-use files fails outright. An in-process `pip install` followed by a
self-restart half-applies and bricks the app — the one surface that could fix it (the UI) is what went
down.

## What worked

The web route spawns a **detached** helper (`start_new_session=True` on POSIX; `DETACHED_PROCESS |
CREATE_NEW_PROCESS_GROUP` on Windows), flushes its "Upgrading…" response, then shuts the app down
(`SIGINT` to uvicorn, `os._exit(0)` fallback). The helper is written to a temp file as a
**self-contained, stdlib-only script that imports nothing from the package being replaced** — so the
package can be swapped under it — and it **waits for the parent PID to fully exit** (`os.kill(pid, 0)`
poll with a deadline backstop) before running the upgrade, logging to a file and writing a **one-shot
status file** the next launch reads then deletes. The CLI (`cflow upgrade`) runs the same command
*inline* (no server to outlive, so no detach needed). `spawn`/`timer`/`popen` are injectable so tests
never spawn a real process or kill the runner.

## The rule

When a long-running process must upgrade the package it is itself running: never reinstall in-process.
Spawn a **detached, self-contained, stdlib-only helper** (no import of the package under replacement),
have it **wait for the parent process to fully exit before mutating any files**, and surface the
outcome through a **one-shot status file** read on next launch — the app flushes its response and shuts
itself down rather than hot-restarting. Drive everything through an explicit, confirmed user action
(no auto-upgrade). Make the spawn and the self-shutdown injectable (`popen`/`timer`) so the test suite
proves the wiring without spawning or killing anything. A short-lived CLI invoking the same upgrade can
run inline — the detach dance is only for the process that must outlive its own replacement.

## Reference

- `controlflow_sdk/upgrade/spawn.py` (`_HELPER_SOURCE` stdlib-only helper; `spawn_detached_upgrade`,
  `schedule_shutdown`, one-shot `read_status`).
- `controlflow_sdk/upgrade/detect.py` + `command.py` (install-method-aware command builder).
- `controlflow_sdk/plane/routes/updates.py` (`POST /upgrade` flushes the page, spawns, schedules
  shutdown) and `controlflow_sdk/cli/upgrade_cmd.py` (inline path).
- `tests/upgrade/test_spawn.py` (helper compiles + imports no `controlflow_sdk`; popen/timer injected).
- Manual-verification checklist: `docs/superpowers/specs/2026-06-22-control-plane-upgrade-design.md`.
