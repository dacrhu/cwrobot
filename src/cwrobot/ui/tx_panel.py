"""TX panel: speed control, TX text input.

Neither audio output device selection nor the audio-vs-Hamlib backend
choice have any UI here -- both live entirely on the Settings dialog's
"Audio Devices" and "TX" tabs (ui.settings_dialog). This panel only
reacts to the configured backend indirectly, via set_manual_keying_available
(main_window calls it at startup and again whenever the Settings dialog is
accepted, since manual-keying jitter has no effect over CAT -- see
tx.hamlib_backend's module docstring). Send/Stop and the "match RX speed"
button are wired up in main_window.py.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cwrobot.config import AppConfig
from cwrobot.tx.encoder import MANUAL_KEYING_LEVEL_MAX, MANUAL_KEYING_LEVEL_MIN
from cwrobot.ui.form_helpers import FIELD_BLOCK_SPACING, SETTINGS_COLUMN_WIDTH, labeled_field
from cwrobot.ui.macro_bar import MacroBar
from cwrobot.ui.style import make_separator

MANUAL_KEYING_SLIDER_DEFAULT = MANUAL_KEYING_LEVEL_MIN

# Background used to highlight the character currently being keyed in
# tx_text_edit -- same green as the RX panel's "squelch: open" indicator,
# for a consistent "this is live/active" visual language across the app.
_TX_PROGRESS_HIGHLIGHT = QColor(46, 204, 113)

_MANUAL_KEYING_TOOLTIP = (
    "When checked, the timing of individual elements/spaces gets some "
    "random jitter, to resemble manual keying instead of machine-perfect "
    "timing."
)
# Appended to the tooltip (not replacing it) whenever set_manual_keying_available(False)
# disables the checkbox -- otherwise a grayed-out, unexplained checkbox just
# reads as broken, since the *reason* (the configured TX backend, chosen on
# the Settings dialog's "TX" tab) has no other indicator left on this panel.
_MANUAL_KEYING_DISABLED_REASON = (
    "\n\nNot available in Hamlib CAT TX mode -- the rig's own internal "
    "keyer paces the timing, which the software can't influence. Switch to "
    '"Audio (sound card)" mode on the Settings → TX tab to enable it.'
)


class TxPanel(QGroupBox):
    """The 'TX' (transmission) group box."""

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__("TX", parent)
        self.setObjectName("txPanel")

        # Tracks the latest set_manual_keying_available() call so set_sending
        # can restore the *right* enabled state once a send finishes, rather
        # than unconditionally re-enabling controls that should stay off in
        # Hamlib CAT mode.
        self._manual_keying_available = True

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.macro_bar = MacroBar(config, self)
        layout.addWidget(self.macro_bar)
        layout.addWidget(make_separator(self))

        # Below the macro bar: TX text (+ Send/Stop, unmoved) on the left,
        # every setting/control stacked in a narrow column on the right --
        # mirrors RxPanel's decoded-text-left/controls-right split.
        content_row = QHBoxLayout()
        content_row.setSpacing(14)

        text_send_row = QHBoxLayout()
        self.tx_text_edit = QPlainTextEdit(self)
        # Capped -- TX messages are short (a macro or a line or two typed by
        # hand), so this box doesn't need to keep growing to fill whatever
        # window height happens to be available, unlike RX's decoded_text
        # (an ever-growing log, which does benefit from more room). Sized to
        # roughly match controls_column's own natural height (~100px, built
        # below) rather than the old 140px: since this row's height is
        # whichever side is taller, a bigger cap here just padded the panel
        # out with dead space under the (shorter) controls column -- and
        # because TxPanel has no stretch factor in MainWindow's layout while
        # RxPanel does, every pixel added here is a pixel RxPanel doesn't get
        # when the window is short, which was squeezing its own controls.
        self.tx_text_edit.setMaximumHeight(100)
        self.tx_text_edit.setPlaceholderText("Type the text to send here…")
        self.tx_text_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tx_text_edit.customContextMenuRequested.connect(self._show_tx_text_context_menu)
        # Conventional for CW logging/keying software: the TX text is
        # displayed in uppercase as it's typed/pasted, even though the
        # encoder itself is already case-insensitive (tx.encoder upper()s
        # each character regardless) -- this is purely so what's on screen
        # matches what an operator expects to see being keyed out.
        self.tx_text_edit.textChanged.connect(self._force_tx_text_uppercase)
        text_send_row.addWidget(self.tx_text_edit, stretch=1)

        buttons_col = QVBoxLayout()
        buttons_col.setSpacing(6)
        self.send_button = QPushButton("Send", self)
        self.send_button.setObjectName("sendButton")
        self.stop_button = QPushButton("■ Stop", self)
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setEnabled(False)  # only meaningful while a send is in progress
        # Equal stretch instead of a trailing addStretch(1) -- the old
        # top-anchored pair left a growing gap of dead space below them as
        # tx_text_edit got taller, which also read as the buttons sitting
        # noticeably higher than the text box beside them. Splitting the
        # available height 50/50 makes both buttons taller and keeps the
        # pair flush with the text box's own top *and* bottom edge.
        # QPushButton's own vertical size policy defaults to Fixed though,
        # which ignores a layout stretch factor entirely -- Expanding is
        # what actually lets it grow into the extra space.
        self.send_button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.stop_button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        buttons_col.addWidget(self.send_button, stretch=1)
        buttons_col.addWidget(self.stop_button, stretch=1)
        text_send_row.addLayout(buttons_col)
        content_row.addLayout(text_send_row, stretch=1)

        content_row.addWidget(make_separator(self, Qt.Orientation.Vertical))

        # A fixed-width, top-aligned QVBoxLayout of "caption label above its
        # control" blocks (see ui.form_helpers) -- not a QFormLayout: that
        # shares one label-column width across every row, which silently
        # clips long labels ("Speed (WPM):") the moment total content
        # needs more than the column's cap allows. Stacking sidesteps the
        # shared-column concept entirely.
        controls_column = QWidget(self)
        controls_column.setFixedWidth(SETTINGS_COLUMN_WIDTH)
        controls_layout = QVBoxLayout(controls_column)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(FIELD_BLOCK_SPACING)

        speed_row = QHBoxLayout()
        self.speed_spinbox = QSpinBox(self)
        self.speed_spinbox.setRange(5, 60)
        self.speed_spinbox.setValue(20)
        speed_row.addWidget(self.speed_spinbox)
        self.match_rx_speed_button = QPushButton("⇄ RX speed", self)
        self.match_rx_speed_button.setToolTip("Set TX speed to match the received speed")
        self.match_rx_speed_button.setEnabled(False)  # enabled once decoder feeds a live WPM (milestone 4/7)
        self.match_rx_speed_button.setProperty("role", "secondary")
        speed_row.addWidget(self.match_rx_speed_button)
        controls_layout.addLayout(labeled_field("Speed (WPM):", speed_row, self))

        controls_layout.addWidget(make_separator(self))

        self.manual_keying_checkbox = QCheckBox("Manual keying emulation", self)
        self.manual_keying_checkbox.setToolTip(_MANUAL_KEYING_TOOLTIP)
        controls_layout.addWidget(self.manual_keying_checkbox)

        manual_keying_row = QHBoxLayout()
        self.manual_keying_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.manual_keying_slider.setRange(MANUAL_KEYING_LEVEL_MIN, MANUAL_KEYING_LEVEL_MAX)
        self.manual_keying_slider.setValue(MANUAL_KEYING_SLIDER_DEFAULT)
        self.manual_keying_slider.setToolTip("Amount of jitter (relative scale, not an exact %)")
        self.manual_keying_slider.setEnabled(self.manual_keying_checkbox.isChecked())
        self.manual_keying_checkbox.toggled.connect(self.manual_keying_slider.setEnabled)
        manual_keying_row.addWidget(self.manual_keying_slider, stretch=1)

        # A bare 1-8 slider is easy to forget the meaning of between
        # sessions ("what did I have this set to last week?") -- a small
        # numeric readout next to it is a cheap reminder, purely a display
        # of the slider's own current value.
        self.manual_keying_level_label = QLabel(str(self.manual_keying_slider.value()), self)
        self.manual_keying_level_label.setMinimumWidth(14)
        self.manual_keying_slider.valueChanged.connect(
            lambda value: self.manual_keying_level_label.setText(str(value))
        )
        manual_keying_row.addWidget(self.manual_keying_level_label)
        controls_layout.addLayout(manual_keying_row)

        # Top-aligned: without this, Qt stretches controls_column to match
        # text_send_row's height, leaving the column's own rows oddly spread
        # out instead of sitting compactly at the top (mirrors RxPanel).
        content_row.addWidget(controls_column, alignment=Qt.AlignmentFlag.AlignTop)

        layout.addLayout(content_row)

        self.tx_progress_label = QLabel("")
        layout.addWidget(self.tx_progress_label)

    def set_manual_keying_available(self, available: bool) -> None:
        """Called by main_window at startup and again whenever the Settings
        dialog is accepted, since the configured TX backend (audio vs.
        Hamlib CAT, chosen on the Settings dialog's "TX" tab) is the only
        thing that changes this -- manual-keying jitter has no effect over
        CAT (the rig's own keyer paces the real timing regardless of what we
        pass, see tx.hamlib_backend's module docstring), so the controls are
        disabled entirely rather than left sitting there implying an effect
        they don't have -- but *why* it's grayed out has no other indicator
        left on this panel (the backend picker itself lives on the Settings
        dialog now), so the tooltip explains it rather than leaving what
        looks like a broken checkbox."""
        self._manual_keying_available = available
        self.manual_keying_checkbox.setEnabled(available)
        self.manual_keying_slider.setEnabled(available and self.manual_keying_checkbox.isChecked())
        tooltip = _MANUAL_KEYING_TOOLTIP if available else _MANUAL_KEYING_TOOLTIP + _MANUAL_KEYING_DISABLED_REASON
        self.manual_keying_checkbox.setToolTip(tooltip)

    def set_sending(self, is_sending: bool) -> None:
        self.send_button.setEnabled(not is_sending)
        self.stop_button.setEnabled(is_sending)
        # Locked while sending: the worker thread already captured its own
        # copy of the text at send time, but letting the user edit it live
        # would desync the progress highlight (and be confusing regardless).
        self.tx_text_edit.setReadOnly(is_sending)
        # QPlainTextEdit.setReadOnly only blocks *keyboard* input -- a
        # macro click still inserts via QTextCursor.insertText() regardless,
        # so the bar itself needs disabling too.
        self.macro_bar.setEnabled(not is_sending)
        # The jitter for the whole message is computed once, upfront, when
        # Send is clicked (see tx.backend.TxBackend.send_text) -- dragging
        # the slider or toggling the checkbox mid-send has no effect on the
        # transmission already in flight, so disable both rather than let
        # the operator change a setting that silently does nothing until
        # the *next* send. Gated on _manual_keying_available too, so a send
        # finishing in Hamlib CAT mode doesn't incorrectly re-enable controls
        # that mode has no effect on (see set_manual_keying_available).
        manual_keying_enabled = not is_sending and self._manual_keying_available
        self.manual_keying_checkbox.setEnabled(manual_keying_enabled)
        self.manual_keying_slider.setEnabled(manual_keying_enabled and self.manual_keying_checkbox.isChecked())
        if not is_sending:
            self.set_tx_progress(None)

    def set_tx_progress(self, text_index: int | None) -> None:
        """Highlights the character currently being keyed (text_index is an
        index into tx_text_edit's own text) and updates the progress label.
        Pass None to clear the highlight, e.g. once sending finishes."""
        text_length = len(self.tx_text_edit.toPlainText())
        if text_index is not None and 0 <= text_index < text_length:
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(_TX_PROGRESS_HIGHLIGHT)
            selection.format.setForeground(QColor(0, 0, 0))
            cursor = self.tx_text_edit.textCursor()
            cursor.setPosition(text_index)
            cursor.setPosition(text_index + 1, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            self.tx_text_edit.setExtraSelections([selection])
            self.tx_progress_label.setText(f"Sending: {text_index + 1} / {text_length} characters")
        else:
            self.tx_text_edit.setExtraSelections([])
            self.tx_progress_label.setText("")

    def _force_tx_text_uppercase(self) -> None:
        text = self.tx_text_edit.toPlainText()
        upper = text.upper()
        if upper == text:
            return
        cursor = self.tx_text_edit.textCursor()
        pos = min(cursor.position(), len(upper))
        # blockSignals during the rewrite so this doesn't recurse back into
        # itself via the very textChanged signal it's handling.
        self.tx_text_edit.blockSignals(True)
        self.tx_text_edit.setPlainText(upper)
        cursor.setPosition(pos)
        self.tx_text_edit.setTextCursor(cursor)
        self.tx_text_edit.blockSignals(False)

    def _show_tx_text_context_menu(self, pos) -> None:
        # Standard menu (cut/copy/paste, select all, ...) plus a "Clear"
        # action to clear the TX text field -- e.g. to start fresh with a
        # new message without manually selecting and deleting the old one.
        menu = self.tx_text_edit.createStandardContextMenu()
        menu.addSeparator()
        clear_action = menu.addAction("Clear")
        clear_action.triggered.connect(self.tx_text_edit.clear)
        menu.exec(self.tx_text_edit.mapToGlobal(pos))
