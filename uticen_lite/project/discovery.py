"""Control discovery and test.py loading for Uticen SDK projects.

Walks a project root for ``controls/*/control.yaml`` files, validates and
parses each one into a :class:`~uticen_lite.model.control.ControlDef`,
resolves source references against the project's ``sources.yaml``, and
provides a loader that imports each control's ``test.py`` and returns the
``test`` callable.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from uticen_lite.model.control import (
    ControlDef,
    FrameworkRefs,
    RiskRef,
    SourceBinding,
    Threshold,
)
from uticen_lite.project.loader import (
    ProjectConfig,
    ProjectError,
    load_project_config,
    load_sources,
)
from uticen_lite.schema.validate import validate_control


def _parse_control(
    doc: dict[str, Any],
    sources_map: dict[str, SourceBinding],
    control_dir: Path,
) -> ControlDef:
    """Build a :class:`ControlDef` from a validated control document.

    Args:
        doc:         Parsed YAML dict for the control.
        sources_map: All project sources keyed by id.
        control_dir: Directory that contains ``control.yaml`` (used to resolve
                     ``test_path`` to an absolute path).

    Returns:
        A fully populated :class:`ControlDef` whose ``test_path`` is an
        absolute path string resolved relative to *control_dir*.

    Raises:
        ProjectError: If a source id referenced in ``sources:`` is not present
                      in *sources_map*.
    """
    # Resolve source references
    resolved_sources: list[SourceBinding] = []
    for src_ref in doc.get("sources", []):
        src_id = src_ref["id"]
        if src_id not in sources_map:
            raise ProjectError(
                f"Control '{doc.get('id')}' references unknown source '{src_id}'. "
                f"Available sources: {sorted(sources_map)}"
            )
        resolved_sources.append(sources_map[src_id])

    # Parse framework_refs — make a copy so we don't mutate the doc
    fr_raw: dict[str, Any] = dict(doc.get("framework_refs") or {})
    nist: list[str] = list(fr_raw.pop("nist", []))
    extra: dict[str, list[str]] = {k: list(v) for k, v in fr_raw.items()}
    framework_refs = FrameworkRefs(nist=nist, extra=extra)

    # Parse optional risk block
    risk_raw = doc.get("risk")
    risk: RiskRef | None = None
    if risk_raw is not None:
        risk = RiskRef(
            name=risk_raw["name"],
            description=risk_raw.get("description", ""),
            inherent_rating=risk_raw.get("inherent_rating"),
        )

    # Parse optional threshold block (pass/fail tolerance).
    threshold = Threshold.from_raw(doc.get("threshold"))

    # Resolve test_path to an absolute path at construction time so that
    # load_test_callable can use it directly without needing the project root.
    test_path: str = str((control_dir / doc.get("test_path", "test.py")).resolve())

    return ControlDef(
        id=doc["id"],
        title=doc["title"],
        objective=doc["objective"],
        narrative=doc.get("narrative", ""),
        framework_refs=framework_refs,
        risk=risk,
        sources=resolved_sources,
        test_path=test_path,
        severity_policy=dict(doc.get("severity_policy") or {}),
        threshold=threshold,
    )


def discover_controls(
    root: Path,
    sources: dict[str, SourceBinding] | None = None,
) -> list[ControlDef]:
    """Walk ``<root>/controls/*/control.yaml`` and return parsed controls.

    Each control is validated against the JSON schema, then its ``sources:``
    entries are resolved against the provided *sources* map (or
    ``<root>/sources.yaml`` if *sources* is omitted).  The ``test_path``
    field is stored as an absolute path resolved relative to each control's
    directory.

    Args:
        root:    Path to the project root directory (must contain a
                 ``controls/`` subtree, and ``sources.yaml`` when *sources*
                 is not provided).
        sources: Optional pre-loaded sources map (keyed by source id).  When
                 supplied, ``sources.yaml`` is **not** re-read — avoids a
                 redundant disk read when the caller (e.g. :meth:`Project.load`)
                 has already loaded sources.

    Returns:
        List of :class:`~uticen_lite.model.control.ControlDef` instances,
        one per discovered ``control.yaml``.

    Raises:
        FileNotFoundError: If ``sources.yaml`` is missing and *sources* is not
                           provided.
        ProjectError:
            - If any ``control.yaml`` fails schema validation.
            - If a control references a source id not defined in the sources map.
    """
    sources_map = sources if sources is not None else load_sources(root)
    controls_root = root / "controls"
    results: list[ControlDef] = []

    if not controls_root.is_dir():
        return results

    for control_yaml in sorted(controls_root.glob("*/control.yaml")):
        with control_yaml.open(encoding="utf-8") as fh:
            doc: dict[str, Any] = yaml.safe_load(fh) or {}

        errors = validate_control(doc)
        if errors:
            msg = f"{control_yaml} failed schema validation:\n" + "\n".join(
                f"  - {e}" for e in errors
            )
            raise ProjectError(msg)

        control_def = _parse_control(doc, sources_map, control_yaml.parent)
        results.append(control_def)

    return results


def load_test_callable(control: ControlDef) -> Callable[..., list[Any]]:
    """Import a control's ``test.py`` and return its ``test`` function.

    The function is imported via :func:`importlib.util.spec_from_file_location`
    so it does **not** need to be on ``sys.path``.  The ``test`` callable is
    returned as-is; it is **not** executed.

    When ``control.test_code`` is set (e.g. a store-backed control), the inline
    source is compiled and executed in a fresh namespace and its ``test``
    callable is returned directly — no file I/O required.  When only
    ``control.test_path`` is set (YAML/file controls), the original file-import
    path is used unchanged.

    Args:
        control: A :class:`~uticen_lite.model.control.ControlDef` whose
                 ``test_code`` (inline source) or ``test_path`` (file path)
                 identifies the test implementation.

    Returns:
        The ``test`` callable from the control's test source.

    Raises:
        ProjectError: If ``test.py`` is missing, if ``test`` is not defined in
                      it, or if ``test`` is not callable.
    """
    # Inline code (control-plane store) takes precedence over a file path.
    inline_code: str | None = getattr(control, "test_code", None)
    if inline_code:
        namespace: dict[str, Any] = {}
        try:
            exec(compile(inline_code, f"<control:{control.id}>", "exec"), namespace)  # noqa: S102
        except SyntaxError as exc:
            raise ProjectError(
                f"control {control.id}: test code has a syntax error: {exc}"
            ) from exc
        fn = namespace.get("test")
        if not callable(fn):
            raise ProjectError(f"control {control.id}: inline test code defines no callable 'test'")
        return fn  # type: ignore[no-any-return]

    test_file = Path(control.test_path)

    if not test_file.exists():
        raise ProjectError(f"Control '{control.id}': test file not found at {test_file}")

    module_name = f"uticen_lite._tests.{control.id}"
    spec = importlib.util.spec_from_file_location(module_name, test_file)
    if spec is None or spec.loader is None:
        raise ProjectError(f"Control '{control.id}': could not create module spec from {test_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    fn = getattr(module, "test", None)
    if fn is None:
        raise ProjectError(f"Control '{control.id}': test.py at {test_file} has no 'test' function")
    if not callable(fn):
        raise ProjectError(
            f"Control '{control.id}': 'test' in {test_file} is not callable "
            f"(got {type(fn).__name__})"
        )
    return fn  # type: ignore[return-value]


@dataclass
class Project:
    """A fully loaded Uticen project.

    Attributes:
        config:   Parsed ``cflow.yaml`` configuration.
        sources:  Data source bindings keyed by source id.
        controls: Discovered and parsed control definitions.
    """

    config: ProjectConfig
    sources: dict[str, SourceBinding]
    controls: list[ControlDef]

    @classmethod
    def load(cls, root: Path) -> Project:
        """Load a complete project from *root*.

        Orchestrates :func:`load_project_config`, :func:`load_sources`, and
        :func:`discover_controls` in order.

        Args:
            root: Path to the project root directory.

        Returns:
            A :class:`Project` instance with all fields populated.

        Raises:
            FileNotFoundError: If ``cflow.yaml`` or ``sources.yaml`` are absent.
            ProjectError: If any validation error is encountered.
        """
        config = load_project_config(root)
        sources = load_sources(root)
        controls = discover_controls(root, sources=sources)
        return cls(config=config, sources=sources, controls=controls)
