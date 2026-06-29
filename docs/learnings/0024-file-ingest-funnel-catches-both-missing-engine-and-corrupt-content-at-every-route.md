---
id: 0024
date: 2026-06-22
area: backend
tags: [error-handling, never-500, ingest, optional-deps, plane]
status: active
supersedes:
superseded_by:
---

# A user-file ingest funnel must convert BOTH "optional engine absent" AND "content corrupt/unreadable" into typed friendly errors — at every route that reads a file, not just create

## Context
The control plane ingests user-uploaded/fetched files through one funnel
(`plane/ingest.py::extract_table`). Two independent failure classes exist: the optional
`[adapters]` engine (openpyxl/pyarrow) being absent, and the file content being corrupt or
the wrong type (`zipfile.BadZipFile` for a bad `.xlsx`, `pyarrow.ArrowInvalid` for a bad
`.parquet`, `UnicodeDecodeError` for non-UTF-8 CSV). The product promises "never a 500".

## What went wrong
The first pass handled only the missing-engine case (`AdaptersUnavailable`). Corrupt
content stayed uncaught and produced HTTP 500s on upload, URL-create, preview, and
re-fetch. The preview route also originally lacked even the `AdaptersUnavailable` catch.

## The rule
At the single read funnel, raise **two typed errors**: a dependency-missing error and a
parse/corruption error (`TableParseError`). Order matters — catch `ImportError`
(→ dependency-missing) **before** a broad `except Exception` (→ parse error), or a missing
engine gets mislabeled as a corrupt file. Scope the broad `except` to the read call only,
and re-raise a typed error (never return empty/None silently — that is a swallowed failure
[[0008]] would also flag). Then catch **both** typed errors at **every** entry point that
reaches the funnel — create, preview, URL-create, re-fetch, **and** refresh — and re-render
a friendly page at HTTP 200. A test that only covers the missing-engine path does not prove
the corrupt-content path; add a corrupt-bytes test per format.

## Reference
- `uticen_lite/plane/ingest.py` (`extract_table`, `AdaptersUnavailable`, `TableParseError`; ImportError-before-Exception ordering)
- `uticen_lite/plane/routes/sources.py` (`create_source`, `source_data`, `create_source_from_url`, `refetch_source`, `refresh_source` all catch both)
- `tests/plane/test_sources_multiformat.py`, `tests/plane/test_ingest.py` (corrupt-file + missing-engine tests)
</content>
