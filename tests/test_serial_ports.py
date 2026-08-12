"""Unit tests for cwrobot.hamlib.serial_ports.

Serial-port enumeration is exercised via a mocked
cwrobot.hamlib.serial_ports.comports call, so this suite runs headless
without any real serial hardware or platform-specific pyserial backend.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from cwrobot.hamlib.serial_ports import SerialPort, list_serial_ports


def _fake_port(device: str, description: str | None) -> SimpleNamespace:
    # Duck-typed stand-in for pyserial's ListPortInfo -- list_serial_ports()
    # only ever reads .device and .description, so a real ListPortInfo
    # isn't needed here.
    return SimpleNamespace(device=device, description=description)


def test_returns_dataclasses_for_each_reported_port():
    fake_ports = [
        _fake_port("/dev/ttyUSB0", "USB2.0-Serial"),
        _fake_port("/dev/ttyACM0", "Arduino Uno"),
    ]
    with patch("cwrobot.hamlib.serial_ports.comports", return_value=fake_ports):
        result = list_serial_ports()
    assert result == [
        SerialPort(device="/dev/ttyUSB0", description="USB2.0-Serial"),
        SerialPort(device="/dev/ttyACM0", description="Arduino Uno"),
    ]


def test_no_ports_found_returns_empty_list():
    with patch("cwrobot.hamlib.serial_ports.comports", return_value=[]):
        assert list_serial_ports() == []


def test_falsy_description_falls_back_to_na():
    with patch(
        "cwrobot.hamlib.serial_ports.comports",
        return_value=[_fake_port("/dev/ttyUSB0", "")],
    ):
        assert list_serial_ports() == [SerialPort(device="/dev/ttyUSB0", description="n/a")]


def test_none_description_falls_back_to_na():
    with patch(
        "cwrobot.hamlib.serial_ports.comports",
        return_value=[_fake_port("/dev/ttyUSB0", None)],
    ):
        assert list_serial_ports() == [SerialPort(device="/dev/ttyUSB0", description="n/a")]
