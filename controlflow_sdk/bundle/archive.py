"""Bundle archive writer and reader — zip compression for import/export.

``write_bundle`` packages a manifest dict + rendered workpapers/evidence into
a zip file for transport. ``read_bundle`` extracts and parses the manifest.
"""

from __future__ import annotations

import json
import pathlib
import zipfile
from typing import Any


def write_bundle(
    manifest: dict[str, Any], target_dir: pathlib.Path, out_path: pathlib.Path
) -> pathlib.Path:
    """Write a bundle zip containing manifest.json plus workpapers and evidence.

    Args:
        manifest:   A plain dict conforming to bundle.schema.json.
        target_dir: Directory containing optional subdirs:
                    - workpapers/ (*.md, *.html files copied as-is)
                    - evidence/ (*.json files copied as-is)
        out_path:   Path where the zip will be written.

    Returns:
        The out_path argument (for convenience in pipelines).

    The manifest is pretty-printed with indent=2 and sorted keys so the zip
    is reproducible and human-readable if inspected directly.
    """
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Write manifest.json (pretty-printed, sorted)
        manifest_json = json.dumps(manifest, indent=2, sort_keys=True)
        zf.writestr("manifest.json", manifest_json)

        # Write workpapers if the subdirectory exists
        workpapers_dir = target_dir / "workpapers"
        if workpapers_dir.exists():
            for file_path in workpapers_dir.iterdir():
                if file_path.is_file():
                    arcname = f"workpapers/{file_path.name}"
                    zf.write(file_path, arcname=arcname)

        # Write evidence if the subdirectory exists
        evidence_dir = target_dir / "evidence"
        if evidence_dir.exists():
            for file_path in evidence_dir.iterdir():
                if file_path.is_file():
                    arcname = f"evidence/{file_path.name}"
                    zf.write(file_path, arcname=arcname)

    return out_path


def read_bundle(path: pathlib.Path) -> dict[str, Any]:
    """Read and parse manifest.json from a bundle zip.

    Args:
        path: Path to the bundle zip file.

    Returns:
        The parsed manifest dict.

    Raises:
        FileNotFoundError: If manifest.json is not in the zip.
        json.JSONDecodeError: If manifest.json is not valid JSON.
    """
    with zipfile.ZipFile(path, "r") as zf:
        manifest_json = zf.read("manifest.json").decode("utf-8")
        return json.loads(manifest_json)
