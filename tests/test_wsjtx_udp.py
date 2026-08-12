"""Unit tests for cwrobot.models.wsjtx_udp: the WSJT-X Network Message
"Logged ADIF" (type 12) datagram builder. Pure struct packing, no I/O, no
Qt involved -- verified by hand-unpacking the bytes back with `struct`."""

from __future__ import annotations

import struct

from cwrobot.models.wsjtx_udp import (
    CLIENT_ID,
    MAGIC,
    SCHEMA,
    TYPE_LOGGED_ADIF,
    build_logged_adif_message,
)


def _read_utf8_field(data: bytes, offset: int) -> tuple[str, int]:
    (length,) = struct.unpack_from(">I", data, offset)
    offset += 4
    text = data[offset : offset + length].decode("utf-8")
    return text, offset + length


def test_build_logged_adif_message_header_matches_documented_constants():
    message = build_logged_adif_message("<CALL:6>DL1XYZ<EOR>\n")

    magic, schema, msg_type = struct.unpack_from(">III", message, 0)
    assert magic == MAGIC == 0xADBCCBDA
    assert schema == SCHEMA == 3
    assert msg_type == TYPE_LOGGED_ADIF == 12


def test_build_logged_adif_message_round_trips_id_and_adif_text():
    adif_text = "<CALL:6>DL1XYZ<MODE:2>CW<EOR>\n"
    message = build_logged_adif_message(adif_text, client_id="Test App")

    offset = struct.calcsize(">III")
    client_id, offset = _read_utf8_field(message, offset)
    embedded_adif, offset = _read_utf8_field(message, offset)

    assert client_id == "Test App"
    assert embedded_adif == adif_text
    assert offset == len(message)  # nothing trailing/unaccounted for


def test_build_logged_adif_message_defaults_to_cw_robot_client_id():
    message = build_logged_adif_message("<EOR>\n")
    offset = struct.calcsize(">III")
    client_id, _offset = _read_utf8_field(message, offset)
    assert client_id == CLIENT_ID == "CW Robot"
