---
id: 0042
date: 2026-06-29
area: frontend
tags: [control-plane, htmx, infinite-loop, header, updates, run-the-real-thing]
status: active
supersedes: null
superseded_by: null
---

# Never re-fetch an element from inside an `htmx:load` handler that swaps that same element — `htmx:load` fires on every swap, so the handler re-triggers itself into an infinite request loop

## Context

The header update-indicator (`#header-update-indicator`) is populated by an htmx swap
(`htmx.ajax("GET", "/updates/indicator", {target, swap:"outerHTML"})`). To keep it fresh,
`base.html` registered `document.addEventListener("htmx:load", refreshHeaderIndicator)`.

## What went wrong

`htmx:load` fires whenever htmx adds content to the DOM — **including the indicator's own
outerHTML swap**. So the listener fetched the indicator → swapped it in → which fired `htmx:load`
→ which fetched it again → … an unbounded loop. It hammered `/updates/indicator` **~4000 requests
in a few seconds** and left the button perpetually detached/re-rendered, so it could never be
clicked (the modal never opened). It was masked twice: the toggle defaults OFF (the empty response
removes the element, self-terminating the loop), and a prior cross-thread 500 (learning 0002) broke
the swap so the loop couldn't sustain — both hid it until the connection bug was fixed AND the
toggle was ON. `TestClient`/unit tests can't see it; only driving the real page (counting requests /
trying to click) revealed it.

## The rule

- Do NOT refresh an element from an `htmx:load` (or `htmx:afterSwap`) handler that itself swaps that
  element — it is self-triggering. An element that lives in the persistent base layout (header,
  nav) is never part of a partial swap, so it needs only a **one-time load on page load** plus an
  explicit **`setInterval` poll**; drop the per-swap refresh entirely.
- If a swap-driven refresh is genuinely required, **guard against self-trigger**: ignore the event
  when `evt.target.closest("#that-element")` matches, or refresh via a plain `fetch`+`innerHTML`
  that does not re-enter htmx's load pipeline.
- Verify event-loop wiring by **driving the real page** and asserting the network-request count
  stays bounded (and the control is clickable) — unit/`TestClient` tests cannot catch a client-side
  htmx loop. Run-the-real-thing kin of [[0040]]/[[0012]].

## Reference

- `uticen_lite/plane/templates/base.html` (the removed `htmx:load` listener; one-time
  `setTimeout` load + 120s `setInterval` check retained).
