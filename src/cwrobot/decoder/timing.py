"""Adaptive dot-unit / WPM tracking and dot-dash-to-character classification.

Consumes a stream of tone on/off hops (from dsp.tone_detector) and turns
them into decoded characters, while continuously re-estimating the sender's
current keying speed -- this is what lets the decoder handle both very slow
and very fast signals, and hand-keyed (variable-tempo) sending, without any
fixed WPM assumption.

Two independent estimators track the dot-unit length (see Specifikacio.md
plan section 3.2):
  - a minimum-tracker (authoritative for symbol classification): the
    shortest recent mark can only be a dot, and noise/dashes can never pull
    it down, only genuinely shorter marks can -- so it's self-correcting.
  - an EMA cross-check (diagnostic/smoothing companion, not authoritative).
"""

from __future__ import annotations

from cwrobot.decoder.morse_table import MORSE_TO_CHAR

DEFAULT_WPM = 20.0
MIN_DOT_UNIT_MS = 12.0  # ~100 WPM
MAX_DOT_UNIT_MS = 2000.0  # ~0.6 WPM

# Absolute noise-rejection floor, independent of the (possibly still-wrong)
# dot_unit_ms estimate: genuine Morse elements at any sane hand- or
# machine-sent speed are essentially never this short. Anything shorter is
# treated as a radio/audio-chain glitch (hum, a static crash, a relay
# click) rather than a real keying transition -- this matters most during
# bootstrap, where the estimate isn't trustworthy yet and a single noise
# blip could otherwise get locked in as "the dot length" for the session.
MIN_PLAUSIBLE_ELEMENT_MS = 10.0

# Self-healing: if dot_unit_ms is badly wrong (e.g. bootstrapped from a
# noise glitch), real marks will come out "too long to be a dash" and get
# silently discarded (see _classify_mark) -- a strong signal to recalibrate
# rather than stay stuck mis-decoding for the rest of the session. This is
# tracked as a leaky counter (+1 per implausible mark, -1 -- floored at 0 --
# per normally-classified mark), not a strictly-consecutive streak: when
# dot_unit_ms is badly too small, real *dots* usually still measure under
# the (too-small) dash ceiling and classify "normally" as dashes, so a
# strictly-consecutive streak could reset to 0 on every other mark and
# never reach this threshold at all for perfectly ordinary text (verified
# against a real "CQ CQ DE" style message, which never happens to contain
# enough consecutive genuine dashes to trigger a strict streak). The leaky
# counter still accumulates net upward under that systematic pattern while
# staying immune to a single isolated real QRM-driven implausible mark
# surrounded by otherwise-clean decoding.
IMPLAUSIBLE_MARKS_BEFORE_RECALIBRATION = 3

DOT_UNIT_RISE_PER_MARK = 1.02
EMA_BETA = 0.2
EMA_MAX_RELATIVE_STEP = 0.4

# When a dash is classified normally (i.e. it didn't even reach the
# implausible-mark path above), its implied dot length (duration / 3) is a
# much less reliable dot-unit reference than an actual dot -- so it must
# never pull dot_unit_ms *down* (that stays the dot-only tracker's job in
# _update_dot_unit_estimate). But if dot_unit_ms is stuck moderately too
# *small* (not badly enough to ever hit the implausible-mark path above),
# real dots stop registering as dots at all -- they measure over the
# dot/dash boundary -- so that primary tracker never gets an actual dot to
# correct itself with, and the decoder would otherwise stay stuck wrong
# indefinitely. A partial nudge toward each dash's implied length breaks
# that deadlock -- but only once the gap is *large*: ordinary hand-keyed
# jitter (see tests/synth.py) perturbs each element by at most +/-30%, so a
# correctly-calibrated dot_unit_ms will still occasionally see a dash whose
# *own* implied length is somewhat above the estimate purely from jitter,
# with no real miscalibration at all. Gating on a 1.5x ratio (well above
# that 1.3x jitter ceiling) means only a genuine, substantial miscalibration
# ever triggers this, never ordinary jitter noise.
DASH_IMPLIED_DOT_TRIGGER_RATIO = 1.5
DASH_IMPLIED_DOT_BLEND = 0.5

MARK_DOT_MAX_UNITS = 2.0
MARK_DASH_MAX_UNITS = 6.0
GAP_INTRA_CHAR_MAX_UNITS = 2.0
GAP_WORD_MAX_UNITS = 6.0

SPEED_RESET_SILENCE_MS = 45_000.0

# Cold-start calibration: classifying marks against a generic default WPM
# guess is unreliable if the real speed is far from that guess (e.g. a
# dash at 60 WPM is shorter than a dot at 5 WPM) -- misclassifying even one
# early mark corrupts the first character. So the first few marks are
# buffered raw (not classified) until there's enough data to set a sound
# initial dot_unit_ms, then replayed retroactively.
BOOTSTRAP_MARKS_NEEDED = 2
# Fallback for a very short transmission (e.g. a single "E") that never
# reaches BOOTSTRAP_MARKS_NEEDED: force calibration from whatever marks
# exist once a long silence has clearly ended the transmission, rather than
# withholding output indefinitely.
BOOTSTRAP_FORCE_SILENCE_MS = 5000.0


def wpm_from_dot_unit_ms(dot_unit_ms: float) -> float:
    """Standard PARIS-word WPM formula."""
    return 1200.0 / dot_unit_ms


def dot_unit_ms_from_wpm(wpm: float) -> float:
    return 1200.0 / wpm


class AdaptiveMorseDecoder:
    def __init__(self, initial_wpm: float = DEFAULT_WPM) -> None:
        self.dot_unit_ms = dot_unit_ms_from_wpm(initial_wpm)
        self._dot_unit_ema = self.dot_unit_ms

        self._symbol_buffer = ""

        # Debounce state: the "committed" run is the one considered real; a
        # "pending" run of the opposite state must persist long enough
        # before it's promoted to committed (see feed_hop docstring).
        self._committed_state: bool | None = None
        self._committed_duration_ms = 0.0
        self._pending_state: bool | None = None
        self._pending_duration_ms = 0.0

        # A gap run only ever *ends* if another mark eventually arrives --
        # but a transmission can simply stop (radio silence at the end of a
        # QSO, or the very end of a test clip). So gap classification can't
        # wait for the run to terminate the way mark classification does; it
        # fires progressively, the moment the accumulating gap crosses each
        # threshold, guarded by these one-shot-per-gap flags.
        self._gap_char_flushed = False
        self._gap_word_flushed = False
        self._speed_reset_done = False

        self._bootstrap_active = True
        self._bootstrap_events: list[tuple[bool, float]] = []
        self._bootstrap_marks_seen = 0

        self._implausible_mark_pressure = 0

    @property
    def current_wpm(self) -> float:
        return wpm_from_dot_unit_ms(self.dot_unit_ms)

    @property
    def is_calibrated(self) -> bool:
        """Whether dot_unit_ms reflects a real measurement yet, rather than
        still being the generic bootstrap default. Consumers that act on
        dot_unit_ms as a *speed estimate* (e.g. the adaptive Goertzel window
        in dsp.tone_detector) must gate on this -- retuning off the
        pre-bootstrap default would apply an arbitrary, likely-wrong window
        change right as the decoder is still trying to measure the sender's
        actual timing, which can corrupt that very measurement."""
        return not self._bootstrap_active

    def reset(self, wpm: float = DEFAULT_WPM) -> None:
        """Fully reset the speed estimate and any in-progress symbol. Normally
        only needed after a very long silence or an explicit user action --
        ordinary inter-word/inter-character pauses must *not* trigger this,
        or every pause would force the decoder to re-learn the speed."""
        self.dot_unit_ms = max(MIN_DOT_UNIT_MS, min(MAX_DOT_UNIT_MS, dot_unit_ms_from_wpm(wpm)))
        self._dot_unit_ema = self.dot_unit_ms
        self._symbol_buffer = ""
        self._bootstrap_active = True
        self._bootstrap_events = []
        self._bootstrap_marks_seen = 0
        self._implausible_mark_pressure = 0

    def feed_hop(self, is_on: bool, hop_duration_ms: float) -> str:
        """Feed one tone-detector hop. Returns any newly decoded text (zero
        or more characters -- usually empty, occasionally one character,
        occasionally a character plus a trailing space at a word gap)."""
        if self._committed_state is None:
            self._committed_state = is_on
            self._committed_duration_ms = hop_duration_ms
            return ""

        if is_on == self._committed_state:
            if self._pending_state is not None:
                # A transient blip reverted before being confirmed: it was
                # noise, fold its duration back into the committed run.
                self._committed_duration_ms += self._pending_duration_ms
                self._pending_state = None
                self._pending_duration_ms = 0.0
            self._committed_duration_ms += hop_duration_ms
            if self._bootstrap_active:
                if (
                    self._committed_state is False
                    and self._bootstrap_marks_seen >= 1
                    and self._committed_duration_ms >= BOOTSTRAP_FORCE_SILENCE_MS
                ):
                    return self._finish_bootstrap()
                return ""  # don't classify/flush anything until calibrated
            return self._progressive_gap_output()

        # `is_on` differs from the committed state: extend (or start) a
        # pending run of the opposite state.
        if self._pending_state == is_on:
            self._pending_duration_ms += hop_duration_ms
        else:
            self._pending_state = is_on
            self._pending_duration_ms = hop_duration_ms

        if self._bootstrap_active:
            # dot_unit_ms is still just the generic default guess here, which
            # can be badly wrong at the real speed (e.g. far too large at
            # high WPM) -- sizing debounce off it would let debounce itself
            # swallow genuine elements before bootstrap ever sees accurate
            # durations. Fall back to the absolute noise floor instead of a
            # dot_unit-relative one until calibrated.
            debounce_ms = max(2 * hop_duration_ms, MIN_PLAUSIBLE_ELEMENT_MS)
        else:
            debounce_ms = max(0.3 * self.dot_unit_ms, 2 * hop_duration_ms, MIN_PLAUSIBLE_ELEMENT_MS)
        if self._pending_duration_ms >= debounce_ms:
            finished_is_mark = self._committed_state
            finished_duration_ms = self._committed_duration_ms

            self._committed_state = self._pending_state
            self._committed_duration_ms = self._pending_duration_ms
            self._pending_state = None
            self._pending_duration_ms = 0.0
            self._speed_reset_done = False

            output = ""
            if self._bootstrap_active:
                self._bootstrap_events.append((finished_is_mark, finished_duration_ms))
                if finished_is_mark:
                    self._bootstrap_marks_seen += 1
                if self._bootstrap_marks_seen >= BOOTSTRAP_MARKS_NEEDED:
                    output += self._finish_bootstrap()
                return output

            # The old committed run is now confirmed final. Marks are
            # classified here, at their one and only well-defined end point;
            # gaps have already been flushed progressively as they grew (see
            # `_progressive_gap_output`), so there's nothing left to do here
            # for them.
            if finished_is_mark:
                self._classify_mark(finished_duration_ms)

            if not self._committed_state:
                self._gap_char_flushed = False
                self._gap_word_flushed = False
                output += self._progressive_gap_output()
            return output

        return ""

    def _finish_bootstrap(self) -> str:
        """Calibrate dot_unit_ms from the shortest of the first few finished
        marks, then retroactively classify the whole buffered event sequence
        (marks and the completed gaps between them) with that estimate."""
        mark_durations = [duration for is_mark, duration in self._bootstrap_events if is_mark]
        self.dot_unit_ms = max(MIN_DOT_UNIT_MS, min(MAX_DOT_UNIT_MS, min(mark_durations)))
        self._dot_unit_ema = self.dot_unit_ms
        self._bootstrap_active = False

        output = ""
        for is_mark, duration_ms in self._bootstrap_events:
            if is_mark:
                self._classify_mark(duration_ms)
            elif duration_ms >= GAP_WORD_MAX_UNITS * self.dot_unit_ms:
                output += self._flush_character() + " "
            elif duration_ms >= GAP_INTRA_CHAR_MAX_UNITS * self.dot_unit_ms:
                output += self._flush_character()
        self._bootstrap_events = []

        # Prime the one-shot gap flags for whichever run is now current (the
        # run being accumulated at the moment bootstrap completed) and let
        # normal live processing take over from here.
        self._gap_char_flushed = False
        self._gap_word_flushed = False
        if not self._committed_state:
            output += self._progressive_gap_output()
        return output

    def _progressive_gap_output(self) -> str:
        """While sitting in a (confirmed) gap run, emit the character/word
        boundary the instant the accumulated silence crosses each timing
        threshold -- not just when the sender eventually keys again, since
        they might not (end of transmission)."""
        if self._bootstrap_active or self._committed_state is not False:
            return ""

        output = ""
        duration = self._committed_duration_ms

        if not self._gap_char_flushed and duration >= GAP_INTRA_CHAR_MAX_UNITS * self.dot_unit_ms:
            output += self._flush_character()
            self._gap_char_flushed = True

        if not self._gap_word_flushed and duration >= GAP_WORD_MAX_UNITS * self.dot_unit_ms:
            output += " "
            self._gap_word_flushed = True

        if not self._speed_reset_done and duration >= SPEED_RESET_SILENCE_MS:
            self.reset(wpm=DEFAULT_WPM)
            self._speed_reset_done = True

        return output

    def _classify_mark(self, duration_ms: float) -> None:
        if duration_ms < MARK_DOT_MAX_UNITS * self.dot_unit_ms:
            self._symbol_buffer += "."
            # Only a *dot* classification decays the implausible-mark
            # pressure counter -- a dash landing "in range" is weak
            # evidence the calibration is actually fine (see module
            # docstring: dots are the authoritative estimator, dashes are
            # not). Verified against real captured audio (pelda/m3.wav):
            # once dot_unit_ms got poisoned by one spuriously short mark,
            # a long run of otherwise-ordinary dashes kept landing "in
            # range" purely by coincidence of the wrong (too-small)
            # dot_unit_ms and repeatedly reset this counter, delaying
            # self-heal recalibration by ~12 real seconds of garbled
            # output instead of a handful of marks.
            self._implausible_mark_pressure = max(0, self._implausible_mark_pressure - 1)
            self._update_dot_unit_estimate(duration_ms)
        elif duration_ms < MARK_DASH_MAX_UNITS * self.dot_unit_ms:
            self._symbol_buffer += "-"
            self._nudge_dot_unit_from_dash(duration_ms)
        else:
            # Implausibly long for a dash at the current speed: could be
            # genuine QRM, but repeated occurrences are a much stronger
            # signal that dot_unit_ms itself is wrong (e.g. it was
            # bootstrapped from a noise glitch) -- self-heal by
            # recalibrating from this mark's own length instead of staying
            # stuck mis-decoding for the rest of the session.
            self._implausible_mark_pressure += 1
            if self._implausible_mark_pressure >= IMPLAUSIBLE_MARKS_BEFORE_RECALIBRATION:
                # Guess this mark was itself a dash (duration_ms / 3 ~ dot).
                self._recalibrate(duration_ms / 3.0)

    def _nudge_dot_unit_from_dash(self, duration_ms: float) -> None:
        """See DASH_IMPLIED_DOT_BLEND above: an upward-only, partial nudge
        toward a normally-classified dash's implied dot length, so a
        dot_unit_ms stuck too small isn't solely dependent on reaching the
        much more disruptive implausible-mark path below to ever recover."""
        implied_dot_ms = duration_ms / 3.0
        if implied_dot_ms > self.dot_unit_ms * DASH_IMPLIED_DOT_TRIGGER_RATIO:
            self.dot_unit_ms += DASH_IMPLIED_DOT_BLEND * (implied_dot_ms - self.dot_unit_ms)
            self.dot_unit_ms = max(MIN_DOT_UNIT_MS, min(MAX_DOT_UNIT_MS, self.dot_unit_ms))

    def _recalibrate(self, seed_dot_unit_ms: float) -> None:
        """Self-heal recovery from a badly wrong dot_unit_ms, triggered by a
        run of implausible marks. Deliberately lighter-weight than reset():
        reseeds dot_unit_ms directly and clears in-progress symbol state,
        but -- unlike reset() -- does *not* re-enter bootstrap. Routing
        self-heal back through bootstrap was the actual bug behind
        persistent high-WPM misdecodes: _finish_bootstrap() recalibrates
        from scratch using just the next BOOTSTRAP_MARKS_NEEDED raw marks,
        silently discarding the seed computed here and re-exposing the
        exact same fragile-first-few-marks failure mode that likely caused
        the bad estimate in the first place -- so a single early noise-
        influenced mark could keep re-poisoning the estimate indefinitely
        instead of the decoder ever converging."""
        self.dot_unit_ms = max(MIN_DOT_UNIT_MS, min(MAX_DOT_UNIT_MS, seed_dot_unit_ms))
        self._dot_unit_ema = self.dot_unit_ms
        self._symbol_buffer = ""
        self._implausible_mark_pressure = 0

    def _flush_character(self) -> str:
        if not self._symbol_buffer:
            return ""
        char = MORSE_TO_CHAR.get(self._symbol_buffer, "*")
        self._symbol_buffer = ""
        return char

    def _update_dot_unit_estimate(self, duration_ms: float) -> None:
        # Primary estimator: slow-rising minimum tracker. Only a genuinely
        # shorter mark can pull it down; it drifts upward slowly otherwise,
        # so it can still follow a sender who speeds up.
        self.dot_unit_ms = min(self.dot_unit_ms * DOT_UNIT_RISE_PER_MARK, duration_ms)
        self.dot_unit_ms = max(MIN_DOT_UNIT_MS, min(MAX_DOT_UNIT_MS, self.dot_unit_ms))

        # Secondary estimator: EMA cross-check (not authoritative for
        # classification), clamped so a single misclassified outlier can't
        # swing it far.
        target = self._dot_unit_ema * (1 - EMA_BETA) + duration_ms * EMA_BETA
        max_step = self._dot_unit_ema * EMA_MAX_RELATIVE_STEP
        self._dot_unit_ema = max(self._dot_unit_ema - max_step, min(self._dot_unit_ema + max_step, target))
