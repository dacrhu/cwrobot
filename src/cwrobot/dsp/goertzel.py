"""Goertzel algorithm: efficient single-bin DFT power for a target frequency
over a fixed-size block of samples.

Implemented via `scipy.signal.lfilter` (the Goertzel recursion is a 2nd-order
IIR filter) rather than a hand-rolled Python loop, so it runs in compiled
code and stays cheap even at high hop rates.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter


def goertzel_power(samples: np.ndarray, sample_rate: float, target_freq: float) -> float:
    """Return the (relative, un-normalized) power of `target_freq` within `samples`."""
    n = len(samples)
    if n < 2:
        return 0.0

    k = int(0.5 + (n * target_freq) / sample_rate)
    omega = (2.0 * np.pi * k) / n
    coeff = 2.0 * np.cos(omega)

    # s[i] = x[i] + coeff*s[i-1] - s[i-2]  <=>  IIR filter b=[1], a=[1, -coeff, 1]
    s = lfilter([1.0], [1.0, -coeff, 1.0], samples)
    s_prev, s_prev2 = s[-1], s[-2]
    power = s_prev2**2 + s_prev**2 - coeff * s_prev * s_prev2
    return float(power)
