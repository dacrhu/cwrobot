"""Unit tests for rig_client's RIG_BUSBUSY ("collision on the CAT bus")
retry helper. Pure logic (a fake callable standing in for the actual
ctypes/Hamlib call), no real libhamlib.so needed -- unlike
test_hamlib_dummy.py, this doesn't skip on machines without Hamlib
installed."""

from __future__ import annotations

from cwrobot.hamlib.ctypes_bindings import RIG_BUSBUSY, RIG_OK
from cwrobot.hamlib.rig_client import _BUS_COLLISION_RETRY_ATTEMPTS, _retry_on_bus_collision


def test_retry_on_bus_collision_returns_immediately_on_success(monkeypatch):
    monkeypatch.setattr("cwrobot.hamlib.rig_client.time.sleep", lambda _seconds: None)
    calls = []

    def call():
        calls.append(1)
        return RIG_OK

    assert _retry_on_bus_collision(call) == RIG_OK
    assert len(calls) == 1


def test_retry_on_bus_collision_retries_and_succeeds(monkeypatch):
    monkeypatch.setattr("cwrobot.hamlib.rig_client.time.sleep", lambda _seconds: None)
    results = iter([-RIG_BUSBUSY, -RIG_BUSBUSY, RIG_OK])

    assert _retry_on_bus_collision(lambda: next(results)) == RIG_OK


def test_retry_on_bus_collision_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr("cwrobot.hamlib.rig_client.time.sleep", lambda _seconds: None)
    calls = []

    def call():
        calls.append(1)
        return -RIG_BUSBUSY

    assert _retry_on_bus_collision(call) == -RIG_BUSBUSY
    assert len(calls) == _BUS_COLLISION_RETRY_ATTEMPTS


def test_retry_on_bus_collision_does_not_retry_other_errors(monkeypatch):
    monkeypatch.setattr("cwrobot.hamlib.rig_client.time.sleep", lambda _seconds: None)
    calls = []

    def call():
        calls.append(1)
        return -6  # RIG_EIO -- a real, non-transient failure

    assert _retry_on_bus_collision(call) == -6
    assert len(calls) == 1
