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
from cwrobot.decoder.timing import AdaptiveMorseDecoder
from cwrobot.dsp.tone_detector import GoertzelToneDetector, StreamingDecimator

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


def _decode_noise_at_ratio(seed: int, ratio: float, duration_s: float = 5.0, amplitude: float = 0.05) -> int:
    """Like _decode_noise, but drives GoertzelToneDetector directly (rather
    than via CwDecoderCore) so squelch_ratio can be set to something other
    than the decoder's default -- returns the decoded output's length."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, amplitude, size=int(SAMPLE_RATE * duration_s)).astype(np.float32)
    decimator = StreamingDecimator(input_rate=SAMPLE_RATE)
    detector = GoertzelToneDetector(target_freq_hz=600)
    detector.squelch_ratio = ratio
    morse = AdaptiveMorseDecoder()
    output = ""
    for i in range(0, len(noise), CHUNK_SIZE):
        for hop in detector.process(decimator.process(noise[i : i + CHUNK_SIZE])):
            output += morse.feed_hop(hop.is_on, hop.duration_ms)
    return len(output)


def test_raising_squelch_ratio_actually_reduces_false_output():
    """Regression guard for a cold-start bug (see dsp/tone_detector.py's
    GoertzelToneDetector._evaluate_window window-fill guard): the analysis
    window starts partly zero-padded, and Goertzel power computed over that
    padding could seed noise_floor/peak/squelch_mean with an artifact,
    occasionally latching a false "on" state right at startup that then
    starved squelch_mean of real data -- entrenching a false-positive run
    that raising squelch_ratio did essentially nothing to prevent (verified
    before the fix: ratio=8 through ratio=30 produced byte-for-byte
    identical decoded noise garbage across 20 seeds). A stricter ratio must
    make a real difference."""
    low_ratio_lengths = [_decode_noise_at_ratio(seed, ratio=3.0) for seed in range(15)]
    high_ratio_lengths = [_decode_noise_at_ratio(seed, ratio=14.0) for seed in range(15)]
    avg_low = sum(low_ratio_lengths) / len(low_ratio_lengths)
    avg_high = sum(high_ratio_lengths) / len(high_ratio_lengths)
    assert avg_high < avg_low * 0.5, f"squelch_ratio had little effect: avg_low={avg_low}, avg_high={avg_high}"


def test_squelch_led_never_reads_closed_while_a_hop_is_accepted():
    """The 'squelch open' indicator (squelch_currently_open, driving the RX
    panel's green LED) must never disagree with what the decoder is
    actually doing: previously it was a completely separate, memory-less
    computation that could read "closed" while hops were still being
    accepted into decoding (via the real gate's hang-time leniency),
    confirmed against a real noise trace -- see squelch_currently_open's
    docstring."""
    import sys

    sys.path.insert(0, "tests")
    from synth import generate_morse_audio  # noqa: E402

    def _assert_led_consistent(samples: np.ndarray, ratio: float) -> None:
        decimator = StreamingDecimator(input_rate=SAMPLE_RATE)
        detector = GoertzelToneDetector(target_freq_hz=600)
        detector.squelch_ratio = ratio
        for i in range(0, len(samples), CHUNK_SIZE):
            for hop in detector.process(decimator.process(samples[i : i + CHUNK_SIZE])):
                if hop.is_on:
                    assert detector.squelch_currently_open, "LED read closed while a hop was accepted"

    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.05, size=int(SAMPLE_RATE * 5.0)).astype(np.float32)
    _assert_led_consistent(noise, ratio=14.0)

    audio = generate_morse_audio("CQ CQ DE TEST", wpm=20, pitch_hz=600, sample_rate=SAMPLE_RATE)
    _assert_led_consistent(audio, ratio=14.0)
