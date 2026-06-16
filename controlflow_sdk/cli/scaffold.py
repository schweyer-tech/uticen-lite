"""Scaffold helpers for the cflow CLI.

All file-creation functions are *non-destructive*: they skip any file that
already exists so that re-running ``cflow init`` or ``cflow new`` is safe.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

_TEMPLATES = files("controlflow_sdk.cli.templates")


def _read_template(name: str) -> str:
    """Return the text of a bundled template file."""
    return _TEMPLATES.joinpath(name).read_text(encoding="utf-8")


def _write_if_absent(path: Path, content: str) -> None:
    """Write *content* to *path* only if the file does not already exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def scaffold_project(root: Path) -> None:
    """Create the skeleton of a new cflow project under *root*.

    Creates (skips if already present):
    - ``cflow.yaml``
    - ``sources.yaml``
    - ``controls/`` directory
    - ``.gitignore``

    Args:
        root: Target project directory (created if it does not exist).
    """
    root.mkdir(parents=True, exist_ok=True)

    _write_if_absent(root / "cflow.yaml", _read_template("cflow.yaml"))
    _write_if_absent(root / "sources.yaml", _read_template("sources.yaml"))
    _write_if_absent(root / ".gitignore", _read_template(".gitignore"))

    controls_dir = root / "controls"
    controls_dir.mkdir(exist_ok=True)


def scaffold_control(root: Path, slug: str) -> None:
    """Scaffold a new control directory under ``<root>/controls/<slug>/``.

    Creates (skips if already present):
    - ``controls/<slug>/control.yaml`` — with ``id`` set to *slug*
    - ``controls/<slug>/test.py``

    Args:
        root: Project root directory.
        slug: Control slug (used as directory name and as the ``id`` value).
    """
    control_dir = root / "controls" / slug
    control_dir.mkdir(parents=True, exist_ok=True)

    control_yaml_content = _read_template("control.yaml").replace("SLUG", slug)
    _write_if_absent(control_dir / "control.yaml", control_yaml_content)

    test_py_content = _read_template("test.py").replace("SLUG", slug)
    _write_if_absent(control_dir / "test.py", test_py_content)
