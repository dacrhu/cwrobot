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
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from cwrobot.config import AppConfig
from cwrobot.macros import DEFAULT_MACROS, MacroDefinition, get_macro_text, set_macro_text
from cwrobot.ui.macro_editor_dialog import MacroEditorDialog

# A touch looser than the 4px within-group button gap, tighter than
# form_helpers.FIELD_BLOCK_SPACING (10) -- this caption sits above a wide
# centered cluster, not a single control.
MACRO_CAPTION_SPACING = 6


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
        previous_group: str | None = None
        for macro in DEFAULT_MACROS:
            if previous_group is not None and macro.group != previous_group:
                # Extra breathing room *between* groups (vs. within one) so
                # the 8 buttons visually read as 4 short/long pairs, not one
                # undifferentiated row.
                buttons_row.addSpacing(20)
            previous_group = macro.group

            button = QPushButton(macro.label, self)
            button.setProperty("role", "macro")
            button.setProperty("macroGroup", macro.group)
            button.setProperty("macroShade", "light" if macro.key.endswith("_short") else "dark")
            button.setToolTip(get_macro_text(self.config, macro))
            button.clicked.connect(lambda checked=False, key=macro.key: self._on_clicked(key))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda _pos, key=macro.key: self._open_editor(key))
            buttons_row.addWidget(button)
            self._buttons[macro.key] = button
        buttons_row.addStretch(1)
        outer.addLayout(buttons_row)

    def _macro_by_key(self, key: str) -> MacroDefinition:
        return next(macro for macro in DEFAULT_MACROS if macro.key == key)

    def _on_clicked(self, key: str) -> None:
        macro = self._macro_by_key(key)
        self.macro_activated.emit(get_macro_text(self.config, macro))

    def _open_editor(self, key: str) -> None:
        macro = self._macro_by_key(key)
        current_text = get_macro_text(self.config, macro)
        dialog = MacroEditorDialog(macro.label, current_text, self)
        if dialog.exec():
            new_text = dialog.text()
            set_macro_text(self.config, key, new_text)
            self.config.save()
            self._buttons[key].setToolTip(new_text)
