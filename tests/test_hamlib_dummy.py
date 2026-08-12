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

import pytest

from cwrobot.hamlib import ctypes_bindings
from cwrobot.hamlib.ctypes_bindings import HamlibUnavailableError
from cwrobot.hamlib.rig_client import HamlibError, HamlibRig, list_rig_models
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
