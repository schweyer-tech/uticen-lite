---
id: 0034
date: 2026-06-28
area: process
tags: [debugging, triage, plane, fresh-install, persisted-state]
status: active
supersedes:
superseded_by:
---

# Triage a "broken on my machine, can't reproduce in the dev tree" report by reproducing a genuine clean-venv wheel install + real server FIRST — if fresh passes, the cause is corrupted persisted state; harden the path AND ship recovery, don't patch the one bad row

## Context

The control plane is a **stateful localhost tool**: users run the shipped wheel against their
own `controlplane.db` on their own machine. A user reported "all the Run buttons 500" with
the bundled demo, which a dev-tree run could not reproduce. Two confounders make the dev tree
an unreliable oracle: a dev-tree `TestClient` import resolves to the **editable/main
checkout, not the worktree** ([[0019]]), and it never exercises the **built wheel** ([[0003]]).

## What worked

A genuine fresh install localized the bug in one step: `python -m venv` → `pip install
'<repo>[plane]'` → launch the real `controlplane` server over a fresh project dir → load the
demo → run every control. It passed with zero errors, proving the **distribution is fine** and
the failure lives in **persisted/user state**. Empirically reproducing each corrupt state
(deleted cross-source, ordering-op-on-text, bad regex, missing data file) then drove the fix
([[0033]]).

## The rule

For a stateful local tool, when a "broken for me" report does not reproduce in the dev tree:

1. **Reproduce a genuine fresh install before hypothesizing a code bug** — clean venv, the
   *built wheel* (not an editable install), the *real server* (not a dev-tree `TestClient`),
   over a fresh project dir. Assert an identity/change string so you know which code actually
   ran ([[0019]]).
2. **If fresh install passes, the failure is in persisted/user state.** Treat that as a
   first-class failure mode: make the affected path **degrade gracefully, never crash**
   ([[0033]]) AND ship a **one-click reset/recovery to a known-good state** — do not just fix
   the single corrupt row.
3. **Drive the fix from empirically reproduced corrupt states**, one regression test each —
   not from speculation about what the user "might have done".

## Reference

- Fresh-install repro (clean venv → built wheel → real `controlplane` server → load demo → run all).
- `uticen_lite/store/import_service.py` — `reset_to_demo` (the recovery path).
- Commit d56bf99 (#98). Relates to [[0003]], [[0019]], [[0033]].
