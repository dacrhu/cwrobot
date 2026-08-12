"""Tests for audio.playback.SidetonePlayback's envelope/tone rendering.

Exercises `_callback` directly with hand-built output buffers -- stream
creation is deferred to `start()` (never called here), so no real audio
device is touched.
"""

from __future__ import annotations

import numpy as np
import pytest

from cwrobot.audio.playback import SidetonePlayback


def _run_callback(playback: SidetonePlayback, frames: int) -> np.ndarray:
    outdata = np.zeros((frames, 1), dtype=np.float32)
    playback._callback(outdata, frames, None, None)
    return outdata[:, 0]


def test_silence_when_never_keyed():
    playback = SidetonePlayback(frequency_hz=600, samplerate=8000, blocksize=64)
    out = _run_callback(playback, 64)
    assert np.allclose(out, 0.0)


def test_keying_on_ramps_amplitude_up_toward_full_scale():
    playback = SidetonePlayback(frequency_hz=600, samplerate=8000, blocksize=800)
    playback.key(True)
    out = _run_callback(playback, 800)  # 100 ms at 8 kHz, several ramp time constants
    envelope = np.abs(out)
    # Starts near zero, ends near full scale.
    assert envelope[:5].max() < 0.2
    assert envelope[-5:].max() > 0.9


def test_keying_off_ramps_amplitude_back_down_to_silence():
    playback = SidetonePlayback(frequency_hz=600, samplerate=8000, blocksize=800)
    playback.key(True)
    _run_callback(playback, 800)  # let it ramp up to (near) full scale first
    playback.key(False)
    out = _run_callback(playback, 800)
    envelope = np.abs(out)
    assert envelope[-5:].max() < 0.05


def test_no_amplitude_discontinuity_across_callback_boundaries():
    # The whole point of computing the ramp in closed form per-block is that
    # state (self._amplitude) carries over seamlessly between callbacks --
    # a naive per-block reset would produce an audible click at every
    # blocksize boundary.
    playback = SidetonePlayback(frequency_hz=600, samplerate=8000, blocksize=64)
    playback.key(True)
    first = _run_callback(playback, 64)
    second = _run_callback(playback, 64)
    # Reconstruct the amplitude envelope (not the raw signed tone, which
    # legitimately oscillates) via the magnitude of consecutive samples
    # around the boundary -- should not jump sharply.
    boundary_gap = abs(abs(second[0]) - abs(first[-1]))
    assert boundary_gap < 0.2  # well under a full on/off step of 1.0


def test_tone_frequency_matches_configured_pitch():
    samplerate = 8000
    frequency_hz = 500
    playback = SidetonePlayback(frequency_hz=frequency_hz, samplerate=samplerate, blocksize=samplerate)
    playback.key(True)
    _run_callback(playback, samplerate)  # let the ramp fully settle
    playback.key(True)
    out = _run_callback(playback, samplerate)  # one full second, steady-state amplitude

    spectrum = np.abs(np.fft.rfft(out))
    freqs = np.fft.rfftfreq(samplerate, d=1.0 / samplerate)
    peak_freq = freqs[np.argmax(spectrum)]
    assert peak_freq == pytest.approx(frequency_hz, abs=2)
