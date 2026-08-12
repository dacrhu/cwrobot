"""Sidetone output: a continuous OutputStream that renders a sine tone
whenever "keyed" and silence otherwise, with an exponential amplitude ramp
on every transition so key edges don't produce audible/decodable clicks.

Independent of the RX AudioCapture stream (its own OutputStream, own
device) per Specifikacio.md. Stream creation is deferred to `start()` (not
done in `__init__`), mirroring audio/capture.py -- this also means the
class can be constructed and its `_callback` exercised directly in tests
without opening a real audio device.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd

from cwrobot.audio.pipewire_route import (
    PipeWireSink,
    route_active_stream_to_sink,
    snapshot_sink_input_ids,
)

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_BLOCKSIZE = 240  # 5 ms at 48 kHz, matching audio/capture.py

# Exponential amplitude ramp time constant applied on every key transition.
# Mirrors the envelope-smoothing style used in dsp/tone_detector.py. Fast
# enough that keying still sounds crisp, slow enough that the edges don't
# produce audible (or RX-decoder-detectable) clicks.
RAMP_TIME_CONSTANT_MS = 3.0


class SidetonePlayback:
    def __init__(
        self,
        frequency_hz: float,
        device: str | int | None = None,
        samplerate: int = DEFAULT_SAMPLE_RATE,
        blocksize: int = DEFAULT_BLOCKSIZE,
        route_to_pipewire_sink: PipeWireSink | None = None,
    ) -> None:
        self.frequency_hz = float(frequency_hz)
        self.device = device
        self.samplerate = samplerate
        self._blocksize = blocksize
        #: See AudioCapture.__init__'s equivalent field -- same idea,
        #: mirrored for playback: `device` is ignored and the stream opens
        #: on the ALSA default, then gets moved onto this sink via `pactl`
        #: right after it starts.
        self._route_to_pipewire_sink = route_to_pipewire_sink

        self._phase = 0.0
        self._amplitude = 0.0  # current, ramping toward _target_amplitude
        self._target_amplitude = 0.0
        self._ramp_alpha = float(np.exp(-(1000.0 / samplerate) / RAMP_TIME_CONSTANT_MS))

        self._stream: sd.OutputStream | None = None

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        phase_inc = 2.0 * np.pi * self.frequency_hz / self.samplerate
        phases = self._phase + phase_inc * np.arange(frames, dtype=np.float64)
        tone = np.sin(phases)

        # Target amplitude is constant across one callback (it only changes
        # via key(), an async attribute write from the TX thread), so the
        # whole ramp for this block can be computed in closed form rather
        # than iterated sample-by-sample:
        #   a[n] = target + (a[0] - target) * alpha**n
        target = self._target_amplitude
        n = np.arange(1, frames + 1, dtype=np.float64)
        amp = target + (self._amplitude - target) * (self._ramp_alpha**n)
        self._amplitude = float(amp[-1])

        outdata[:, 0] = (tone * amp).astype(np.float32)
        self._phase = (self._phase + phase_inc * frames) % (2.0 * np.pi)

    def set_frequency(self, hz: float) -> None:
        self.frequency_hz = float(hz)

    def key(self, is_on: bool) -> None:
        self._target_amplitude = 1.0 if is_on else 0.0

    def start(self) -> None:
        route = self._route_to_pipewire_sink
        before_ids = snapshot_sink_input_ids() if route is not None else None
        self._stream = sd.OutputStream(
            device=None if route is not None else self.device,
            samplerate=self.samplerate,
            blocksize=self._blocksize,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()
        if route is not None and before_ids is not None:
            # Best-effort: route_active_stream_to_sink already logs on
            # failure; log-and-continue-on-default is the deliberate
            # failure mode here, never raise.
            route_active_stream_to_sink(route, before_ids)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "SidetonePlayback":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
