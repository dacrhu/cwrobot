"""A small single-producer/single-consumer ring buffer for audio samples.

The producer is the PortAudio callback thread (writes must be fast and
allocation-free on the hot path); the consumer is the decoder worker thread.
A lock guards the (cheap) index/count bookkeeping so concurrent write/read
can never corrupt state, while the actual sample copies happen outside the
lock where possible.
"""

from __future__ import annotations

import threading

import numpy as np


class RingBuffer:
    def __init__(self, capacity: int) -> None:
        self._buf = np.zeros(capacity, dtype=np.float32)
        self._capacity = capacity
        self._write_idx = 0
        self._read_idx = 0
        self._count = 0
        self._lock = threading.Lock()
        self._new_data = threading.Event()

    def write(self, samples: np.ndarray) -> None:
        """Append samples, called from the audio callback thread.

        If the buffer is full, the oldest unread samples are overwritten
        (the decoder thread is expected to keep up; this only protects
        against pathological stalls from corrupting memory).
        """
        n = len(samples)
        if n == 0:
            return
        if n >= self._capacity:
            samples = samples[-self._capacity :]
            n = len(samples)

        with self._lock:
            end_space = self._capacity - self._write_idx
            if n <= end_space:
                self._buf[self._write_idx : self._write_idx + n] = samples
            else:
                self._buf[self._write_idx :] = samples[:end_space]
                self._buf[: n - end_space] = samples[end_space:]
            self._write_idx = (self._write_idx + n) % self._capacity

            if self._count + n > self._capacity:
                overflow = self._count + n - self._capacity
                self._read_idx = (self._read_idx + overflow) % self._capacity
                self._count = self._capacity
            else:
                self._count += n

        self._new_data.set()

    def read_available(self, max_samples: int | None = None) -> np.ndarray:
        """Read (and consume) up to max_samples, called from the decoder thread."""
        with self._lock:
            n = self._count if max_samples is None else min(self._count, max_samples)
            if n == 0:
                return np.empty(0, dtype=np.float32)
            end_space = self._capacity - self._read_idx
            if n <= end_space:
                out = self._buf[self._read_idx : self._read_idx + n].copy()
            else:
                out = np.concatenate([self._buf[self._read_idx :], self._buf[: n - end_space]])
            self._read_idx = (self._read_idx + n) % self._capacity
            self._count -= n
            return out

    def available(self) -> int:
        with self._lock:
            return self._count

    def wait_for_data(self, timeout: float) -> bool:
        """Block until new data has arrived or the timeout elapses."""
        triggered = self._new_data.wait(timeout)
        self._new_data.clear()
        return triggered
