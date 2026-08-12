"""Unit tests for AdaptiveMorseDecoder driven directly with synthetic hop
sequences (fixed (is_on, duration_ms) lists), independent of the tone
detector/audio pipeline -- see test_decoder_synthetic.py for full end-to-end
audio-based coverage."""

from cwrobot.decoder.timing import AdaptiveMorseDecoder, wpm_from_dot_unit_ms

HOP_MS = 4.0


def _feed(decoder: AdaptiveMorseDecoder, runs: list[tuple[bool, float]]) -> str:
    """Feed whole (is_on, duration_ms) runs as a sequence of HOP_MS-sized hops."""
    output = ""
    for is_on, duration_ms in runs:
        remaining = duration_ms
        while remaining > 0:
            hop = min(HOP_MS, remaining)
            output += decoder.feed_hop(is_on, hop)
            remaining -= hop
    return output


def test_bootstrap_then_decode_simple_word():
    # "E" = "." and "T" = "-", sent as one word (no space -- only a char gap
    # separates them) at a clean 20 WPM (dot=60ms, dash=180ms, gaps 180/420).
    runs = [
        (False, 40),
        (True, 60),  # E: dot
        (False, 180),  # char gap (< word gap -- no space between E and T)
        (True, 180),  # T: dash
        (False, 420),  # word gap -- flushes the last char
    ]
    decoder = AdaptiveMorseDecoder()
    output = _feed(decoder, runs)
    assert output.strip() == "ET"


def test_unrecognized_sequence_decodes_as_asterisk():
    # An absurdly long run of dots that doesn't correspond to any table entry.
    runs = [(False, 40)]
    for _ in range(8):
        runs.append((True, 60))
        runs.append((False, 60))
    runs.append((False, 400))  # flush
    decoder = AdaptiveMorseDecoder()
    output = _feed(decoder, runs)
    assert "*" in output


def test_reset_reverts_to_default_speed_and_clears_buffer():
    decoder = AdaptiveMorseDecoder()
    _feed(decoder, [(False, 40), (True, 30), (False, 30), (True, 30)])
    decoder.reset(wpm=15)
    assert decoder.current_wpm == 15
    assert decoder._symbol_buffer == ""


def test_wpm_from_dot_unit_matches_paris_formula():
    # Standard calibration point: 60ms dot unit == 20 WPM (PARIS word).
    assert abs(wpm_from_dot_unit_ms(60.0) - 20.0) < 1e-9


def test_pure_silence_yields_no_output():
    decoder = AdaptiveMorseDecoder()
    output = _feed(decoder, [(False, 3000)])
    assert output == ""


def _runs_for_text_at_wpm(text: str, wpm: float) -> list[tuple[bool, float]]:
    """Build (is_on, duration_ms) runs for `text` at `wpm`, reusing the TX
    encoder's timing (tx/encoder.py) so this test's notion of "correct
    timing" can't silently drift from what real transmissions look like."""
    from cwrobot.tx.encoder import text_to_elements

    return [(e.is_on, e.duration_ms) for e in text_to_elements(text, wpm=wpm)]


def test_recovers_when_dot_unit_is_badly_stuck_too_small_at_high_wpm():
    # Reproduces a real reported failure: a clean, fast, correctly-timed
    # signal decodes as a long run of lone "T"s once dot_unit_ms is badly
    # stuck too small (e.g. left over from an earlier noise-poisoned
    # bootstrap) -- every real dot then measures over the dot/dash
    # boundary (misclassified as a dash), and every ordinary intra-/inter-
    # character gap measures over the char/word-gap boundaries too (both
    # thresholds scale off the same too-small dot_unit_ms), splitting the
    # whole message into isolated one-mark "characters".
    wpm = 35
    runs = _runs_for_text_at_wpm("CQ CQ DE TEST", wpm=wpm)

    decoder = AdaptiveMorseDecoder()
    decoder._bootstrap_active = False
    decoder.dot_unit_ms = 12.0  # MIN_DOT_UNIT_MS: ~2.9x too small for 35 WPM (~34 ms dot)
    decoder._dot_unit_ema = decoder.dot_unit_ms

    output = _feed(decoder, runs)
    output += _feed(decoder, [(False, 3000)])  # flush the trailing character

    # The very first character or two may still come out corrupted while
    # enough evidence accumulates to recalibrate -- there's no way around
    # that starting from a deliberately-sabotaged estimate with zero prior
    # evidence. What matters is that recovery actually happens well within
    # this short message and stays converged, rather than degrading into a
    # permanent run of lone "T"s for the rest of the transmission (the
    # reported bug).
    assert output.strip().endswith("DE TEST")
    assert 30.0 <= decoder.dot_unit_ms <= 36.0  # converged near the true ~34.3 ms dot


def test_self_heal_recalibration_does_not_reenter_fragile_bootstrap():
    # The actual bug behind the above: self-heal recalibration used to call
    # reset(), which re-enters bootstrap and immediately discards the good
    # seed it just computed, recalibrating instead from whatever the next
    # couple of raw marks happen to be -- if those are noise-influenced or
    # simply unlucky, the estimate gets re-poisoned right back to badly
    # wrong, over and over, without ever converging.
    decoder = AdaptiveMorseDecoder()
    decoder._bootstrap_active = False
    decoder.dot_unit_ms = 12.0
    decoder._dot_unit_ema = decoder.dot_unit_ms

    # Feed enough implausibly-long marks (at this dot_unit_ms) to trigger
    # self-heal recalibration.
    from cwrobot.decoder.timing import IMPLAUSIBLE_MARKS_BEFORE_RECALIBRATION

    runs: list[tuple[bool, float]] = [(False, 40)]
    for _ in range(IMPLAUSIBLE_MARKS_BEFORE_RECALIBRATION):
        runs.append((True, 103.0))  # ~6.9x the bad dot_unit_ms -- implausible
        runs.append((False, 40))
    _feed(decoder, runs)

    assert decoder._bootstrap_active is False
    assert 30.0 <= decoder.dot_unit_ms <= 36.0  # converged near the true ~34.3 ms dot


def test_dash_classification_does_not_decay_implausible_pressure():
    # Only a *dot* classification is trusted evidence that dot_unit_ms is
    # currently correct (see module docstring: dots are the authoritative
    # estimator). A dash landing "in range" doesn't prove that -- it can
    # happen purely by coincidence even when dot_unit_ms is badly wrong
    # (see the next test) -- so it must not reset the self-heal signal.
    decoder = AdaptiveMorseDecoder()
    decoder._bootstrap_active = False
    decoder.dot_unit_ms = 24.0
    decoder._dot_unit_ema = decoder.dot_unit_ms

    decoder._classify_mark(200.0)  # implausibly long -> pressure 1
    assert decoder._implausible_mark_pressure == 1
    decoder._classify_mark(68.0)  # ordinary dash at this dot_unit_ms
    assert decoder._implausible_mark_pressure == 1  # unchanged, not decayed


def test_self_heal_recovers_promptly_despite_dashes_landing_in_range():
    # Reproduces a pattern measured in real captured hardware audio: a
    # single short mark (e.g. a brief noise click) poisons dot_unit_ms down
    # to ~24ms, and the real signal's own dashes (~60-72ms) happen to still
    # measure "in range" as ordinary dashes against that wrong, too-small
    # estimate (2x24=48 < ~65 < 6x24=144) purely by coincidence. Before the
    # dash-doesn't-decay fix above, each such dash reset the implausible-
    # mark pressure counter, and full recovery took ~14 marks (~12 real
    # seconds on the actual recording) instead of a handful.
    decoder = AdaptiveMorseDecoder()
    decoder._bootstrap_active = False
    decoder.dot_unit_ms = 24.0
    decoder._dot_unit_ema = decoder.dot_unit_ms

    durations = [200.0, 68.0, 212.0, 68.0, 64.0, 72.0, 200.0]
    for count, duration in enumerate(durations, start=1):
        decoder._classify_mark(duration)
        if decoder.dot_unit_ms > 30.0:
            break

    assert decoder.dot_unit_ms > 30.0  # actually recalibrated, not still stuck at 24
    assert 60.0 <= decoder.dot_unit_ms <= 75.0  # converged near the true ~65-70 ms dot
    assert count <= 7  # recovers within this same short mark sequence


def test_dash_nudge_ignores_ordinary_jitter_but_corrects_moderate_miscalibration():
    decoder = AdaptiveMorseDecoder()
    decoder._bootstrap_active = False
    decoder.dot_unit_ms = 25.0
    decoder._dot_unit_ema = decoder.dot_unit_ms

    # A dash only ~20% longer than 3x the current estimate is well within
    # ordinary +/-30% hand-keyed jitter (see tests/synth.py) and must not
    # nudge dot_unit_ms at all -- see test_decodes_hand_keyed_jitter, which
    # regression-caught an earlier version of this nudge being too eager.
    decoder._classify_mark(25.0 * 3 * 1.2)
    assert decoder.dot_unit_ms == 25.0

    # A dash implying a dot length comfortably past the trigger ratio is a
    # genuine miscalibration signal (never produced by jitter alone) and
    # should nudge the estimate upward.
    decoder._classify_mark(130.0)  # implies a ~43.3 ms dot, 1.73x the current estimate
    assert decoder.dot_unit_ms > 25.0
