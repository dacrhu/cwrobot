"""Route cwrobot's own audio streams onto a specific PipeWire sink/source,
without ever writing an ALSA config file.

Why this exists: the system PortAudio build has no PulseAudio host API (this
has never shipped in a released PortAudio version), so `sd.query_hostapis()`
cannot reach PipeWire/Pulse virtual sinks and sources directly. An earlier
version of this project worked around that by driving PipeWire's own ALSA
plugin -- generating a named ALSA PCM stanza per PipeWire node into the
user's `~/.config/alsa/asoundrc` so PortAudio's (perfectly ordinary) ALSA
host API would pick them up. That works, but it means cwrobot mutates a
config file that isn't its own on every rescan, and every routing target
has to survive an ALSA-PCM-name round trip.

This module replaces that with the same technique Open-SSTV uses: never
target PipeWire directly through PortAudio at all. Always open the stream
on the safe ALSA "default" device (which itself already tunnels through
PipeWire on a PipeWire system), then use PipeWire's PulseAudio-compatible
control protocol -- `pactl move-sink-input <id> <target-sink>` for
playback, `pactl move-source-output <id> <target-source>` for capture --
to move *that specific, already-open* stream onto the chosen node, after
the fact. This only retargets cwrobot's own stream; every other
application's undirected audio is untouched, and no file on disk is ever
written.

Linux/PipeWire only. Every public function degrades to "nothing to route"
(``False`` / ``None`` / ``[]``) on any failure -- pactl not installed, no
PipeWire session, a timeout, unparseable output -- never raises. A routing
failure must fall back to exactly the plain "play/record on the system
default" behaviour, never to something worse.

Public API:
    is_pipewire_active() -> bool
    list_pipewire_sinks() -> list[PipeWireSink]
    list_pipewire_sources() -> list[PipeWireSource]
    find_pipewire_sink_by_name(name) -> PipeWireSink | None
    find_pipewire_source_by_name(name) -> PipeWireSource | None
    snapshot_sink_input_ids() -> set[int]
    snapshot_source_output_ids() -> set[int]
    route_active_stream_to_sink(target, before_ids) -> bool
    route_active_stream_to_source(target, before_ids) -> bool
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

#: How long the route_* functions wait for the new sink-input/source-output
#: to register before giving up. Generous relative to the sub-100ms
#: typically observed -- a slow/loaded system still gets a fair shot before
#: falling back.
_ROUTE_TIMEOUT_S: float = 1.0
_ROUTE_POLL_INTERVAL_S: float = 0.02

#: Timeout for each individual pactl subprocess call. pactl talking to a
#: live local PipeWire socket is normally sub-10ms; this is a safety net
#: against a wedged/unresponsive server, not a tuning knob.
_PACTL_TIMEOUT_S: float = 2.0


@dataclass(frozen=True, slots=True)
class PipeWireSink:
    """One PipeWire sink (a playback target), as seen through pactl's
    PulseAudio-compatible view.

    ``id`` is pactl's numeric sink index *at listing time* -- PipeWire node
    ids churn across sessions/graph changes, so callers should re-resolve
    (via ``find_pipewire_sink_by_name``) right before use rather than
    caching this across calls.
    """

    id: int
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class PipeWireSource:
    """One PipeWire source (a capture target), as seen through pactl's
    PulseAudio-compatible view.

    Includes both real input devices and *monitors* of sinks -- a sink's
    monitor is what lets cwrobot "listen in" on another application's (or
    the system's) output as an RX input, which Specifikacio.md requires
    alongside real hardware inputs. Same id-churn caveat as
    ``PipeWireSink``.
    """

    id: int
    name: str
    description: str


def is_pipewire_active() -> bool:
    """Best-effort check for whether PipeWire's own daemon is live.

    PipeWire's daemon binds a Unix-domain socket at
    ``$XDG_RUNTIME_DIR/pipewire-0`` for as long as it's running. Never
    raises -- an unset env var or an unstattable path just means "no".
    """
    try:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime_dir:
            return False
        return (Path(runtime_dir) / "pipewire-0").exists()
    except (OSError, ValueError):
        return False


def _run_pactl(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run ``pactl`` with the given args. ``None`` on any failure, logged."""
    try:
        return subprocess.run(
            ["pactl", *args],
            capture_output=True,
            text=True,
            timeout=_PACTL_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        _log.debug("pactl not found -- PipeWire routing unavailable")
        return None
    except subprocess.TimeoutExpired:
        _log.warning("pactl %s timed out after %.1fs", args, _PACTL_TIMEOUT_S)
        return None
    except OSError as exc:  # noqa: BLE001 -- never let a routing helper crash audio I/O
        _log.warning("pactl %s failed: %s", args, exc)
        return None


def _describe(entry: dict) -> str | None:
    """pactl reports a description of either JSON null or the literal
    string "(null)" for some nodes (seen on real systems for a bare ALSA
    hardware device with no nicer PipeWire description set) -- fall back to
    the raw name in both cases so callers never see the literal text
    "(null)"."""
    description = entry.get("description")
    if not description or description == "(null)":
        return None
    return str(description)


def list_pipewire_sinks() -> list[PipeWireSink]:
    """All PipeWire sinks pactl currently knows about.

    Empty list (never raises) if PipeWire isn't active, ``pactl`` isn't
    installed, or its output can't be parsed -- callers treat that exactly
    like "no PipeWire sinks available" rather than an error.
    """
    if not is_pipewire_active():
        return []
    result = _run_pactl(["-f", "json", "list", "sinks"])
    if result is None or result.returncode != 0:
        return []
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        _log.warning("pactl list sinks produced unparseable JSON")
        return []

    out: list[PipeWireSink] = []
    for entry in raw:
        try:
            sink_id = int(entry["index"])
            name = str(entry["name"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(PipeWireSink(id=sink_id, name=name, description=_describe(entry) or name))
    return out


def list_pipewire_sources() -> list[PipeWireSource]:
    """All PipeWire sources pactl currently knows about, including sink
    monitors.

    Empty list (never raises) under the same conditions as
    ``list_pipewire_sinks``.
    """
    if not is_pipewire_active():
        return []
    result = _run_pactl(["-f", "json", "list", "sources"])
    if result is None or result.returncode != 0:
        return []
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        _log.warning("pactl list sources produced unparseable JSON")
        return []

    out: list[PipeWireSource] = []
    for entry in raw:
        try:
            source_id = int(entry["index"])
            name = str(entry["name"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(PipeWireSource(id=source_id, name=name, description=_describe(entry) or name))
    return out


def find_pipewire_sink_by_name(name: str | None) -> PipeWireSink | None:
    """Look up a sink by its saved description string.

    Always re-queries (``list_pipewire_sinks()`` is cheap, one pactl call)
    rather than caching, since sink ids/availability can change between
    when a name was saved to config and when it's used for playback.
    """
    if not name:
        return None
    for sink in list_pipewire_sinks():
        if sink.description == name:
            return sink
    return None


def find_pipewire_source_by_name(name: str | None) -> PipeWireSource | None:
    """Look up a source by its saved description string. See
    ``find_pipewire_sink_by_name`` for why this always re-queries."""
    if not name:
        return None
    for source in list_pipewire_sources():
        if source.description == name:
            return source
    return None


def _snapshot_ids(list_command: list[str]) -> set[int]:
    if not is_pipewire_active():
        return set()
    result = _run_pactl(["-f", "json", *list_command])
    if result is None or result.returncode != 0:
        return set()
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return set()
    ids: set[int] = set()
    for entry in raw:
        try:
            ids.add(int(entry["index"]))
        except (KeyError, TypeError, ValueError):
            continue
    return ids


def snapshot_sink_input_ids() -> set[int]:
    """The set of currently-live pactl sink-input indices.

    Call this immediately *before* opening the PortAudio OutputStream that
    will be routed -- ``route_active_stream_to_sink`` diffs against it to
    find the one new sink-input our own stream creates. Empty set (never
    raises) if PipeWire/pactl is unavailable.
    """
    return _snapshot_ids(["list", "sink-inputs"])


def snapshot_source_output_ids() -> set[int]:
    """The set of currently-live pactl source-output indices. Same
    call-before-opening-the-stream contract as ``snapshot_sink_input_ids``,
    for InputStreams and ``route_active_stream_to_source``."""
    return _snapshot_ids(["list", "source-outputs"])


def _route_new_stream(
    *,
    target_kind: str,
    item_noun: str,
    list_command: list[str],
    move_command: str,
    target_id: int,
    target_description: str,
    before_ids: set[int],
    timeout_s: float,
    poll_interval_s: float,
) -> bool:
    """Shared polling/move logic behind ``route_active_stream_to_sink`` and
    ``route_active_stream_to_source`` -- identical except for which pactl
    subcommands they poll/move with and what to call things in logs.
    ``target_kind`` is "sink"/"source"; ``item_noun`` is the polled/moved
    stream object's pactl name ("sink-input"/"source-output")."""
    if not is_pipewire_active():
        return False
    deadline = time.monotonic() + timeout_s
    new_id: int | None = None
    while time.monotonic() < deadline:
        after_ids = _snapshot_ids(list_command)
        new_ids = after_ids - before_ids
        if new_ids:
            new_id = min(new_ids)
            if len(new_ids) > 1:
                _log.warning(
                    "route_active_stream_to_%s: %d new %ss appeared at once "
                    "(%s) -- picking the lowest id (%d)",
                    target_kind, len(new_ids), item_noun, sorted(new_ids), new_id,
                )
            break
        time.sleep(poll_interval_s)

    if new_id is None:
        _log.warning(
            "route_active_stream_to_%s: no new %s appeared within %.1fs -- "
            "stream stays on the system default",
            target_kind, item_noun, timeout_s,
        )
        return False

    result = _run_pactl([move_command, str(new_id), str(target_id)])
    if result is None or result.returncode != 0:
        _log.warning(
            "route_active_stream_to_%s: pactl %s %d -> %r (id %d) failed%s",
            target_kind, move_command, new_id, target_description, target_id,
            f": {result.stderr.strip()}" if result is not None else "",
        )
        return False

    _log.info(
        "audio stream routed: %s %d -> %r (pactl %s id %d)",
        item_noun, new_id, target_description, target_kind, target_id,
    )
    return True


def route_active_stream_to_sink(
    target: PipeWireSink,
    before_ids: set[int],
    timeout_s: float = _ROUTE_TIMEOUT_S,
    poll_interval_s: float = _ROUTE_POLL_INTERVAL_S,
) -> bool:
    """Move the sink-input that appeared since ``before_ids`` onto
    ``target``.

    Polls ``pactl list sink-inputs`` for a new index not present in
    ``before_ids`` -- that's cwrobot's own just-opened playback stream,
    identified by process of elimination rather than by name matching
    (PortAudio's ALSA-pulse-bridge client name varies by build/platform).
    If more than one new id shows up in the same poll, the lowest new
    index is used -- deterministic, and logged so it's diagnosable --
    since cwrobot itself never opens more than one TX OutputStream at a
    time.

    Returns ``False`` (never raises) if no new sink-input appears within
    ``timeout_s``, or if the move itself fails -- the caller's playback
    simply continues on whatever device it already opened.
    """
    return _route_new_stream(
        target_kind="sink",
        item_noun="sink-input",
        list_command=["list", "sink-inputs"],
        move_command="move-sink-input",
        target_id=target.id,
        target_description=target.description,
        before_ids=before_ids,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )


def route_active_stream_to_source(
    target: PipeWireSource,
    before_ids: set[int],
    timeout_s: float = _ROUTE_TIMEOUT_S,
    poll_interval_s: float = _ROUTE_POLL_INTERVAL_S,
) -> bool:
    """Move the source-output that appeared since ``before_ids`` onto
    ``target``. The capture-side mirror of ``route_active_stream_to_sink``
    -- same process-of-elimination identification, same
    lowest-id-wins tie-break, same "fall back silently" failure mode.
    """
    return _route_new_stream(
        target_kind="source",
        item_noun="source-output",
        list_command=["list", "source-outputs"],
        move_command="move-source-output",
        target_id=target.id,
        target_description=target.description,
        before_ids=before_ids,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )


__all__ = [
    "PipeWireSink",
    "PipeWireSource",
    "find_pipewire_sink_by_name",
    "find_pipewire_source_by_name",
    "is_pipewire_active",
    "list_pipewire_sinks",
    "list_pipewire_sources",
    "route_active_stream_to_sink",
    "route_active_stream_to_source",
    "snapshot_sink_input_ids",
    "snapshot_source_output_ids",
]
