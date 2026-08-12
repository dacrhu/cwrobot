"""Unit tests for cwrobot.models.adif: ADIF record formatting and band
lookup. Pure logic, no I/O, no Qt involved."""

from __future__ import annotations

from datetime import datetime

from cwrobot.models.adif import (
    ADIF_HEADER,
    band_for_frequency_hz,
    format_qso_record,
    format_qso_record_as_mini_file,
)
from cwrobot.models.qso import QsoRecord


def test_format_qso_record_includes_every_populated_field():
    record = QsoRecord(
        callsign="DL1XYZ",
        freq_hz=14_074_000.0,
        mode="CW",
        rx_start=datetime(2026, 8, 12, 10, 30, 0),
        rx_end=datetime(2026, 8, 12, 10, 35, 15),
        rst_sent="599",
        rst_rcvd="579",
        qth="Berlin",
        locator="JO62",
        name="Hans",
        notes="Nice QSO",
        station_callsign="HA1ABC",
    )
    result = format_qso_record(record)

    assert result.endswith("<EOR>\n")
    assert "<CALL:6>DL1XYZ" in result
    assert "<QSO_DATE:8>20260812" in result
    assert "<TIME_ON:6>103000" in result
    assert "<QSO_DATE_OFF:8>20260812" in result
    assert "<TIME_OFF:6>103515" in result
    assert "<FREQ:9>14.074000" in result
    assert "<BAND:3>20m" in result
    assert "<MODE:2>CW" in result
    assert "<RST_SENT:3>599" in result
    assert "<RST_RCVD:3>579" in result
    assert "<NAME:4>Hans" in result
    assert "<QTH:6>Berlin" in result
    assert "<GRIDSQUARE:4>JO62" in result
    assert "<STATION_CALLSIGN:6>HA1ABC" in result
    assert "<COMMENT:8>Nice QSO" in result


def test_format_qso_record_omits_empty_optional_fields():
    record = QsoRecord(callsign="DL1XYZ", station_callsign="HA1ABC")
    result = format_qso_record(record)

    assert result == "<CALL:6>DL1XYZ<MODE:2>CW<STATION_CALLSIGN:6>HA1ABC<EOR>\n"
    for tag in ("QSO_DATE", "TIME_ON", "FREQ", "BAND", "RST_SENT", "QTH", "GRIDSQUARE", "COMMENT"):
        assert f"<{tag}:" not in result


def test_format_qso_record_without_frequency_omits_band():
    record = QsoRecord(callsign="DL1XYZ", station_callsign="HA1ABC", freq_hz=None)
    result = format_qso_record(record)
    assert "<FREQ" not in result
    assert "<BAND" not in result


def test_band_for_frequency_hz_known_bands():
    assert band_for_frequency_hz(14_074_000) == "20m"
    assert band_for_frequency_hz(7_030_000) == "40m"
    assert band_for_frequency_hz(3_560_000) == "80m"
    assert band_for_frequency_hz(28_050_000) == "10m"


def test_band_for_frequency_hz_out_of_band_or_missing():
    assert band_for_frequency_hz(1_000) is None
    assert band_for_frequency_hz(None) is None


def test_format_qso_record_as_mini_file_wraps_record_with_header():
    record = QsoRecord(callsign="DL1XYZ", station_callsign="HA1ABC")
    result = format_qso_record_as_mini_file(record)

    assert result.startswith(ADIF_HEADER)
    assert result == ADIF_HEADER + format_qso_record(record)
    assert result.endswith("<EOR>\n")
