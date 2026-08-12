"""Unit tests for tx.encoder.text_to_elements: pure timing logic, no audio."""

from __future__ import annotations

import random

import pytest

from cwrobot.tx.encoder import (
    CHAR_GAP_UNITS,
    DASH_UNITS,
    DOT_UNITS,
    INTRA_CHAR_GAP_UNITS,
    MANUAL_KEYING_JITTER_MAX,
    MANUAL_KEYING_JITTER_MIN,
    MANUAL_KEYING_LEVEL_MAX,
    MANUAL_KEYING_LEVEL_MIN,
    WORD_GAP_UNITS,
    dot_unit_ms,
    manual_keying_jitter_from_level,
    text_to_elements,
)


def test_dot_unit_ms_paris_formula():
    # 20 WPM -> 60 ms/dot (the textbook PARIS-word value).
    assert dot_unit_ms(20) == pytest.approx(60.0)


def test_single_dot_character_has_no_surrounding_gaps():
    elements = text_to_elements("E", wpm=20)
    unit = dot_unit_ms(20)
    assert len(elements) == 1
    assert elements[0].is_on is True
    assert elements[0].duration_ms == pytest.approx(DOT_UNITS * unit)


def test_dash_is_three_units():
    elements = text_to_elements("T", wpm=20)
    unit = dot_unit_ms(20)
    assert len(elements) == 1
    assert elements[0].is_on is True
    assert elements[0].duration_ms == pytest.approx(DASH_UNITS * unit)


def test_intra_character_gap_between_elements():
    # "A" = .- : dot, intra-char gap, dash
    elements = text_to_elements("A", wpm=20)
    unit = dot_unit_ms(20)
    assert [e.is_on for e in elements] == [True, False, True]
    assert elements[0].duration_ms == pytest.approx(DOT_UNITS * unit)
    assert elements[1].duration_ms == pytest.approx(INTRA_CHAR_GAP_UNITS * unit)
    assert elements[2].duration_ms == pytest.approx(DASH_UNITS * unit)


def test_character_gap_within_a_word():
    # "ET" = . <char gap> - : two single-element characters separated by a
    # 3-unit inter-character gap (not the 1-unit intra-character gap).
    elements = text_to_elements("ET", wpm=20)
    unit = dot_unit_ms(20)
    assert [e.is_on for e in elements] == [True, False, True]
    assert elements[1].duration_ms == pytest.approx(CHAR_GAP_UNITS * unit)


def test_word_gap_between_words():
    elements = text_to_elements("E E", wpm=20)
    unit = dot_unit_ms(20)
    on_off = [(e.is_on, e.duration_ms) for e in elements]
    assert on_off == [
        (True, pytest.approx(DOT_UNITS * unit)),
        (False, pytest.approx(WORD_GAP_UNITS * unit)),
        (True, pytest.approx(DOT_UNITS * unit)),
    ]


def test_repeated_spaces_collapse_into_a_single_word_gap():
    elements = text_to_elements("E   E", wpm=20)
    assert [e.is_on for e in elements] == [True, False, True]
    assert elements[1].duration_ms == pytest.approx(WORD_GAP_UNITS * dot_unit_ms(20))


def test_unsupported_character_is_skipped_without_a_stray_gap():
    # "~" has no Morse mapping; "E~T" should behave exactly like "ET" in
    # is_on/duration terms (text_index legitimately differs, since "T" sits
    # at a different position in the two source strings).
    with_stray = text_to_elements("E~T", wpm=20)
    without = text_to_elements("ET", wpm=20)
    assert [(e.is_on, e.duration_ms) for e in with_stray] == [(e.is_on, e.duration_ms) for e in without]


def test_leading_and_trailing_spaces_produce_no_dangling_gap():
    elements = text_to_elements("  E  ", wpm=20)
    assert [e.is_on for e in elements] == [True]


def test_empty_text_produces_no_elements():
    assert text_to_elements("", wpm=20) == []
    assert text_to_elements("   ", wpm=20) == []


def test_lowercase_input_is_encoded_same_as_uppercase():
    assert text_to_elements("sos", wpm=25) == text_to_elements("SOS", wpm=25)


def test_text_index_points_at_the_source_character_in_the_original_string():
    # "A B": index 0 = 'A' (.-), index 2 = 'B' (-...) -- index 1 (the space)
    # never appears since spaces themselves never produce an element.
    elements = text_to_elements("A B", wpm=20)
    assert {e.text_index for e in elements if e.text_index < 2} == {0}
    assert {e.text_index for e in elements if e.text_index >= 2} == {2}


def test_text_index_survives_unsupported_characters_at_their_real_position():
    elements = text_to_elements("A~B", wpm=20)
    assert {e.text_index for e in elements if e.text_index < 2} == {0}
    assert {e.text_index for e in elements if e.text_index >= 2} == {2}


def test_farnsworth_stretches_only_inter_character_and_word_gaps():
    # At farnsworth_wpm < wpm, dot/dash/intra-char-gap durations stay at the
    # full wpm's speed, but char/word gaps use the slower farnsworth speed.
    fast_unit = dot_unit_ms(30)
    slow_unit = dot_unit_ms(10)
    elements = text_to_elements("ET E", wpm=30, farnsworth_wpm=10)
    assert elements[0].duration_ms == pytest.approx(DOT_UNITS * fast_unit)  # E
    assert elements[1].duration_ms == pytest.approx(CHAR_GAP_UNITS * slow_unit)  # char gap
    assert elements[2].duration_ms == pytest.approx(DASH_UNITS * fast_unit)  # T
    assert elements[3].duration_ms == pytest.approx(WORD_GAP_UNITS * slow_unit)  # word gap
    assert elements[4].duration_ms == pytest.approx(DOT_UNITS * fast_unit)  # E


def test_zero_jitter_matches_exact_textbook_timing():
    exact = text_to_elements("PARIS PARIS", wpm=20)
    jittered_but_zero = text_to_elements("PARIS PARIS", wpm=20, jitter=0.0)
    assert exact == jittered_but_zero


def test_jitter_perturbs_every_element_within_bounds_but_not_the_structure():
    wpm = 20
    jitter = 0.3
    rng = random.Random(1234)
    exact = text_to_elements("PARIS PARIS", wpm=wpm)
    jittered = text_to_elements("PARIS PARIS", wpm=wpm, jitter=jitter, rng=rng)

    # Same number of elements, same is_on sequence, same text_index sequence
    # -- jitter only ever touches duration_ms, never the Morse structure.
    assert [e.is_on for e in jittered] == [e.is_on for e in exact]
    assert [e.text_index for e in jittered] == [e.text_index for e in exact]

    saw_a_difference = False
    for exact_el, jittered_el in zip(exact, jittered, strict=True):
        lo = exact_el.duration_ms * (1.0 - jitter)
        hi = exact_el.duration_ms * (1.0 + jitter)
        assert lo <= jittered_el.duration_ms <= hi
        if jittered_el.duration_ms != exact_el.duration_ms:
            saw_a_difference = True
    assert saw_a_difference  # jitter actually did something across this many elements


def test_jitter_is_reproducible_with_a_seeded_rng():
    first = text_to_elements("CQ CQ DE TEST", wpm=25, jitter=0.2, rng=random.Random(42))
    second = text_to_elements("CQ CQ DE TEST", wpm=25, jitter=0.2, rng=random.Random(42))
    assert first == second


def test_jitter_without_an_explicit_rng_still_works_and_varies():
    # No seeded rng passed: text_to_elements must create its own generator
    # rather than requiring the caller to always supply one.
    a = text_to_elements("PARIS PARIS PARIS PARIS", wpm=20, jitter=0.3)
    b = text_to_elements("PARIS PARIS PARIS PARIS", wpm=20, jitter=0.3)
    assert any(x.duration_ms != y.duration_ms for x, y in zip(a, b, strict=True))


def test_manual_keying_jitter_from_level_endpoints():
    assert manual_keying_jitter_from_level(MANUAL_KEYING_LEVEL_MIN) == pytest.approx(MANUAL_KEYING_JITTER_MIN)
    assert manual_keying_jitter_from_level(MANUAL_KEYING_LEVEL_MAX) == pytest.approx(MANUAL_KEYING_JITTER_MAX)


def test_manual_keying_jitter_from_level_is_monotonically_increasing():
    values = [manual_keying_jitter_from_level(level) for level in range(MANUAL_KEYING_LEVEL_MIN, MANUAL_KEYING_LEVEL_MAX + 1)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)  # every level maps to a distinct jitter


def test_manual_keying_jitter_from_level_clamps_out_of_range_input():
    assert manual_keying_jitter_from_level(0) == pytest.approx(MANUAL_KEYING_JITTER_MIN)
    assert manual_keying_jitter_from_level(100) == pytest.approx(MANUAL_KEYING_JITTER_MAX)
