"""A small floating popup that appears near a text selection, letting the
operator route the selected text into one of the QSO fields (see
ui.qso_panel) with a single click -- e.g. select a callsign in the RX
decoded-text pane, click "Callsign" in the popup, done.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class SelectionMacroPopup(QWidget):
    field_chosen = Signal(str)  # QSO field key, e.g. "callsign" (see qso_fields.QSO_FIELDS)

    def __init__(self, fields: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        # Qt.Popup: auto-closes on an outside click or Escape, exactly the
        # transient-toolbar behavior we want here, and -- because the
        # caller only ever shows() this once a text-selection drag has
        # actually finished (see RxPanel's debounce/mouse-button check) --
        # its mouse grab never fights an in-progress selection gesture.
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        for key, label in fields:
            button = QPushButton(label, self)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, field_key=key: self._choose(field_key))
            layout.addWidget(button)
        self.adjustSize()

    def _choose(self, field_key: str) -> None:
        self.field_chosen.emit(field_key)
        self.hide()

    def show_near(self, global_pos: QPoint) -> None:
        self.move(global_pos)
        self.show()
        self.raise_()
