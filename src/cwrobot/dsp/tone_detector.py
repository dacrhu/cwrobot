"""Narrowband tone detection: decimate -> sliding Goertzel -> envelope ->
adaptive hysteresis threshold -> a stream of (is_on, duration_ms) hops.

This is the CW-decoder front end (Specifikacio.md plan section 3). It does
not know about dots/dashes/characters -- that's decoder/timing.py's job --
it only answers "is the configured-pitch tone present right now?", robustly
enough to tolerate a few tens of Hz of radio mistuning and ordinary QRM/QRN.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import firwin, lfilter, lfilter_zi

from cwrobot.dsp.goertzel import goertzel_power

PROCESSING_SAMPLE_RATE = 8_000
WINDOW_SAMPLES = 128  # 16 ms at 8 kHz
HOP_SAMPLES = 32  # 4 ms at 8 kHz
BIN_SPAN = 1  # target bin +/- this many bins are summed (tolerates VFO mistuning)

ENVELOPE_TIME_CONSTANT_MS = 4.0
NOISE_FLOOR_RISE_PER_HOP = 1.0008  # lets the floor slowly track upward drift
PEAK_DECAY_PER_HOP = 0.9992  # lets the ceiling slowly decay if the signal drops

# Squelch: a *separate*, statistically-grounded background tracker, kept
# independent from noise_floor/peak above (those are tuned for classifying
# on/off once a real signal is present, not for judging whether there's a
# signal at all -- a peak/floor *ratio* was tried here first and rejected:
# Goertzel power from Gaussian noise is exponentially distributed with a
# long tail, so even short-term min/max ratios of pure noise are large
# purely from that tail, regardless of how fast or slow the trackers decay).
#
# Instead this tracks a slow running mean of the envelope *during quiet
# periods only* (see the contamination-avoidance note below), and only lets
# a *new* mark start once the instantaneous envelope clears that mean by a
# multiplicative ratio.
#
# A ratio, not a mean+K*std z-score, is the right tool here: Goertzel power
# from Gaussian noise is exponentially distributed, not Gaussian, and an
# exponential's tail is far heavier than a normal distribution's -- a
# nominal "8 sigma" mean+std threshold turned out to be crossed by this
# noise far more often than a true 8-sigma Gaussian event ever would be.
# For an exponential distribution, P(X > k * mean) = exp(-k) exactly, so a
# ratio directly against the mean gives a threshold with a known, well-
# behaved false-trigger rate instead of one silently relying on a Gaussian
# assumption the underlying quantity doesn't actually satisfy.
SQUELCH_TIME_CONSTANT_MS = 200.0
SQUELCH_MIN_HOPS_BEFORE_OPEN = 25  # ~100 ms warm-up before the estimator is trusted
SQUELCH_RATIO = 14.0

# Hang time: once a mark has recently passed the strict z-score test, later
# marks within this window skip re-testing entirely. Without this, a fast
# sender (short dots, short gaps) can fail the statistical test mid-word --
# not because there's no real signal, but because the 200 ms averaging
# window above hasn't had time to fully settle between two closely-spaced
# elements. Real transmissions keep marks coming well within this window;
# real silence does not, so the strict test still applies at the start of
# every fresh transmission.
SQUELCH_HANG_TIME_MS = 150.0

# Hang time has a failure mode of its own: noise occasionally clears the
# strict test by chance, and every mark that follows within the hang window
# then also skips testing, which can chain into a longer run of false
# marks before the gaps between them finally exceed the hang time. This
# forces a strict re-test periodically regardless of hang time, bounding
# how long such a chain can persist -- a real signal keeps clearing the
# strict test easily and is unaffected, since it would have passed anyway.
SQUELCH_MAX_HANG_CHAIN_MS = 300.0

# Adaptive Goertzel window: when auto-bandwidth is enabled (see
# CwDecoderCore.auto_bandwidth), the analysis window length tracks the
# currently-estimated dot length (AdaptiveMorseDecoder.dot_unit_ms) instead
# of staying fixed at WINDOW_SAMPLES -- a slower sender gets a longer window
# (narrower Goertzel bins -> better noise/QRM rejection), a faster sender
# gets a shorter one (needed for time resolution to resolve short dots).
#
# A sliding Goertzel window smears the perceived on/off transition by
# roughly its own absolute duration (the window has to fully fill with --
# or drain of -- the tone before the envelope fully reflects the new
# state), and that absolute smear, not its ratio to the dot length, is
# what corrupts element/gap timing once it gets large enough to compete
# with genuinely short gaps (an intra-character gap is only one dot unit).
#
# Measured empirically against the full existing decoder test suite
# (clean signal across 5-60 WPM, hand-keyed jitter, a mid-transmission
# speed change, and -- the tightest constraint -- a fixed marginal-SNR
# broadband-noise case at 20 WPM/seed 7 that a *fixed* 16 ms window only
# barely already decodes correctly): growing the window at all below
# WINDOW_GROWTH_STARTS_AT_DOT_UNIT_MS (60 ms, i.e. >=20 WPM) regresses
# that noise case, even by a few ms -- so the window stays pinned at
# WINDOW_SAMPLES for any dot_unit_ms at or below that point (the >=20 WPM
# range is completely unchanged from today). Below that point it ramps up
# linearly (continuous at the boundary, so no jump/flap right at 20 WPM)
# at the same 0.8 ratio the fixed window already sits at relative to a 60
# WPM dot, capped at MAX_WINDOW_MS -- both ends of this range were
# verified safe against the same test battery.
WINDOW_GROWTH_STARTS_AT_DOT_UNIT_MS = 60.0  # ~20 WPM
ADAPTIVE_WINDOW_K = 0.8
MIN_WINDOW_MS = 1000.0 * WINDOW_SAMPLES / PROCESSING_SAMPLE_RATE  # 16.0 ms, today's fixed window
MAX_WINDOW_MS = 48.0

# Retuning is evaluated on the same cadence as the WPM readout (see
# CwDecoderCore.WPM_EMIT_INTERVAL_SECONDS) rather than every hop, and even
# then only actually resizes if the target differs from the current window
# by more than this fraction -- otherwise ordinary jitter in the dot_unit_ms
# estimate would cause the window to flap continuously.
RETUNE_DEADBAND_RATIO = 0.15


class StreamingDecimator:
    """Anti-alias FIR filter + decimation, safe to call incrementally on a
    continuous stream of chunks (filter state persists across calls, and the
    decimation phase stays aligned across chunk boundaries of any size).
    """

    def __init__(
        self,
        input_rate: int,
        output_rate: int = PROCESSING_SAMPLE_RATE,
        numtaps: int = 63,
    ) -> None:
        if input_rate % output_rate != 0:
            raise ValueError(f"input_rate {input_rate} must be an integer multiple of output_rate {output_rate}")
        self.factor = input_rate // output_rate
        self.output_rate = output_rate
        nyquist = input_rate / 2.0
        cutoff_hz = 0.9 * (output_rate / 2.0)  # a bit inside the new Nyquist for margin
        self._taps = firwin(numtaps, cutoff_hz / nyquist)
        self._zi = lfilter_zi(self._taps, [1.0]) * 0.0
        self._total_consumed = 0

    def process(self, samples: np.ndarray) -> np.ndarray:
        if len(samples) == 0:
            return np.empty(0, dtype=np.float32)
        filtered, self._zi = lfilter(self._taps, [1.0], samples, zi=self._zi)
        start = (-self._total_consumed) % self.factor
        decimated = filtered[start :: self.factor]
        self._total_consumed += len(samples)
        return decimated.astype(np.float32)


@dataclass(frozen=True)
class ToneHop:
    is_on: bool
    duration_ms: float


class GoertzelToneDetector:
    """Consumes decimated samples incrementally, evaluating one Goertzel
    window per `HOP_SAMPLES`-sample hop, and returns the on/off decision for
    each hop via an adaptive Schmitt-trigger threshold.
    """

    def __init__(
        self,
        target_freq_hz: float,
        sample_rate: int = PROCESSING_SAMPLE_RATE,
        window_samples: int = WINDOW_SAMPLES,
        hop_samples: int = HOP_SAMPLES,
        bin_span: int = BIN_SPAN,
    ) -> None:
        self.target_freq_hz = target_freq_hz
        self.sample_rate = sample_rate
        self.window_samples = window_samples
        self.hop_samples = hop_samples
        self.bin_span = bin_span
        self.hop_duration_ms = 1000.0 * hop_samples / sample_rate

        self._window = np.zeros(window_samples, dtype=np.float32)
        self._accumulated = 0

        self._envelope_alpha = np.exp(-self.hop_duration_ms / ENVELOPE_TIME_CONSTANT_MS)
        self._envelope = 0.0
        self._noise_floor: float | None = None
        self._peak: float | None = None
        self._is_on = False

        self._squelch_alpha = np.exp(-self.hop_duration_ms / SQUELCH_TIME_CONSTANT_MS)
        self._squelch_mean = 0.0
        self._squelch_hops_seen = 0
        self._ms_since_confirmed_mark: float | None = None  # None = never confirmed yet
        self._ms_since_strict_test = 0.0
        self.squelch_ratio = SQUELCH_RATIO  # user-adjustable at runtime (a UI slider, say)

        # Adaptive window: set by request_retune()/request_manual_window(),
        # applied at the next safe (confirmed-silent) hop -- see
        # _evaluate_window and _apply_resize.
        self._pending_window_samples: int | None = None

    @property
    def effective_bandwidth_hz(self) -> float:
        """Full width of the frequency band actually being monitored (target
        bin +/- bin_span bins), for display as a tuning aid."""
        bin_width = self.sample_rate / self.window_samples
        return (2 * self.bin_span + 1) * bin_width

    @property
    def squelch_currently_open(self) -> bool:
        """Side-effect-free live readout of whether squelch would accept a
        new mark right now -- for a UI indicator, independent of the
        hang-time/periodic-audit bookkeeping that _is_squelched() updates."""
        if self._squelch_hops_seen < SQUELCH_MIN_HOPS_BEFORE_OPEN:
            return True
        # bool(...): a bare numpy.bool_ from this comparison isn't accepted
        # by PySide6's Signal(bool) marshaling further downstream.
        return bool(self._envelope >= self._squelch_mean * self.squelch_ratio)

    def request_retune(self, dot_unit_ms: float) -> None:
        """Record a desired window length derived from the current keying-
        speed estimate (see the ADAPTIVE_WINDOW_K comment above). Only
        actually applied once the difference clears RETUNE_DEADBAND_RATIO,
        and even then not until the next confirmed-silent hop (see
        _evaluate_window) -- never mid-mark."""
        overshoot_ms = max(0.0, dot_unit_ms - WINDOW_GROWTH_STARTS_AT_DOT_UNIT_MS)
        window_ms = min(MAX_WINDOW_MS, MIN_WINDOW_MS + ADAPTIVE_WINDOW_K * overshoot_ms)
        target_samples = max(1, round(window_ms * self.sample_rate / 1000.0))
        if abs(target_samples - self.window_samples) > RETUNE_DEADBAND_RATIO * self.window_samples:
            self._pending_window_samples = target_samples

    def request_manual_window(self, window_samples: int = WINDOW_SAMPLES) -> None:
        """Force the window back to a fixed size (used when auto-bandwidth
        is turned off), via the same safe/deferred resize path as
        request_retune."""
        if window_samples != self.window_samples:
            self._pending_window_samples = window_samples

    def _apply_resize(self, new_window_samples: int) -> None:
        """Actually resize the analysis window. Only ever called from
        _evaluate_window while confirmed-silent (see there) -- Goertzel
        power scales with window length for both signal and noise, so every
        power-scaled running statistic must be re-seeded from scratch
        rather than carried across the resize, exactly as at startup."""
        self.window_samples = new_window_samples
        self._window = np.zeros(new_window_samples, dtype=np.float32)
        self._envelope = 0.0
        self._noise_floor = None
        self._peak = None
        self._squelch_mean = 0.0
        self._squelch_hops_seen = 0

    def process(self, decimated_samples: np.ndarray) -> list[ToneHop]:
        hops: list[ToneHop] = []
        idx = 0
        n = len(decimated_samples)
        while idx < n:
            take = min(self.hop_samples - self._accumulated, n - idx)
            self._append_to_window(decimated_samples[idx : idx + take])
            self._accumulated += take
            idx += take
            if self._accumulated >= self.hop_samples:
                self._accumulated = 0
                hops.append(ToneHop(is_on=self._evaluate_window(), duration_ms=self.hop_duration_ms))
        return hops

    def _append_to_window(self, chunk: np.ndarray) -> None:
        n = len(chunk)
        if n == 0:
            return
        window_len = len(self._window)
        if n >= window_len:
            self._window[:] = chunk[-window_len:]
        else:
            self._window[: window_len - n] = self._window[n:]
            self._window[window_len - n :] = chunk

    def _evaluate_window(self) -> bool:
        # A pending resize is only ever applied while confirmed-silent (the
        # previous hop's decision, self._is_on) -- exactly like squelch
        # itself never truncates an in-progress mark. This can defer a
        # resize indefinitely during a long mark, which is correct: a
        # resize mid-mark would corrupt that mark's measured duration/power.
        if self._pending_window_samples is not None and not self._is_on:
            self._apply_resize(self._pending_window_samples)
            self._pending_window_samples = None

        bin_width = self.sample_rate / self.window_samples
        power = sum(
            goertzel_power(self._window, self.sample_rate, self.target_freq_hz + offset * bin_width)
            for offset in range(-self.bin_span, self.bin_span + 1)
        )

        self._envelope = self._envelope * self._envelope_alpha + power * (1 - self._envelope_alpha)

        if self._noise_floor is None:
            self._noise_floor = self._envelope
            self._peak = self._envelope
        else:
            self._noise_floor = min(self._noise_floor * NOISE_FLOOR_RISE_PER_HOP, self._envelope)
            self._peak = max(self._peak * PEAK_DECAY_PER_HOP, self._envelope)

        span = max(self._peak - self._noise_floor, 1e-9)
        t_on = self._noise_floor + 0.5 * span
        t_off = self._noise_floor + 0.35 * span

        if self._envelope > t_on:
            candidate_is_on = True
        elif self._envelope < t_off:
            candidate_is_on = False
        else:
            candidate_is_on = self._is_on  # stays in the hysteresis band

        squelch_blocked = False
        if candidate_is_on and not self._is_on and self._is_squelched():
            # Squelch only ever blocks a *new* mark from starting; it must
            # never truncate one already in progress, which would corrupt
            # that mark's measured duration and cascade into a decode error
            # right at the moment it matters most.
            candidate_is_on = False
            squelch_blocked = True

        # Only feed *this* hop's envelope into the background statistics if
        # it's genuinely, unambiguously quiet -- not "on" (that would drag
        # the background up with every real mark, progressively squelching
        # a real transmission the longer it runs) and not a sample squelch
        # itself just rejected (contested/ambiguous, not clearly quiet
        # either -- including it would entrench a wrong squelch decision).
        if not candidate_is_on and not squelch_blocked:
            self._squelch_hops_seen += 1
            self._squelch_mean = self._squelch_mean * self._squelch_alpha + self._envelope * (1 - self._squelch_alpha)

        if candidate_is_on:
            self._ms_since_confirmed_mark = 0.0
        elif self._ms_since_confirmed_mark is not None:
            self._ms_since_confirmed_mark += self.hop_duration_ms

        # Elapsed every hop, regardless of whether _is_squelched() below even
        # gets called this hop (it's only invoked at "new mark candidate"
        # moments) -- this must reflect real wall-clock time since the last
        # strict test, not just how many times that test happened to run.
        self._ms_since_strict_test += self.hop_duration_ms

        self._is_on = candidate_is_on
        return self._is_on

    def _is_squelched(self) -> bool:
        within_hang_time = (
            self._ms_since_confirmed_mark is not None and self._ms_since_confirmed_mark < SQUELCH_HANG_TIME_MS
        )
        if within_hang_time and self._ms_since_strict_test < SQUELCH_MAX_HANG_CHAIN_MS:
            # A real mark was confirmed recently enough that we're almost
            # certainly still mid-transmission -- don't re-run the strict
            # statistical test on every single element (which can fail for
            # a fast sender simply because the averaging window below
            # hasn't fully settled between two closely-spaced elements).
            # Bounded by SQUELCH_MAX_HANG_CHAIN_MS: periodically force a
            # real audit anyway, so a chain of noise-driven false opens
            # can't ride this shortcut indefinitely.
            return False

        self._ms_since_strict_test = 0.0

        if self._squelch_hops_seen < SQUELCH_MIN_HOPS_BEFORE_OPEN:
            # Not enough history yet to judge -- pass through unsquelched.
            # This only affects the first ~100 ms after startup (once per
            # session), whereas defaulting to muted would clip the start of
            # the very first real signal every time, which is worse.
            return False
        return self._envelope < self._squelch_mean * self.squelch_ratio
