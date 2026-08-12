"""Application configuration: a plain, human-readable JSON file.

We deliberately avoid QSettings (see Specifikacio.md plan discussion) in favor
of a dataclasses-based config with an explicit schema version, so the config
file stays easy to read/edit by hand (useful when debugging Hamlib port/baud
issues) and can be migrated cleanly as the schema grows (e.g. toward the
future external-logger integration).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

CONFIG_VERSION = 8

TxBackendName = Literal["audio", "hamlib"]
LoggingMethod = Literal["file", "udp"]
LoggingUdpFormat = Literal["adif", "wsjtx"]


def config_dir() -> Path:
    """Return the XDG-compliant config directory for cwrobot, creating it if needed."""
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    directory = base / "cwrobot"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class AppConfig:
    config_version: int = CONFIG_VERSION

    # Shared RX/TX pitch reference (see Specifikacio.md: the radio's sidetone
    # pitch is fixed and pre-configured here; the operator tunes the radio's
    # VFO to match it, aided by the waterfall marker).
    target_pitch_hz: int = 600

    # TX
    tx_speed_wpm: int = 20
    tx_backend: TxBackendName = "audio"
    tx_output_device: str | None = None
    # "Kezi adas emulalasa" -- see tx.encoder.text_to_elements's jitter
    # param and ui.tx_panel's manual_keying_checkbox. Intensity is the
    # unitless 1-8 slider level (see tx.encoder.manual_keying_jitter_from_level
    # for the actual jitter-fraction mapping) -- default 1 matches
    # MANUAL_KEYING_LEVEL_MIN there.
    manual_keying_emulation: bool = False
    manual_keying_intensity: int = 1

    # RX
    rx_input_device: str | None = None
    waterfall_half_span_hz: int = 1000
    detection_bin_span: int = 1  # Goertzel bins +/- target; see dsp.tone_detector
    squelch_ratio: float = 14.0
    auto_bandwidth: bool = True  # window/bandwidth auto-tracks detected WPM; see dsp.tone_detector

    # Hamlib CAT
    hamlib_rig_model_id: int | None = None
    hamlib_port_path: str = ""
    hamlib_baud_rate: int = 9600

    # Own (operator) data -- see ui.settings_dialog. operator_callsign also
    # doubles as QsoRecord.station_callsign (the de-facto counterpart to
    # each logged QSO's remote callsign) when logging -- see models.qso.
    operator_callsign: str = ""
    operator_name: str = ""
    operator_locator: str = ""
    operator_qth: str = ""

    # QSO logging (see ui.qso_panel's "Log QSO" button, models.qso) -- either
    # an ADIF file (one per operator callsign, in logging_folder) or ADIF
    # over UDP to logging_udp_host:logging_udp_port, itself in either of two
    # wire formats (logging_udp_format -- "wsjtx" by default: the WSJT-X
    # Network Message protocol most current companion loggers speak, e.g.
    # QLog/JTAlert/GridTracker; "adif" is a bare ADIF record instead, for
    # Log4OM-style listeners). See ui.settings_dialog's "Logging" tab.
    logging_method: LoggingMethod = "file"
    logging_folder: str = ""
    logging_udp_host: str = "127.0.0.1"
    logging_udp_port: int = 2237
    logging_udp_format: LoggingUdpFormat = "wsjtx"

    # TX quick-button macro texts (see cwrobot.macros / ui.macro_bar),
    # keyed by MacroDefinition.key. Only overrides live here -- a macro
    # missing from this dict just falls back to its DEFAULT_MACROS text,
    # so shipping a new default macro in a later version never needs a
    # migration step.
    macro_texts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "AppConfig":
        path = config_path()
        if not path.exists():
            return cls()
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable config: fall back to defaults rather than crash.
            return cls()
        data = _migrate(data)
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def save(self) -> None:
        path = config_path()
        tmp_path = path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp_path.replace(path)


def _migrate(data: dict) -> dict:
    """Placeholder for forward config migrations, keyed off config_version."""
    version = data.get("config_version", 1)
    if version < 2:
        data.setdefault("auto_bandwidth", True)
    if version < 3:
        data.setdefault("operator_callsign", "")
        data.setdefault("operator_name", "")
        data.setdefault("operator_locator", "")
        data.setdefault("operator_qth", "")
    if version < 4:
        data.setdefault("macro_texts", {})
    if version < 5:
        data.setdefault("manual_keying_emulation", False)
    if version < 6:
        data.setdefault("manual_keying_intensity", 1)
    if version < 7:
        data.setdefault("logging_method", "file")
        data.setdefault("logging_folder", "")
        data.setdefault("logging_udp_host", "127.0.0.1")
        data.setdefault("logging_udp_port", 2237)
    if version < 8:
        data.setdefault("logging_udp_format", "wsjtx")
    if version < CONFIG_VERSION:
        data["config_version"] = CONFIG_VERSION
    return data
