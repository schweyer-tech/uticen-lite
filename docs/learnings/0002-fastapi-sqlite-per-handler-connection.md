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

The control plane (`uticen_lite/plane/`) is a local FastAPI + Jinja + HTMX app over a single
`sqlite3` connection. A per-request `Depends(get_conn)` generator worked for sync `GET` handlers but
threw on the first `async def` POST: `sqlite3.ProgrammingError: SQLite objects created in a thread
can only be used in that same thread`. FastAPI runs a **sync** dependency generator in its
threadpool, while an `async def` endpoint runs in the **event-loop thread** — the connection is
created in one thread and used in another. Separately, the pre-Starlette-1.3 `TemplateResponse`
signature raised `TypeError: cannot use 'tuple' as a dict key` from the Jinja LRU cache.

## Correction (2026-06-29): sync GET + sync dependency do NOT reliably share a thread

The original claim below — that `Depends(get_conn)` is safe for sync `GET`s because the sync endpoint
and sync dependency "share the threadpool thread" — is **FALSE**. AnyIO's threadpool hands each task
whatever worker is free: the dependency *setup* (which calls `connect()`) and the endpoint share a
thread only when the pool has one warm idle thread — i.e. under **sequential** load (dev clicking, and
`TestClient`, which is single-threaded). Under any **concurrency** they land on different threads ~97%
of the time, so `conn.execute()`/`conn.close()` raised the same `ProgrammingError` and **500'd every
`Depends(get_conn)` GET**. The header update-indicator exposed it because it fetches concurrently with
each page load; the suite stayed green because `TestClient` never reproduces it. **Root-cause fix:**
`store/db.connect()` now opens with `check_same_thread=False` (+ a `busy_timeout`), making a
per-request connection thread-agnostic — safe because each request owns its connection and uses it
sequentially, never two threads at once. Pinned by `tests/store/test_db_threading.py`.

## The rule

- **`connect()` opens with `check_same_thread=False`** so a request's connection can be created in the
  dependency-setup threadpool task and used in the (different) endpoint thread without erroring. Never
  assume FastAPI puts a sync dependency and its sync endpoint on the same thread.
- **Any `async def` handler — and any handler that does DB writes — still opens its OWN connection** in
  the handler body: `conn = connect(root); try: ...; finally: conn.close()`. `Depends(get_conn)` is
  acceptable for sync `GET`s now that connections are thread-agnostic.
- **Put the response `return` INSIDE the `try`,** before the `finally`. A `return` placed after the
  `try/finally` that references a variable assigned in the `try` raises `UnboundLocalError` and masks
  the real error when the body throws.
- **Starlette ≥1.3 requires the request-first signature:**
  `templates.TemplateResponse(request, "name.html", {<context without "request">})`. The legacy
  `(name, {"request": request, ...})` form raises `TypeError`.

## Reference

- `uticen_lite/store/db.py` (`connect()` — `check_same_thread=False` + `busy_timeout`).
- `uticen_lite/plane/app.py` (`get_conn` Depends generator).
- `uticen_lite/plane/routes/{sources,controls,runs,export}.py` (async/writing handlers each open
  their own `connect(root)` in `try/finally`; `TemplateResponse(request, ...)` throughout).
- `tests/store/test_db_threading.py` (pins create-in-one-thread, use-in-another).
