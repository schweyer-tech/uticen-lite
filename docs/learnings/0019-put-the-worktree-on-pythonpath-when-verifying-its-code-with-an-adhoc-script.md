---
id: 0019
date: 2026-06-22
area: process
tags: [worktree, verification, editable-install, pythonpath, tooling]
status: active
supersedes: null
superseded_by: null
---

# When an ad-hoc script must exercise a worktree's code, put the worktree on PYTHONPATH — `python /abs/script.py` imports the editable-installed main checkout, not the worktree

## Context

Background sessions isolate work in a git worktree (`.claude/worktrees/...`), but the package is
installed **editable** (`pip install -e`) pointing at the **main checkout**. To screenshot a worktree
template change, a standalone render script (`python "$JOB_DIR/tmp/render.py"`) imported
`uticen_lite` — and got the **main checkout's** old templates, rendering zero copy-rows even though
the worktree had them and the worktree tests passed.

## What went wrong

`python /abs/path/script.py` prepends the **script's own directory** to `sys.path`, not the current
working directory — so `import uticen_lite` falls through to the editable install (the main
checkout). `python -m pytest` and `python -m module` did the right thing because `-m` prepends **cwd**,
which (run from the worktree) shadowed the editable install with the worktree's source. The two
invocation styles resolve the package from different trees.

## The rule

When verifying a worktree's changes by **running code** (a render/screenshot/repro script — anything
other than `pytest`/`python -m` launched from the worktree), do not trust that `import <pkg>` resolves
to the worktree: an editable install silently shadows it. Run the script with the worktree root on the
path ahead of site-packages — `PYTHONPATH="$PWD" python /abs/script.py` from the worktree — or invoke
via `python -m` / run the script from a path **inside** the worktree. Confirm the right tree loaded
(e.g. assert a string the change introduced is present) before trusting the output; a clean render of
the *wrong* tree looks identical to a failure to apply the change.

## Reference

- Standard bg-session isolation: `EnterWorktree` → `.claude/worktrees/...`; package installed editable.
- Symptom this cycle: render of `uticen_lite/plane/templates/upgrading.html` showed 0 copy-rows
  until `PYTHONPATH="$PWD"` pointed at the worktree.
- Related: [[0018]] (other process rules for the finishing/verification loop).
