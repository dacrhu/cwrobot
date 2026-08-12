"""Unit tests for cwrobot.audio.devices.

PipeWire-backed listing/resolution is exercised via mocked
cwrobot.audio.pipewire_route calls, so this suite runs headless without a
live PipeWire session and without touching sounddevice/PortAudio.
"""

from __future__ import annotations

from unittest.mock import patch

from cwrobot.audio.devices import (
    AudioDevice,
    find_input_device_by_name,
    find_output_device_by_name,
    list_input_devices,
    list_output_devices,
    resolve_input_device,
    resolve_output_device,
)
from cwrobot.audio.pipewire_route import PipeWireSink, PipeWireSource

_PORTAUDIO_DEVICES = [
    {"name": "HDA Intel PCH", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "HDA Intel PCH", "max_input_channels": 0, "max_output_channels": 2},
    {"name": "default", "max_input_channels": 2, "max_output_channels": 2},
]

_SOURCES = [PipeWireSource(id=1, name="radio.monitor", description="Radio monitor")]
_SINKS = [PipeWireSink(id=2, name="radio_null", description="Radio")]


def _patch_query_devices():
    return patch(
        "cwrobot.audio.devices.sd.query_devices", return_value=_PORTAUDIO_DEVICES
    )


class TestListInputDevices:
    def test_merges_portaudio_and_pipewire_sources(self) -> None:
        with (
            _patch_query_devices(),
            patch("cwrobot.audio.devices.list_pipewire_sources", return_value=_SOURCES),
        ):
            devices = list_input_devices()
        assert AudioDevice(
            name="HDA Intel PCH", label="HDA Intel PCH", max_input_channels=2, max_output_channels=0
        ) in devices
        assert AudioDevice(
            name="Radio monitor", label="Radio monitor", max_input_channels=1, max_output_channels=0
        ) in devices
        # The output-only PortAudio entry never shows up as an input device.
        assert not any(d.name == "default" and d.max_input_channels == 0 for d in devices)

    def test_no_pipewire_sources_still_lists_portaudio_devices(self) -> None:
        with (
            _patch_query_devices(),
            patch("cwrobot.audio.devices.list_pipewire_sources", return_value=[]),
        ):
            devices = list_input_devices()
        assert [d.name for d in devices] == ["HDA Intel PCH", "default"]


class TestListOutputDevices:
    def test_merges_portaudio_and_pipewire_sinks(self) -> None:
        with (
            _patch_query_devices(),
            patch("cwrobot.audio.devices.list_pipewire_sinks", return_value=_SINKS),
        ):
            devices = list_output_devices()
        assert [d.name for d in devices] == ["HDA Intel PCH", "default", "Radio"]


class TestFindDeviceByName:
    def test_find_input_device_by_name_matches_portaudio_only(self) -> None:
        with _patch_query_devices():
            found = find_input_device_by_name("HDA Intel PCH")
            assert found is not None and found.max_input_channels == 2

            # A PipeWire-only name isn't a Tier A device.
            assert find_input_device_by_name("Radio monitor") is None

    def test_find_output_device_by_name_matches_portaudio_only(self) -> None:
        with _patch_query_devices():
            found = find_output_device_by_name("default")
            assert found is not None and found.max_output_channels == 2
            assert find_output_device_by_name("Radio") is None

    def test_none_and_empty_name_return_none_without_querying(self) -> None:
        with patch(
            "cwrobot.audio.devices.sd.query_devices",
            side_effect=AssertionError("must not query PortAudio for an unset name"),
        ):
            assert find_input_device_by_name(None) is None
            assert find_input_device_by_name("") is None
            assert find_output_device_by_name(None) is None
            assert find_output_device_by_name("") is None


class TestResolveInputDevice:
    def test_portaudio_match_wins_and_needs_no_routing(self) -> None:
        with (
            _patch_query_devices(),
            patch(
                "cwrobot.audio.devices.find_pipewire_source_by_name",
                side_effect=AssertionError("must not fall through to PipeWire on a Tier A hit"),
            ),
        ):
            device, source = resolve_input_device("HDA Intel PCH")
        assert device == "HDA Intel PCH"
        assert source is None

    def test_falls_back_to_pipewire_source(self) -> None:
        with (
            _patch_query_devices(),
            patch(
                "cwrobot.audio.devices.find_pipewire_source_by_name",
                return_value=_SOURCES[0],
            ),
        ):
            device, source = resolve_input_device("Radio monitor")
        assert device is None
        assert source == _SOURCES[0]

    def test_unknown_name_resolves_to_default(self) -> None:
        with (
            _patch_query_devices(),
            patch("cwrobot.audio.devices.find_pipewire_source_by_name", return_value=None),
        ):
            assert resolve_input_device("Nonexistent") == (None, None)

    def test_none_name_resolves_to_default_without_querying_pipewire(self) -> None:
        with (
            _patch_query_devices(),
            patch(
                "cwrobot.audio.devices.find_pipewire_source_by_name",
                side_effect=AssertionError("must not query PipeWire for an unset name"),
            ),
        ):
            assert resolve_input_device(None) == (None, None)


class TestResolveOutputDevice:
    def test_portaudio_match_wins_and_needs_no_routing(self) -> None:
        with (
            _patch_query_devices(),
            patch(
                "cwrobot.audio.devices.find_pipewire_sink_by_name",
                side_effect=AssertionError("must not fall through to PipeWire on a Tier A hit"),
            ),
        ):
            device, sink = resolve_output_device("default")
        assert device == "default"
        assert sink is None

    def test_falls_back_to_pipewire_sink(self) -> None:
        with (
            _patch_query_devices(),
            patch("cwrobot.audio.devices.find_pipewire_sink_by_name", return_value=_SINKS[0]),
        ):
            device, sink = resolve_output_device("Radio")
        assert device is None
        assert sink == _SINKS[0]
