"""cwrobot.audio package init.

Runs before any of this package's submodules (devices.py/capture.py/
playback.py) import `sounddevice` -- Python always executes a package's
__init__.py before any of its submodules, regardless of which submodule an
outside caller happens to import first, which is what makes it safe to do
the patch below exactly once, here, for all three.

Why this exists: sounddevice's own library-loading code (top of
sounddevice.py) calls `ctypes.util.find_library("portaudio")` on Linux and
raises OSError on the spot if that comes up empty -- no fallback, unlike
its Windows/macOS code paths, which fall back to a prebuilt binary shipped
inside the `_sounddevice_data` wheel (there's no Linux equivalent of that:
PortAudio's Linux backends need to link against the host's own ALSA/
PulseAudio/JACK, so a portable prebuilt .so isn't practical the same way).

`ctypes.util.find_library` on Linux only consults the *system's* ldconfig
cache (or shells out to gcc/ld, if present) -- it does NOT search
LD_LIBRARY_PATH. So packaging/build_appimage.sh bundling libportaudio.so.2
next to libhamlib.so isn't enough by itself, unlike Hamlib (which cwrobot
loads via a direct ctypes.CDLL() call -- a real dlopen() that *does* honor
LD_LIBRARY_PATH). packaging/AppRun instead exports CWROBOT_BUNDLED_PORTAUDIO
pointing at the bundled copy, and this monkeypatches find_library() to fall
back to that path -- but only when the normal lookup has already failed, and
only when that env var is actually set (never true outside the AppImage) --
so this is a no-op for every other way of running cwrobot (pip install,
`python -m cwrobot.app`, the Windows/macOS PyInstaller builds, which don't
hit this code path at all since sounddevice's wheel already bundles
PortAudio for those two platforms).
"""

from __future__ import annotations

import ctypes.util
import os


def _patch_find_library_for_bundled_portaudio() -> None:
    bundled_path = os.environ.get("CWROBOT_BUNDLED_PORTAUDIO")
    if not bundled_path or not os.path.isfile(bundled_path):
        return

    original_find_library = ctypes.util.find_library

    def find_library_with_bundled_fallback(name: str) -> str | None:
        found = original_find_library(name)
        if found is not None:
            return found
        return bundled_path if name == "portaudio" else None

    ctypes.util.find_library = find_library_with_bundled_fallback


_patch_find_library_for_bundled_portaudio()
