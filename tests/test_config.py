"""Tests for cwrobot.config -- schema migration and the serial-vs-network
Hamlib connection resolver."""

from __future__ import annotations

import json

from cwrobot.config import CONFIG_VERSION, AppConfig, config_path
from cwrobot.hamlib.ctypes_bindings import RIG_MODEL_NETRIGCTL


def test_hamlib_effective_target_serial_passes_fields_through():
    config = AppConfig(
        hamlib_connection_type="serial",
        hamlib_rig_model_id=3073,
        hamlib_port_path="/dev/ttyUSB0",
        hamlib_baud_rate=19200,
    )
    assert config.hamlib_effective_target() == (3073, "/dev/ttyUSB0", 19200)


def test_hamlib_effective_target_serial_allows_no_rig_selected():
    config = AppConfig(hamlib_connection_type="serial", hamlib_rig_model_id=None)
    model_id, _, _ = config.hamlib_effective_target()
    assert model_id is None


def test_hamlib_effective_target_network_uses_netrigctl_and_host_port():
    config = AppConfig(
        hamlib_connection_type="network",
        hamlib_network_host="192.168.1.5",
        hamlib_network_port=4533,
        # Serial fields are ignored in network mode:
        hamlib_rig_model_id=3073,
        hamlib_baud_rate=19200,
    )
    assert config.hamlib_effective_target() == (RIG_MODEL_NETRIGCTL, "192.168.1.5:4533", None)


def test_load_migrates_pre_v9_config_with_network_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = {
        "config_version": 8,
        "hamlib_rig_model_id": 3073,
        "hamlib_port_path": "/dev/ttyUSB0",
    }
    config_path().write_text(json.dumps(legacy), encoding="utf-8")

    config = AppConfig.load()

    assert config.config_version == CONFIG_VERSION
    assert config.hamlib_connection_type == "serial"
    assert config.hamlib_network_host == "localhost"
    assert config.hamlib_network_port == 4532
    # Existing serial settings survive the migration untouched.
    assert config.hamlib_rig_model_id == 3073
    assert config.hamlib_port_path == "/dev/ttyUSB0"
