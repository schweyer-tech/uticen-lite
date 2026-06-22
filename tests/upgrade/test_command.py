import pytest

from controlflow_sdk.upgrade.command import build_upgrade_command
from controlflow_sdk.upgrade.detect import InstallMethod


def test_pip_command():
    cmds = build_upgrade_command(InstallMethod.PIP, python="/py")
    assert cmds == [["/py", "-m", "pip", "install", "-U", "controlflow-sdk"]]


def test_pipx_command():
    cmds = build_upgrade_command(InstallMethod.PIPX)
    assert cmds == [["pipx", "upgrade", "controlflow-sdk"]]


def test_git_command_is_two_steps():
    cmds = build_upgrade_command(
        InstallMethod.GIT_EDITABLE, python="/py", source_dir="/repo"
    )
    assert cmds == [
        ["git", "-C", "/repo", "pull", "--ff-only"],
        ["/py", "-m", "pip", "install", "-e", "/repo"],
    ]


def test_git_without_source_dir_raises():
    with pytest.raises(ValueError):
        build_upgrade_command(InstallMethod.GIT_EDITABLE, source_dir=None)


def test_unknown_raises():
    with pytest.raises(ValueError):
        build_upgrade_command(InstallMethod.UNKNOWN)
