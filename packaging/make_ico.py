#!/usr/bin/env python3
"""Generate packaging/build/cwrobot.ico from assets/icon-256.png.

Used by the Windows job in .github/workflows/windows-macos-build.yml,
before the PyInstaller build (cwrobot.spec looks for the .ico at that
path). Needs Pillow, which the workflow installs alongside PyInstaller --
it's not a runtime dependency of the app itself, so it's not in
pyproject.toml's own dependency list.

See make_icns.sh for the macOS equivalent (.icns needs macOS's own
iconutil/sips, so that one can't be a portable Python script).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PNG = REPO_ROOT / "assets" / "icon-256.png"
OUTPUT_DIR = REPO_ROOT / "packaging" / "build"
OUTPUT_ICO = OUTPUT_DIR / "cwrobot.ico"

# Standard Windows icon sizes; Pillow downsamples the 256x256 source for
# each smaller size and packs them all into one multi-resolution .ico.
_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    if not SOURCE_PNG.is_file():
        print(f"ERROR: source icon not found: {SOURCE_PNG}", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.open(SOURCE_PNG).convert("RGBA")
    image.save(OUTPUT_ICO, sizes=_SIZES)
    print(f"Wrote {OUTPUT_ICO} ({OUTPUT_ICO.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
