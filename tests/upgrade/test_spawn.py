import json

from controlflow_sdk.upgrade import spawn


def test_status_roundtrip_then_clears(tmp_path):
    spawn.write_status(tmp_path, {"ok": True, "from": "0.1.0"})
    assert spawn.read_status(tmp_path) == {"ok": True, "from": "0.1.0"}
    # read is one-shot — the file is gone, so a second read is None
    assert spawn.read_status(tmp_path) is None


def test_read_status_missing_is_none(tmp_path):
    assert spawn.read_status(tmp_path) is None


def test_helper_source_is_self_contained():
    # The detached helper must not import controlflow_sdk (the package may be
    # replaced under it) and must be valid Python.
    assert "import controlflow_sdk" not in spawn._HELPER_SOURCE
    compile(spawn._HELPER_SOURCE, "<helper>", "exec")


def test_spawn_writes_helper_and_invokes_popen(tmp_path):
    calls = {}

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return object()

    commands = [["pipx", "upgrade", "controlflow-sdk"]]
    helper = spawn.spawn_detached_upgrade(
        tmp_path,
        commands,
        current="0.1.0",
        restart_command=["python", "-m", "controlflow_sdk.plane", "--project", str(tmp_path)],
        popen=fake_popen,
    )
    assert helper.exists()
    # argv[0] is the interpreter; argv[1] is the helper; argv[2] is JSON config.
    assert calls["argv"][1] == str(helper)
    cfg = json.loads(calls["argv"][2])
    assert cfg["commands"] == commands
    assert cfg["restart_command"] == [
        "python", "-m", "controlflow_sdk.plane", "--project", str(tmp_path)
    ]
    assert cfg["from"] == "0.1.0"
    assert cfg["status"].endswith(spawn.STATUS_FILE)


def test_schedule_shutdown_uses_injected_timer():
    fired = {}

    class FakeTimer:
        def __init__(self, delay, fn):
            fired["delay"] = delay
            fired["fn"] = fn

        def start(self):
            fired["started"] = True

    spawn.schedule_shutdown(0.3, timer=FakeTimer)
    assert fired["delay"] == 0.3
    assert fired["started"] is True
