from cwrobot.updates import is_newer, parse_version


def test_parse_version():
    assert parse_version("v1.0.19") == (1, 0, 19)
    assert parse_version("1.2") == (1, 2)
    assert parse_version("nightly") is None


def test_is_newer():
    assert is_newer("v1.0.20", "1.0.19")
    assert is_newer("v1.1.0", "1.0.19")
    assert not is_newer("v1.0.19", "1.0.19")
    assert not is_newer("v1.0.9", "1.0.19")
    assert not is_newer("garbage", "1.0.19")
