"""WSJT-X Network Message protocol -- just enough of it to build a single
outgoing "Logged ADIF" (message type 12) datagram, the interface WSJT-X
itself uses to announce a logged QSO and that several companion loggers
(QLog, JTAlert, GridTracker, ...) accept from third-party senders too. Pure
stdlib (`struct` for big-endian packing), no Qt, no I/O -- mirrors
`models.adif`'s own "pure, unit-testable" shape; `models.qso.UdpQsoLogger`
is what actually sends the bytes this module builds.

Verified against WSJT-X's own `Network/NetworkMessage.hpp` source rather
than guessed. Every message starts with a 4-byte magic number and a 4-byte
schema number (both big-endian, matching QDataStream's default byte
order), followed by a type-specific payload that itself always starts with
a 4-byte message type and a length-prefixed "Id" string identifying the
sender. Message type 12 ("Logged ADIF")'s payload is just that common
prefix plus one more string: a complete ADIF *file* fragment (header +
`<EOH>` + the QSO record) -- not a bare `<EOR>`-terminated record like the
plain ADIF-over-UDP path (models.qso.UdpQsoLogger's "adif" format) sends.

A "utf8" field in this protocol is a 4-byte big-endian length (in bytes,
not characters) followed by that many raw UTF-8 bytes -- no terminator. A
null string is represented by length `0xffffffff`; not used here, since
every string cwrobot sends is always present.
"""

from __future__ import annotations

import struct

MAGIC = 0xADBCCBDA
# The schema number real, current WSJT-X builds send (Qt >= 5.4, true for
# any modern build) -- see NetworkMessage.hpp's schema_number constant.
SCHEMA = 3
TYPE_LOGGED_ADIF = 12
# Identifies cwrobot as the sender -- receivers use this only for display/
# tracking (e.g. distinguishing multiple simultaneous senders), not
# protocol validation, so any stable, recognizable string works.
CLIENT_ID = "CW Robot"


def _utf8_field(text: str) -> bytes:
    encoded = text.encode("utf-8")
    return struct.pack(">I", len(encoded)) + encoded


def build_logged_adif_message(adif_text: str, client_id: str = CLIENT_ID) -> bytes:
    """The complete bytes for one WSJT-X "Logged ADIF" (type 12) UDP
    datagram. `adif_text` should be a full ADIF file fragment (see
    `models.adif.format_qso_record_as_mini_file`), not a bare record."""
    return (
        struct.pack(">III", MAGIC, SCHEMA, TYPE_LOGGED_ADIF)
        + _utf8_field(client_id)
        + _utf8_field(adif_text)
    )
