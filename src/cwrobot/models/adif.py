"""Pure ADIF (Amateur Data Interchange Format) record formatting.

No I/O, no Qt -- just `models.qso.QsoRecord` in, an ADIF text fragment out,
directly unit-testable (mirrors decoder.CwDecoderCore's own "pure logic,
thin I/O shell elsewhere" split). See `models.qso.FileQsoLogger`/
`UdpQsoLogger` for what actually writes/sends the result.

ADIF itself is a simple tagged-text format: each field is
`<FIELDNAME:length>value` (length = character count of value, no
delimiter), concatenated with no required separators, and a record ends
with `<EOR>`. A file may start with free-form header text terminated by
`<EOH>`. See https://adif.org for the full spec -- only the handful of
fields cwrobot actually has data for are emitted here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cwrobot.models.qso import QsoRecord

# Header written once, only when a log file is created for the first time
# (see models.qso.FileQsoLogger). The free-form comment line and "<EOH>"
# marker alone are enough per the bare ADIF spec, but ADIF_VER/PROGRAMID
# are standard, expected header fields real-world importers look for --
# cheap to include, and avoids a strict importer treating an otherwise-
# valid file as suspect just because the header looks unusually bare.
ADIF_HEADER = "CW Robot ADIF export\n<ADIF_VER:5>3.1.4<PROGRAMID:8>CW Robot<EOH>\n"

# (low_hz, high_hz, band) for the common amateur bands cwrobot is likely to
# see CW traffic on. Deliberately not exhaustive (WARC/VHF/UHF edge cases,
# regional allocation differences, etc.) -- BAND is optional in ADIF when
# FREQ is present (many readers derive it themselves), so an unmatched
# frequency just omits the field rather than guessing wrong.
_BAND_TABLE: list[tuple[float, float, str]] = [
    (135_700, 137_800, "2190m"),
    (472_000, 479_000, "630m"),
    (1_800_000, 2_000_000, "160m"),
    (3_500_000, 4_000_000, "80m"),
    (5_330_000, 5_410_000, "60m"),
    (7_000_000, 7_300_000, "40m"),
    (10_100_000, 10_150_000, "30m"),
    (14_000_000, 14_350_000, "20m"),
    (18_068_000, 18_168_000, "17m"),
    (21_000_000, 21_450_000, "15m"),
    (24_890_000, 24_990_000, "12m"),
    (28_000_000, 29_700_000, "10m"),
    (50_000_000, 54_000_000, "6m"),
    (144_000_000, 148_000_000, "2m"),
    (420_000_000, 450_000_000, "70cm"),
]


def band_for_frequency_hz(hz: float | None) -> str | None:
    """The amateur band name for `hz` (e.g. `"20m"`), or `None` if `hz` is
    `None` or doesn't fall inside any band in `_BAND_TABLE`."""
    if hz is None:
        return None
    for low, high, band in _BAND_TABLE:
        if low <= hz <= high:
            return band
    return None


def _field(name: str, value: str) -> str:
    return f"<{name}:{len(value)}>{value}"


def format_qso_record(record: QsoRecord) -> str:
    """One complete ADIF record for `record`, terminated with `<EOR>\\n`.
    Fields with no data (empty strings, unset optional values) are simply
    omitted -- ADIF has no concept of a "blank" field, only present/absent."""
    parts = [_field("CALL", record.callsign)]

    if record.rx_start is not None:
        parts.append(_field("QSO_DATE", record.rx_start.strftime("%Y%m%d")))
        parts.append(_field("TIME_ON", record.rx_start.strftime("%H%M%S")))
    if record.rx_end is not None:
        parts.append(_field("QSO_DATE_OFF", record.rx_end.strftime("%Y%m%d")))
        parts.append(_field("TIME_OFF", record.rx_end.strftime("%H%M%S")))

    if record.freq_hz is not None:
        parts.append(_field("FREQ", f"{record.freq_hz / 1_000_000:.6f}"))
        band = band_for_frequency_hz(record.freq_hz)
        if band is not None:
            parts.append(_field("BAND", band))

    parts.append(_field("MODE", record.mode))

    for name, value in (
        ("RST_SENT", record.rst_sent),
        ("RST_RCVD", record.rst_rcvd),
        ("NAME", record.name),
        ("QTH", record.qth),
        ("GRIDSQUARE", record.locator),
        ("STATION_CALLSIGN", record.station_callsign),
        ("COMMENT", record.notes),
    ):
        if value:
            parts.append(_field(name, value))

    parts.append("<EOR>")
    return "".join(parts) + "\n"


def format_qso_record_as_mini_file(record: QsoRecord) -> str:
    """`format_qso_record`'s single record, preceded by `ADIF_HEADER` --
    a complete, self-contained ADIF *file* fragment (header + `<EOH>` +
    the record). Needed by models.wsjtx_udp: unlike plain ADIF-over-UDP
    (a bare record is enough there), the WSJT-X protocol's "Logged ADIF"
    message explicitly requires its embedded ADIF text to be a valid ADIF
    file, not just a record."""
    return ADIF_HEADER + format_qso_record(record)
