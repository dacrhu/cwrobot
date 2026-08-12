"""Unit tests for cwrobot.models.qso's real QsoLogger implementations
(FileQsoLogger, UdpQsoLogger) and the build_qso_logger factory. Pure
stdlib I/O (filesystem / a real loopback UDP socket), no Qt involved."""

from __future__ import annotations

import socket
import struct

import pytest

from cwrobot.config import AppConfig
from cwrobot.models.adif import ADIF_HEADER
from cwrobot.models.qso import (
    FileQsoLogger,
    QsoLoggingError,
    QsoRecord,
    UdpQsoLogger,
    build_qso_logger,
)
from cwrobot.models.wsjtx_udp import MAGIC, SCHEMA, TYPE_LOGGED_ADIF


def _record(**overrides) -> QsoRecord:
    defaults = dict(callsign="DL1XYZ", station_callsign="HA1ABC")
    defaults.update(overrides)
    return QsoRecord(**defaults)


# -- FileQsoLogger --


def test_file_logger_creates_file_with_header_on_first_log(tmp_path):
    logger = FileQsoLogger(str(tmp_path))
    logger.log_qso(_record())

    path = tmp_path / "HA1ABC.adi"
    content = path.read_text(encoding="utf-8")
    assert content.startswith(ADIF_HEADER)
    assert "<CALL:6>DL1XYZ" in content
    assert content.count("<EOR>") == 1


def test_file_logger_appends_without_duplicating_header(tmp_path):
    logger = FileQsoLogger(str(tmp_path))
    logger.log_qso(_record(callsign="DL1XYZ"))
    logger.log_qso(_record(callsign="DL2ABC"))

    content = (tmp_path / "HA1ABC.adi").read_text(encoding="utf-8")
    assert content.count(ADIF_HEADER) == 1
    assert content.count("<EOR>") == 2
    assert "<CALL:6>DL1XYZ" in content
    assert "<CALL:6>DL2ABC" in content


def test_file_logger_sanitizes_slash_in_callsign_for_filename(tmp_path):
    logger = FileQsoLogger(str(tmp_path))
    logger.log_qso(_record(station_callsign="HA1ABC/P"))

    assert (tmp_path / "HA1ABC-P.adi").exists()
    assert not (tmp_path / "HA1ABC").exists()


def test_file_logger_raises_when_folder_not_configured():
    logger = FileQsoLogger("")
    with pytest.raises(QsoLoggingError, match="folder"):
        logger.log_qso(_record())


def test_file_logger_raises_when_station_callsign_not_configured(tmp_path):
    logger = FileQsoLogger(str(tmp_path))
    with pytest.raises(QsoLoggingError, match="callsign"):
        logger.log_qso(_record(station_callsign=""))


# -- UdpQsoLogger --


def test_udp_logger_sends_adif_record_as_a_single_datagram():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2.0)
        host, port = receiver.getsockname()

        logger = UdpQsoLogger(host, port)
        logger.log_qso(_record(callsign="DL1XYZ"))

        data, _addr = receiver.recvfrom(4096)
        text = data.decode("utf-8")
        assert text.startswith("<CALL:6>DL1XYZ")
        assert text.endswith("<EOR>\n")


def test_udp_logger_sends_wsjtx_protocol_message_when_configured():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2.0)
        host, port = receiver.getsockname()

        logger = UdpQsoLogger(host, port, format="wsjtx")
        logger.log_qso(_record(callsign="DL1XYZ"))

        data, _addr = receiver.recvfrom(4096)
        magic, schema, msg_type = struct.unpack_from(">III", data, 0)
        assert (magic, schema, msg_type) == (MAGIC, SCHEMA, TYPE_LOGGED_ADIF)

        # The embedded ADIF text is a full mini-file (header + <EOH> +
        # record), not a bare record -- unlike the "adif" format above.
        assert ADIF_HEADER.encode("utf-8") in data
        assert b"<CALL:6>DL1XYZ" in data
        assert data.endswith(b"<EOR>\n")


def test_udp_logger_raises_qso_logging_error_on_send_failure(monkeypatch):
    logger = UdpQsoLogger("127.0.0.1", 2237)

    class FailingSocket:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def sendto(self, *args):
            raise OSError("simulated failure")

    monkeypatch.setattr(socket, "socket", lambda *a, **kw: FailingSocket())
    with pytest.raises(QsoLoggingError):
        logger.log_qso(_record())


# -- build_qso_logger --


def test_build_qso_logger_returns_file_logger_by_default(tmp_path):
    config = AppConfig(logging_method="file", logging_folder=str(tmp_path))
    logger = build_qso_logger(config)
    assert isinstance(logger, FileQsoLogger)


def test_build_qso_logger_returns_udp_logger_when_configured():
    config = AppConfig(logging_method="udp", logging_udp_host="127.0.0.1", logging_udp_port=2237)
    logger = build_qso_logger(config)
    assert isinstance(logger, UdpQsoLogger)


def test_build_qso_logger_passes_udp_format_through():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2.0)
        host, port = receiver.getsockname()

        config = AppConfig(
            logging_method="udp", logging_udp_host=host, logging_udp_port=port, logging_udp_format="wsjtx"
        )
        build_qso_logger(config).log_qso(_record())

        data, _addr = receiver.recvfrom(4096)
        magic, _schema, msg_type = struct.unpack_from(">III", data, 0)
        assert (magic, msg_type) == (MAGIC, TYPE_LOGGED_ADIF)
