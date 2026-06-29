---
id: 0008
date: 2026-06-20
area: security
tags: [pipeline, custom-code, ast, codegen, export]
status: active
supersedes: null
superseded_by: null
---

# Run untrusted authored code behind three layers — allowlist-AST lint at save, lexical starvation at compile, and a hard re-scan at the export boundary

## Context

The visual pipeline (#25) lets a non-developer attach **Custom Python** nodes that must never read
files or touch `sources` (all cross-source work goes through visible Import/Join nodes). Threat model:
a **guardrail against accidental bypass**, not a sandbox against a malicious local user (who already has
the full Python escape hatch plus a shell). So: light, pure-Python, layered — no subprocess / seccomp /
RestrictedPython / WASM (those fight the offline, brittle-by-design ethos, and the trust boundary that
truly matters — raw population never in the bundle — is enforced at export, see [[0001]]).

## What worked / what went wrong

A naive name deny-list is **not enough**: the adversarial review found two real bypasses that passed
both the save lint and the export gate — `__builtins__['open'](...)` (builtins via subscript) and
`getattr(__builtins__, 'open')(...)`. The working design is three layers:

1. **Allowlist AST lint at save** (`pipeline/lint.py`): allow only a tiny pure import set
   (`re`/`datetime`/`decimal` + the provided helper) and reject everything else — including the
   indirection vectors below.
2. **Lexical starvation at compile** (`pipeline/compile.py`): a Custom node compiles to a module-level
   `def _node_<id>(rows):`. Because `sources` is a *parameter of* `test(pop, sources)`, a module-level
   function **structurally cannot see it**. This is the real teeth, not a lint.
3. **Hard re-scan at the export/build boundary** (`store/export_service.py`): re-run the same scan and
   **refuse to produce the bundle** if any node trips it — enforce where the artifact is *consumed*,
   not only where it is typed.

## The rule

When executing user-authored code in-process, do **not** trust a name deny-list. Use an **allowlist AST
gate that also blocks indirection** — builtins-via-subscript (`__builtins__[...]`), `getattr`/`setattr`,
dunder attribute access, and dynamic import (`importlib`/`__import__`) — or `getattr(__builtins__,'open')`
and `__builtins__['open']` walk straight through. Pair the lint with **lexical starvation** (compile the
code into a scope that structurally lacks the forbidden capability) and **re-run the identical scan as a
hard gate at the trust boundary where the artifact is consumed** (export/build), refusing output on
violation — a hard block, never a warning. Surface the violation as an inline, actionable message that
names the legitimate path (here: "pull data in with an Import node, or convert to a full Python test").

## Reference

- `uticen_lite/pipeline/lint.py` (`_DenyScanner`, `lint_custom_code`, `lint_pipeline`).
- `uticen_lite/pipeline/compile.py` (module-level `_node_<id>` emission = lexical starvation).
- `uticen_lite/store/export_service.py` (hard export gate; raises `LintError`).
- `tests/pipeline/test_lint.py`, `tests/store/test_export_gate.py`.
- The trust boundary it complements: [[0001]].
