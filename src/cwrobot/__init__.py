"""CW Robot -- CW (Morse) transceiver desktop application."""

# Single source of truth for the app's release version -- read from here by
# pyproject.toml (see [tool.hatch.version]), the About dialog
# (ui.main_window._show_about), and the macOS bundle metadata
# (packaging/cwrobot.spec's CFBundleShortVersionString). Bump only this line
# on a release; everything else derives from it.
__version__ = "1.0.13"
