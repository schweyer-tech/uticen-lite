---
id: 0027
date: 2026-06-22
area: process
tags: [code-review, ai, verification, refactor, triage]
status: active
supersedes: null
superseded_by: null
---

# Adversarially verify AI-filed code-review issues against the real repo before acting — they hallucinate paths, names, and counts

## Context

A batch of ten code-quality issues (#55–#64) was auto-filed by an AI code-review tool. Each carried
very specific, confident claims: exact file paths, function names, line numbers, and "appears N times
verbatim" counts. Treated at face value they read as a ready-made backlog. Verified against the actual
codebase, **four of the ten (#59, #60, #62, #63) were largely hallucinated** — they cited files that do
not exist (`store/errors.py`, `bundle/errors.py`, `pipeline/errors.py`, `runner/errors.py`,
`model/source.py`, `rules/compile.py`) and functions that do not exist (`load_engagement`,
`read_project`, `parse_control_def_from_raw`, `record_source`, `upsert_parameter`, …). Of the six that
were real, **two needed correction**: #58 claimed three duplicate sites when there were four, and #64's
cited line numbers/names were wrong even though the underlying `-> Any` problem was real and broader
than described.

## What went wrong / what worked

What worked was running one **adversarial verifier per issue** that ignored the issue's prose and
re-derived every concrete claim from the code (grep the whole repo incl. `tests/` and `examples/` to
prove a function is truly unused; read all cited sites to prove duplication is real; check the claim's
file actually exists). Each returned a structured verdict (VALID / PARTIALLY_VALID / INVALID →
IMPLEMENT / IMPLEMENT_MODIFIED / REJECT) plus the **corrected** scope and the real files touched. That
turned a 10-item list into 6 accept (2 with corrected scope), 4 reject — and the rejects were rejected
on *fact*, not taste. Skipping this step would have sent agents chasing nonexistent `errors.py` files
and renaming functions that do not exist.

## The rule

Never implement an AI-filed review issue from its description. For **every concrete claim** (a named
symbol, a path, a line number, a count, an "unused"/"duplicated" assertion), independently confirm it
against the real repo first, and record the *actual* fact. Judge the corrected finding — not the
issue's — against this repo's values: the only hard contract is the bundle ([[0001]]); the control
plane is brittle-by-design and prizes a minimal surface, so a mass-rename "for consistency" whose cited
functions don't even exist is REJECT, not IMPLEMENT_MODIFIED. Also weigh real value vs real churn:
naming-standardization issues (#59/#63) that propose renames across many call sites + tests earn their
keep only if the *verified* names are genuinely confusing, which here they were not. Fan the
verification out (one skeptic per issue) so the triage is cheap and parallel.

## Reference

- GitHub issues #55–#64 (the batch); accepted: #55, #56, #57, #58, #61, #64. Rejected as
  hallucinated/low-value: #59, #60, #62, #63.
- Verification ran as a fan-out workflow (one `Explore`-typed verifier per issue, structured-output
  verdict schema) before any edit.
- Corollary on an *accepted* issue still needing a contract check: [[0028]].
- Cardinal contract that anchors the accept/reject judgement: [[0001]].
