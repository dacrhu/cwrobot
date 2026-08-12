import numpy as np

from cwrobot.dsp.goertzel import goertzel_power


def test_pure_tone_at_target_frequency_has_high_power():
    sample_rate = 8000
    freq = 600
    t = np.arange(128) / sample_rate
    tone = np.sin(2 * np.pi * freq * t)

    on_target = goertzel_power(tone, sample_rate, freq)
    off_target = goertzel_power(tone, sample_rate, freq * 2)

    assert on_target > off_target * 10


def test_silence_has_near_zero_power():
    silence = np.zeros(128)
    power = goertzel_power(silence, 8000, 600)
    assert power < 1e-6


def test_too_short_input_returns_zero():
    assert goertzel_power(np.array([1.0]), 8000, 600) == 0.0
    assert goertzel_power(np.array([]), 8000, 600) == 0.0
