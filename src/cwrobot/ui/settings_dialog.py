"""Settings dialog.

Four tabs: "My Data" (own/station data -- callsign/name/locator/QTH),
"Audio Devices" (audio input/output device selection), "TX" (the TX
backend -- audio tone vs. Hamlib CAT -- plus, when Hamlib CAT is the
configured backend, its rig model/port/baud), and "Logging" (how the "Log
QSO" button on ui.qso_panel logs each contact -- an ADIF file or ADIF over
UDP). Kept as a QTabWidget so future settings categories (decoder tuning,
...) can be added without a layout rework.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cwrobot.audio import devices as audio_devices
from cwrobot.config import AppConfig
from cwrobot.hamlib.ctypes_bindings import HamlibUnavailableError
from cwrobot.hamlib.rig_client import HamlibError, HamlibRig, list_rig_models
from cwrobot.hamlib.serial_ports import list_serial_ports
from cwrobot.ui.style import make_separator

# Offered as presets in the (editable) baud rate combo -- the common serial
# speeds CAT interfaces use. Editable so an unusual rig-specific rate can
# still be typed in by hand.
_BAUD_RATE_PRESETS = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings")

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        layout.addWidget(tabs)

        tabs.addTab(self._build_station_tab(), "My Data")
        tabs.addTab(self._build_audio_tab(), "Audio Devices")
        tabs.addTab(self._build_tx_tab(), "TX")
        tabs.addTab(self._build_logging_tab(), "Logging")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_station_tab(self) -> QWidget:
        widget = QWidget(self)
        form = QFormLayout(widget)

        self.callsign_edit = QLineEdit(self.config.operator_callsign, self)
        # Uppercased live as typed, not just on save -- textEdited (not
        # textChanged) only fires for actual user edits, so setText() below
        # never recurses into itself.
        self.callsign_edit.textEdited.connect(lambda text: self._force_uppercase(self.callsign_edit, text))
        self.name_edit = QLineEdit(self.config.operator_name, self)
        self.locator_edit = QLineEdit(self.config.operator_locator, self)
        # Uppercased live too -- Maidenhead locators are conventionally
        # mixed-case (e.g. "JN58td"), but the operator explicitly wants the
        # same all-caps-as-typed behavior as the callsign field here.
        self.locator_edit.textEdited.connect(lambda text: self._force_uppercase(self.locator_edit, text))
        self.qth_edit = QLineEdit(self.config.operator_qth, self)

        form.addRow("Callsign:", self.callsign_edit)
        form.addRow("Name:", self.name_edit)
        form.addRow("Locator:", self.locator_edit)
        form.addRow("QTH:", self.qth_edit)
        return widget

    def _build_audio_tab(self) -> QWidget:
        widget = QWidget(self)
        form = QFormLayout(widget)

        # Not editable (unlike the Hamlib port combo below): a device label
        # is never something an operator would hand-type, and there's no
        # "unplugged but configured" case worth preserving verbatim here --
        # an unavailable configured device just falls back to whatever ends
        # up first in the freshly enumerated list.
        self.input_device_combo = QComboBox(self)
        self.output_device_combo = QComboBox(self)

        self.audio_device_error_label = QLabel("", self)
        self.audio_device_error_label.setWordWrap(True)

        self._populate_audio_device_combos()

        form.addRow("Input device:", self.input_device_combo)
        form.addRow("Output device:", self.output_device_combo)

        refresh_button = QPushButton("Rescan devices", self)
        refresh_button.setProperty("role", "secondary")
        refresh_button.clicked.connect(self._on_refresh_audio_devices_clicked)
        form.addRow(refresh_button)

        form.addRow(self.audio_device_error_label)
        return widget

    def _populate_audio_device_combos(
        self, preserve_input: str | None = None, preserve_output: str | None = None
    ) -> None:
        """(Re)fills both device combos from a live enumeration. `preserve_*`
        is the label to pre-select if it's still present -- defaults to the
        configured value on the initial build; _on_refresh_audio_devices_clicked
        passes each combo's current selection instead, so an already-made
        selection survives a mid-session rescan (mirrors
        _populate_port_combo's `preserve` param on the Hamlib tab)."""
        if preserve_input is None:
            preserve_input = self.config.rx_input_device
        if preserve_output is None:
            preserve_output = self.config.tx_output_device

        try:
            input_devices = audio_devices.list_input_devices()
            output_devices = audio_devices.list_output_devices()
        except Exception as exc:  # sounddevice/PortAudio failures must not crash the dialog
            self.audio_device_error_label.setText(f"Audio device listing error: {exc}")
            return
        self.audio_device_error_label.setText("")

        self._fill_device_combo(self.input_device_combo, [d.label for d in input_devices], preserve_input)
        self._fill_device_combo(self.output_device_combo, [d.label for d in output_devices], preserve_output)

    @staticmethod
    def _fill_device_combo(combo: QComboBox, labels: list[str], selected_label: str | None) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(labels)
        if selected_label is not None:
            index = combo.findText(selected_label)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _on_refresh_audio_devices_clicked(self) -> None:
        try:
            audio_devices.rescan_devices()
        except Exception as exc:
            self.audio_device_error_label.setText(f"Device rescan error: {exc}")
        self._populate_audio_device_combos(
            preserve_input=self.input_device_combo.currentText() or None,
            preserve_output=self.output_device_combo.currentText() or None,
        )

    def _build_tx_tab(self) -> QWidget:
        widget = QWidget(self)
        form = QFormLayout(widget)

        # The mode picker itself doesn't depend on libhamlib.so being
        # installed -- "Audio (sound card)" must stay pickable even when
        # list_rig_models() below is about to degrade the rest of this tab
        # (Hamlib being genuinely unavailable is exactly when an operator
        # needs to be *able* to fall back to it).
        self.tx_backend_combo = QComboBox(self)
        self.tx_backend_combo.addItem("Audio (sound card)", userData="audio")
        self.tx_backend_combo.addItem("Hamlib CAT", userData="hamlib")
        backend_index = self.tx_backend_combo.findData(self.config.tx_backend)
        self.tx_backend_combo.setCurrentIndex(backend_index if backend_index >= 0 else 0)
        form.addRow("TX mode:", self.tx_backend_combo)
        form.addRow(make_separator(self))

        # list_rig_models() needs libhamlib.so -- if it's not installed,
        # cwrobot.hamlib.ctypes_bindings.get_library() (called under the
        # hood) raises HamlibUnavailableError. Rather than let that surface
        # as a crash when the dialog opens, degrade this one tab: a single
        # explanatory label instead of the (unusable) form.
        try:
            models = list_rig_models()
        except HamlibUnavailableError as exc:
            form.addRow(QLabel(f"CAT not available: {exc}"))
            self.rig_model_combo = None
            self.port_combo = None
            self.baud_rate_combo = None
            return widget

        self.rig_model_combo = QComboBox(self)
        self.rig_model_combo.setEditable(True)
        self.rig_model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.rig_model_combo.addItem("(none selected)", userData=None)
        for model in models:
            self.rig_model_combo.addItem(str(model), userData=model.model_id)
        # ~300 entries is too many to scroll through by eye -- an editable
        # combo with a "contains" completer lets the operator just type part
        # of a manufacturer or model name (e.g. "7300") to filter down to it.
        completer = QCompleter([str(m) for m in models], self.rig_model_combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.rig_model_combo.setCompleter(completer)
        selected_index = self.rig_model_combo.findData(self.config.hamlib_rig_model_id)
        self.rig_model_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        form.addRow("Rig model:", self.rig_model_combo)

        self.port_combo = QComboBox(self)
        self.port_combo.setEditable(True)
        self.port_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.port_combo.lineEdit().setPlaceholderText("/dev/ttyUSB0")
        self._populate_port_combo()

        port_row = QHBoxLayout()
        port_row.setContentsMargins(0, 0, 0, 0)
        port_row.addWidget(self.port_combo, stretch=1)
        refresh_port_button = QPushButton("Refresh", self)
        refresh_port_button.setProperty("role", "secondary")
        refresh_port_button.clicked.connect(self._on_refresh_ports_clicked)
        port_row.addWidget(refresh_port_button)
        form.addRow("Port:", port_row)

        self.baud_rate_combo = QComboBox(self)
        self.baud_rate_combo.setEditable(True)
        for baud in _BAUD_RATE_PRESETS:
            self.baud_rate_combo.addItem(str(baud))
        self.baud_rate_combo.setCurrentText(str(self.config.hamlib_baud_rate))
        form.addRow("Speed (baud):", self.baud_rate_combo)

        test_button = QPushButton("Test connection", self)
        test_button.setProperty("role", "secondary")
        test_button.clicked.connect(self._on_test_connection_clicked)
        form.addRow(test_button)

        self.hamlib_test_result_label = QLabel("", self)
        self.hamlib_test_result_label.setWordWrap(True)
        form.addRow(self.hamlib_test_result_label)

        return widget

    def _build_logging_tab(self) -> QWidget:
        widget = QWidget(self)
        form = QFormLayout(widget)

        self.logging_method_combo = QComboBox(self)
        self.logging_method_combo.addItem("File", userData="file")
        self.logging_method_combo.addItem("UDP", userData="udp")
        method_index = self.logging_method_combo.findData(self.config.logging_method)
        self.logging_method_combo.setCurrentIndex(method_index if method_index >= 0 else 0)
        form.addRow("Method:", self.logging_method_combo)
        form.addRow(make_separator(self))

        # Both the file and UDP fields stay visible regardless of which
        # method is selected -- same "don't bother hiding the inactive
        # half" choice already made for the TX tab's Hamlib fields (visible
        # even in audio mode); simpler than wiring conditional show/hide,
        # and the operator can freely pre-fill either one before switching.
        self.logging_folder_edit = QLineEdit(self.config.logging_folder, self)
        folder_row = QHBoxLayout()
        folder_row.setContentsMargins(0, 0, 0, 0)
        folder_row.addWidget(self.logging_folder_edit, stretch=1)
        browse_button = QPushButton("Browse…", self)
        browse_button.setProperty("role", "secondary")
        browse_button.clicked.connect(self._on_browse_log_folder_clicked)
        folder_row.addWidget(browse_button)
        form.addRow("Log folder:", folder_row)

        self.logging_udp_host_edit = QLineEdit(self.config.logging_udp_host, self)
        form.addRow("UDP host:", self.logging_udp_host_edit)

        self.logging_udp_port_spinbox = QSpinBox(self)
        self.logging_udp_port_spinbox.setRange(1, 65535)
        self.logging_udp_port_spinbox.setValue(self.config.logging_udp_port)
        form.addRow("UDP port:", self.logging_udp_port_spinbox)

        # Two incompatible UDP conventions in the wild -- a bare ADIF record
        # (Log4OM-style listeners) vs. the WSJT-X Network Message protocol's
        # framed "Logged ADIF" datagram (QLog, JTAlert, GridTracker, ...).
        # See models.wsjtx_udp/models.qso.UdpQsoLogger.
        self.logging_udp_format_combo = QComboBox(self)
        self.logging_udp_format_combo.addItem("Raw ADIF (Log4OM-style)", userData="adif")
        self.logging_udp_format_combo.addItem(
            "WSJT-X protocol (QLog, JTAlert, GridTracker, ...)", userData="wsjtx"
        )
        format_index = self.logging_udp_format_combo.findData(self.config.logging_udp_format)
        self.logging_udp_format_combo.setCurrentIndex(format_index if format_index >= 0 else 0)
        form.addRow("UDP format:", self.logging_udp_format_combo)

        return widget

    def _on_browse_log_folder_clicked(self) -> None:
        start_dir = self.logging_folder_edit.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select Log Folder", start_dir)
        if folder:
            self.logging_folder_edit.setText(folder)

    def _populate_port_combo(self, preserve: str | None = None) -> None:
        """(Re)fills the port combo from a live pyserial enumeration.
        `preserve` is the value to pre-select/pre-fill if it's not found
        among the freshly enumerated ports -- defaults to the configured
        value on the initial build; _on_refresh_ports_clicked passes the
        combo's current text instead, so an in-progress manual edit or
        already-made selection survives a mid-session rescan. Never falls
        back to whatever ends up first in the list (unlike the non-editable
        audio device combos) -- a configured-but-currently-unplugged port
        must stay visible, not get silently swapped for an unrelated one."""
        if preserve is None:
            preserve = self.config.hamlib_port_path

        try:
            ports = list_serial_ports()
        except Exception:
            ports = []

        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for port in ports:
            self.port_combo.addItem(port.device)
            self.port_combo.setItemData(
                self.port_combo.count() - 1,
                f"{port.device} -- {port.description}",
                Qt.ItemDataRole.ToolTipRole,
            )

        existing_index = self.port_combo.findText(preserve)
        if existing_index >= 0:
            self.port_combo.setCurrentIndex(existing_index)
        else:
            self.port_combo.setEditText(preserve)
        self.port_combo.blockSignals(False)

    def _on_refresh_ports_clicked(self) -> None:
        self._populate_port_combo(preserve=self.port_combo.currentText())

    def _on_test_connection_clicked(self) -> None:
        model_id = self.rig_model_combo.currentData()
        if model_id is None:
            self.hamlib_test_result_label.setText("Select a rig model first.")
            return

        port_path = self.port_combo.currentText().strip()
        baud_rate = self._parsed_baud_rate()

        self.hamlib_test_result_label.setText("Connecting…")
        rig = HamlibRig(model_id=model_id, port_path=port_path, baud_rate=baud_rate)
        try:
            rig.open()
        except (HamlibError, HamlibUnavailableError) as exc:
            self.hamlib_test_result_label.setText(f"Failed: {exc}")
            return
        rig.close()
        self.hamlib_test_result_label.setText("Connected successfully ✓")

    def _parsed_baud_rate(self) -> int | None:
        if self.baud_rate_combo is None:
            return None
        try:
            return int(self.baud_rate_combo.currentText().strip())
        except ValueError:
            return None

    @staticmethod
    def _force_uppercase(edit: QLineEdit, text: str) -> None:
        upper = text.upper()
        if upper == text:
            return
        pos = edit.cursorPosition()
        edit.setText(upper)
        edit.setCursorPosition(pos)

    def _on_accept(self) -> None:
        # .upper() here too as a defensive fallback (e.g. a pre-existing
        # lowercase value loaded from config that the operator never
        # retyped).
        self.config.operator_callsign = self.callsign_edit.text().strip().upper()
        self.config.operator_name = self.name_edit.text().strip()
        self.config.operator_locator = self.locator_edit.text().strip().upper()
        self.config.operator_qth = self.qth_edit.text().strip()

        self.config.rx_input_device = self.input_device_combo.currentText().strip() or None
        self.config.tx_output_device = self.output_device_combo.currentText().strip() or None

        self.config.tx_backend = self.tx_backend_combo.currentData()

        # None on the Hamlib tab means list_rig_models() failed (no
        # libhamlib.so) and the tab degraded to just an explanatory label --
        # leave the existing config values untouched in that case rather
        # than clobbering them with nothing to save.
        if self.rig_model_combo is not None:
            self.config.hamlib_rig_model_id = self.rig_model_combo.currentData()
            self.config.hamlib_port_path = self.port_combo.currentText().strip()
            baud_rate = self._parsed_baud_rate()
            if baud_rate is not None:
                self.config.hamlib_baud_rate = baud_rate

        self.config.logging_method = self.logging_method_combo.currentData()
        self.config.logging_folder = self.logging_folder_edit.text().strip()
        self.config.logging_udp_host = self.logging_udp_host_edit.text().strip() or "127.0.0.1"
        self.config.logging_udp_port = self.logging_udp_port_spinbox.value()
        self.config.logging_udp_format = self.logging_udp_format_combo.currentData()

        self.config.save()
        self.accept()
