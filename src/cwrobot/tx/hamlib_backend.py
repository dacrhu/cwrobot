"""Hamlib CAT keying TX backend.

Unlike AudioTxBackend, this backend does *not* use TxBackend.send_text's
shared per-element `_set_key` loop for the actual keying: over CAT, Hamlib's
`rig_send_morse` hands text to the rig's own internal keyer, which paces the
real dit/dah timing in hardware -- toggling PTT once per element over a
serial link would be far too slow/jittery to sound right (the link's own
command latency dwarfs a dot's duration at any real WPM). This is also why
`jitter` ("manual keying emulation") has no effect here: once the rig has
text queued, cwrobot no longer controls its output timing at all.

What *is* reused from the base class is `_wait_until` and the element list
from tx.encoder.text_to_elements -- not to key anything, but as a local
clock: it's the same estimate a human operator would get by ear, and gives
`progress_callback` the same per-character highlight behavior as the audio
backend, with `stop_flag` checked on the same cadence so Stop still feels
immediate (it interrupts the rig via `rig_stop_morse`, not just our own
wait loop).

Text is sent to the rig in **character-budget-sized chunks**
(_chunk_text/_MAX_CHUNK_CHARS below), not as a single `rig_send_morse` call
for the whole message, and each chunk is followed by a best-effort wait for
the rig to actually confirm (`rig_wait_morse`) that chunk is done before the
next one is queued (see `_wait_for_rig_drain`). Two real-hardware findings
shaped this, in that order:

1. The local per-element clock is only an estimate, and any small mismatch
   between it and how a specific rig actually paces its keyer (weighting,
   internal buffer refills, ...) accumulates over a long message -- badly
   enough that the app can consider a long send "done" and tear down the
   CAT connection (see ui.main_window._on_tx_finished -> _stop_tx_pipeline
   -> backend.close()) well before the rig has actually finished
   transmitting it, cutting the tail of the message off over the air.
   Resyncing periodically bounds the worst-case drift to one chunk instead
   of the whole message, and as a side effect keeps every `rig_send_morse`
   call well under the small message-buffer limit some rigs enforce
   (commonly ~24-30 characters) -- a risk a single whole-message call
   doesn't protect against.
2. An earlier version of this fix chunked strictly *per word* (a fresh
   `rig_send_morse` call, and thus a fresh PTT engage/disengage cycle on
   the rig, for every single word). Confirmed against real hardware, that
   was too fine-grained: a short, isolated word (e.g. a lone "A") sent as
   its own tiny burst right after the rig had gone quiet could be dropped
   entirely by the rig's own keyer/PTT attack-time handling, and every
   word boundary added a full extra software-side round-trip + gap wait on
   top of the standard inter-word spacing, making pauses between words
   noticeably longer than normal. Chunking by a character budget instead
   -- keeping consecutive words (with their original spaces) bundled into
   one continuous `rig_send_morse` call whenever they fit -- lets the rig's
   own keyer pace inter-word spacing/PTT natively within a chunk, the same
   way the original single-call implementation did, while still
   resyncing/bounding drift at the (much less frequent) chunk boundaries.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from cwrobot.hamlib.rig_client import HamlibRig
from cwrobot.tx.backend import TxBackend
from cwrobot.tx.encoder import WORD_GAP_UNITS, dot_unit_ms, text_to_elements

logger = logging.getLogger(__name__)

# How often to poll stop_flag while waiting for the rig to confirm (via
# wait_morse) that the chunk just queued has actually finished sending.
_DRAIN_POLL_INTERVAL_S = 0.02

# Floor for the per-chunk drain timeout (see _wait_for_rig_drain), so even a
# very short chunk gets a sane minimum grace period rather than being capped
# at a few milliseconds.
_MIN_DRAIN_TIMEOUT_S = 1.0

# Matches one run of non-whitespace characters -- cwrobot's definition of
# "word" for chunking purposes, mirroring how tx.encoder.text_to_elements
# already collapses any run of spaces into a single word gap.
_WORD_RE = re.compile(r"\S+")

# Max characters handed to the rig in a single rig_send_morse call -- see
# the module docstring for why (drift-bounding + real per-command CW-buffer
# limits on many rigs). Deliberately conservative relative to the commonly
# cited ~24-30 character limits.
_MAX_CHUNK_CHARS = 20


def _chunk_text(text: str, max_chars: int) -> list[tuple[int, str]]:
    """Split `text` into (offset, chunk) pieces of at most `max_chars`
    characters each, only ever breaking on whitespace (never mid-word) --
    keeps consecutive short words bundled into one continuous chunk instead
    of splitting on every single word (see module docstring for why that
    matters on real hardware). A single word longer than `max_chars` is
    still returned as its own (over-budget) chunk -- there's no way to
    split it further without corrupting the Morse content, so the budget is
    a soft cap on typical text, not a hard invariant.

    `offset` is the chunk's start index into the *original* `text`, so a
    caller can offset text_to_elements(chunk, ...)'s text_index values back
    onto the original string for progress reporting.
    """
    chunks: list[tuple[int, str]] = []
    current_start: int | None = None
    current_end: int | None = None
    for match in _WORD_RE.finditer(text):
        start, end = match.start(), match.end()
        if current_start is None:
            current_start, current_end = start, end
        elif end - current_start <= max_chars:
            current_end = end
        else:
            chunks.append((current_start, text[current_start:current_end]))
            current_start, current_end = start, end
    if current_start is not None:
        chunks.append((current_start, text[current_start:current_end]))
    return chunks


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

        # Precompute each chunk's elements up front (rather than skipping
        # empty ones mid-loop) so a message made up entirely of unsupported
        # characters can bail out before ever touching the rig.
        chunks = [
            (offset, chunk, text_to_elements(chunk, wpm))
            for offset, chunk in _chunk_text(text, _MAX_CHUNK_CHARS)
        ]
        chunks = [(offset, chunk, elements) for offset, chunk, elements in chunks if elements]
        if not chunks:
            return

        self._rig.set_keyer_speed(wpm)

        # Only needed *between* chunks -- spacing between words within one
        # chunk is already part of that chunk's own elements (text_to_elements
        # emits it from the embedded spaces), paced by the rig itself.
        inter_chunk_gap_s = (WORD_GAP_UNITS * dot_unit_ms(wpm)) / 1000.0
        last_index: int | None = None

        for chunk_number, (offset, chunk, elements) in enumerate(chunks):
            self._rig.send_morse(chunk)

            deadline = time.perf_counter()
            stopped = False
            for element in elements:
                if stop_flag():
                    stopped = True
                    break
                global_index = element.text_index + offset
                if progress_callback is not None and global_index != last_index:
                    last_index = global_index
                    progress_callback(global_index)
                deadline += element.duration_ms / 1000.0
                self._wait_until(deadline, stop_flag)
                if stop_flag():
                    stopped = True
                    break

            if stopped:
                self._rig.stop_morse()
                return

            estimated_chunk_s = sum(element.duration_ms for element in elements) / 1000.0
            self._wait_for_rig_drain(stop_flag, estimated_chunk_s)
            if stop_flag():
                # _wait_for_rig_drain already called stop_morse in this case.
                return

            if chunk_number < len(chunks) - 1:
                self._wait_until(time.perf_counter() + inter_chunk_gap_s, stop_flag)
                if stop_flag():
                    self._rig.stop_morse()
                    return

    def _wait_for_rig_drain(self, stop_flag, estimated_chunk_s: float) -> None:
        """Block until the rig confirms (`rig_wait_morse`) the chunk just
        queued has actually finished sending, before the next chunk is
        queued -- see the module docstring for why the local per-element
        clock alone can't be trusted as "done" truth.

        `rig_wait_morse` is a single blocking ctypes call with no
        timeout/cancel hook, so it runs on a small daemon helper thread
        (ctypes releases the GIL for the duration of the call, so this is
        safe) while this method polls `stop_flag` here on the calling
        thread: if Stop is requested mid-drain, `rig_stop_morse` aborts the
        rig's queue, which should unblock the waiting thread promptly.

        Also caps the total wait so a rig/backend combination where
        `wait_morse` never returns (firmware quirk, lost connection, ...)
        can't hang the TX thread forever -- on timeout this just logs a
        warning and moves on to the next chunk, exactly as if `wait_morse`
        weren't supported at all. On backends that don't implement
        `rig_wait_morse` (e.g. the Hamlib Dummy backend used in tests),
        `HamlibRig.wait_morse` already returns immediately, so this is a
        fast no-op there -- per-chunk timing then falls back to exactly the
        previous local-clock-only behavior, just resynced every chunk
        instead of drifting across the whole message.
        """
        done = threading.Event()

        def _wait() -> None:
            self._rig.wait_morse()
            done.set()

        threading.Thread(target=_wait, daemon=True).start()

        deadline = time.perf_counter() + max(_MIN_DRAIN_TIMEOUT_S, estimated_chunk_s * 2)
        while not done.is_set():
            if stop_flag():
                self._rig.stop_morse()
                done.wait(_MIN_DRAIN_TIMEOUT_S)
                return
            if time.perf_counter() >= deadline:
                logger.warning(
                    "Timed out waiting for the rig to confirm CW completion "
                    "for one chunk; continuing with the next chunk anyway"
                )
                return
            done.wait(_DRAIN_POLL_INTERVAL_S)

    def _set_key(self, is_on: bool) -> None:
        # Not used by the overridden send_text above (see module docstring)
        # -- implemented only to satisfy TxBackend's abstract interface, as
        # a harmless PTT-based fallback should something ever call it
        # directly.
        self._rig.set_ptt(is_on)
