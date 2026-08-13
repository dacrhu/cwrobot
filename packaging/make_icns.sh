#!/bin/bash
# Generates packaging/build/cwrobot.icns from assets/icon-256.png, using
# macOS's own iconutil/sips (there's no portable way to write .icns, unlike
# make_ico.py's Pillow-based Windows equivalent). Only runs as a step in the
# macOS job of .github/workflows/windows-macos-build.yml.
#
# Caveat: the source PNG is 256x256, so the two largest .icns slots
# (512x512 and 1024x1024, used for Finder icon previews and Launchpad) are
# upscaled from it by sips rather than a real high-res source -- looks
# slightly soft at those sizes. Fine for now; replace assets/icon-256.png
# with a higher-resolution source (ideally >=1024x1024) to fix.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_PNG="${REPO_ROOT}/assets/icon-256.png"
BUILD_DIR="${SCRIPT_DIR}/build"
ICONSET="${BUILD_DIR}/cwrobot.iconset"
OUTPUT_ICNS="${BUILD_DIR}/cwrobot.icns"

[ -f "${SOURCE_PNG}" ] || { echo "ERROR: source icon not found: ${SOURCE_PNG}" >&2; exit 1; }

mkdir -p "${BUILD_DIR}"
rm -rf "${ICONSET}"
mkdir -p "${ICONSET}"

# iconutil expects this exact naming convention inside a .iconset dir:
# icon_<size>x<size>[@2x].png for each required resolution.
for size in 16 32 128 256 512; do
    sips -z "${size}" "${size}" "${SOURCE_PNG}" --out "${ICONSET}/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "${double}" "${double}" "${SOURCE_PNG}" --out "${ICONSET}/icon_${size}x${size}@2x.png" >/dev/null
done

iconutil -c icns "${ICONSET}" -o "${OUTPUT_ICNS}"
echo "Wrote ${OUTPUT_ICNS} ($(du -sh "${OUTPUT_ICNS}" | cut -f1))"
