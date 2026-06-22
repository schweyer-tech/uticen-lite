"""Bundle assembler: project + run log → validated manifest dict.

``assemble_bundle`` produces the versioned import contract that the ControlFlow
app consumes.  It reads each control's test source from disk, serialises all
definitions, wires in the run history, and validates the result against the
bundle JSON schema.

Trust-boundary rules enforced here:
- No raw population data (``rows``, ``data``, ``data_rows``) in the output.
- No local filesystem paths (``test_path``) in the output; only file contents.
- Sources are emitted as the app ``data_sources`` shape + ``id`` — never rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from controlflow_sdk.rules.resolve import resolve_test_code
from controlflow_sdk.schema import SCHEMA_VERSION
from controlflow_sdk.schema.validate import validate_bundle

if TYPE_CHECKING:
    from controlflow_sdk.model.control import ControlDef
    from controlflow_sdk.project.discovery import Project


class BundleError(Exception):
    """Raised when the assembled manifest fails JSON-Schema validation.

    The message aggregates all schema error strings so callers see the full
    picture in one exception.
    """


def _sort_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Return *d* with keys sorted (top-level only; nested dicts are unchanged)."""
    return dict(sorted(d.items()))


def _serialise_risk(control: ControlDef) -> dict[str, Any] | None:
    """Return a plain-dict risk block, or None if the control has no risk."""
    if control.risk is None:
        return None
    return {
        "description": control.risk.description,
        "inherent_rating": control.risk.inherent_rating,
        "name": control.risk.name,
    }


def _serialise_sources(control: ControlDef) -> list[dict[str, Any]]:
    """Return the sources list in the app ``data_sources`` shape + ``id``.

    Each entry is ``{id, type, key_config, column_mappings}`` — no raw rows.
    """
    result: list[dict[str, Any]] = []
    for src in control.sources:
        entry = {"id": src.id, **src.to_data_source()}
        result.append(_sort_dict(entry))
    return result


def _serialise_framework_refs(control: ControlDef) -> dict[str, Any]:
    return {
        "extra": {k: list(v) for k, v in control.framework_refs.extra.items()},
        "nist": list(control.framework_refs.nist),
    }


def _build_workpaper(
    control: ControlDef,
    test_code: str,
    runs: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    """Build the workpaper dict for a control.

    When runs are available the latest run is used to assemble a ``Procedure``
    (mirrors ``Workpaper.assemble``).  When there are no runs the workpaper has
    an empty ``procedures`` list.
    """
    framework_refs = _serialise_framework_refs(control)

    if not runs:
        return {
            "control_id": control.id,
            "framework_refs": framework_refs,
            "generated_at": generated_at,
            "narrative": control.narrative,
            "objective": control.objective,
            "procedures": [],
            "title": control.title,
        }

    # Use the most recent run (last entry) as the workpaper's canonical result.
    latest_run = runs[-1]
    procedure = {
        "narrative": control.narrative,
        "result": latest_run,
        "test_code": test_code,
        "title": control.title,
    }

    return {
        "control_id": control.id,
        "framework_refs": framework_refs,
        "generated_at": generated_at,
        "narrative": control.narrative,
        "objective": control.objective,
        "procedures": [procedure],
        "title": control.title,
    }


def _build_control_block(
    control: ControlDef,
    runs: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    """Assemble the full control block for the bundle.

    Resolves ``test_code`` via
    :func:`~controlflow_sdk.rules.resolve.resolve_test_code` (inline → rule →
    file content).  The path itself is never included in the output.
    """
    test_code = resolve_test_code(control)

    block: dict[str, Any] = {
        "framework_refs": _serialise_framework_refs(control),
        "id": control.id,
        "narrative": control.narrative,
        "objective": control.objective,
        "risk": _serialise_risk(control),
        "runs": runs,
        "sources": _serialise_sources(control),
        "test_code": test_code,
        "title": control.title,
        "workpaper": _build_workpaper(control, test_code, runs, generated_at),
    }
    return _sort_dict(block)


def _build_project_block(project: Project) -> dict[str, Any]:
    """Build the ``project`` block from ``project.config``.

    The bundle schema expects ``project.system`` to be a **string**.  When the
    project config holds a dict (from ``cflow.yaml``'s ``system:`` mapping), we
    use the ``name`` key if present, otherwise fall back to an empty string.
    """
    block: dict[str, Any] = {"name": project.config.name}

    if project.config.framework:
        block["framework"] = project.config.framework

    system = project.config.system
    if system:
        if isinstance(system, dict):
            block["system"] = system.get("name", "")
        else:
            block["system"] = str(system)

    return _sort_dict(block)


def assemble_bundle(
    project: Project,
    runs_by_control: dict[str, list[dict[str, Any]]],
    generated_at: str,
) -> dict[str, Any]:
    """Build a validated import-manifest dict from a project and its run log.

    Args:
        project:          A fully loaded :class:`~controlflow_sdk.project.Project`.
        runs_by_control:  Run records keyed by control id.  Each value is a list
                          of plain ``RunRecord.to_dict()`` dicts.  Controls absent
                          from this dict receive an empty ``runs`` list.
        generated_at:     ISO-8601 timestamp string supplied by the caller so the
                          output is fully deterministic.

    Returns:
        A plain dict matching ``bundle.schema.json``, with all keys sorted for
        reproducibility and diffability.

    Raises:
        BundleError: If the assembled manifest fails JSON-Schema validation.
                     The error message lists all schema errors.
    """
    controls: list[dict[str, Any]] = []
    for control in project.controls:
        runs = runs_by_control.get(control.id, [])
        controls.append(_build_control_block(control, runs, generated_at))

    manifest: dict[str, Any] = {
        "controls": controls,
        "project": _build_project_block(project),
        "schema_version": SCHEMA_VERSION,
    }
    # Keys already sorted by construction (alphabetical: controls, project, schema_version).
    manifest = _sort_dict(manifest)

    errors = validate_bundle(manifest)
    if errors:
        error_lines = "\n".join(f"  - {e}" for e in errors)
        raise BundleError(f"Bundle failed schema validation:\n{error_lines}")

    return manifest
