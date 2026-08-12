"""Serial-port enumeration for the Hamlib CAT "Port" field.

Wraps pyserial's serial.tools.list_ports.comports(), which auto-selects a
platform-specific backend (Linux/Windows/macOS) internally -- no OS-specific
code is needed here. Queried live on every call, no caching, matching
audio.devices' convention: USB-serial adapters can be hot-plugged between
Settings dialog opens (or even during one).
"""

from __future__ import annotations

from dataclasses import dataclass

from serial.tools.list_ports import comports


@dataclass(frozen=True)
class SerialPort:
    device: str  # e.g. "/dev/ttyUSB0" or "COM3" -- HamlibRig's port_path
    description: str  # human-readable; pyserial itself falls back to "n/a"


def list_serial_ports() -> list[SerialPort]:
    """Live-enumerate available serial ports. Returns [] if none found --
    that's a normal result, not an error. Does NOT catch comports() itself
    raising -- callers guard that, mirroring audio.devices' convention
    (see ui.settings_dialog._populate_port_combo)."""
    return [SerialPort(device=p.device, description=p.description or "n/a") for p in comports()]


__all__ = ["SerialPort", "list_serial_ports"]
