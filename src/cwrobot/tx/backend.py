"""Backend abstraction for actually transmitting Morse.

`TxBackend.send_text` is the shared driver: render the text to elements
(tx.encoder.text_to_elements) and key them out one by one on a deadline-
based clock (not cumulative sleep(), which would drift over a long
message), checking `stop_flag()` between elements so a Stop request
interrupts promptly. A concrete backend normally only needs to implement
`_set_key` (audio-tone sidetone -- AudioTxBackend). Hamlib CAT keying
(HamlibTxBackend, milestone 6) is the exception: over CAT, the rig's own
internal keyer paces the real dit/dah timing once `rig_send_morse` hands it
the whole message, so that backend overrides `send_text` entirely instead
of implementing `_set_key` -- see tx/hamlib_backend.py's module docstring
for why.

This class is plain Python (no Qt) so it's directly unit-testable; the
QThread worker shell around it lives in tx/worker.py, mirroring the
decoder/decoder.py split between pure processing core and Qt worker.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from cwrobot.tx.encoder import text_to_elements

# How often to re-check stop_flag() while waiting out one element's
# duration -- short enough that Stop feels immediate, long enough not to
# busy-loop.
_POLL_INTERVAL_S = 0.01


class TxBackend(ABC):
    def send_text(
        self,
        text: str,
        wpm: float,
        stop_flag: Callable[[], bool],
        progress_callback: Callable[[int], None] | None = None,
        jitter: float = 0.0,
    ) -> None:
        """`progress_callback`, if given, is called once with each element's
        `text_index` the first time that index is reached (i.e. once per
        source character as keying for it begins) -- lets a UI track "here's
        what's being sent right now" without polling.

        `jitter` (0.0-1.0): see tx.encoder.text_to_elements -- "kezi adas
        emulalasa"."""
        elements = text_to_elements(text, wpm, jitter=jitter)
        deadline = time.perf_counter()
        last_index: int | None = None
        for element in elements:
            if stop_flag():
                break
            if progress_callback is not None and element.text_index != last_index:
                last_index = element.text_index
                progress_callback(element.text_index)
            self._set_key(element.is_on)
            deadline += element.duration_ms / 1000.0
            self._wait_until(deadline, stop_flag)
        self._set_key(False)

    def _wait_until(self, deadline: float, stop_flag: Callable[[], bool]) -> None:
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0 or stop_flag():
                return
            time.sleep(min(remaining, _POLL_INTERVAL_S))

    @abstractmethod
    def _set_key(self, is_on: bool) -> None:
        """Key on/off right now."""

    def close(self) -> None:
        """Release any resources (output stream, rig handle, ...). Default no-op."""
