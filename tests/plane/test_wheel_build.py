"""Build the wheel and inspect it (learning 0003's "build, don't parse" half).

`tests/plane/test_packaging.py` only parses ``pyproject.toml``. These tests build
the real distribution wheel once (module-scoped) and assert the shipped contents:
the web assets + the bundled Northwind demo are inside, the example ``target/``
(which could hold run output) is NOT, and no compiled deps leak into the RECORD.

The optional clean-venv install test (``CFLOW_WHEEL_VENV_TEST=1``) proves the
packaged ``_demo/`` path resolves from OUTSIDE the repo, so the repo-path fallback
in ``demo_source_dir()`` can't mask a packaging gap.
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]  # repo root


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pytest.importorskip("build")
    out = tmp_path_factory.mktemp("dist")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return next(out.glob("*.whl"))


def _names(whl: Path) -> list[str]:
    with zipfile.ZipFile(whl) as z:
        return z.namelist()


def test_wheel_ships_web_assets(built_wheel: Path) -> None:
    names = _names(built_wheel)
    for asset in (
        "controlflow_sdk/plane/static/app.css",
        "controlflow_sdk/plane/static/htmx.min.js",
        "controlflow_sdk/plane/templates/base.html",
        "controlflow_sdk/plane/templates/setup.html",
        "controlflow_sdk/plane/templates/partials/rule_builder.html",
    ):
        assert asset in names


def test_wheel_ships_bundled_demo(built_wheel: Path) -> None:
    names = _names(built_wheel)
    demo = "controlflow_sdk/_demo/northwind-trading/"
    csvs = [n for n in names if n.startswith(demo + "data/") and n.endswith(".csv")]
    ctrls = [
        n
        for n in names
        if n.startswith(demo + "controls/") and n.endswith("control.yaml")
    ]
    assert len(csvs) == 9
    assert len(ctrls) == 9
    assert demo + "cflow.yaml" in names
    assert demo + "sources.yaml" in names


def test_wheel_excludes_example_target_dir(built_wheel: Path) -> None:
    # target/ workpapers/evidence are gitignored in the example and must not ship.
    assert not any("/target/" in n for n in _names(built_wheel))


def test_wheel_has_no_compiled_deps_in_record(built_wheel: Path) -> None:
    # Mirror the release.yml Pyodide-safe guard at unit level.
    with zipfile.ZipFile(built_wheel) as z:
        record = z.read(
            next(n for n in z.namelist() if n.endswith("/RECORD"))
        ).decode()
    assert not [line for line in record.splitlines() if "pydantic" in line.lower()]


@pytest.mark.skipif(
    os.environ.get("CFLOW_WHEEL_VENV_TEST") != "1",
    reason="slow/network: set CFLOW_WHEEL_VENV_TEST=1 to run the clean-venv install proof",
)
def test_clean_venv_install_resolves_packaged_demo(
    built_wheel: Path, tmp_path: Path
) -> None:
    # Prove the PACKAGED _demo/ path resolves, not the repo fallback: install the
    # wheel into a fresh venv and resolve demo_source_dir() from a cwd OUTSIDE the repo.
    venv = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    pip = venv / "bin" / "pip"
    py = venv / "bin" / "python"
    subprocess.run(
        [str(pip), "install", f"{built_wheel}[plane]"],
        check=True,
        capture_output=True,
        text=True,
    )
    proc = subprocess.run(
        [
            str(py),
            "-c",
            "from controlflow_sdk.store.import_service import demo_source_dir;"
            "p=str(demo_source_dir());"
            "assert '_demo' in p, p;"
            "print(p)",
        ],
        cwd=tmp_path,  # outside the repo
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "_demo" in proc.stdout
