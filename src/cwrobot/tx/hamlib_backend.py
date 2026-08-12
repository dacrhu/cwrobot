"""Hamlib CAT keying TX backend.

Unlike AudioTxBackend, this backend does *not* use TxBackend.send_text's
shared per-element `_set_key` loop for the actual keying: over CAT, Hamlib's
`rig_send_morse` hands the whole message to the rig's own internal keyer,
which paces the real dit/dah timing in hardware -- toggling PTT once per
element over a serial link would be far too slow/jittery to sound right (the
link's own command latency dwarfs a dot's duration at any real WPM). This is
also why `jitter` ("manual keying emulation") has no effect here: once the rig
has the text queued, cwrobot no longer controls its output timing at all.

What *is* reused from the base class is `_wait_until` and the element list
from tx.encoder.text_to_elements -- not to key anything, but as a local
clock: it's the same estimate a human operator would get by ear, and gives
`progress_callback` the same per-character highlight behavior as the audio
backend, with `stop_flag` checked on the same cadence so Stop still feels
immediate (it interrupts the rig via `rig_stop_morse`, not just our own
wait loop).
"""

from __future__ import annotations

import logging
import time

from cwrobot.hamlib.rig_client import HamlibRig
from cwrobot.tx.backend import TxBackend
from cwrobot.tx.encoder import text_to_elements

logger = logging.getLogger(__name__)


class HamlibTxBackend(TxBackend):
    def __init__(self, model_id: int, port_path: str, baud_rate: int | None = None) -> None:
        self._rig = HamlibRig(model_id=model_id, port_path=port_path, baud_rate=baud_rate)

    def start(self) -> None:
        self._rig.open()

    def close(self) -> None:
        self._rig.close()

    def send_text(
        self,
        text: str,
        wpm: float,
        stop_flag,
        progress_callback=None,
        jitter: float = 0.0,
    ) -> None:
        if jitter:
            logger.info(
                "manual_keying jitter=%.2f ignored -- over CAT the rig's own "
                "keyer paces the timing, not the software",
                jitter,
            )

        elements = text_to_elements(text, wpm)
        if not elements:
            return

        self._rig.set_keyer_speed(wpm)
        self._rig.send_morse(text)

        deadline = time.perf_counter()
        last_index: int | None = None
        stopped = False
        for element in elements:
            if stop_flag():
                stopped = True
                break
            if progress_callback is not None and element.text_index != last_index:
                last_index = element.text_index
                progress_callback(element.text_index)
            deadline += element.duration_ms / 1000.0
            self._wait_until(deadline, stop_flag)
            if stop_flag():
                stopped = True
                break

        if stopped:
            self._rig.stop_morse()

    def _set_key(self, is_on: bool) -> None:
        # Not used by the overridden send_text above (see module docstring)
        # -- implemented only to satisfy TxBackend's abstract interface, as
        # a harmless PTT-based fallback should something ever call it
        # directly.
        self._rig.set_ptt(is_on)
