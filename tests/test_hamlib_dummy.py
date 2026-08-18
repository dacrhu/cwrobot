"""Automatic tests for the Hamlib CAT layer, exercised against Hamlib's own
RIG_MODEL_DUMMY backend -- no real radio or serial port needed.

Skipped wholesale if libhamlib.so isn't installed (e.g. a dev machine
without the `hamlib` package) -- this is exactly the "no CAT available"
scenario cwrobot itself degrades gracefully out of, so a skip here rather
than a failure is the correct signal.

The `rigctl` CLI cross-check mentioned in TODO.md's Milestone 6 plan is a
manual, one-off verification step (comparing this module's raw ctypes calls
against Hamlib's own reference CLI tool) rather than something this suite
automates -- see the commit/session notes for that check's results.
"""

from __future__ import annotations

import ctypes
import threading
import time

import pytest

from cwrobot.hamlib import ctypes_bindings
from cwrobot.hamlib.ctypes_bindings import HamlibUnavailableError
from cwrobot.hamlib.rig_client import HamlibError, HamlibRig, list_rig_models
from cwrobot.tx import hamlib_backend
from cwrobot.tx.encoder import text_to_elements
from cwrobot.tx.hamlib_backend import HamlibTxBackend

try:
    ctypes_bindings.get_library()
    _HAMLIB_AVAILABLE = True
except HamlibUnavailableError:
    _HAMLIB_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _HAMLIB_AVAILABLE, reason="libhamlib.so not installed")

# Kept low so a full send_text() run stays a handful of milliseconds.
FAST_WPM = 300


def test_load_library_raises_hamlib_unavailable_when_no_soname_resolves(monkeypatch):
    """The graceful-degradation path: simulate libhamlib.so simply not being
    on the system, independent of whether it actually is here."""
    monkeypatch.setattr(ctypes_bindings.ctypes.util, "find_library", lambda name: None)
    monkeypatch.setattr(ctypes_bindings, "_CANDIDATE_SONAMES", ["libtotally_not_a_real_hamlib.so.999"])

    with pytest.raises(HamlibUnavailableError, match="libhamlib"):
        ctypes_bindings._load_library()


def test_list_rig_models_includes_the_dummy_backend():
    models = list_rig_models(force_refresh=True)
    assert len(models) > 100  # sanity: Hamlib ships ~300 backends

    dummy = next(m for m in models if m.model_id == ctypes_bindings.RIG_MODEL_DUMMY)
    assert dummy.mfg_name == "Hamlib"
    assert dummy.model_name == "Dummy"
    assert str(dummy) == "Hamlib Dummy"


def test_rig_open_close_lifecycle():
    rig = HamlibRig(model_id=ctypes_bindings.RIG_MODEL_DUMMY, port_path="/dev/null")
    rig.open()
    try:
        rig.set_keyer_speed(25)
        rig.set_ptt(True)
        rig.set_ptt(False)
    finally:
        rig.close()
    # Closing an already-closed rig must be a harmless no-op (mirrors
    # AudioTxBackend/SidetonePlayback's tolerance of a repeated stop/close).
    rig.close()


def test_rig_open_with_unknown_model_id_raises_hamlib_error():
    rig = HamlibRig(model_id=0xFFFFFFF, port_path="/dev/null")
    with pytest.raises(HamlibError):
        rig.open()


def test_send_morse_succeeds_once_open():
    rig = HamlibRig(model_id=ctypes_bindings.RIG_MODEL_DUMMY, port_path="/dev/null")
    rig.open()
    try:
        rig.set_keyer_speed(FAST_WPM)
        rig.send_morse("TEST")  # raises HamlibError on failure -- e.g. if
        # open() hadn't already switched the dummy rig into CW mode.
        rig.stop_morse()  # best-effort, must not raise even once "done"
    finally:
        rig.close()


def test_hamlib_tx_backend_send_text_runs_to_completion_and_reports_progress():
    backend = HamlibTxBackend(model_id=ctypes_bindings.RIG_MODEL_DUMMY, port_path="/dev/null")
    backend.start()
    try:
        progressed: list[int] = []
        backend.send_text("SOS", wpm=FAST_WPM, stop_flag=lambda: False, progress_callback=progressed.append)
        assert progressed == [0, 1, 2]  # one callback per character of "SOS"
    finally:
        backend.close()


def test_hamlib_tx_backend_stop_flag_interrupts_and_calls_rig_stop_morse(monkeypatch):
    backend = HamlibTxBackend(model_id=ctypes_bindings.RIG_MODEL_DUMMY, port_path="/dev/null")
    backend.start()
    try:
        stop_calls = []
        monkeypatch.setattr(backend._rig, "stop_morse", lambda: stop_calls.append(True))

        # Stop immediately, before the first element's wait even starts.
        backend.send_text("SOS SOS SOS", wpm=1, stop_flag=lambda: True)

        assert stop_calls == [True]
    finally:
        backend.close()


def test_hamlib_tx_backend_sends_multi_word_text_progress_matches_full_text_encoding():
    """Text is now sent one word at a time (see hamlib_backend's module
    docstring), but progress_callback should still fire once per character
    in the same order a single whole-text encoding would -- proves the
    per-word text_index offsetting lines back up with the original text."""
    backend = HamlibTxBackend(model_id=ctypes_bindings.RIG_MODEL_DUMMY, port_path="/dev/null")
    backend.start()
    try:
        text = "SOS DE TEST"
        expected_indices: list[int] = []
        for element in text_to_elements(text, wpm=FAST_WPM):
            if not expected_indices or element.text_index != expected_indices[-1]:
                expected_indices.append(element.text_index)

        progressed: list[int] = []
        backend.send_text(text, wpm=FAST_WPM, stop_flag=lambda: False, progress_callback=progressed.append)
        assert progressed == expected_indices
    finally:
        backend.close()


def test_hamlib_tx_backend_keeps_short_words_bundled_in_one_chunk(monkeypatch):
    """Regression test for a real-hardware bug: an earlier version of this
    fix chunked strictly per word (a fresh, PTT-cycling rig_send_morse call
    for every single word), and that caused a short isolated word (a lone
    "A") to be dropped by the rig entirely, plus made inter-word gaps
    noticeably longer than standard -- see hamlib_backend's module
    docstring. Chunking by a character budget instead keeps consecutive
    words -- including single-letter ones -- bundled into the same
    continuous rig_send_morse call whenever they fit, so the rig's own
    keyer paces spacing/PTT across them natively."""
    backend = HamlibTxBackend(model_id=ctypes_bindings.RIG_MODEL_DUMMY, port_path="/dev/null")
    backend.start()
    try:
        sent_chunks: list[str] = []
        real_send_morse = backend._rig.send_morse

        def _record_send_morse(chunk: str) -> None:
            sent_chunks.append(chunk)
            real_send_morse(chunk)

        monkeypatch.setattr(backend._rig, "send_morse", _record_send_morse)

        text = "THIS IS A KEY TEST THIS IS A KEY TEST"
        progressed: list[int] = []
        backend.send_text(text, wpm=FAST_WPM, stop_flag=lambda: False, progress_callback=progressed.append)

        assert sent_chunks == ["THIS IS A KEY TEST", "THIS IS A KEY TEST"]

        expected_indices: list[int] = []
        for element in text_to_elements(text, wpm=FAST_WPM):
            if not expected_indices or element.text_index != expected_indices[-1]:
                expected_indices.append(element.text_index)
        assert progressed == expected_indices
        # Both occurrences of the standalone "A" (indices 8 and 27) must
        # actually have been keyed, not silently skipped.
        assert 8 in progressed
        assert 27 in progressed
    finally:
        backend.close()


def test_hamlib_tx_backend_splits_into_multiple_chunks_when_budget_exceeded(monkeypatch):
    backend = HamlibTxBackend(model_id=ctypes_bindings.RIG_MODEL_DUMMY, port_path="/dev/null")
    backend.start()
    try:
        sent_chunks: list[str] = []
        real_send_morse = backend._rig.send_morse

        def _record_send_morse(chunk: str) -> None:
            sent_chunks.append(chunk)
            real_send_morse(chunk)

        monkeypatch.setattr(backend._rig, "send_morse", _record_send_morse)

        # Long enough that it must be split across multiple rig_send_morse
        # calls, but no single word exceeds the chunk budget on its own.
        text = " ".join(["TEST"] * 10)
        backend.send_text(text, wpm=FAST_WPM, stop_flag=lambda: False)

        assert len(sent_chunks) > 1
        assert all(len(chunk) <= hamlib_backend._MAX_CHUNK_CHARS for chunk in sent_chunks)
        # Reassembling the calls (single spaces back in between) must
        # reproduce the original text exactly, in order -- no word dropped,
        # duplicated, or reordered by the chunking.
        assert " ".join(sent_chunks) == text
    finally:
        backend.close()


def test_hamlib_tx_backend_stop_during_drain_interrupts_promptly(monkeypatch):
    """Simulates a rig backend whose wait_morse() genuinely blocks (as a
    real implementation would while a word is still draining) -- proves
    Stop pressed during that wait still aborts promptly via rig_stop_morse,
    rather than waiting out _wait_for_rig_drain's full timeout."""
    backend = HamlibTxBackend(model_id=ctypes_bindings.RIG_MODEL_DUMMY, port_path="/dev/null")
    backend.start()
    try:
        stop_calls = []
        release = threading.Event()

        def _stop_morse() -> None:
            stop_calls.append(True)
            release.set()  # aborting unblocks the rig's own wait, same as real hardware

        monkeypatch.setattr(backend._rig, "stop_morse", _stop_morse)
        monkeypatch.setattr(backend._rig, "wait_morse", lambda: release.wait())

        stop_requested = threading.Event()
        threading.Timer(0.15, stop_requested.set).start()

        start = time.perf_counter()
        backend.send_text("SOS", wpm=FAST_WPM, stop_flag=stop_requested.is_set)
        elapsed = time.perf_counter() - start

        assert stop_calls == [True]
        # Comfortably below _MIN_DRAIN_TIMEOUT_S (1s) -- proves the stop
        # path fired, not the timeout path.
        assert elapsed < 0.6
    finally:
        release.set()
        backend.close()


def test_hamlib_tx_backend_drain_timeout_gives_up_without_hanging(monkeypatch):
    """A wait_morse() that never returns at all (rig/backend hang) must not
    hang the TX thread forever -- _wait_for_rig_drain gives up at its own
    (here shrunk for test speed) timeout and moves on."""
    monkeypatch.setattr(hamlib_backend, "_MIN_DRAIN_TIMEOUT_S", 0.05)

    backend = HamlibTxBackend(model_id=ctypes_bindings.RIG_MODEL_DUMMY, port_path="/dev/null")
    backend.start()
    try:
        never_returns = threading.Event()
        monkeypatch.setattr(backend._rig, "wait_morse", lambda: never_returns.wait())

        start = time.perf_counter()
        backend.send_text("SOS", wpm=FAST_WPM, stop_flag=lambda: False)
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0
    finally:
        never_returns.set()
        backend.close()
