# Uticen SDK Contract Policy

## Overview

This document defines the versioning and compatibility contract for the Uticen SDK package and its exported bundle schema. The contract ensures forward compatibility, reproducible audits, and safe evolution across the SDK and the Uticen application.

## Package Versioning (Semantic Versioning)

The SDK package itself follows **Semantic Versioning** (`MAJOR.MINOR.PATCH`):

- **MAJOR** — Incremented on breaking API changes (e.g., renamed public functions, removed exports, incompatible CLI flag changes)
- **MINOR** — Incremented on new features and backward-compatible enhancements
- **PATCH** — Incremented on bug fixes and internal improvements with no API changes

Examples:
- `0.1.0` → `0.2.0` — Add `uticen-lite export` command (new feature)
- `0.1.0` → `0.1.1` — Fix validation error message (bug fix)
- `0.1.0` → `1.0.0` — Remove deprecated `runner` module (breaking change)

## Bundle Schema Versioning

The bundle export contract is versioned independently via the **`schema_version`** field in `contract/bundle.schema.json` and the exported bundle JSON:

```json
{
  "schema_version": "1.0",
  "project": { ... },
  "controls": [ ... ]
}
```

The `schema_version` follows the pattern `MAJOR.MINOR`:

- **MAJOR** — Incremented on any breaking field change (removal, rename, type change, semantic shift)
- **MINOR** — Incremented on additive-only changes (new optional fields, new top-level objects)

Current: `schema_version: "1.0"`

## Compatibility Rules

### Within a MAJOR Version

Within a single `schema_version` MAJOR (e.g., `1.0` → `1.5` → `1.99`), only **additive changes** are permitted:

- ✅ Add a new optional field to an object
- ✅ Add a new top-level key to the bundle
- ✅ Add a new property to `additionalProperties: true` objects
- ✅ Extend an enum with new values (if the consumer uses `default` handling for unknown values)

Violations:

- ❌ Remove an existing field
- ❌ Rename an existing field
- ❌ Change the type of an existing field (e.g., `string` → `number`)
- ❌ Change the semantic meaning of a field (e.g., `duration_seconds` now means milliseconds)
- ❌ Move a field from optional to required (breaks existing bundles)
- ❌ Tighten constraints (e.g., `string` → `string` with `maxLength`)

### Breaking Changes

Any breaking change to the bundle schema **must**:

1. **Increment `schema_version` MAJOR** (e.g., `1.x` → `2.0`)
2. **Require a coordinated release** of both the SDK **and** the Uticen application
3. **Be documented** in `CHANGELOG.md` with migration guidance

Example: Removing the `framework_refs` field from controls would require:
- SDK version `2.0.0` with `schema_version: "2.0"`
- Uticen application updated to validate `schema_version >= "2.0"` and interpret bundles without `framework_refs`

## CI Parity Test: Bundle Schema Export

The SDK includes a test (`tests/test_contract_export.py`) that enforces byte-identical schema export:

```python
def test_contract_is_byte_identical_to_packaged_schema() -> None:
    """contract/bundle.schema.json must match the packaged schema."""
```

This test **gates every commit**: if the canonical schema in `uticen_lite/schema/bundle.schema.json` drifts from `contract/bundle.schema.json`, the build fails and the developer must run `python scripts/export_contract.py` to regenerate the contract file.

**Purpose**: Prevents silent schema drift. The `contract/` folder is the single source of truth for external consumers (the Uticen app) to vendor.

## Uticen App Integration

The Uticen application pins a specific SDK version and bundles the matching schema at that version. The pinning workflow:

### 1. Initial Pinning (Uticen Setup)

When Uticen adopts SDK version `0.1.0`:

```bash
# Download the SDK
pip install uticen-lite==0.1.0

# Vendor the schema into the app (one-time or per-version)
cp node_modules/uticen-lite/contract/bundle.schema.json \
   src/lib/bundle-schema-0.1.0.json

# Record the SDK version and commit SHA for reference
echo "SDK version: 0.1.0" >> docs/SDK_PINNING.md
echo "Commit: abc123def456 (uticen-lite)" >> docs/SDK_PINNING.md
```

### 2. On SDK Patch/Minor Version Updates

If the SDK releases `0.1.1` (patch) or `0.2.0` (minor) **without breaking changes**:

- Verify `schema_version` has not changed (e.g., still `"1.0"`)
- Optionally update the vendored schema (if there are structural improvements)
- Update the pinned version in `package.json` or `pyproject.toml`

### 3. On SDK MAJOR or Schema Breaking Change

If the SDK releases `1.0.0` or increments `schema_version` to `2.0`:

- **New PR required**: Update Uticen to support both old and new schemas
- **Validation change**: Migrate bundle parsing to handle new schema
- **Coordinated release**: Merge both SDK and Uticen changes to `main`, release together
- **Vendor the new schema**: Copy the new `bundle.schema.json` into Uticen
- **Document migration**: Update `CHANGELOG.md` with the app-side changes required

### 4. CI Parity Check (Uticen Side)

The Uticen app CI includes a check that verifies its vendored schema matches the pinned SDK version:

```typescript
// In Uticen CI (e.g., .github/workflows/pr-checks.yml)
it("vendored bundle schema matches SDK version", () => {
  const pinned_version = packageJson.dependencies["uticen-lite"];
  const sdk = require(`uticen-lite@${pinned_version}`);
  const pinned_schema = require(`./src/lib/bundle-schema-${pinned_version}.json`);
  const sdk_schema = require(`uticen-lite@${pinned_version}/contract/bundle.schema.json`);
  
  assert.deepStrictEqual(pinned_schema, sdk_schema, 
    `Vendored schema for SDK ${pinned_version} has drifted. Regenerate with: npm install && cp node_modules/uticen-lite/contract/bundle.schema.json src/lib/bundle-schema-${pinned_version}.json`
  );
});
```

If the check fails, the Uticen build blocks and the developer regenerates the vendored schema.

## Schema Evolution Example

### Scenario: Adding an optional `compliance_mapping` field to controls

**Version `1.0` → `1.1`:**

1. SDK `0.1.x` adds `compliance_mapping: { [framework: string]: string[] }?` to the control $defs
2. `schema_version` remains `"1.0"` (additive change)
3. Uticen app continues parsing bundles without breaking; new bundles have the field and can use it
4. No app code changes required

### Scenario: Removing the `severity` field from violations

**Version `1.x` → `2.0`:**

1. SDK `2.0.0` removes `severity` from the violation $defs
2. `schema_version` increments to `"2.0"`
3. `CHANGELOG.md` documents: "Breaking: removed `violation.severity` field. Apps must migrate to severity-free schemas or implement local post-processing."
4. Uticen app must update:
   - Bundle parser to reject `schema_version < "2.0"` (or accept both with conditional logic)
   - Exception creation to not expect severity from bundles
   - Migration docs for existing bundles
5. Both SDK and app releases are coordinated (e.g., both merge to `main` in a single PR)

## Testing & Validation

### SDK Tests

- **`test_contract_export.py`** — Ensures `contract/bundle.schema.json` is byte-identical to the packaged schema (gates all commits)
- **Schema validation tests** — Verify generated bundles match `schema_version` and all required fields

### Uticen Tests

- **Parity CI check** — Confirms vendored schema matches the pinned SDK version
- **Bundle import tests** — Validate that real bundles round-trip correctly through the import pipeline

## Deprecation & Sunset

Fields or features deprecated within a MAJOR version:

1. **Document in `CHANGELOG.md`** with the deprecation notice and suggested alternatives
2. **Maintain backward compatibility** — deprecated fields/behaviors remain functional
3. **Warn in logs** — SDK may emit warnings when deprecated features are used
4. **Remove in the next MAJOR** — the next `schema_version` MAJOR can remove deprecated fields

Example:

```
## [0.x.0] — (current major)
- **Deprecated**: `run.summary` field will be removed in v2.0. Use `run.details.violations` for pass/fail reasoning.

## [2.0.0] — (future major)
- **Breaking**: Removed `run.summary` field. Bundles from v1.x must be upgraded before import.
```

## Summary

| Aspect | Rule |
|--------|------|
| **Package version** | Semantic versioning (`MAJOR.MINOR.PATCH`) |
| **Schema version** | `MAJOR.MINOR` in bundle JSON |
| **Compatibility within MAJOR** | Additive-only (new optional fields only) |
| **Breaking changes** | Increment schema MAJOR; coordinate with app release |
| **Export test** | Byte-identical `contract/bundle.schema.json` vs. packaged schema (CI gate) |
| **App pinning** | Vendor the schema file; record SDK version + commit SHA |
| **App parity check** | CI verifies vendored schema matches pinned SDK version |
| **Deprecation** | Document in `CHANGELOG.md`; maintain backward compatibility; remove in next MAJOR |

---

**Last updated:** 2026-06-16  
**Current SDK version:** `0.1.0`  
**Current schema_version:** `"1.0"`
