"""Squelch regression coverage.

Squelch is a statistical test (see dsp/tone_detector.py's SQUELCH_* constants
and comments), not a hard guarantee -- band noise can occasionally produce a
burst of correlated in-band energy that briefly mimics real keying, and no
simple envelope-based squelch can perfectly rule that out. These tests check
that squelch keeps the *average* false-output rate low across many
independent noise realizations, not that it is perfectly silent on every one.
"""

import numpy as np

from cwrobot.decoder.decoder import CwDecoderCore

SAMPLE_RATE = 48_000
CHUNK_SIZE = 240


def _decode_noise(seed: int, duration_s: float = 5.0, amplitude: float = 0.05) -> str:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, amplitude, size=int(SAMPLE_RATE * duration_s)).astype(np.float32)
    core = CwDecoderCore(sample_rate=SAMPLE_RATE, target_pitch_hz=600)
    output = ""
    for i in range(0, len(noise), CHUNK_SIZE):
        output += core.process_block(noise[i : i + CHUNK_SIZE]).decoded_text
    return output


def test_squelch_keeps_average_false_output_low_across_many_noise_realizations():
    lengths = [len(_decode_noise(seed)) for seed in range(20)]
    average_length = sum(lengths) / len(lengths)
    # Pre-squelch, this averaged 50+ characters of continuous garbage per
    # 5 s clip; squelch brings the average down substantially, even though
    # a handful of individual seeds still produce a short burst (see the
    # module docstring) -- this is a regression guard, not a claim of
    # perfectly silent squelch.
    assert average_length < 25, f"average false-decode length too high: {average_length}"


def test_squelch_does_not_delay_a_real_signal_start():
    """A real tone beginning immediately (no leading silence) must not be
    clipped by squelch's warm-up window."""
    import sys

    sys.path.insert(0, "tests")
    from synth import generate_morse_audio  # noqa: E402

    audio = generate_morse_audio("SOS", wpm=20, pitch_hz=600, sample_rate=SAMPLE_RATE)
    core = CwDecoderCore(sample_rate=SAMPLE_RATE, target_pitch_hz=600)
    output = ""
    for i in range(0, len(audio), CHUNK_SIZE):
        output += core.process_block(audio[i : i + CHUNK_SIZE]).decoded_text
    assert output.strip() == "SOS"
