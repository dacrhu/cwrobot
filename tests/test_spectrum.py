import numpy as np

from cwrobot.dsp.spectrum import SpectrumAnalyzer


def test_pure_tone_peak_is_near_expected_frequency():
    sample_rate = 8000
    freq = 600
    analyzer = SpectrumAnalyzer(sample_rate=sample_rate, fft_size=512)

    t = np.arange(1024, dtype=np.float32) / sample_rate
    tone = np.sin(2 * np.pi * freq * t).astype(np.float32)

    result = analyzer.analyze(tone, center_hz=freq, half_span_hz=500)
    peak_freq = result.freqs_hz[np.argmax(result.power_db)]

    # rFFT bin resolution at fft_size=512, sample_rate=8000 is ~15.6 Hz/bin
    assert abs(peak_freq - freq) < 20


def test_short_input_is_zero_padded_not_erroring():
    analyzer = SpectrumAnalyzer(sample_rate=8000, fft_size=512)
    short_samples = np.ones(10, dtype=np.float32)
    result = analyzer.analyze(short_samples, center_hz=600, half_span_hz=200)
    assert len(result.freqs_hz) == len(result.power_db)
    assert len(result.freqs_hz) > 0


def test_band_slice_respects_half_span():
    analyzer = SpectrumAnalyzer(sample_rate=8000, fft_size=512)
    samples = np.random.default_rng(0).normal(size=1024).astype(np.float32)
    result = analyzer.analyze(samples, center_hz=600, half_span_hz=100)
    assert result.freqs_hz.min() >= 500
    assert result.freqs_hz.max() <= 700
