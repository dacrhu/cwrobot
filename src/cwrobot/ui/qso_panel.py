"""QSO data panel: the current contact's callsign / exchanged RST / QTH /
locator / name / frequency / start-end time -- sits between the RX and TX
group boxes.

Per Specifikacio.md's forward-looking plans, these fields exist for two
uses: supplying the variables for the TX macro buttons (ui.macro_bar), and
feeding a QsoLogger via the "Log QSO" button (self.log_button) -- building
the actual models.qso.QsoRecord and picking/running a logger is
main_window's job (_on_log_qso_clicked), same as every other cross-panel
action here; this panel only owns the fields and the button. Fields are
plain editable, fillable either by typing or by selecting text in the RX
decoded-text pane and routing it here via the selection popup
(ui.selection_popup) -- MainWindow wires that hand-off too.

The time fields are the exception to "plain editable": Start auto-captures
"now" the moment the callsign field goes from empty to filled, and End is a
read-only field that continuously ticks to the current time -- so whenever
the operator is done and (eventually) logs the QSO, it already reads the
right end time without any manual entry. Both are UTC, not local time --
ADIF's QSO_DATE/TIME_ON/TIME_OFF fields are defined as UTC (see
models.adif), and main_window passes these fields' values straight through
with no timezone conversion of its own, so capturing anything but UTC here
would silently log the wrong time for every operator not sitting in UTC+0.
See _tick_end_time/_on_callsign_text_changed/clear_all below -- all three
use QDateTime.currentDateTimeUtc(), never plain currentDateTime(). That
alone isn't sufficient, though: QDateTimeEdit.setDateTime() silently
re-expresses whatever QDateTime it's given in the *widget's own* time
zone for internal storage and display -- defaulting to local time -- so
without also calling setTimeZone(QTimeZone.UTC) on both widgets below
right after construction, a UTC value going in would still silently come
back out (via .dateTime(), and so .toPython()) as the equivalent local
wall-clock time instead.

Frequency is a *conditional* exception -- MainWindow drives it read-only,
ticking once a second from a live Hamlib CAT connection, exactly when one
is configured and available;
see set_frequency_editable/set_frequency_hz. It's kept separate from
`self.fields`/QSO_FIELDS entirely since it's neither a selection-popup
routing destination (ui.selection_popup) nor part of the macro variable
catalog (cwrobot.macros) that those fields feed.
"""

from __future__ import annotations

from PySide6.QtCore import QDateTime, QTimer, QTimeZone
from PySide6.QtWidgets import QDateTimeEdit, QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QWidget

from cwrobot.ui.qso_fields import QSO_FIELDS

# Callsign and locator/RST are short, fixed-format tokens; QTH and name tend
# to be actual words, so they get a bit more room in the grid.
_NARROW_FIELDS = {"callsign", "rst_sent", "rst_rcvd", "locator"}

_DATETIME_DISPLAY_FORMAT = "yyyy-MM-dd HH:mm:ss"

# How often the end-time field ticks forward. Real-time-visible ("keeps
# counting up") without being an attention-grabbing stopwatch.
END_TIME_TICK_MS = 1000


class QsoPanel(QGroupBox):
    """One compact row of labeled fields for the QSO currently in progress."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("QSO Data", parent)
        self.setObjectName("qsoPanel")

        layout = QGridLayout(self)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(6)
        self.fields: dict[str, QLineEdit] = {}
        for column, (key, label) in enumerate(QSO_FIELDS):
            layout.addWidget(QLabel(label, self), 0, column)
            edit = QLineEdit(self)
            edit.setMinimumWidth(60 if key in _NARROW_FIELDS else 110)
            layout.addWidget(edit, 1, column)
            self.fields[key] = edit

        # Uppercased live as typed -- same pattern (and, for locator, the
        # same operator request) as SettingsDialog's own operator-callsign/
        # operator-locator fields. textEdited (not textChanged) only fires
        # for actual user edits, so setText() below never recurses into
        # itself.
        self.fields["callsign"].textEdited.connect(lambda text: self._force_uppercase(self.fields["callsign"], text))
        self.fields["locator"].textEdited.connect(lambda text: self._force_uppercase(self.fields["locator"], text))
        # textChanged (not textEdited) also fires on the programmatic
        # setText() from set_field_text() -- filling the callsign via the
        # RX selection popup must start the clock just as much as typing
        # it in by hand.
        self.fields["callsign"].textChanged.connect(self._on_callsign_text_changed)
        # A red "required field" highlight from a failed Log QSO attempt
        # (see set_callsign_error) should disappear the moment the operator
        # starts fixing it, not linger until the next click.
        self.fields["callsign"].textEdited.connect(lambda _: self.set_callsign_error(False))
        self._start_time_captured = False

        freq_column = len(QSO_FIELDS)
        layout.addWidget(QLabel("Frequency (MHz):", self), 0, freq_column)
        self.frequency_edit = QLineEdit(self)
        self.frequency_edit.setMinimumWidth(90)
        self.frequency_edit.setPlaceholderText("14.074000")
        self.frequency_edit.textEdited.connect(lambda _: self.set_frequency_error(False))
        layout.addWidget(self.frequency_edit, 1, freq_column)

        time_column = freq_column + 1
        layout.addWidget(QLabel("Start (UTC):", self), 0, time_column)
        self.start_time_edit = QDateTimeEdit(self)
        self.start_time_edit.setDisplayFormat(_DATETIME_DISPLAY_FORMAT)
        self.start_time_edit.setCalendarPopup(True)
        # See the module docstring above -- without this, values set via
        # currentDateTimeUtc() would silently come back as local time.
        self.start_time_edit.setTimeZone(QTimeZone.UTC)
        layout.addWidget(self.start_time_edit, 1, time_column)

        layout.addWidget(QLabel("End (UTC):", self), 0, time_column + 1)
        self.end_time_edit = QDateTimeEdit(self)
        self.end_time_edit.setDisplayFormat(_DATETIME_DISPLAY_FORMAT)
        self.end_time_edit.setTimeZone(QTimeZone.UTC)
        # Read-only: this field is a continuously-ticking "now" clock (see
        # _tick_end_time below), not something the operator fills in --
        # letting it be edited would just have every keystroke overwritten
        # by the next tick within a second.
        self.end_time_edit.setReadOnly(True)
        self.end_time_edit.setButtonSymbols(QDateTimeEdit.ButtonSymbols.NoButtons)
        layout.addWidget(self.end_time_edit, 1, time_column + 1)

        # Logging itself (building a QsoRecord from these fields, picking a
        # QsoLogger per Settings → Logging, clearing the panel afterward) is
        # main_window's job, same as every other cross-panel action here --
        # this panel only owns the button.
        self.log_button = QPushButton("Log QSO", self)
        self.log_button.setProperty("role", "secondary")
        layout.addWidget(self.log_button, 1, time_column + 2)

        self._end_time_timer = QTimer(self)
        self._end_time_timer.timeout.connect(self._tick_end_time)
        self._end_time_timer.start(END_TIME_TICK_MS)
        self._tick_end_time()

    def _tick_end_time(self) -> None:
        self.end_time_edit.setDateTime(QDateTime.currentDateTimeUtc())

    def _on_callsign_text_changed(self, text: str) -> None:
        if text.strip():
            if not self._start_time_captured:
                self.start_time_edit.setDateTime(QDateTime.currentDateTimeUtc())
                self._start_time_captured = True
        else:
            # Cleared back to empty -- ready to capture a fresh start time
            # the next time a callsign is filled in (new QSO).
            self._start_time_captured = False

    @staticmethod
    def _force_uppercase(edit: QLineEdit, text: str) -> None:
        upper = text.upper()
        if upper == text:
            return
        pos = edit.cursorPosition()
        edit.setText(upper)
        edit.setCursorPosition(pos)

    # Fields that are always-uppercase, whether typed by hand (see the
    # textEdited connections in __init__) or filled programmatically here
    # (e.g. via the RX selection popup, ui.selection_popup).
    _UPPERCASE_FIELDS = {"callsign", "locator"}

    def set_field_text(self, key: str, text: str) -> None:
        edit = self.fields.get(key)
        if edit is None:
            return
        edit.setText(text.upper() if key in self._UPPERCASE_FIELDS else text)

    def set_frequency_hz(self, hz: float) -> None:
        """Called by main_window once a second while a live Hamlib CAT poll
        succeeds (see set_frequency_editable) -- MHz with 6 decimals is the
        conventional ham-log precision (matches ADIF's own FREQ field
        convention), enough to distinguish adjacent CW sub-band QSOs."""
        self.frequency_edit.setText(f"{hz / 1_000_000:.6f}")
        self.set_frequency_error(False)

    def frequency_hz(self) -> float | None:
        """The inverse of set_frequency_hz -- used when logging a QSO
        (see main_window._on_log_qso_clicked). None if empty/unparsable
        (e.g. never populated and never manually filled in)."""
        text = self.frequency_edit.text().strip()
        if not text:
            return None
        try:
            return float(text) * 1_000_000
        except ValueError:
            return None

    def set_frequency_editable(self, editable: bool) -> None:
        """Called by main_window whenever the configured TX backend might
        have changed (startup, Settings dialog accepted): read-only and
        driven from set_frequency_hz while a Hamlib CAT connection is
        configured (matches end_time_edit's own "auto-updated fields aren't
        hand-editable" convention below), a plain manually-typed field
        otherwise (audio TX has no rig link to query it from)."""
        self.frequency_edit.setReadOnly(not editable)

    def set_callsign_error(self, has_error: bool) -> None:
        """Called by main_window when a Log QSO attempt finds the (remote)
        callsign field empty -- see set_frequency_error's own docstring for
        why this needs the explicit unpolish/polish dance."""
        self._set_error_highlight(self.fields["callsign"], has_error)

    def set_frequency_error(self, has_error: bool) -> None:
        """Called by main_window when a Log QSO attempt finds the frequency
        field empty (see ui.style's QLineEdit[error="true"] rule for the
        look). Cleared automatically the moment the operator edits the
        field by hand (see the textEdited connections in __init__) or it
        gets filled from a live Hamlib poll (see set_frequency_hz) -- a
        stale highlight on an already-fixed field would be actively
        misleading."""
        self._set_error_highlight(self.frequency_edit, has_error)

    @staticmethod
    def _set_error_highlight(edit: QLineEdit, has_error: bool) -> None:
        # QSS only re-reads a dynamic property's value on the widget's next
        # style *polish* -- for a property toggled after the widget is
        # already visible (unlike e.g. log_button's "role", set once during
        # __init__), that has to be forced explicitly or the border/tint
        # change would never actually show up on screen.
        edit.setProperty("error", has_error)
        edit.style().unpolish(edit)
        edit.style().polish(edit)

    def clear_all(self) -> None:
        for edit in self.fields.values():
            edit.clear()
        self.frequency_edit.clear()
        self.set_callsign_error(False)
        self.set_frequency_error(False)
        self._start_time_captured = False
        self.start_time_edit.setDateTime(QDateTime.currentDateTimeUtc())
