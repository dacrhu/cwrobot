"""Morse encoder: text -> a timed stream of key-down/key-up elements.

Uses the same CHAR_TO_MORSE table as the decoder (decoder/morse_table.py),
so encode and decode directions can never drift apart.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from cwrobot.decoder.morse_table import CHAR_TO_MORSE

# Standard Morse timing, in dot-units (PARIS-word convention: wpm = 1200 / dot_unit_ms).
DOT_UNITS = 1
DASH_UNITS = 3
INTRA_CHAR_GAP_UNITS = 1  # between dots/dashes within one character
CHAR_GAP_UNITS = 3  # between characters within a word
WORD_GAP_UNITS = 7  # between words

# Jitter range for "kezi adas emulalasa" (ui.tx_panel.manual_keying_checkbox
# + manual_keying_slider): the actual +/-jitter fraction stays within
# [MIN, MAX] -- comfortably inside the range our own RX decoder
# (decoder.timing) is verified to track cleanly (see
# tests/test_decoder_synthetic.py's jitter=0.30 case), and kept subtle even
# at the top end, since the point is a believable hand, not a sloppy one.
# The operator never sees these raw fractions, only the unitless 1-8 slider
# (see manual_keying_jitter_from_level) -- the exact percentage range is an
# implementation detail they shouldn't need to reason about.
MANUAL_KEYING_JITTER_MIN = 0.07
MANUAL_KEYING_JITTER_MAX = 0.15
MANUAL_KEYING_LEVEL_MIN = 1
MANUAL_KEYING_LEVEL_MAX = 8


def manual_keying_jitter_from_level(level: int) -> float:
    """Map the TX panel's relative 1-8 slider level to an actual jitter
    fraction (text_to_elements's `jitter` param), linearly between
    MANUAL_KEYING_JITTER_MIN and _MAX. Out-of-range levels are clamped."""
    level = max(MANUAL_KEYING_LEVEL_MIN, min(MANUAL_KEYING_LEVEL_MAX, level))
    span_levels = MANUAL_KEYING_LEVEL_MAX - MANUAL_KEYING_LEVEL_MIN
    fraction = (level - MANUAL_KEYING_LEVEL_MIN) / span_levels
    return MANUAL_KEYING_JITTER_MIN + fraction * (MANUAL_KEYING_JITTER_MAX - MANUAL_KEYING_JITTER_MIN)


@dataclass(frozen=True)
class Element:
    is_on: bool
    duration_ms: float
    # Index of the character in the *original* (unmodified) input text this
    # element belongs to -- lets a UI highlight "here's what's being sent
    # right now" directly against what the user typed, without having to
    # re-derive positions from a separately-normalized copy. Gap elements
    # are attributed to the character they lead *into*.
    text_index: int


def dot_unit_ms(wpm: float) -> float:
    return 1200.0 / wpm


def text_to_elements(
    text: str,
    wpm: float,
    farnsworth_wpm: float | None = None,
    jitter: float = 0.0,
    rng: random.Random | None = None,
) -> list[Element]:
    """Render `text` into key-down/key-up elements at `wpm`.

    farnsworth_wpm (optional, should be <= wpm): dot/dash/intra-char-gap
    timing stays at full `wpm` speed (so individual characters still
    *sound* like `wpm`), but inter-character and inter-word gaps stretch
    out to whatever a slower `farnsworth_wpm` would use, giving more
    thinking time between characters. Not exposed in the MVP TX panel yet
    (see plan section 5/7) but implemented so wiring it up later is a UI
    change only.

    jitter (0.0-1.0, "kezi adas emulalasa" in the TX panel): randomly
    perturbs *every* element's duration -- marks and gaps alike -- by up to
    +/-jitter*100%, independently, so the output sounds like an imprecise
    hand keyer rather than a machine-perfect keyer. 0 (the default) keeps
    textbook-exact timing. Mirrors tests/synth.py's jitter model (kept
    deliberately independent of this module there, to avoid a shared-bug
    blind spot in decoder tests) -- this is the real, production version of
    the same idea. `rng` is exposed for deterministic tests; a fresh
    `random.Random()` is created per call otherwise (a *shared* generator
    across multiple sends would make consecutive characters' jitter
    correlated in ways a real hand keyer's wouldn't be).

    Characters with no Morse mapping (see morse_table.CHAR_TO_MORSE) are
    silently skipped rather than raising -- pasted TX text may well contain
    the odd unsupported symbol, and refusing the whole message over it
    would be worse than just not keying that one character. Runs of spaces
    of any length collapse into a single word gap.
    """
    char_unit_ms = dot_unit_ms(wpm)
    gap_unit_ms = dot_unit_ms(farnsworth_wpm) if farnsworth_wpm else char_unit_ms
    if jitter and rng is None:
        rng = random.Random()

    def jittered(duration_ms: float) -> float:
        if not jitter:
            return duration_ms
        return duration_ms * (1.0 + rng.uniform(-jitter, jitter))

    elements: list[Element] = []
    # Whether a space has been seen since the last real (mapped) character
    # -- determines whether the *next* character gets a word gap or an
    # ordinary character gap in front of it.
    pending_word_gap = False

    for index, raw_char in enumerate(text):
        if raw_char == " ":
            if elements:  # no leading word gap before the very first character
                pending_word_gap = True
            continue

        morse = CHAR_TO_MORSE.get(raw_char.upper())
        if morse is None:
            continue  # unsupported character: skip without disturbing pending_word_gap

        if elements:
            gap_ms = (WORD_GAP_UNITS if pending_word_gap else CHAR_GAP_UNITS) * gap_unit_ms
            elements.append(Element(is_on=False, duration_ms=jittered(gap_ms), text_index=index))
        pending_word_gap = False

        for i, symbol in enumerate(morse):
            if i > 0:
                elements.append(
                    Element(
                        is_on=False,
                        duration_ms=jittered(INTRA_CHAR_GAP_UNITS * char_unit_ms),
                        text_index=index,
                    )
                )
            duration = (DOT_UNITS if symbol == "." else DASH_UNITS) * char_unit_ms
            elements.append(Element(is_on=True, duration_ms=jittered(duration), text_index=index))

    return elements
