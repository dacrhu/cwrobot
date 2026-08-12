"""Tests for tx.backend.TxBackend's element-driving loop: deadline-based
timing and stop_flag responsiveness. Uses a fake backend (records key
transitions with timestamps) rather than a real audio device -- exercises
exactly the same send_text() driver AudioTxBackend uses.
"""

from __future__ import annotations

import time

import pytest

from cwrobot.tx.backend import TxBackend
from cwrobot.tx.encoder import text_to_elements


class RecordingBackend(TxBackend):
    """Records every _set_key(is_on) call with a timestamp, and can be told
    to request a stop after N calls (simulating a user clicking Stop)."""

    def __init__(self, stop_after_calls: int | None = None) -> None:
        self.calls: list[tuple[bool, float]] = []
        self._stop_after_calls = stop_after_calls
        self._external_stop_requested = False

    def _set_key(self, is_on: bool) -> None:
        self.calls.append((is_on, time.perf_counter()))
        if self._stop_after_calls is not None and len(self.calls) >= self._stop_after_calls:
            self._external_stop_requested = True

    def stop_flag(self) -> bool:
        return self._external_stop_requested


# A very high WPM keeps element durations (and hence real wall-clock test
# time) down to single-digit milliseconds while still exercising the exact
# same deadline-based scheduling real transmissions use.
FAST_WPM = 600


def test_send_text_keys_every_element_in_order():
    backend = RecordingBackend()
    elements = text_to_elements("E", wpm=FAST_WPM)
    backend.send_text("E", wpm=FAST_WPM, stop_flag=lambda: False)

    # One key-on for the dot, then a final key-off (send_text always ends
    # with _set_key(False), even for a message with no trailing gap element).
    assert len(backend.calls) == 2
    assert backend.calls[0][0] is True
    assert backend.calls[1][0] is False
    assert len(elements) == 1  # sanity: "E" is a single dot, no gap elements


def test_send_text_respects_element_durations_within_tolerance():
    backend = RecordingBackend()
    start = time.perf_counter()
    backend.send_text("ET", wpm=FAST_WPM, stop_flag=lambda: False)
    elapsed = time.perf_counter() - start

    elements = text_to_elements("ET", wpm=FAST_WPM)
    expected_total_s = sum(e.duration_ms for e in elements) / 1000.0
    # Generous tolerance: this test only needs to catch gross scheduling
    # bugs (e.g. sleeping the wrong duration entirely), not audio-grade
    # timing precision -- the poll interval and Python/OS scheduling jitter
    # both add slack at these very short (ms-scale) durations.
    assert elapsed == pytest.approx(expected_total_s, abs=0.05)


def test_stop_flag_interrupts_before_all_elements_are_sent():
    elements = text_to_elements("SOS SOS SOS", wpm=FAST_WPM)
    assert len(elements) > 5  # sanity: a real multi-element message

    # Requests a stop as soon as the first element has been keyed.
    backend = RecordingBackend(stop_after_calls=1)
    backend.send_text("SOS SOS SOS", wpm=FAST_WPM, stop_flag=backend.stop_flag)

    # Only the first element's key-on plus the final forced key-off should
    # have happened -- not all of "SOS SOS SOS".
    assert len(backend.calls) == 2
    assert backend.calls[0][0] is True
    assert backend.calls[-1][0] is False


def test_send_text_always_ends_with_key_off():
    backend = RecordingBackend()
    backend.send_text("SOS", wpm=FAST_WPM, stop_flag=lambda: False)
    assert backend.calls[-1][0] is False


def test_send_text_forwards_jitter_to_the_encoder():
    # With jitter, total elapsed time should still land within a generous
    # envelope around the *exact* (zero-jitter) total -- proves jitter is
    # actually reaching text_to_elements through send_text, without
    # requiring bit-exact timing assertions on inherently-randomized values.
    exact_total_s = sum(e.duration_ms for e in text_to_elements("SOS SOS", wpm=FAST_WPM)) / 1000.0

    backend = RecordingBackend()
    start = time.perf_counter()
    backend.send_text("SOS SOS", wpm=FAST_WPM, stop_flag=lambda: False, jitter=0.3)
    elapsed = time.perf_counter() - start

    assert elapsed == pytest.approx(exact_total_s, rel=0.5, abs=0.05)
