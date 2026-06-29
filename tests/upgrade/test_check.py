from types import SimpleNamespace

from uticen_lite.upgrade.check import (
    UpdateInfo,
    check_for_update,
    current_version,
    latest_version,
)
from uticen_lite.upgrade.detect import InstallMethod


def test_current_version_is_a_string():
    assert isinstance(current_version(), str)
    assert current_version() != ""


def test_latest_version_uses_injected_fetcher():
    assert latest_version(fetch=lambda: "9.9.9") == "9.9.9"
    assert latest_version(fetch=lambda: None) is None


def test_pip_update_available(monkeypatch):
    monkeypatch.setattr(
        "uticen_lite.upgrade.check.current_version", lambda: "0.1.0"
    )
    info = check_for_update(InstallMethod.PIP, fetch=lambda: "0.2.0")
    assert isinstance(info, UpdateInfo)
    assert info.available is True
    assert info.latest == "0.2.0"
    assert "0.2.0" in info.message


def test_pip_up_to_date(monkeypatch):
    monkeypatch.setattr(
        "uticen_lite.upgrade.check.current_version", lambda: "0.2.0"
    )
    info = check_for_update(InstallMethod.PIP, fetch=lambda: "0.2.0")
    assert info.available is False


def test_pip_unreachable_index_degrades(monkeypatch):
    monkeypatch.setattr(
        "uticen_lite.upgrade.check.current_version", lambda: "0.1.0"
    )
    info = check_for_update(InstallMethod.PIP, fetch=lambda: None)
    assert info.available is False
    assert "couldn't" in info.message.lower()


def test_unknown_method_is_not_available(monkeypatch):
    monkeypatch.setattr(
        "uticen_lite.upgrade.check.current_version", lambda: "0.1.0"
    )
    info = check_for_update(InstallMethod.UNKNOWN)
    assert info.available is False


def test_git_behind_uses_injected_runner(monkeypatch):
    monkeypatch.setattr(
        "uticen_lite.upgrade.check.current_version", lambda: "0.1.0"
    )
    monkeypatch.setattr(
        "uticen_lite.upgrade.check.source_dir", lambda: __import__("pathlib").Path(".")
    )

    def fake_git(args):
        if args[:2] == ["git", "rev-list"]:
            return SimpleNamespace(stdout="3\n", returncode=0)
        if args[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(stdout="abc1234\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    info = check_for_update(InstallMethod.GIT_EDITABLE, git_run=fake_git)
    assert info.available is True
    assert "3" in info.message
