"""Regression coverage for the adaptive Goertzel window/bandwidth (see
dsp/tone_detector.py's ADAPTIVE_WINDOW_K comment): the analysis window
widens for a slow sender (narrower bins, better noise rejection) and stays
at today's fixed size for a fast one, retuned only at safe (confirmed-
silent) hop boundaries so an in-progress mark is never corrupted.
"""

import numpy as np

from cwrobot.decoder.decoder import CwDecoderCore
from cwrobot.decoder.timing import MAX_DOT_UNIT_MS, MIN_DOT_UNIT_MS
from cwrobot.dsp.tone_detector import (
    MAX_WINDOW_MS,
    PROCESSING_SAMPLE_RATE,
    WINDOW_SAMPLES,
    GoertzelToneDetector,
)
from synth import generate_morse_audio

SAMPLE_RATE = 48_000
PITCH_HZ = 600
CHUNK_SIZE = 240
DEFAULT_BANDWIDTH_HZ = (2 * 1 + 1) * (PROCESSING_SAMPLE_RATE / WINDOW_SAMPLES)  # bin_span=1 default


def _decode(core: CwDecoderCore, audio: np.ndarray) -> str:
    output = ""
    for i in range(0, len(audio), CHUNK_SIZE):
        output += core.process_block(audio[i : i + CHUNK_SIZE]).decoded_text
    return output


def test_window_widens_for_slow_sender_and_narrows_for_fast_sender():
    text = "CQ CQ DE TEST"

    slow_core = CwDecoderCore(sample_rate=SAMPLE_RATE, target_pitch_hz=PITCH_HZ)
    slow_audio = generate_morse_audio(text, wpm=5, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE)
    assert _decode(slow_core, slow_audio).strip() == text

    fast_core = CwDecoderCore(sample_rate=SAMPLE_RATE, target_pitch_hz=PITCH_HZ)
    fast_audio = generate_morse_audio(text, wpm=60, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE)
    assert _decode(fast_core, fast_audio).strip() == text

    # 60 WPM sits at exactly the ratio the fixed window was already tuned
    # for, so it should stay at (or very near) today's default bandwidth,
    # while 5 WPM should have widened the window and thus narrowed the band.
    assert fast_core.effective_bandwidth_hz == DEFAULT_BANDWIDTH_HZ
    assert slow_core.effective_bandwidth_hz < fast_core.effective_bandwidth_hz


def test_manual_mode_reproduces_fixed_window_behavior_when_auto_off():
    text = "CQ CQ DE TEST"
    for wpm in (5, 15, 25, 40, 60):
        core = CwDecoderCore(sample_rate=SAMPLE_RATE, target_pitch_hz=PITCH_HZ)
        core.auto_bandwidth = False
        audio = generate_morse_audio(text, wpm=wpm, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE)
        assert _decode(core, audio).strip() == text
        assert core.effective_bandwidth_hz == DEFAULT_BANDWIDTH_HZ


def test_retune_deferred_until_safe_off_hop():
    detector = GoertzelToneDetector(target_freq_hz=PITCH_HZ, sample_rate=PROCESSING_SAMPLE_RATE)

    # Feed a sustained tone long enough to be confirmed "on".
    t = np.arange(int(PROCESSING_SAMPLE_RATE * 0.3)) / PROCESSING_SAMPLE_RATE
    tone = (0.6 * np.sin(2 * np.pi * PITCH_HZ * t)).astype(np.float32)
    hops = detector.process(tone)
    assert hops[-1].is_on

    detector.request_retune(dot_unit_ms=240.0)  # a much slower speed -> bigger window
    assert detector.window_samples == WINDOW_SAMPLES  # not applied yet

    # Still mid-tone: further "on" hops must not pick up the pending resize.
    more_hops = detector.process(tone[: PROCESSING_SAMPLE_RATE // 10])
    assert any(hop.is_on for hop in more_hops)
    assert detector.window_samples == WINDOW_SAMPLES

    # Now silence -- the resize should land on the next confirmed-off hop.
    silence = np.zeros(int(PROCESSING_SAMPLE_RATE * 0.3), dtype=np.float32)
    detector.process(silence)
    assert detector.window_samples > WINDOW_SAMPLES


def test_manual_toggle_restores_default_window():
    core = CwDecoderCore(sample_rate=SAMPLE_RATE, target_pitch_hz=PITCH_HZ)
    slow_audio = generate_morse_audio("CQ CQ DE TEST", wpm=5, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE)
    _decode(core, slow_audio)
    assert core.effective_bandwidth_hz < DEFAULT_BANDWIDTH_HZ  # widened by auto mode

    core.auto_bandwidth = False
    # The manual-window request also only applies on a safe off-hop -- feed
    # some silence to let it land.
    silence = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32)
    for i in range(0, len(silence), CHUNK_SIZE):
        core.process_block(silence[i : i + CHUNK_SIZE])

    assert core.effective_bandwidth_hz == DEFAULT_BANDWIDTH_HZ


def test_dot_unit_extreme_bounds_clamp_window():
    max_samples = round(MAX_WINDOW_MS * PROCESSING_SAMPLE_RATE / 1000.0)

    fast_detector = GoertzelToneDetector(target_freq_hz=PITCH_HZ, sample_rate=PROCESSING_SAMPLE_RATE)
    fast_detector.request_retune(dot_unit_ms=MIN_DOT_UNIT_MS)
    fast_detector.process(np.zeros(fast_detector.hop_samples * 4, dtype=np.float32))
    assert fast_detector.window_samples == WINDOW_SAMPLES  # already the floor, no change

    slow_detector = GoertzelToneDetector(target_freq_hz=PITCH_HZ, sample_rate=PROCESSING_SAMPLE_RATE)
    slow_detector.request_retune(dot_unit_ms=MAX_DOT_UNIT_MS)
    slow_detector.process(np.zeros(slow_detector.hop_samples * 4, dtype=np.float32))
    assert WINDOW_SAMPLES <= slow_detector.window_samples <= max_samples
    assert slow_detector.window_samples == max_samples


def test_resize_does_not_spike_false_decodes_from_noise():
    """A real slow transmission (forcing at least one auto-bandwidth resize)
    followed by broadband noise should not leave decoding meaningfully
    noisier than the pre-existing squelch guarantee (see test_squelch.py) --
    the resize's tracker reset is a bounded, already-tested warm-up cost,
    not a new false-trigger source."""

    def decode_signal_then_noise(seed: int) -> str:
        rng = np.random.default_rng(seed)
        signal = generate_morse_audio("SOS", wpm=8, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE)
        noise = rng.normal(0, 0.05, size=int(SAMPLE_RATE * 2.5)).astype(np.float32)
        audio = np.concatenate([signal, noise])
        core = CwDecoderCore(sample_rate=SAMPLE_RATE, target_pitch_hz=PITCH_HZ)
        return _decode(core, audio)

    garbage_lengths = []
    for seed in range(10):
        output = decode_signal_then_noise(seed)
        prefix = "SOS "
        garbage_lengths.append(len(output) - len(prefix) if output.startswith(prefix) else len(output))

    average_garbage = sum(garbage_lengths) / len(garbage_lengths)
    assert average_garbage < 30, f"average false-decode length too high after a resize: {average_garbage}"
