---
id: 0018
date: 2026-06-22
area: process
tags: [workflow, compound-bridge, auto-merge, learnings, governance]
status: active
supersedes: null
superseded_by: null
---

# In an auto-merge-on-green repo, commit the cycle's learnings to the feature branch BEFORE opening the PR — never after

## Context

This repo auto-merges maintainer PRs on green (PR #18 governance). The compound-bridge loop runs
`compounding-learnings` **after** `finishing-a-development-branch`, and finishing is what opens the PR.
On the control-plane-upgrade cycle (#11), PR #47 was opened, CI went green, and auto-merge **squash-merged
it before** the learnings commit was authored — so the learnings landed on a post-merge commit that was
never part of the PR, stranded on the now-merged branch, and had to be rescued with a redundant
follow-up PR (#48).

## What went wrong

The skill ordering (finish → open PR → capture learnings) assumes the PR waits for a human to merge.
With auto-merge-on-green that assumption is false: the squash lands the instant CI passes, freezing the
PR's contents before the learnings exist.

## The rule

When finishing a development cycle in a repo that **auto-merges PRs on green**, author and commit the
cycle's learnings (and any other post-implementation artifacts) to the feature branch **before** opening
the PR — or, equivalently, before CI can go green and trigger auto-merge. Treat "open the PR" as the
last step of the cycle, not the step before capture. If learnings are nonetheless authored after the PR
has already merged, do not reopen the merged branch: cut a fresh branch from the updated `main` and land
them in a clean follow-up PR (cherry-picking only the learnings commit, since the rest is already on
`main`). Verify "fully merged" by a tree diff (`git diff main..branch` empty), not by `git cherry` —
squash merges defeat patch-id matching.

**Corollary — don't trust `Closes #N` to fire on a squash auto-merge.** After the PR lands, confirm
each issue the PR claimed to close is actually closed (`gh issue list --state open`) and close any
stragglers manually with a reference to the merged PR — do not assume the closing keywords took effect.
On the #55–#64 review-cleanup cycle, PR #65 squash auto-merged to `main` with six `Closes #N` lines in
its body, yet all six issues stayed **open**; they had to be closed by hand. Treat post-merge issue
closure as a verified step, not an automatic side effect.

## Reference

- Governance: PR #18 (auto-merge maintainer PRs on green) and the repo's `.github/workflows`.
- This cycle: PR #47 (feature, squash-merged before learnings) + PR #48 (rescued learnings 0016/0017).
- Closing-keyword miss: PR #65 (squash auto-merge; `Closes #55–#58,#61,#64` did not fire — closed manually).
- Related captures: [[0016]], [[0017]].
