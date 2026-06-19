---
id: 0002
date: 2026-06-19
area: backend
tags: [fastapi, sqlite, threading, starlette, plane, web]
status: active
supersedes: null
superseded_by: null
---

# In the control-plane app (FastAPI + sqlite3), open a per-handler connection in async/writing handlers — a sync `Depends` generator runs on a different threadpool thread

## Context

The control plane (`controlflow_sdk/plane/`) is a local FastAPI + Jinja + HTMX app over a single
`sqlite3` connection. A per-request `Depends(get_conn)` generator worked for sync `GET` handlers but
threw on the first `async def` POST: `sqlite3.ProgrammingError: SQLite objects created in a thread
can only be used in that same thread`. FastAPI runs a **sync** dependency generator in its
threadpool, while an `async def` endpoint runs in the **event-loop thread** — the connection is
created in one thread and used in another. Separately, the pre-Starlette-1.3 `TemplateResponse`
signature raised `TypeError: cannot use 'tuple' as a dict key` from the Jinja LRU cache.

## The rule

- **Any `async def` handler — and any handler that does DB writes — opens its OWN connection** in the
  handler body: `conn = connect(root); try: ...; finally: conn.close()`. Do NOT take it from
  `Depends(get_conn)`. Reserve `Depends(get_conn)` for plain `sync def` `GET` handlers (sync endpoint
  + sync dependency share the threadpool thread). A handler that creates and uses the connection in
  its own body guarantees same-thread use.
- **Put the response `return` INSIDE the `try`,** before the `finally`. A `return` placed after the
  `try/finally` that references a variable assigned in the `try` raises `UnboundLocalError` and masks
  the real error when the body throws.
- **Starlette ≥1.3 requires the request-first signature:**
  `templates.TemplateResponse(request, "name.html", {<context without "request">})`. The legacy
  `(name, {"request": request, ...})` form raises `TypeError`.

## Reference

- `controlflow_sdk/plane/app.py` (`get_conn` Depends generator, used only by sync GETs).
- `controlflow_sdk/plane/routes/{sources,controls,runs,export}.py` (async/writing handlers each open
  their own `connect(root)` in `try/finally`; `TemplateResponse(request, ...)` throughout).
