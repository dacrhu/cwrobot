"""Per-macro text editor: the template on the left, a clickable list of
available {VAR} variables (see cwrobot.macros) on the right -- double-click
a variable to insert it at the cursor position in the template.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from cwrobot.macros import OWN_VARIABLES, QSO_VARIABLES


class MacroEditorDialog(QDialog):
    def __init__(self, macro_label: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Quick Button — {macro_label}")
        self.resize(600, 340)

        outer = QVBoxLayout(self)
        content = QHBoxLayout()
        outer.addLayout(content, 1)

        left = QVBoxLayout()
        left.addWidget(QLabel("Text:", self))
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setPlainText(text)
        left.addWidget(self.text_edit)
        content.addLayout(left, 3)

        right = QVBoxLayout()
        right.addWidget(QLabel("Variables (double-click to insert):", self))
        self.variable_list = QListWidget(self)
        self._add_variable_section("My Data", OWN_VARIABLES)
        self._add_variable_section("QSO Data", QSO_VARIABLES)
        self.variable_list.itemDoubleClicked.connect(self._insert_variable)
        right.addWidget(self.variable_list)
        content.addLayout(right, 2)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _add_variable_section(self, title: str, variables: list[tuple[str, str]]) -> None:
        header = QListWidgetItem(title)
        header.setFlags(Qt.ItemFlag.NoItemFlags)  # not selectable/clickable, just a label row
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        self.variable_list.addItem(header)

        for token, description in variables:
            item = QListWidgetItem(f"{{{token}}}  —  {description}")
            item.setData(Qt.ItemDataRole.UserRole, token)
            self.variable_list.addItem(item)

    def _insert_variable(self, item: QListWidgetItem) -> None:
        token = item.data(Qt.ItemDataRole.UserRole)
        if not token:  # a header row
            return
        self.text_edit.insertPlainText(f"{{{token}}}")
        self.text_edit.setFocus()

    def text(self) -> str:
        return self.text_edit.toPlainText()
