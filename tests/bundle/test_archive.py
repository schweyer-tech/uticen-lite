"""TDD tests for controlflow_sdk.bundle.archive (Phase 3, Task 3).

Red → Green cycle:
  1. Write tests (RED – archive module does not exist yet).
  2. Implement archive.py.
  3. Tests turn GREEN.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

from controlflow_sdk.bundle import read_bundle, write_bundle

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_write_bundle_creates_zip_with_manifest() -> None:
    """write_bundle creates a zip file containing manifest.json."""
    manifest = {
        "controls": [],
        "project": {"name": "Test Project"},
        "schema_version": "1.0.0",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = pathlib.Path(tmpdir) / "target"
        target_dir.mkdir()
        out_path = pathlib.Path(tmpdir) / "bundle.zip"

        result = write_bundle(manifest, target_dir, out_path)

        assert result == out_path
        assert out_path.exists()
        assert out_path.is_file()


def test_write_bundle_includes_manifest_json() -> None:
    """write_bundle includes manifest.json in the zip."""
    manifest = {
        "controls": [],
        "project": {"name": "Test Project"},
        "schema_version": "1.0.0",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = pathlib.Path(tmpdir) / "target"
        target_dir.mkdir()
        out_path = pathlib.Path(tmpdir) / "bundle.zip"

        write_bundle(manifest, target_dir, out_path)

        # Verify manifest.json is in the zip
        import zipfile

        with zipfile.ZipFile(out_path, "r") as zf:
            namelist = zf.namelist()
            assert "manifest.json" in namelist


def test_write_bundle_manifest_is_pretty_sorted() -> None:
    """write_bundle stores manifest.json with indent=2 and sorted keys."""
    manifest = {
        "schema_version": "1.0.0",
        "project": {"name": "Test Project"},
        "controls": [],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = pathlib.Path(tmpdir) / "target"
        target_dir.mkdir()
        out_path = pathlib.Path(tmpdir) / "bundle.zip"

        write_bundle(manifest, target_dir, out_path)

        import zipfile

        with zipfile.ZipFile(out_path, "r") as zf:
            manifest_json = zf.read("manifest.json").decode("utf-8")
            # Verify it's pretty-printed (has indentation)
            assert "\n" in manifest_json
            assert "  " in manifest_json  # Has 2-space indent
            # Verify keys are sorted (project before schema_version, controls first)
            assert manifest_json.index('"controls"') < manifest_json.index('"project"')
            assert manifest_json.index('"project"') < manifest_json.index('"schema_version"')


def test_write_bundle_includes_workpapers() -> None:
    """write_bundle copies workpapers/*.html from target_dir."""
    manifest = {
        "controls": [],
        "project": {"name": "Test Project"},
        "schema_version": "1.0.0",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = pathlib.Path(tmpdir) / "target"
        target_dir.mkdir()
        workpapers_dir = target_dir / "workpapers"
        workpapers_dir.mkdir()

        # Create sample workpaper files
        (workpapers_dir / "control_001.html").write_text("<html>Control 001</html>")
        (workpapers_dir / "control_002.md").write_text("# Control 002")

        out_path = pathlib.Path(tmpdir) / "bundle.zip"
        write_bundle(manifest, target_dir, out_path)

        import zipfile

        with zipfile.ZipFile(out_path, "r") as zf:
            namelist = zf.namelist()
            assert "workpapers/control_001.html" in namelist
            assert "workpapers/control_002.md" in namelist
            # Verify content
            content = zf.read("workpapers/control_001.html").decode("utf-8")
            assert content == "<html>Control 001</html>"


def test_write_bundle_includes_evidence() -> None:
    """write_bundle copies evidence/*.json from target_dir."""
    manifest = {
        "controls": [],
        "project": {"name": "Test Project"},
        "schema_version": "1.0.0",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = pathlib.Path(tmpdir) / "target"
        target_dir.mkdir()
        evidence_dir = target_dir / "evidence"
        evidence_dir.mkdir()

        # Create sample evidence files
        evidence_data = {"run_id": "run-001", "status": "passed"}
        (evidence_dir / "run_001.json").write_text(json.dumps(evidence_data))

        out_path = pathlib.Path(tmpdir) / "bundle.zip"
        write_bundle(manifest, target_dir, out_path)

        import zipfile

        with zipfile.ZipFile(out_path, "r") as zf:
            namelist = zf.namelist()
            assert "evidence/run_001.json" in namelist
            # Verify content
            read_data = json.loads(zf.read("evidence/run_001.json").decode("utf-8"))
            assert read_data == evidence_data


def test_write_bundle_without_subdirs() -> None:
    """write_bundle works even when target_dir has no workpapers/ or evidence/ subdirs."""
    manifest = {
        "controls": [],
        "project": {"name": "Test Project"},
        "schema_version": "1.0.0",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = pathlib.Path(tmpdir) / "target"
        target_dir.mkdir()
        # Don't create workpapers/ or evidence/ subdirs

        out_path = pathlib.Path(tmpdir) / "bundle.zip"
        write_bundle(manifest, target_dir, out_path)

        # Should succeed and contain only manifest.json
        import zipfile

        with zipfile.ZipFile(out_path, "r") as zf:
            namelist = zf.namelist()
            assert namelist == ["manifest.json"]


def test_read_bundle_returns_manifest_dict() -> None:
    """read_bundle reads manifest.json and returns the parsed dict."""
    manifest = {
        "controls": [],
        "project": {"name": "Test Project"},
        "schema_version": "1.0.0",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = pathlib.Path(tmpdir) / "target"
        target_dir.mkdir()
        out_path = pathlib.Path(tmpdir) / "bundle.zip"

        write_bundle(manifest, target_dir, out_path)
        read_manifest = read_bundle(out_path)

        assert read_manifest == manifest


def test_read_bundle_round_trip_with_files() -> None:
    """read_bundle successfully round-trips after write_bundle with workpapers."""
    manifest = {
        "controls": [
            {
                "id": "ctrl-001",
                "title": "Test Control",
                "objective": "Test objective",
                "narrative": "Test narrative",
                "test_code": "def test(): pass",
                "framework_refs": {"nist": ["AC-1"], "extra": {}},
                "risk": None,
                "runs": [],
                "sources": [],
                "workpaper": {
                    "control_id": "ctrl-001",
                    "title": "Test Control",
                    "objective": "Test objective",
                    "narrative": "Test narrative",
                    "framework_refs": {"nist": ["AC-1"], "extra": {}},
                    "generated_at": "2026-06-16T00:00:00Z",
                    "procedures": [],
                },
            }
        ],
        "project": {"name": "Test Project"},
        "schema_version": "1.0.0",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = pathlib.Path(tmpdir) / "target"
        target_dir.mkdir()
        workpapers_dir = target_dir / "workpapers"
        workpapers_dir.mkdir()
        (workpapers_dir / "ctrl_001.html").write_text("<html>Test</html>")

        out_path = pathlib.Path(tmpdir) / "bundle.zip"
        write_bundle(manifest, target_dir, out_path)
        read_manifest = read_bundle(out_path)

        assert read_manifest == manifest


def test_write_bundle_returns_out_path() -> None:
    """write_bundle returns the out_path argument."""
    manifest = {
        "controls": [],
        "project": {"name": "Test Project"},
        "schema_version": "1.0.0",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = pathlib.Path(tmpdir) / "target"
        target_dir.mkdir()
        out_path = pathlib.Path(tmpdir) / "bundle.zip"

        result = write_bundle(manifest, target_dir, out_path)

        assert result is out_path
