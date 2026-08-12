"""Real-time-safe audio capture: PortAudio InputStream -> RingBuffer.

The callback (running on PortAudio's own realtime thread) does nothing but a
fixed-size copy into the ring buffer -- no numpy math, no locks beyond the
ring buffer's own cheap bookkeeping lock, no Python object churn -- so it
can never block waiting on the (potentially slower, GIL-sharing) decoder
thread.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd

from cwrobot.audio.pipewire_route import (
    PipeWireSource,
    route_active_stream_to_source,
    snapshot_source_output_ids,
)
from cwrobot.audio.ringbuffer import RingBuffer

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_BLOCKSIZE = 240  # 5 ms at 48 kHz


class AudioCapture:
    def __init__(
        self,
        device: str | int | None = None,
        samplerate: int = DEFAULT_SAMPLE_RATE,
        blocksize: int = DEFAULT_BLOCKSIZE,
        buffer_seconds: float = 5.0,
        route_to_pipewire_source: PipeWireSource | None = None,
    ) -> None:
        self.samplerate = samplerate
        self.device = device
        self.ring_buffer = RingBuffer(capacity=int(samplerate * buffer_seconds))
        self._stream: sd.InputStream | None = None
        self._blocksize = blocksize
        #: When set, `device` is ignored and the stream always opens on the
        #: safe ALSA default -- PortAudio only exposes PipeWire's own named
        #: sources via its JACK host API, which this project avoids writing
        #: to directly (see audio/pipewire_route.py). Right after the
        #: stream starts, the newly-created source-output for *this* stream
        #: is moved onto the target source via `pactl`. A routing failure
        #: is logged and capture simply continues on the system default.
        self._route_to_pipewire_source = route_to_pipewire_source

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        # `status` flags (e.g. input overflow) are surfaced via `self.last_status`
        # rather than raised here -- raising inside a PortAudio callback is unsafe.
        if status:
            self.last_status = status
        # Mono-ize by taking channel 0 if the device is multi-channel; this is a
        # fixed, allocation-light slice/copy, safe for the realtime callback.
        mono = indata[:, 0] if indata.ndim > 1 else indata
        self.ring_buffer.write(mono.astype(np.float32, copy=False))

    def start(self) -> None:
        self.last_status = None
        route = self._route_to_pipewire_source
        before_ids = snapshot_source_output_ids() if route is not None else None
        self._stream = sd.InputStream(
            device=None if route is not None else self.device,
            samplerate=self.samplerate,
            blocksize=self._blocksize,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()
        if route is not None and before_ids is not None:
            # Best-effort: route_active_stream_to_source already logs on
            # failure; log-and-continue-on-default is the deliberate
            # failure mode here, never raise.
            route_active_stream_to_source(route, before_ids)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "AudioCapture":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
