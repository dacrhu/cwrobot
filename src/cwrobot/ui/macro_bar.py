"""TX quick-button macro bar: one button per cwrobot.macros.DEFAULT_MACROS
entry, each with independently-editable canned text.

Left-click activates a button: emits macro_activated with that button's
*raw* (unresolved) template text. Right-click opens the per-button editor
(ui.macro_editor_dialog).

MacroBar owns each macro's template text (in-memory + persisted via
AppConfig.macro_texts) and the editing UI, but deliberately knows nothing
about {VAR} *resolution* -- it has no access to the QSO panel's live field
values, only to the operator's own AppConfig -- so MainWindow is the one
that resolves macro_activated's template against the current variables and
inserts the result into TxPanel's TX text field. This mirrors RxPanel's
selection_routed_to_field: the panel that owns the UI gesture emits raw
intent, MainWindow wires it to whatever else needs to react.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from cwrobot.config import AppConfig
from cwrobot.macros import (
    DEFAULT_MACROS,
    MacroDefinition,
    get_macro_label,
    get_macro_text,
    set_macro_label,
    set_macro_text,
)
from cwrobot.ui.macro_editor_dialog import MacroEditorDialog

# A touch looser than the 4px within-group button gap, tighter than
# form_helpers.FIELD_BLOCK_SPACING (10) -- this caption sits above a wide
# centered cluster, not a single control.
MACRO_CAPTION_SPACING = 6

# Gap on each side of the divider between the two sets.
SET_GAP = 22

# Number of buttons per row-set: DEFAULT_MACROS is ordered set 1 (F1-F4)
# then set 2 (F5-F8), each CQ -> Reply -> Report -> Bye.
_SET_SIZE = 4

# One glyph per macro group (see MacroDefinition.group), prefixed onto the
# button in both sets -- purely a display concern, so it
# lives here rather than on macros.MacroDefinition itself (kept Qt-free/
# widget-agnostic, see macros.py's own docstring). Mirrors send_button's
# "▶"/log_button's "✓" elsewhere in the app: plain, monochrome Unicode
# symbols, not image icons.
_GROUP_ICONS = {
    "cq": "→",  # calling out, unaddressed
    "reply_cq": "↩",  # replying to someone else's call
    "report": "#",  # exchanging the RST (a number code)
    "bye": "✕",  # signing off, ending the QSO
}


class MacroBar(QWidget):
    macro_activated = Signal(str)  # the macro's raw, unresolved template text

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config

        # A small caption above the (still centered) button row -- without
        # it, this was the only centered, borderless, uncaptioned element in
        # either panel, and read as visually disconnected/floating rather
        # than as a named part of the TX panel.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(MACRO_CAPTION_SPACING)

        caption = QLabel("Quick Buttons", self)
        caption.setProperty("role", "fieldLabel")
        caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(caption)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(4)  # within a group (short/long pair); groups get more, below
        buttons_row.addStretch(1)  # leading stretch too (see the trailing one below): centers the row

        self._buttons: dict[str, QPushButton] = {}
        self._shortcuts: list[QShortcut] = []
        for index, macro in enumerate(DEFAULT_MACROS):
            if index == _SET_SIZE:
                # A clear divider between set 1 (F1-F4) and set 2 (F5-F8)
                # so the 8 buttons read as two separate CQ->Bye sequences.
                buttons_row.addSpacing(SET_GAP)
                buttons_row.addWidget(self._make_set_divider())
                buttons_row.addSpacing(SET_GAP)

            button = QPushButton(self)
            button.setProperty("role", "macro")
            button.setProperty("macroGroup", macro.group)
            button.setProperty("macroShade", "light" if macro.key.endswith("_short") else "dark")
            button.clicked.connect(lambda checked=False, key=macro.key: self._on_clicked(key))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda _pos, key=macro.key: self._open_editor(key))
            buttons_row.addWidget(button)
            self._buttons[macro.key] = button

            # F1..F8 in DEFAULT_MACROS order. Parented to the bar, so the
            # shortcut is disabled together with it while a send runs.
            shortcut = QShortcut(QKeySequence(f"F{index + 1}"), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda key=macro.key: self._on_clicked(key))
            self._shortcuts.append(shortcut)

            self._refresh_button(macro)
        buttons_row.addStretch(1)
        outer.addLayout(buttons_row)

    def _make_set_divider(self) -> QFrame:
        divider = QFrame(self)
        divider.setFrameShape(QFrame.Shape.NoFrame)
        divider.setFixedWidth(3)
        divider.setMinimumHeight(36)
        divider.setStyleSheet("background-color: rgba(255, 255, 255, 140); border-radius: 1px;")
        return divider

    def _refresh_button(self, macro: MacroDefinition) -> None:
        index = DEFAULT_MACROS.index(macro)
        icon = _GROUP_ICONS.get(macro.group, "")
        label = get_macro_label(self.config, macro)
        button = self._buttons[macro.key]
        text = f"{icon} {label}" if icon else label
        button.setText(f"{text}  [F{index + 1}]")
        button.setToolTip(f"[F{index + 1}] {get_macro_text(self.config, macro)}")

    def _macro_by_key(self, key: str) -> MacroDefinition:
        return next(macro for macro in DEFAULT_MACROS if macro.key == key)

    def _on_clicked(self, key: str) -> None:
        macro = self._macro_by_key(key)
        self.macro_activated.emit(get_macro_text(self.config, macro))

    def _open_editor(self, key: str) -> None:
        macro = self._macro_by_key(key)
        current_text = get_macro_text(self.config, macro)
        dialog = MacroEditorDialog(
            get_macro_label(self.config, macro), macro.label, current_text, self
        )
        if dialog.exec():
            set_macro_text(self.config, key, dialog.text())
            set_macro_label(self.config, key, dialog.label())
            self.config.save()
            self._refresh_button(macro)
