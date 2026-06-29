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

## Corollary (2026-06-29) — flipping an egress DEFAULT is the same fan-out as adding the capability

Changing the toggle's **default** from OFF→ON re-falsifies the egress claim in exactly the same set
of places adding the capability did. PR #110 flipped `get_check_updates_on_launch` to default **True**
(so the header indicator shows out of the box) but reworded only the route/repo docstrings + settings
hint — leaving "**zero network egress by default**" stale-and-now-false in `README.md` (×2),
`docs/INSTALL.md` (×2), and `PRODUCT-MAP.md`. A follow-up PR fixed all of them. Rules when you flip an
egress default ON:
- **Re-run the grep.** `grep -rniE "zero[ -]?egress|by default|opt-in|off by default"` across
  `README.md`/`docs/`/`PRODUCT-MAP.md`/source docstrings and reword EVERY copy — same discipline as
  first shipping the capability. "Zero egress by default" is retired; the durable, still-true claim to
  lead with is "**client data never leaves the machine**" (the check fetches only a version number),
  plus "on by default — disable in Settings ▸ Updates for zero egress."
- **Flip the OFF-path tests to set the toggle OFF explicitly.** The call-counter / empty-body tests
  (`test_badge_empty_when_toggle_off`, `test_refresh_indicator_skips_check_when_toggle_off`) relied on
  the OLD default to land in the OFF branch; under default-ON they must call
  `set_check_updates_on_launch(conn, False)` first, or they silently stop exercising the zero-egress
  path. Add a positive `test_default_is_true`.
- **It is a product-direction change** — defaulting egress ON weakens the "zero-egress" positioning;
  route it to `setting-strategy` to reconcile STRATEGY.md, don't just reword copy.

## Reference

- `uticen_lite/plane/routes/updates.py` (`GET /updates/badge` returns empty before any check when
  the toggle is OFF; `POST /settings/updates/check` is the explicit-action path that may egress).
- `uticen_lite/store/repo.py` (`get/set_check_updates_on_launch`, **default True** since PR #110, in
  `project.system`).
- `tests/plane/test_dashboard_upgrade.py::test_badge_empty_when_toggle_off`,
  `tests/plane/test_settings_updates.py::test_refresh_indicator_skips_check_when_toggle_off` (both set
  the toggle OFF then assert the check ran 0×); `tests/store/test_repo_update_setting.py::test_default_is_true`.
- Reworded claim sites: `README.md`, `docs/INSTALL.md`, `PRODUCT-MAP.md`.
