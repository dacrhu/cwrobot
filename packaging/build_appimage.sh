#!/bin/bash
# Builds a self-contained Linux AppImage for CW Robot: bundles a portable
# CPython, the project's own pip dependencies (PySide6, numpy, scipy,
# sounddevice, pyserial), and the system's libhamlib.so (+ its transitive
# shared-library dependencies) -- so the resulting .AppImage runs without
# any of this needing to be installed on the target machine.
#
# Prerequisites on the BUILD machine (see README.md's "Installation
# (Fedora)" section for the dnf install line):
#   - hamlib installed (libhamlib.so is copied out of the system for
#     bundling; CAT keying is skipped with a warning, not a hard failure,
#     if it's missing -- see cwrobot.hamlib.ctypes_bindings' own graceful
#     HamlibUnavailableError fallback, which the AppImage build mirrors).
#   - curl, python3 (stdlib json -- used to query the GitHub API instead
#     of requiring jq), ldconfig, ldd, appimagetool
#     (https://github.com/AppImageKit/AppImageKit/releases -- must be on
#     PATH as `appimagetool`).
#
# Portability note: the bundled libhamlib.so is copied straight from this
# build machine, so it's linked against *this machine's* glibc -- the
# AppImage will run on similarly-recent-or-newer distros, but not
# necessarily on very old ones. See README.md and the plan this was built
# from for the (not yet implemented) option of cross-building Hamlib
# inside an old manylinux-style container for maximum portability.

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
    BASELINE_LIB_PATTERN='^(linux-vdso|ld-linux|libc\.so|libm\.so|libpthread\.so|libdl\.so|librt\.so|libresolv\.so)'
    ldd "${REAL_HAMLIB_SO}" | awk '{print $(NF-1), $3}' | while read -r libname libpath; do
        [ -n "${libpath}" ] && [ -f "${libpath}" ] || continue
        echo "${libname}" | grep -Eq "${BASELINE_LIB_PATTERN}" && continue
        cp -Ln "${libpath}" "${APPDIR}/usr/lib/$(basename "${libpath}")" 2>/dev/null || true
    done
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
log "Running appimagetool..."
ARCH=x86_64 appimagetool "${APPDIR}" "${OUTPUT_FILE}"

log "Done: ${OUTPUT_FILE}"
