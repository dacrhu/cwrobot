"""Audio-tone TX backend: keys a SidetonePlayback sine tone according to the
element stream from tx.encoder, via the shared TxBackend.send_text driver.
"""

from __future__ import annotations

from cwrobot.audio.pipewire_route import PipeWireSink
from cwrobot.audio.playback import SidetonePlayback
from cwrobot.tx.backend import TxBackend


class AudioTxBackend(TxBackend):
    def __init__(
        self,
        frequency_hz: float,
        device: str | int | None = None,
        route_to_pipewire_sink: PipeWireSink | None = None,
    ) -> None:
        self._playback = SidetonePlayback(
            frequency_hz=frequency_hz,
            device=device,
            route_to_pipewire_sink=route_to_pipewire_sink,
        )

    def start(self) -> None:
        self._playback.start()

    def _set_key(self, is_on: bool) -> None:
        self._playback.key(is_on)

    def close(self) -> None:
        self._playback.stop()
