"""Audio device enumeration, including PipeWire virtual/monitor sources.

Background (see Specifikacio.md plan): the system PortAudio build has no
PulseAudio host API (this has never shipped in a released PortAudio
version), so `sd.query_hostapis()` cannot be used to reach PipeWire/Pulse
virtual sources directly.

Two tiers, merged into one flat list per direction:
  - Tier A: whatever `sd.query_devices()` reports via the ALSA host API
    (hardware devices, plus the system's "pipewire"/"default" entries,
    which already tunnel through PipeWire's own default routing).
  - Tier B: PipeWire sinks/sources fetched live from `pactl` (see
    `audio/pipewire_route.py`), for picking a *specific* named PipeWire
    node -- e.g. a monitor of another application's output, so it can be
    used as an RX input, per Specifikacio.md.

Selecting a Tier A device opens it directly, exactly as before. Selecting a
Tier B device can't go through PortAudio at all (it only exposes PipeWire's
own named nodes via its JACK host API, which is not what we want here);
instead the caller opens the stream on the ALSA "default" device and uses
`pipewire_route.route_active_stream_to_sink`/`..._to_source` to move it
onto the chosen node after the fact. `resolve_input_device`/
`resolve_output_device` below do that Tier A/Tier B lookup so callers don't
have to know which tier a saved device name came from.

Nothing here ever writes a config file -- no `~/.config/alsa/asoundrc`,
no `~/.config/alsa/conf.d/*`. `pactl`/PipeWire are queried live on every
call.
"""

from __future__ import annotations

from dataclasses import dataclass

import sounddevice as sd

from cwrobot.audio.pipewire_route import (
    PipeWireSink,
    PipeWireSource,
    find_pipewire_sink_by_name,
    find_pipewire_source_by_name,
    list_pipewire_sinks,
    list_pipewire_sources,
)


@dataclass(frozen=True)
class AudioDevice:
    """A PortAudio-visible (Tier A) device, ready to pass as `sounddevice`'s
    `device=` arg."""

    name: str
    label: str
    max_input_channels: int
    max_output_channels: int


def rescan_devices() -> None:
    """Ask PortAudio to reinitialize, so hardware devices that appeared or
    disappeared since startup (USB hotplug, etc.) show up in
    `sd.query_devices()`. PipeWire sinks/sources are queried live via
    `pactl` on every `list_*`/`find_*` call already, so there's nothing to
    refresh for those."""
    sd._terminate()
    sd._initialize()


def list_input_devices() -> list[AudioDevice]:
    """Tier A input devices, plus every PipeWire source (including sink
    monitors) as a selectable pseudo-device."""
    devices = [
        AudioDevice(
            name=d["name"],
            label=d["name"],
            max_input_channels=d["max_input_channels"],
            max_output_channels=d["max_output_channels"],
        )
        for d in sd.query_devices()
        if d["max_input_channels"] > 0
    ]
    devices.extend(
        AudioDevice(
            name=source.description,
            label=source.description,
            max_input_channels=1,
            max_output_channels=0,
        )
        for source in list_pipewire_sources()
    )
    return devices


def list_output_devices() -> list[AudioDevice]:
    """Tier A output devices, plus every PipeWire sink as a selectable
    pseudo-device."""
    devices = [
        AudioDevice(
            name=d["name"],
            label=d["name"],
            max_input_channels=d["max_input_channels"],
            max_output_channels=d["max_output_channels"],
        )
        for d in sd.query_devices()
        if d["max_output_channels"] > 0
    ]
    devices.extend(
        AudioDevice(
            name=sink.description,
            label=sink.description,
            max_input_channels=0,
            max_output_channels=1,
        )
        for sink in list_pipewire_sinks()
    )
    return devices


def find_input_device_by_name(name: str | None) -> AudioDevice | None:
    """Tier A only -- look up a real PortAudio input device by name."""
    if not name:
        return None
    for d in sd.query_devices():
        if d["max_input_channels"] > 0 and d["name"] == name:
            return AudioDevice(
                name=d["name"],
                label=d["name"],
                max_input_channels=d["max_input_channels"],
                max_output_channels=d["max_output_channels"],
            )
    return None


def find_output_device_by_name(name: str | None) -> AudioDevice | None:
    """Tier A only -- look up a real PortAudio output device by name."""
    if not name:
        return None
    for d in sd.query_devices():
        if d["max_output_channels"] > 0 and d["name"] == name:
            return AudioDevice(
                name=d["name"],
                label=d["name"],
                max_input_channels=d["max_input_channels"],
                max_output_channels=d["max_output_channels"],
            )
    return None


def resolve_input_device(name: str | None) -> tuple[str | None, PipeWireSource | None]:
    """Resolve a saved/selected input device name to either a plain
    PortAudio device name (pass straight to `sd.InputStream(device=...)`)
    or a `PipeWireSource` to route onto after opening the stream on the
    default device -- never both.

    Tier A is tried first, so a PipeWire source that happens to share a
    name with a real PortAudio device resolves to the direct (cheaper,
    no-routing-needed) path. Returns `(None, None)` for an unset/unknown
    name -- callers should treat that as "use the system default".
    """
    if not name:
        return None, None
    if find_input_device_by_name(name) is not None:
        return name, None
    source = find_pipewire_source_by_name(name)
    if source is not None:
        return None, source
    return None, None


def resolve_output_device(name: str | None) -> tuple[str | None, PipeWireSink | None]:
    """Output-side mirror of `resolve_input_device`."""
    if not name:
        return None, None
    if find_output_device_by_name(name) is not None:
        return name, None
    sink = find_pipewire_sink_by_name(name)
    if sink is not None:
        return None, sink
    return None, None


__all__ = [
    "AudioDevice",
    "find_input_device_by_name",
    "find_output_device_by_name",
    "list_input_devices",
    "list_output_devices",
    "rescan_devices",
    "resolve_input_device",
    "resolve_output_device",
]
