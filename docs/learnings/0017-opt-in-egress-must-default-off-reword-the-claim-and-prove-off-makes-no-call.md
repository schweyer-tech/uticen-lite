---
id: 0017
date: 2026-06-22
area: security
tags: [security, egress, opt-in, docs, testing, plane]
status: active
supersedes: null
superseded_by: null
---

# Any new network-reaching capability defaults OFF, rewords the egress claim everywhere, and proves the OFF path makes zero network calls with a call-counter test

## Context

The control plane's headline guarantee (README + `docs/INSTALL.md`) is "localhost-only with **zero
network egress** … never makes outbound connections, so client data never leaves the machine" — the
trust claim it sells against the hosted SaaS. The opt-in update check (#11) is the first feature that
reaches the network at all (it asks the configured package index for the latest version). A background
"vX.Y.Z available" badge silently punches a hole in that exact sentence.

## What worked

The check is gated behind a **default-OFF** toggle persisted in `project.system`. OFF means the badge
route returns empty **before any network path** — no call. The egress claim was reworded honestly in
**every** place it appears (two in README, one in `docs/INSTALL.md`, the design-principles bullet, the
settings hint): "zero network egress **by default** … except an **opt-in** update check you can leave
off." The OFF invariant is proved by a **call-counter test**: a spy on the network function asserts it
ran **0 times** when the toggle is OFF (`test_badge_empty_when_toggle_off`), not merely that the badge
was empty. Explicit user actions ("Check now", `uticen-lite upgrade --check`) may reach the network; only the
proactive/background path is toggle-gated.

## The rule

When adding any capability that reaches the network to a tool whose security story is "zero egress":
(1) gate it behind a setting that **defaults OFF**, so untouched behavior is unchanged; (2) reword
**every** copy of the egress claim to "zero egress **by default** … except `<named opt-in>`" — grep for
the phrase and fix all of them, never leave one absolute; (3) any **proactive/background** path must
read the toggle and return **before** touching the network when OFF; (4) prove it with a **call-counter
test** that asserts the network function was invoked **zero times** while OFF (an empty-output assertion
is not enough — it can pass while still making the call). Explicit, user-initiated checks may egress;
silent ones may not.

## Reference

- `uticen_lite/plane/routes/updates.py` (`GET /updates/badge` returns empty before any check when
  the toggle is OFF; `POST /settings/updates/check` is the explicit-action path that may egress).
- `uticen_lite/store/repo.py` (`get/set_check_updates_on_launch`, default False, in `project.system`).
- `tests/plane/test_dashboard_upgrade.py::test_badge_empty_when_toggle_off` (asserts the check ran 0×).
- Reworded claim: `README.md`, `docs/INSTALL.md`.
