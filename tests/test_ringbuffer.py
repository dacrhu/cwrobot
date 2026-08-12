import numpy as np

from cwrobot.audio.ringbuffer import RingBuffer


def test_write_then_read_round_trip():
    rb = RingBuffer(capacity=100)
    data = np.arange(10, dtype=np.float32)
    rb.write(data)
    out = rb.read_available()
    assert np.array_equal(out, data)
    assert rb.available() == 0


def test_wraparound():
    rb = RingBuffer(capacity=10)
    rb.write(np.arange(7, dtype=np.float32))
    rb.read_available(5)  # advance read pointer past the midpoint
    rb.write(np.arange(7, 13, dtype=np.float32))  # wraps past the end of the buffer
    out = rb.read_available()
    expected = np.concatenate([np.arange(5, 7, dtype=np.float32), np.arange(7, 13, dtype=np.float32)])
    assert np.array_equal(out, expected)


def test_overflow_drops_oldest_samples():
    rb = RingBuffer(capacity=5)
    rb.write(np.arange(5, dtype=np.float32))
    rb.write(np.array([100, 101], dtype=np.float32))  # overflows by 2
    out = rb.read_available()
    # the two oldest samples (0, 1) should have been overwritten
    assert np.array_equal(out, np.array([2, 3, 4, 100, 101], dtype=np.float32))


def test_partial_read_leaves_remainder():
    rb = RingBuffer(capacity=20)
    rb.write(np.arange(10, dtype=np.float32))
    first = rb.read_available(4)
    assert np.array_equal(first, np.arange(4, dtype=np.float32))
    assert rb.available() == 6
    rest = rb.read_available()
    assert np.array_equal(rest, np.arange(4, 10, dtype=np.float32))


def test_read_from_empty_buffer_returns_empty_array():
    rb = RingBuffer(capacity=10)
    out = rb.read_available()
    assert len(out) == 0
