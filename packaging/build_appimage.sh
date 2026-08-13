#!/bin/bash
# Builds a self-contained Linux AppImage for CW Robot: bundles a portable
# CPython, the project's own pip dependencies (PySide6, numpy, scipy,
# sounddevice, pyserial), and the system's libhamlib.so and
# libportaudio.so.2 (+ libhamlib's own transitive shared-library
# dependencies) -- so the resulting .AppImage runs without any of this
# needing to be installed on the target machine.
#
# Prerequisites on the BUILD machine (see README.md's "Installation
# (Fedora)" section for the dnf install line):
#   - hamlib installed (libhamlib.so is copied out of the system for
#     bundling; CAT keying is skipped with a warning, not a hard failure,
#     if it's missing -- see cwrobot.hamlib.ctypes_bindings' own graceful
#     HamlibUnavailableError fallback, which the AppImage build mirrors).
#   - PortAudio installed (libportaudio.so.2 -- Debian/Ubuntu's
#     `libportaudio2` package, Fedora's `portaudio`). Unlike Hamlib this
#     one's NOT optional: sounddevice imports unconditionally at Python
#     import time and raises on the spot if it can't find it, so a missing
#     PortAudio is a hard startup crash, not a gracefully-disabled feature.
#   - curl, python3 (stdlib json -- used to query the GitHub API instead
#     of requiring jq), ldconfig, ldd, appimagetool
#     (https://github.com/AppImageKit/AppImageKit/releases -- must be on
#     PATH as `appimagetool`).
#
# Portability note: the bundled libhamlib.so/libportaudio.so.2 are copied
# straight from this build machine, so they're linked against *this
# machine's* glibc -- the AppImage will run on similarly-recent-or-newer
# distros, but not necessarily on very old ones. See README.md and the
# plan this was built from for the (not yet implemented) option of
# cross-building both inside an old manylinux-style container for maximum
# portability.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
CACHE_DIR="${SCRIPT_DIR}/.cache"
APPDIR="${BUILD_DIR}/AppDir"
DIST_DIR="${REPO_ROOT}/dist"
PYTHON_SERIES="3.11"
APP_NAME="cwrobot"

log() { echo "[build_appimage] $*"; }
die() { echo "[build_appimage] ERROR: $*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not found on PATH. $2"
}

require_cmd curl "Install curl."
require_cmd python3 "Install python3."
require_cmd ldconfig "Should ship with glibc; check your distro's PATH setup."
require_cmd ldd "Should ship with glibc."
require_cmd appimagetool "Download it from https://github.com/AppImageKit/AppImageKit/releases (or your distro's package), and make sure it's on PATH as 'appimagetool'."

mkdir -p "${BUILD_DIR}" "${CACHE_DIR}" "${DIST_DIR}"

# ---------------------------------------------------------------------------
# 1. Fetch a portable, prebuilt CPython AppImage (from niess/python-appimage,
#    manylinux-based for broad glibc compatibility) and extract it as our
#    starting AppDir.
# ---------------------------------------------------------------------------

log "Looking up the latest python${PYTHON_SERIES} AppImage release..."
PYTHON_APPIMAGE_URL="$(python3 - "$PYTHON_SERIES" <<'PYEOF'
import json
import sys
import urllib.request

series = sys.argv[1]
tag = f"python{series}"
url = f"https://api.github.com/repos/niess/python-appimage/releases/tags/{tag}"
with urllib.request.urlopen(url) as response:
    data = json.load(response)

for asset in data.get("assets", []):
    name = asset["name"]
    if name.endswith("x86_64.AppImage"):
        print(asset["browser_download_url"])
        break
else:
    sys.exit(f"no x86_64 AppImage asset found in release '{tag}'")
PYEOF
)"
[ -n "${PYTHON_APPIMAGE_URL}" ] || die "could not resolve a python${PYTHON_SERIES} AppImage download URL"

PYTHON_APPIMAGE_FILE="${CACHE_DIR}/$(basename "${PYTHON_APPIMAGE_URL}")"
if [ ! -f "${PYTHON_APPIMAGE_FILE}" ]; then
    log "Downloading ${PYTHON_APPIMAGE_URL}"
    curl -fL --progress-bar -o "${PYTHON_APPIMAGE_FILE}" "${PYTHON_APPIMAGE_URL}"
else
    log "Using cached $(basename "${PYTHON_APPIMAGE_FILE}")"
fi
chmod +x "${PYTHON_APPIMAGE_FILE}"

log "Extracting the base Python AppDir..."
rm -rf "${BUILD_DIR}/squashfs-root" "${APPDIR}"
( cd "${BUILD_DIR}" && "${PYTHON_APPIMAGE_FILE}" --appimage-extract >/dev/null )
mv "${BUILD_DIR}/squashfs-root" "${APPDIR}"

PYTHON_BIN="$(find "${APPDIR}/usr/bin" -maxdepth 1 -type f -name 'python3*' -print -quit)"
[ -n "${PYTHON_BIN}" ] || die "couldn't find a bundled python3* interpreter under ${APPDIR}/usr/bin"
log "Bundled interpreter: ${PYTHON_BIN} ($("${PYTHON_BIN}" --version))"

# ---------------------------------------------------------------------------
# 2. Install CW Robot + its pip dependencies into the bundled interpreter.
# ---------------------------------------------------------------------------

log "Installing CW Robot and its dependencies into the AppDir..."
"${PYTHON_BIN}" -m pip install --no-input --upgrade "${REPO_ROOT}"

# ---------------------------------------------------------------------------
# 3. Bundle libhamlib.so + its non-baseline transitive shared-lib deps.
#    ctypes_bindings.py dlopen()s a bare soname, which respects
#    LD_LIBRARY_PATH -- AppRun sets that -- so no source changes are needed.
# ---------------------------------------------------------------------------

mkdir -p "${APPDIR}/usr/lib"

# The `|| true` matters here: awk's early `exit` after the first match
# closes its end of the pipe while ldconfig may still be writing, which
# makes ldconfig die of SIGPIPE (exit 141) -- under `set -o pipefail` that
# fails the whole pipeline even though awk already captured the value we
# wanted just fine.
HAMLIB_SO="$(ldconfig -p | awk '/libhamlib\.so/ {print $NF; exit}')" || true
if [ -z "${HAMLIB_SO}" ]; then
    log "WARNING: libhamlib.so not found via ldconfig -- building WITHOUT bundled Hamlib CAT support."
    log "         (install the 'hamlib' package and re-run this script to include it.)"
else
    log "Bundling Hamlib from ${HAMLIB_SO}"
    REAL_HAMLIB_SO="$(readlink -f "${HAMLIB_SO}")"
    cp -L "${REAL_HAMLIB_SO}" "${APPDIR}/usr/lib/"
    # ctypes tries libhamlib.so.4, libhamlib.so.5, libhamlib.so.3, and bare
    # libhamlib.so, in that order (see _CANDIDATE_SONAMES in
    # src/cwrobot/hamlib/ctypes_bindings.py) -- recreate whichever soname
    # symlinks the system had, so the same lookup order resolves to our
    # bundled copy regardless of which exact version this build machine has.
    for soname in libhamlib.so.4 libhamlib.so.5 libhamlib.so.3 libhamlib.so; do
        # Same SIGPIPE/pipefail caveat as HAMLIB_SO above.
        system_link="$(ldconfig -p | awk -v n="${soname}" '$1 == n {print $NF; exit}')" || true
        if [ -n "${system_link}" ]; then
            ln -sf "$(basename "${REAL_HAMLIB_SO}")" "${APPDIR}/usr/lib/${soname}"
        fi
    done

    # Bundle libhamlib's own transitive dependencies too, except the
    # baseline libs every AppImage is expected to get from the host glibc
    # (see the AppImage packaging guidelines) -- bundling those causes more
    # compatibility problems than it solves.
    # NOTE on a bug that shipped here previously: `ldd` lines come in two
    # shapes -- "name => /resolved/path (0xaddr)" (4 fields) and, for the
    # dynamic linker/vDSO itself, "/resolved/path (0xaddr)" (2 fields, no
    # "=>"). `awk '{print $(NF-1), $3}'` collapsed both into the *path*
    # twice for the common 4-field case (NF-1 == 3), so `libname` held a
    # full path like "/lib64/libc.so.6" instead of the bare soname -- which
    # then never matched BASELINE_LIB_PATTERN's `^libc\.so` anchor, so
    # libc/libpthread/ld-linux/etc. all slipped through and got bundled.
    # Bundling glibc's own libc.so.6 is actively harmful, not just
    # redundant: AppRun's LD_LIBRARY_PATH makes every subsequently-run
    # program in the AppImage (including `find`, which AppRun itself uses
    # to locate the interpreter) load *that* copy instead of the host's --
    # and a libc.so.6 built on a different distro/version than the host's
    # own dynamic linker fails with exactly the "undefined symbol ...
    # GLIBC_PRIVATE" crash this comment is here to prevent a repeat of.
    BASELINE_LIB_PATTERN='^(linux-vdso|ld-linux|libc\.so|libm\.so|libpthread\.so|libdl\.so|librt\.so|libresolv\.so|libnsl\.so|libutil\.so|libcrypt\.so)'
    # Resolve to a path either way (the "=> target" form for ordinary
    # dependencies, or the bare "path (addr)" form ld-linux/vdso's own line
    # uses), then classify by *basename* of that path -- matching
    # BASELINE_LIB_PATTERN against anything other than the bare filename
    # (a full "/lib64/..." path, in particular) silently never matches.
    ldd "${REAL_HAMLIB_SO}" \
        | awk '{ path = ($2 == "=>") ? $3 : $1; print path }' \
        | while read -r libpath; do
            [ -n "${libpath}" ] && [ -f "${libpath}" ] || continue
            libname="$(basename "${libpath}")"
            echo "${libname}" | grep -Eq "${BASELINE_LIB_PATTERN}" && continue
            cp -Ln "${libpath}" "${APPDIR}/usr/lib/${libname}" 2>/dev/null || true
        done
fi

# ---------------------------------------------------------------------------
# 3b. Bundle libportaudio.so.2. NOT a graceful-degradation case like
#     Hamlib above: sounddevice's Linux import path calls
#     ctypes.util.find_library("portaudio") at Python import time and
#     raises immediately with no fallback if that comes up empty -- a
#     missing PortAudio is a hard startup crash, not a disabled feature.
#
#     It's also not as simple as copying the .so next to libhamlib.so and
#     letting AppRun's LD_LIBRARY_PATH handle it: find_library() on Linux
#     only consults the *system's* ldconfig cache (or gcc/ld, if present)
#     -- unlike Hamlib's bare-soname ctypes.CDLL() call, it does NOT
#     search LD_LIBRARY_PATH, so a bundled copy sitting in usr/lib is
#     invisible to it no matter what AppRun sets. Instead, AppRun exports
#     a CWROBOT_BUNDLED_PORTAUDIO env var pointing at this exact bundled
#     file, and cwrobot.audio's __init__.py monkeypatches
#     ctypes.util.find_library to fall back to that path when the normal
#     lookup fails -- so the file just needs to exist at a fixed, known
#     name here; unlike libhamlib.so's several candidate sonames, nothing
#     needs to match what this build machine happens to call it.
# ---------------------------------------------------------------------------

# Same SIGPIPE/pipefail caveat as the ldconfig calls above.
PORTAUDIO_SO="$(ldconfig -p | awk '/libportaudio\.so\.2/ {print $NF; exit}')" || true
if [ -z "${PORTAUDIO_SO}" ]; then
    log "WARNING: libportaudio.so.2 not found via ldconfig -- building WITHOUT"
    log "         bundled PortAudio. Unlike Hamlib above, this is NOT a"
    log "         graceful degradation: the AppImage will hard-crash at"
    log "         startup on any machine that doesn't already have"
    log "         libportaudio2 (or equivalent) installed system-wide."
    log "         (install it -- e.g. 'apt install libportaudio2' or"
    log "         'dnf install portaudio' -- and re-run this script.)"
else
    log "Bundling PortAudio from ${PORTAUDIO_SO}"
    REAL_PORTAUDIO_SO="$(readlink -f "${PORTAUDIO_SO}")"
    cp -L "${REAL_PORTAUDIO_SO}" "${APPDIR}/usr/lib/libportaudio.so.2"

    # Deliberately NOT walking this one's own transitive deps the way the
    # Hamlib block above does: its one real non-baseline dependency is
    # libasound.so.2 (ALSA), and ALSA's own runtime plugin resolution
    # (/usr/lib/*/alsa-lib/, /etc/alsa/, etc.) is extremely host-specific
    # -- bundling a foreign libasound.so.2 risks breaking the *host's* own
    # ALSA setup (including the PipeWire routing this app depends on via
    # pactl, see README.md's "Audio handling" section) rather than fixing
    # anything, and ALSA itself is about as universal on any Linux system
    # that actually has audio hardware as glibc is.
fi

# ---------------------------------------------------------------------------
# 4. Install our own AppRun / .desktop / icon, replacing the base Python
#    AppImage's own (which are for "Python", not our app).
# ---------------------------------------------------------------------------

log "Installing CW Robot's AppRun, .desktop, and icon..."
rm -f "${APPDIR}/AppRun" "${APPDIR}"/*.desktop "${APPDIR}"/*.png "${APPDIR}/.DirIcon"

install -m 0755 "${SCRIPT_DIR}/AppRun" "${APPDIR}/AppRun"

install -m 0644 "${SCRIPT_DIR}/cwrobot.desktop" "${APPDIR}/${APP_NAME}.desktop"
mkdir -p "${APPDIR}/usr/share/applications"
install -m 0644 "${SCRIPT_DIR}/cwrobot.desktop" "${APPDIR}/usr/share/applications/${APP_NAME}.desktop"

install -m 0644 "${REPO_ROOT}/assets/icon-256.png" "${APPDIR}/${APP_NAME}.png"
cp "${APPDIR}/${APP_NAME}.png" "${APPDIR}/.DirIcon"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"
install -m 0644 "${REPO_ROOT}/assets/icon-256.png" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png"

# ---------------------------------------------------------------------------
# 5. Pack it up.
# ---------------------------------------------------------------------------

OUTPUT_FILE="${DIST_DIR}/CW_Robot-x86_64.AppImage"

# appimagetool downloads this "runtime" blob (the small stub that turns the
# squashfs into a self-mounting executable) itself if it's not told
# otherwise -- with no retry, so a single transient GitHub hiccup (seen in
# CI: a bare 503) fails the whole build. Fetching it ourselves with retries
# and handing it over via --runtime-file is more reliable, and doubles as a
# cache across local runs same as the python-appimage download above.
RUNTIME_FILE="${CACHE_DIR}/runtime-x86_64"
if [ ! -f "${RUNTIME_FILE}" ]; then
    log "Downloading the AppImage runtime stub..."
    curl -fL --retry 5 --retry-delay 5 --retry-all-errors --progress-bar \
        -o "${RUNTIME_FILE}" \
        https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64
else
    log "Using cached AppImage runtime stub"
fi

log "Running appimagetool..."
ARCH=x86_64 appimagetool --runtime-file "${RUNTIME_FILE}" "${APPDIR}" "${OUTPUT_FILE}"

log "Done: ${OUTPUT_FILE}"
