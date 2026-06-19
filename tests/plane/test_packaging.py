import tomllib
from pathlib import Path


def test_pyproject_declares_plane_extra_and_entry():
    data = tomllib.loads(Path("pyproject.toml").read_text())
    extras = data["project"]["optional-dependencies"]
    assert "plane" in extras
    joined = " ".join(extras["plane"])
    for dep in ("fastapi", "uvicorn", "jinja2", "python-multipart"):
        assert dep in joined
    assert data["project"]["scripts"]["controlplane"] == "controlflow_sdk.plane.__main__:main"


def test_main_entrypoint_importable():
    from controlflow_sdk.plane.__main__ import main
    assert callable(main)
