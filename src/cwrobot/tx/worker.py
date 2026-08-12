"""QThread worker shell around TxBackend.send_text.

Mirrors the decoder/decoder.py split: TxBackend itself is plain Python
(send_text is a blocking call that checks a stop_flag callback), TxWorker
turns that into Qt signals so the UI thread can react (re-enable Send,
disable Stop, show a status message) without ever blocking on the send.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from cwrobot.tx.backend import TxBackend


class TxWorker(QObject):
    finished = Signal()
    error = Signal(str)
    progress = Signal(int)  # text_index of the character currently being keyed

    def __init__(
        self,
        backend: TxBackend,
        text: str,
        wpm: float,
        jitter: float = 0.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend = backend
        self._text = text
        self._wpm = wpm
        self._jitter = jitter
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        """Intended to run on a dedicated QThread (connect `QThread.started`
        to this method after `moveToThread`)."""
        try:
            self._backend.send_text(
                self._text,
                self._wpm,
                lambda: self._stop_requested,
                progress_callback=self.progress.emit,
                jitter=self._jitter,
            )
        except Exception as exc:
            # A backend failure (e.g. the output device disappearing
            # mid-send) must reach the UI as a status message, not crash
            # the TX thread silently -- mirrors decoder.py's resilience
            # pattern for the RX side.
            logging.getLogger(__name__).exception("TxBackend.send_text failed")
            self.error.emit(f"TX error: {exc}")
        finally:
            self.finished.emit()
