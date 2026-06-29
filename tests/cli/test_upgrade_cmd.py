from uticen_lite.cli import main
from uticen_lite.upgrade.check import UpdateInfo
from uticen_lite.upgrade.detect import InstallMethod


def test_upgrade_check_reports_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(
        "uticen_lite.cli.upgrade_cmd.detect_install", lambda: InstallMethod.PIP
    )
    monkeypatch.setattr(
        "uticen_lite.cli.upgrade_cmd.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    rc = main(["upgrade", "--check"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0.1.0" in out
    assert "0.2.0" in out


def test_upgrade_check_when_up_to_date(monkeypatch, capsys):
    monkeypatch.setattr(
        "uticen_lite.cli.upgrade_cmd.detect_install", lambda: InstallMethod.PIP
    )
    monkeypatch.setattr(
        "uticen_lite.cli.upgrade_cmd.check_for_update",
        lambda method: UpdateInfo(method, "0.2.0", "0.2.0", False, "You're on the latest version."),
    )
    rc = main(["upgrade", "--check"])
    assert rc == 0
    assert "latest" in capsys.readouterr().out.lower()


def test_upgrade_yes_runs_command(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(
        "uticen_lite.cli.upgrade_cmd.detect_install", lambda: InstallMethod.PIP
    )
    monkeypatch.setattr(
        "uticen_lite.cli.upgrade_cmd.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    monkeypatch.setattr(
        "uticen_lite.cli.upgrade_cmd.build_upgrade_command",
        lambda method, source_dir=None: [["pip", "install", "-U", "uticen-lite"]],
    )

    class FakeResult:
        returncode = 0

    monkeypatch.setattr(
        "uticen_lite.cli.upgrade_cmd.subprocess.run",
        lambda cmd: ran.append(cmd) or FakeResult(),
    )
    rc = main(["upgrade", "--yes"])
    assert rc == 0
    assert ran == [["pip", "install", "-U", "uticen-lite"]]


def test_upgrade_unknown_method_is_handled(monkeypatch, capsys):
    monkeypatch.setattr(
        "uticen_lite.cli.upgrade_cmd.detect_install", lambda: InstallMethod.UNKNOWN
    )
    monkeypatch.setattr(
        "uticen_lite.cli.upgrade_cmd.check_for_update",
        lambda method: UpdateInfo(
            method, "0.1.0", None, False, "Automatic upgrade isn't available."
        ),
    )
    rc = main(["upgrade", "--yes"])
    assert rc != 0
    assert "isn't available" in capsys.readouterr().out.lower()
