from uticen_lite.upgrade.version import is_newer


def test_newer_patch_and_minor():
    assert is_newer("0.2.0", "0.1.0") is True
    assert is_newer("0.1.1", "0.1.0") is True
    assert is_newer("1.0.0", "0.9.9") is True


def test_equal_is_not_newer():
    assert is_newer("0.1.0", "0.1.0") is False


def test_older_is_not_newer():
    assert is_newer("0.1.0", "0.2.0") is False


def test_short_vs_long_and_v_prefix():
    assert is_newer("0.1.0", "0.1") is False  # 0.1.0 == 0.1
    assert is_newer("v0.2.0", "0.1.0") is True  # leading v ignored


def test_malformed_never_raises():
    assert is_newer("abc", "0.1.0") is False  # non-numeric -> 0
    assert is_newer("0.2.0rc1", "0.1.0") is True  # trailing junk on a segment ignored
