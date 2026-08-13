"""Tests for cwrobot.audio's find_library() bootstrap patch -- see its
__init__.py module docstring for what this works around (sounddevice's
Linux import path having no fallback if PortAudio isn't registered with
the system's ldconfig) and why it can't just be "bundle the .so and set
LD_LIBRARY_PATH" the way Hamlib is (find_library() doesn't consult it)."""

from __future__ import annotations

import ctypes.util

from cwrobot.audio import _patch_find_library_for_bundled_portaudio


def test_noop_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("CWROBOT_BUNDLED_PORTAUDIO", raising=False)
    original = ctypes.util.find_library

    _patch_find_library_for_bundled_portaudio()

    assert ctypes.util.find_library is original


def test_noop_when_bundled_file_does_not_exist(monkeypatch, tmp_path):
    missing = tmp_path / "does-not-exist.so"
    monkeypatch.setenv("CWROBOT_BUNDLED_PORTAUDIO", str(missing))
    original = ctypes.util.find_library

    _patch_find_library_for_bundled_portaudio()

    assert ctypes.util.find_library is original


def test_falls_back_to_bundled_path_when_normal_lookup_fails(monkeypatch, tmp_path):
    bundled = tmp_path / "libportaudio.so.2"
    bundled.write_bytes(b"")
    monkeypatch.setenv("CWROBOT_BUNDLED_PORTAUDIO", str(bundled))
    monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)

    _patch_find_library_for_bundled_portaudio()

    assert ctypes.util.find_library("portaudio") == str(bundled)


def test_does_not_shadow_a_successful_system_lookup(monkeypatch, tmp_path):
    """A host that already has PortAudio properly installed keeps using
    that copy -- the bundled one is only ever a fallback."""
    bundled = tmp_path / "libportaudio.so.2"
    bundled.write_bytes(b"")
    monkeypatch.setenv("CWROBOT_BUNDLED_PORTAUDIO", str(bundled))
    monkeypatch.setattr(
        ctypes.util,
        "find_library",
        lambda name: "/usr/lib/libportaudio.so.2" if name == "portaudio" else None,
    )

    _patch_find_library_for_bundled_portaudio()

    assert ctypes.util.find_library("portaudio") == "/usr/lib/libportaudio.so.2"


def test_only_applies_to_portaudio_lookups(monkeypatch, tmp_path):
    bundled = tmp_path / "libportaudio.so.2"
    bundled.write_bytes(b"")
    monkeypatch.setenv("CWROBOT_BUNDLED_PORTAUDIO", str(bundled))
    monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)

    _patch_find_library_for_bundled_portaudio()

    assert ctypes.util.find_library("hamlib") is None
