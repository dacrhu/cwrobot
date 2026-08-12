"""QSO data model plus the two real QsoLogger implementations: an ADIF file
(one growing per-callsign log) and ADIF-over-UDP, itself sendable in either
of two wire formats (see UdpQsoLogger's own docstring): a bare ADIF record
(the simple protocol loggers like Log4OM listen for) or a WSJT-X Network
Message ("Logged ADIF", see models.wsjtx_udp) -- the protocol loggers like
QLog/JTAlert/GridTracker expect instead.

`QsoRecord`/`QsoLogger`/`NullQsoLogger` started life as a forward-looking
stub (see Specifikacio.md: "kulso naplo-program kezelese" was explicitly
out of scope for the first version) -- ui.main_window's log-QSO flow is
what actually builds a `QsoRecord` (from ui.qso_panel's fields) and picks a
logger (via `build_qso_logger`, from ui.settings_dialog's "Logging" tab
config) now.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from cwrobot.models.adif import ADIF_HEADER, format_qso_record, format_qso_record_as_mini_file
from cwrobot.models.wsjtx_udp import build_logged_adif_message

if TYPE_CHECKING:
    from cwrobot.config import AppConfig


@dataclass
class QsoRecord:
    callsign: str = ""
    freq_hz: float | None = None
    mode: str = "CW"
    rx_start: datetime | None = None
    rx_end: datetime | None = None
    rst_sent: str = ""
    rst_rcvd: str = ""
    qth: str = ""
    locator: str = ""
    name: str = ""
    tx_log: str = ""
    rx_log: str = ""
    notes: str = ""
    # The operator's own callsign (ADIF STATION_CALLSIGN) -- distinct from
    # `callsign` above, which is the *remote* station's. Not sourced from
    # this dataclass elsewhere; callers fill it from
    # AppConfig.operator_callsign at record-build time (see
    # ui.main_window._on_log_qso_clicked).
    station_callsign: str = ""


class QsoLogger(Protocol):
    def log_qso(self, record: QsoRecord) -> None: ...


class NullQsoLogger:
    """Default no-op logger used until a real external-logger integration exists."""

    def log_qso(self, record: QsoRecord) -> None:
        pass


class QsoLoggingError(RuntimeError):
    """A QsoLogger couldn't log a QSO -- misconfiguration (no folder/host
    set) or a lower-level OSError (disk full, unreachable host, ...),
    normalized into one type callers can catch, mirroring
    hamlib.rig_client.HamlibError's role for Hamlib failures."""


class FileQsoLogger:
    """Appends one ADIF record per QSO to `{folder}/{station_callsign}.adi`
    -- one growing file per operator callsign, matching how most ham
    logging software organizes per-callsign ADIF exports. The file (and its
    ADIF header) is created on first use; later calls just append."""

    def __init__(self, folder: str) -> None:
        self._folder = folder

    def log_qso(self, record: QsoRecord) -> None:
        if not self._folder:
            raise QsoLoggingError("No log folder configured -- set one in Settings → Logging.")
        if not record.station_callsign:
            raise QsoLoggingError("No operator callsign configured -- set one in Settings → My Data.")

        # "/" shows up in real callsigns (portable/mobile suffixes, e.g.
        # "HG7WHD/P") but isn't filesystem-safe -- swapped for the filename
        # only, the ADIF record itself keeps the real callsign untouched.
        filename = record.station_callsign.replace("/", "-") + ".adi"
        path = Path(self._folder) / filename

        try:
            is_new = not path.exists()
            with path.open("a", encoding="utf-8") as f:
                if is_new:
                    f.write(ADIF_HEADER)
                f.write(format_qso_record(record))
        except OSError as exc:
            raise QsoLoggingError(f"Could not write to {path}: {exc}") from exc


class UdpQsoLogger:
    """Sends one QSO per UDP datagram, fire-and-forget (no acknowledgement
    expected or waited for), in either of two wire formats:

    - `"adif"`: a plain UTF-8-encoded, bare ADIF record -- no wrapper/
      framing -- the simple "ADIF over UDP" convention loggers like Log4OM
      listen for.
    - `"wsjtx"`: a WSJT-X Network Message ("Logged ADIF", type 12, see
      models.wsjtx_udp) -- the framed, binary protocol loggers like QLog/
      JTAlert/GridTracker expect instead. Confirmed necessary in practice:
      a real QLog instance didn't recognize plain "adif" datagrams at all.
    """

    def __init__(self, host: str, port: int, format: Literal["adif", "wsjtx"] = "adif") -> None:
        self._host = host
        self._port = port
        self._format = format

    def log_qso(self, record: QsoRecord) -> None:
        if self._format == "wsjtx":
            data = build_logged_adif_message(format_qso_record_as_mini_file(record))
        else:
            data = format_qso_record(record).encode("utf-8")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(data, (self._host, self._port))
        except OSError as exc:
            raise QsoLoggingError(f"Could not send to {self._host}:{self._port}: {exc}") from exc


def build_qso_logger(config: "AppConfig") -> QsoLogger:
    """The QsoLogger matching the current Settings → Logging configuration.
    Cheap enough to build fresh on every log click (see
    ui.main_window._on_log_qso_clicked) rather than caching one -- unlike
    hamlib.frequency_monitor's persistent rig connection, there's no
    ongoing connection or thread to keep in sync with config changes."""
    if config.logging_method == "udp":
        return UdpQsoLogger(config.logging_udp_host, config.logging_udp_port, format=config.logging_udp_format)
    return FileQsoLogger(config.logging_folder)
