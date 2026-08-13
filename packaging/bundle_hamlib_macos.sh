#!/bin/bash
# Copies the Homebrew-installed Hamlib dylib (+ its non-system dependencies,
# one level deep -- Hamlib itself only pulls in libusb, which in turn only
# depends on system libs, so this doesn't need to be fully recursive) into
# a freshly-built CW Robot.app bundle, and rewrites load-time paths so the
# bundle finds them relative to itself instead of Homebrew's own prefix --
# so the .app still works on a Mac that doesn't have Homebrew installed.
#
# Run by the macOS job in .github/workflows/windows-macos-build.yml, right
# after `brew install hamlib` and the PyInstaller build, and BEFORE the
# ad-hoc re-codesign step (rewriting a Mach-O's load commands invalidates
# its existing signature, so re-signing has to happen after this, not
# before -- see that workflow's own comments).
#
# NOTE: written without a Mac available to test it locally. The
# --smoke-test step right after this in the workflow is the safety net --
# it actually dlopen()s the copied dylib on a real macOS runner and fails
# the build loudly if any of this didn't work, rather than silently
# shipping a broken bundle.

set -euo pipefail

APP_BUNDLE="${1:?usage: $0 <path-to-.app> <launcher-name>}"
LAUNCHER_NAME="${2:?usage: $0 <path-to-.app> <launcher-name>}"
MACOS_DIR="${APP_BUNDLE}/Contents/MacOS"

log() { echo "[bundle_hamlib_macos] $*"; }
die() { echo "[bundle_hamlib_macos] ERROR: $*" >&2; exit 1; }

[ -d "${MACOS_DIR}" ] || die "${MACOS_DIR} not found"
[ -f "${MACOS_DIR}/${LAUNCHER_NAME}" ] || die "launcher ${MACOS_DIR}/${LAUNCHER_NAME} not found"

HAMLIB_PREFIX="$(brew --prefix hamlib)"
[ -n "${HAMLIB_PREFIX}" ] || die "'brew --prefix hamlib' returned nothing -- is hamlib installed?"

# Homebrew's autotools build installs the real file as
# libhamlib.<major>.dylib (libtool's versioned-dylib convention) with a
# plain libhamlib.dylib symlink pointing at it -- resolve to the real file,
# not the symlink, so `cp` doesn't just copy a dangling link.
MAIN_DYLIB="$(find "${HAMLIB_PREFIX}/lib" -maxdepth 1 -name 'libhamlib.*.dylib' ! -type l -print -quit)"
[ -n "${MAIN_DYLIB}" ] || die "no libhamlib.*.dylib found under ${HAMLIB_PREFIX}/lib"
MAIN_DYLIB_NAME="$(basename "${MAIN_DYLIB}")"

log "Bundling ${MAIN_DYLIB}"
cp -L "${MAIN_DYLIB}" "${MACOS_DIR}/${MAIN_DYLIB_NAME}"
# Also drop a plain "libhamlib.dylib" copy alongside the versioned one --
# cwrobot.hamlib.ctypes_bindings tries both names next to the executable
# when frozen, so this doesn't rely on guessing exactly which of the two
# names Homebrew's build used for the real (non-symlink) file.
if [ "${MAIN_DYLIB_NAME}" != "libhamlib.dylib" ]; then
    cp -L "${MAIN_DYLIB}" "${MACOS_DIR}/libhamlib.dylib"
fi

# Non-system dependencies of the main dylib (Homebrew-provided libs like
# libusb) -- /usr/lib and /System paths are on every Mac already and
# shouldn't be bundled.
mapfile -t DEPS < <(
    otool -L "${MAIN_DYLIB}" | tail -n +2 | awk '{print $1}' \
        | grep -v '^/usr/lib/' | grep -v '^/System/' || true
)

for dep in "${DEPS[@]}"; do
    [ -f "${dep}" ] || continue
    dep_name="$(basename "${dep}")"
    [ "${dep_name}" = "${MAIN_DYLIB_NAME}" ] && continue
    log "Bundling dependency ${dep}"
    cp -Ln "${dep}" "${MACOS_DIR}/${dep_name}"

    # Rewrite the *dependency's* own id and its references to any of its
    # own dependencies that are also system-baseline (rare for libusb, but
    # keeps this correct if a future Hamlib dependency needs it).
    install_name_tool -id "@executable_path/${dep_name}" "${MACOS_DIR}/${dep_name}"
done

# Point the main dylib's own id, and its references to each bundled
# dependency, at the copies sitting next to it.
install_name_tool -id "@executable_path/${MAIN_DYLIB_NAME}" "${MACOS_DIR}/${MAIN_DYLIB_NAME}"
for dep in "${DEPS[@]}"; do
    dep_name="$(basename "${dep}")"
    [ -f "${MACOS_DIR}/${dep_name}" ] || continue
    install_name_tool -change "${dep}" "@executable_path/${dep_name}" "${MACOS_DIR}/${MAIN_DYLIB_NAME}"
done

log "Bundled Hamlib + $(( ${#DEPS[@]} )) dependency path(s) into ${MACOS_DIR}"
