from uticen_lite.upgrade.detect import InstallMethod, classify_install


def test_editable_with_git_is_git_editable():
    du = {"url": "file:///home/u/repo", "dir_info": {"editable": True}}
    assert classify_install(du, "/home/u/repo/.venv", True) is InstallMethod.GIT_EDITABLE


def test_editable_without_git_is_unknown():
    du = {"url": "file:///home/u/repo", "dir_info": {"editable": True}}
    assert classify_install(du, "/home/u/repo/.venv", False) is InstallMethod.UNKNOWN


def test_pipx_prefix_is_pipx():
    prefix = "/home/u/.local/pipx/venvs/uticen-lite"
    assert classify_install(None, prefix, False) is InstallMethod.PIPX


def test_windows_pipx_prefix_is_pipx():
    prefix = r"C:\Users\u\pipx\venvs\uticen-lite"
    assert classify_install(None, prefix, False) is InstallMethod.PIPX


def test_plain_venv_is_pip():
    assert classify_install(None, "/home/u/project/.venv", False) is InstallMethod.PIP


def test_no_direct_url_is_pip():
    assert classify_install({}, "/usr", False) is InstallMethod.PIP
