"""Synthetic Morse audio generator, for decoder tests only.

Deliberately independent from cwrobot.tx.encoder (added in milestone 5):
if the decoder tests validated against the exact same code that drives the
TX encoder, a shared bug in the timing logic could cancel itself out.
"""

from __future__ import annotations

import numpy as np

from cwrobot.decoder.morse_table import CHAR_TO_MORSE

RAMP_MS = 4.0  # raised-cosine edge ramp, avoids key-click artifacts


def _element_durations_ms(text: str, wpm: float) -> list[tuple[bool, float]]:
    """Return a list of (is_mark, duration_ms) elements for `text` at `wpm`,
    using standard 1x/3x/7x dot/dash/gap timing."""
    dot_ms = 1200.0 / wpm
    dash_ms = 3 * dot_ms
    intra_gap_ms = dot_ms
    char_gap_ms = 3 * dot_ms
    word_gap_ms = 7 * dot_ms

    elements: list[tuple[bool, float]] = []
    words = text.upper().split(" ")
    for word_index, word in enumerate(words):
        for char_index, char in enumerate(word):
            morse = CHAR_TO_MORSE.get(char)
            if morse is None:
                continue
            for symbol_index, symbol in enumerate(morse):
                if symbol_index > 0:
                    elements.append((False, intra_gap_ms))
                elements.append((True, dash_ms if symbol == "-" else dot_ms))
            if char_index < len(word) - 1:
                elements.append((False, char_gap_ms))
        if word_index < len(words) - 1:
            elements.append((False, word_gap_ms))
    return elements


def generate_morse_audio(
    text: str,
    wpm: float,
    pitch_hz: float,
    sample_rate: int,
    amplitude: float = 0.6,
    jitter: float = 0.0,
    noise_amplitude: float = 0.0,
    seed: int = 0,
    trailing_silence_ms: float | None = None,
) -> np.ndarray:
    """Render `text` as a mono float32 CW audio buffer.

    `jitter` (0.0-1.0) randomly perturbs each element's duration by up to
    +/-jitter*100% to simulate an inconsistent hand-keyer's fist.
    `noise_amplitude` adds Gaussian noise to simulate QRM/QRN.
    `trailing_silence_ms` defaults to comfortably more than one word-gap at
    the given `wpm`, so the decoder's final character actually gets flushed
    (a character only flushes on the *next* gap; without trailing silence,
    audio ending mid-mark would silently drop the last character).
    """
    rng = np.random.default_rng(seed)
    elements = _element_durations_ms(text, wpm)

    if trailing_silence_ms is None:
        dot_ms = 1200.0 / wpm
        trailing_silence_ms = 8 * dot_ms + 200.0
    elements.append((False, trailing_silence_ms))

    chunks: list[np.ndarray] = []
    for is_mark, duration_ms in elements:
        if jitter > 0:
            duration_ms *= 1.0 + rng.uniform(-jitter, jitter)
        n_samples = max(1, int(sample_rate * duration_ms / 1000.0))
        if is_mark:
            t = np.arange(n_samples) / sample_rate
            tone = amplitude * np.sin(2 * np.pi * pitch_hz * t)
            ramp_samples = min(int(sample_rate * RAMP_MS / 1000.0), n_samples // 2)
            if ramp_samples > 0:
                ramp = 0.5 * (1 - np.cos(np.pi * np.arange(ramp_samples) / ramp_samples))
                tone[:ramp_samples] *= ramp
                tone[-ramp_samples:] *= ramp[::-1]
            chunks.append(tone.astype(np.float32))
        else:
            chunks.append(np.zeros(n_samples, dtype=np.float32))

    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    if noise_amplitude > 0:
        audio = audio + rng.normal(0, noise_amplitude, size=audio.shape).astype(np.float32)
    return audio
