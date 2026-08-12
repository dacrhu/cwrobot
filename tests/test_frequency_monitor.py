"""Unit tests for hamlib.frequency_monitor.FrequencyMonitor's pause()
settle-delay behavior. Plain method calls, not routed through Qt's
signal/slot machinery (see main_window for how pause() is actually meant
to be driven -- a BlockingQueuedConnection from the GUI thread) -- no
QApplication/QThread needed for that, only a constructed QObject."""

from __future__ import annotations

from unittest.mock import MagicMock

from cwrobot.hamlib.frequency_monitor import _PORT_SETTLE_S, FrequencyMonitor


def test_pause_sleeps_to_let_hamlib_settle_when_a_connection_was_open(monkeypatch):
    sleeps = []
    monkeypatch.setattr("cwrobot.hamlib.frequency_monitor.time.sleep", sleeps.append)

    monitor = FrequencyMonitor()
    monitor._rig = MagicMock()  # simulate an already-open connection

    monitor.pause()

    assert monitor._paused is True
    assert monitor._rig is None  # closed
    assert sleeps == [_PORT_SETTLE_S]


def test_pause_does_not_sleep_when_nothing_was_open(monkeypatch):
    sleeps = []
    monkeypatch.setattr("cwrobot.hamlib.frequency_monitor.time.sleep", sleeps.append)

    monitor = FrequencyMonitor()
    assert monitor._rig is None  # nothing configured/opened yet

    monitor.pause()

    assert monitor._paused is True
    assert sleeps == []  # nothing to settle from -- no wasted wait
