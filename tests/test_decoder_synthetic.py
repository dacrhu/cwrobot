import numpy as np
import pytest

from cwrobot.decoder.decoder import CwDecoderCore
from synth import generate_morse_audio

SAMPLE_RATE = 48_000
PITCH_HZ = 600
CHUNK_SIZE = 240  # matches AudioCapture's default 5 ms blocksize


def _decode(audio: np.ndarray, pitch_hz: float = PITCH_HZ) -> str:
    core = CwDecoderCore(sample_rate=SAMPLE_RATE, target_pitch_hz=pitch_hz)
    output = ""
    for i in range(0, len(audio), CHUNK_SIZE):
        result = core.process_block(audio[i : i + CHUNK_SIZE])
        output += result.decoded_text
    return output


@pytest.mark.parametrize("wpm", [5, 15, 25, 40, 60])
def test_decodes_clean_signal_across_wpm_range(wpm):
    text = "CQ CQ DE TEST"
    audio = generate_morse_audio(text, wpm=wpm, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE)
    decoded = _decode(audio)
    assert decoded.strip() == text


@pytest.mark.parametrize("jitter", [0.0, 0.15, 0.30])
def test_decodes_hand_keyed_jitter(jitter):
    text = "THE QUICK BROWN FOX"
    audio = generate_morse_audio(
        text, wpm=18, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE, jitter=jitter, seed=42
    )
    decoded = _decode(audio)
    assert decoded.strip() == text


def test_tracks_accelerating_speed_mid_transmission():
    """Simulates a hand-keyer speeding up partway through -- the adaptive
    dot-unit estimator should keep up without needing separate decoders."""
    core = CwDecoderCore(sample_rate=SAMPLE_RATE, target_pitch_hz=PITCH_HZ)
    first_half = generate_morse_audio("SLOW START", wpm=12, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE)
    second_half = generate_morse_audio("FAST END", wpm=30, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE)
    audio = np.concatenate([first_half, second_half])

    output = ""
    for i in range(0, len(audio), CHUNK_SIZE):
        result = core.process_block(audio[i : i + CHUNK_SIZE])
        output += result.decoded_text
    assert output.strip() == "SLOW START FAST END"


def test_wpm_estimate_is_reasonable_after_decoding():
    audio = generate_morse_audio("PARIS PARIS PARIS", wpm=20, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE)
    core = CwDecoderCore(sample_rate=SAMPLE_RATE, target_pitch_hz=PITCH_HZ)
    for i in range(0, len(audio), CHUNK_SIZE):
        core.process_block(audio[i : i + CHUNK_SIZE])
    assert 15 <= core.current_wpm <= 25


def test_tolerates_a_few_tens_of_hz_of_mistuning():
    text = "VVV"
    audio = generate_morse_audio(text, wpm=20, pitch_hz=PITCH_HZ + 40, sample_rate=SAMPLE_RATE)
    decoded = _decode(audio, pitch_hz=PITCH_HZ)
    assert decoded.strip() == text


def test_tolerates_broadband_noise():
    text = "CQ CQ DE TEST"
    audio = generate_morse_audio(
        text, wpm=20, pitch_hz=PITCH_HZ, sample_rate=SAMPLE_RATE, amplitude=0.6, noise_amplitude=0.3, seed=7
    )
    decoded = _decode(audio)
    assert decoded.strip() == text


def test_silence_only_produces_no_output():
    silence = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
    decoded = _decode(silence)
    assert decoded == ""
