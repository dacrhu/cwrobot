"""cwrobot application entry point."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from cwrobot.config import AppConfig
from cwrobot.hamlib.ctypes_bindings import HamlibUnavailableError, get_library
from cwrobot.ui.main_window import MainWindow
from cwrobot.ui.style import STYLESHEET


def _report_hamlib_status() -> None:
    """Print whether the Hamlib shared library loaded.

    Used by --smoke-test (see the CI workflows under .github/workflows/):
    a packaged build's smoke-test step greps this line to confirm a
    bundled Hamlib actually links on the target platform, instead of
    silently shipping a build where it's broken.
    """
    try:
        get_library()
    except HamlibUnavailableError as exc:
        print(f"Hamlib: unavailable ({exc})")
    else:
        print("Hamlib: loaded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cwrobot")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help=(
            "Build the main window, report whether Hamlib loaded, then exit "
            "immediately instead of showing the window or entering the "
            "normal event loop. Used by CI to verify a packaged build "
            "actually starts up on the target platform; not intended for "
            "interactive use."
        ),
    )
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("CW Robot")
    app.setOrganizationName("cwrobot")
    app.setStyleSheet(STYLESHEET)

    config = AppConfig.load()
    window = MainWindow(config)

    if args.smoke_test:
        _report_hamlib_status()

        def _shut_down() -> None:
            # MainWindow.__init__ already starts real background work
            # (audio capture + its decoder QThread if a device is
            # available, the Hamlib frequency-monitor QThread always) --
            # window.close() runs the same closeEvent that a normal quit
            # goes through, stopping all of that cleanly. Calling
            # app.quit() directly instead (skipping closeEvent) leaves
            # those threads running while the interpreter starts tearing
            # down, which reliably crashes on exit.
            window.close()
            app.quit()

        QTimer.singleShot(0, _shut_down)
        return app.exec()

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
