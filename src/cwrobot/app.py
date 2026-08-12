"""cwrobot application entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from cwrobot.config import AppConfig
from cwrobot.ui.main_window import MainWindow
from cwrobot.ui.style import STYLESHEET


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("CW Robot")
    app.setOrganizationName("cwrobot")
    app.setStyleSheet(STYLESHEET)

    config = AppConfig.load()

    window = MainWindow(config)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
