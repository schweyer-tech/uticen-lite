import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest


def test_pyproject_declares_plane_extra_and_entry():
    data = tomllib.loads(Path("pyproject.toml").read_text())
    extras = data["project"]["optional-dependencies"]
    assert "plane" in extras
    joined = " ".join(extras["plane"])
    for dep in ("fastapi", "uvicorn", "jinja2", "python-multipart"):
        assert dep in joined
    assert data["project"]["scripts"]["controlplane"] == "uticen_lite.plane.__main__:main"


def test_pyproject_declares_ai_extra():
    data = tomllib.loads(Path("pyproject.toml").read_text())
    extras = data["project"]["optional-dependencies"]
    assert "ai" in extras
    joined = " ".join(extras["ai"])
    assert "anthropic" in joined and "openai" in joined


def test_main_entrypoint_importable():
    from uticen_lite.plane.__main__ import main
    assert callable(main)


def test_ai_module_imports_without_extra():
    # The [ai] SDKs are imported lazily inside the backends; importing the
    # package surface must NOT require anthropic/openai (learning 0003).
    code = (
        "import builtins\n"
        "real = builtins.__import__\n"
        "def blocked(name, *a, **k):\n"
        "    if name == 'anthropic' or name == 'openai' "
        "or name.startswith(('anthropic.', 'openai.')):\n"
        "        raise ImportError(name)\n"
        "    return real(name, *a, **k)\n"
        "builtins.__import__ = blocked\n"
        "import uticen_lite.ai as ai\n"
        "assert ai.available_providers()\n"
        "assert ai.provider_key_present('ollama') is True\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_ai_modules_ship_in_wheel(tmp_path):
    # The AI module is package-internal — hatchling packages=["uticen_lite"]
    # ships it with no force-include needed (learning 0003). Build and inspect.
    try:
        import build  # noqa: F401
    except ImportError:
        pytest.skip("build not installed")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
        check=True, capture_output=True, text=True,
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert wheels, "no wheel built"
    names = set(zipfile.ZipFile(wheels[0]).namelist())
    for mod in ("uticen_lite/ai/__init__.py",
                "uticen_lite/ai/draft.py",
                "uticen_lite/ai/providers.py"):
        assert mod in names, f"{mod} missing from wheel"
