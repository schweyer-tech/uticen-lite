# Product Strategy — uticen-lite

> Durable anchor — keep it short and slow-changing. The SDK is a **sub-product of Uticen**; the
> Uticen app's own `STRATEGY.md` holds the broader product strategy. This doc covers the SDK
> specifically. Last updated: 2026-06-19.

## Core concept (read this first)

`uticen-lite` is **"dbt for controls"**: a standalone, pure-Python tool to author a control test
once, run it over the **entire population** locally, and produce audit-grade evidence + a workpaper +
an **import bundle** the Uticen app consumes 1:1. The hard part it automates is turning a control
+ a messy real-world dataset into a correct, defensible, full-population test — and lowering *who can
do that* from an expert developer toward a GRC analyst, and eventually toward AI.

## Why it exists (the wedge)

Uticen is sold and implemented through consulting. The SDK is the **local authoring surface** of
that wedge: the operator (or the client's analyst) authors and runs full-population tests on-site —
offline, file-first, no platform rollout — then hands a bundle to the SaaS, where the continuous
monitoring loop lives. It meets authors where they are; the heavy CCM platform is not the place to
author. The original motivation for the `controlplane` web app: a CLI + hand-edited YAML was "too hard
to fly in a corporate environment" — so authoring became a pip-install + a local web app.

## The authoring ladder (north-star)

Move test-authoring **up the ladder**: `manual Python → no-code rule builder → AI-assisted →
AI-authored`. The metric that must rise is the **share of tests authored without hand-written
Python**. Today the no-code rule builder covers single-source checks and Python covers the rest;
**AI-assisted authoring is the next rung** (see the prioritized issues).

## The moat

Not "we use AI" — the **encoded dev+GRC know-how** for identifying what to automate and authoring
defensible, full-population tests, captured from real engagements and embodied in the tooling/AI.
Every engagement should compound it.

## The one hard contract

**Stay bundle-compatible with the Uticen app.** `contract/bundle.schema.json` is the single
integration surface; everything else in the SDK can change freely, the bundle shape cannot — without
coordinating both sides (bump `schema_version`, change the SDK schema AND the app's vendored copy
together). **Never put raw population data in the bundle** (trust boundary: definitions + run
provenance only). See `docs/CONTRACT.md` and learning [0001](docs/learnings/0001-stay-compatible-with-the-uticen-app.md).

## Scope / non-goals

- **In scope:** author (metadata + a rule or Python test) · run full-population · render audit-grade
  workpaper + evidence · export the import bundle. File-first local data (CSV / Parquet / Excel).
- **Not the CCM loop.** Exception lifecycle, disposition, self-heal, sign-off, continuous monitoring
  live in the Uticen SaaS — not here. The control plane stops at **author → run → view →
  export**.
- **Not a platform.** Single-user, localhost, **brittle-by-design** (trusts the folder convention;
  the hardened, multi-user, validated experience is paid Uticen). No multi-tenant / auth /
  hosting.
- **Not live connectors.** S3 / Snowflake / REST feeds are the SaaS's job; the SDK is file-first.
- **Not a general data/analytics tool** — purpose-built for control testing, exceptions, evidence.

## Constraints / stack

Pure-Python, ≥3.11; **Pyodide-safe core** (dataclasses + jsonschema; pandas only in `adapters/`) so the
app can run SDK tests in-browser. The control plane adds FastAPI + HTMX + `sqlite3` under the optional
`[plane]` extra. Apache-2.0.

## Current focus

The prioritized GitHub issues are the roadmap. Top three: **usable no-code authoring for non-devs
(#9)**, **AI-assisted authoring (#10)**, **distribution + first-run onboarding (#11)**.
