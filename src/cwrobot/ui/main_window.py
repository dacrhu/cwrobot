"""Main application window.

Layout: two visually separate group boxes (RX/vetel, TX/adas), per
Specifikacio.md's "kulon megjelenes" requirement, stacked vertically, plus a
minimal menu bar and a status bar for connection/error state.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from cwrobot import macros
from cwrobot.audio import devices as audio_devices
from cwrobot.audio.capture import AudioCapture
from cwrobot.config import AppConfig
from cwrobot.decoder.decoder import CwDecoder
from cwrobot.hamlib.ctypes_bindings import HamlibUnavailableError
from cwrobot.hamlib.frequency_monitor import FrequencyMonitor
from cwrobot.hamlib.rig_client import HamlibError
from cwrobot.models.qso import QsoLoggingError, QsoRecord, build_qso_logger
from cwrobot.tx.audio_backend import AudioTxBackend
from cwrobot.tx.backend import TxBackend
from cwrobot.tx.encoder import manual_keying_jitter_from_level
from cwrobot.tx.hamlib_backend import HamlibTxBackend
from cwrobot.tx.worker import TxWorker
from cwrobot.ui.qso_panel import QsoPanel
from cwrobot.ui.rx_panel import RxPanel
from cwrobot.ui.settings_dialog import SettingsDialog
from cwrobot.ui.tx_panel import TxPanel


class MainWindow(QMainWindow):
    # Cross-thread requests into self._freq_monitor (see hamlib
    # .frequency_monitor's module docstring for why these go through signals
    # rather than calling the worker's methods directly).
    _freq_monitor_set_target = Signal(object, str, object)  # model_id, port_path, baud_rate
    _freq_monitor_pause = Signal()
    _freq_monitor_resume = Signal()
    _freq_monitor_stop = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.setWindowTitle("CW Robot")
        self.resize(900, 700)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        self.rx_panel = RxPanel(central)
        self.qso_panel = QsoPanel(central)
        self.tx_panel = TxPanel(config, central)
        # RX is the only one that should keep growing into extra window
        # height -- it holds the waterfall + the decoded-text log, both of
        # which genuinely benefit from more room. QSO Data is a single
        # fixed row of fields, and TX's own content (controls + a short
        # TX text box) doesn't need to keep growing either -- giving it a
        # share of the stretch (as before) just left a large empty-looking
        # gap under its macro buttons on a tall window.
        layout.addWidget(self.rx_panel, stretch=1)
        layout.addWidget(self.qso_panel)
        layout.addWidget(self.tx_panel)
        self.rx_panel.selection_routed_to_field.connect(self.qso_panel.set_field_text)
        self.tx_panel.macro_bar.macro_activated.connect(self._on_macro_activated)

        self._apply_config_to_widgets()
        self._build_menu()

        # Never let a panel shrink below its own comfortable size, via two
        # things that both turned out to be necessary:
        #
        # 1. Per-panel hard floors: when the *total* window height on offer
        #    falls short of every panel's combined minimum, QVBoxLayout
        #    shrinks the stretch=1 item (RxPanel) first/most rather than
        #    spreading the deficit evenly -- QsoPanel/TxPanel (stretch 0)
        #    kept their full preferred size while RxPanel got squeezed
        #    *below* its own minimumSizeHint, crushing its pitch/bandwidth/
        #    squelch controls into an overlapping, unreadable mess.
        #    setMinimumHeight() makes each panel's own minimum a hard floor
        #    the layout can no longer take from -- so if the window really
        #    is too short, panels visibly overlap each other instead of a
        #    single panel silently mangling its own contents (a far more
        #    obvious, fixable failure mode). Safe to compute right after
        #    construction (no show()/layout pass needed first): each panel's
        #    minimumSizeHint() is governed by its own fixed-width
        #    controls_column (SETTINGS_COLUMN_WIDTH, see form_helpers), not
        #    by whatever width the panel eventually gets from this layout.
        # 2. A correctly chrome-aware whole-window floor: central widget's
        #    own minimumSizeHint() only covers *its* content -- setMinimumSize
        #    on self (the QMainWindow) needs the menu bar's and status bar's
        #    height added on top, or the window's floor comes out shorter
        #    than what the central widget actually needs, silently
        #    reintroducing the exact per-panel deficit (1) guards against.
        for panel in (self.rx_panel, self.qso_panel, self.tx_panel):
            panel.setMinimumHeight(panel.minimumSizeHint().height())
        chrome_height = self.menuBar().sizeHint().height() + self.statusBar().sizeHint().height()
        central_minimum = central.minimumSizeHint()
        self.setMinimumSize(central_minimum.width(), central_minimum.height() + chrome_height)

        self.statusBar().showMessage("Radio: not connected | Audio: not initialized")

        self._audio_capture: AudioCapture | None = None
        self._decoder: CwDecoder | None = None
        self._decoder_thread: QThread | None = None

        self._tx_backend: TxBackend | None = None
        self._tx_worker: TxWorker | None = None
        self._tx_thread: QThread | None = None
        self._last_rx_wpm: float | None = None

        # Polls the rig's VFO frequency for the QSO panel, on its own
        # QThread -- separate from the Hamlib connection TX itself opens
        # per-send (see _build_hamlib_tx_backend). Never held open at the
        # same time as a TX connection: two open handles to the same serial
        # port at once could interleave their traffic and corrupt either
        # side's CAT commands, so _build_hamlib_tx_backend pauses this first,
        # and _stop_tx_pipeline resumes it once the send's own connection is
        # closed. See hamlib.frequency_monitor's module docstring for why
        # this needs its own thread at all (blocking Hamlib I/O on the GUI
        # thread -- e.g. retrying against a powered-off rig -- was freezing
        # the whole window).
        self._freq_monitor = FrequencyMonitor()
        self._freq_monitor_thread = QThread(self)
        self._freq_monitor.moveToThread(self._freq_monitor_thread)
        self._freq_monitor_thread.started.connect(self._freq_monitor.run)
        self._freq_monitor.frequency_changed.connect(self.qso_panel.set_frequency_hz)
        self._freq_monitor_set_target.connect(self._freq_monitor.set_target)
        self._freq_monitor_pause.connect(self._freq_monitor.pause)
        self._freq_monitor_resume.connect(self._freq_monitor.resume)
        self._freq_monitor_stop.connect(self._freq_monitor.stop)
        self._freq_monitor_thread.start()
        self._sync_freq_monitor_target()

        self.rx_panel.pitch_spinbox.valueChanged.connect(self._on_target_pitch_changed)
        self.rx_panel.bandwidth_slider.valueChanged.connect(self._on_bandwidth_changed)
        self.rx_panel.squelch_slider.valueChanged.connect(self._on_squelch_ratio_changed)
        self.rx_panel.auto_bandwidth_checkbox.toggled.connect(self._on_auto_bandwidth_changed)

        self.tx_panel.send_button.clicked.connect(self._on_tx_send_clicked)
        self.tx_panel.stop_button.clicked.connect(self._on_tx_stop_clicked)
        self.tx_panel.match_rx_speed_button.clicked.connect(self._on_match_rx_speed_clicked)

        self.qso_panel.log_button.clicked.connect(self._on_log_qso_clicked)

        self._start_audio_pipeline(self.config.rx_input_device)

    def _apply_config_to_widgets(self) -> None:
        self.rx_panel.pitch_spinbox.setValue(self.config.target_pitch_hz)
        self.rx_panel.waterfall.set_half_span(self.config.waterfall_half_span_hz)
        self.rx_panel.bandwidth_slider.setValue(self.config.detection_bin_span)
        self.rx_panel.squelch_slider.setValue(round(self.config.squelch_ratio))
        self.rx_panel.auto_bandwidth_checkbox.setChecked(self.config.auto_bandwidth)
        self.tx_panel.speed_spinbox.setValue(self.config.tx_speed_wpm)
        self.tx_panel.manual_keying_checkbox.setChecked(self.config.manual_keying_emulation)
        self.tx_panel.manual_keying_slider.setValue(self.config.manual_keying_intensity)
        self.tx_panel.set_manual_keying_available(self.config.tx_backend != "hamlib")
        self.qso_panel.set_frequency_editable(self.config.tx_backend != "hamlib")

    def _sync_freq_monitor_target(self) -> None:
        """Tells the frequency-monitor thread what (if anything) to poll,
        from the current config -- called once at startup and again
        whenever the Settings dialog is accepted, since rig model/port/baud
        and the TX backend choice are the only things that change this."""
        if self.config.tx_backend == "hamlib":
            self._freq_monitor_set_target.emit(
                self.config.hamlib_rig_model_id, self.config.hamlib_port_path, self.config.hamlib_baud_rate
            )
        else:
            self._freq_monitor_set_target.emit(None, "", None)

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        quit_action = QAction("&Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        settings_menu = menu_bar.addMenu("&Settings")
        open_settings_action = QAction("&Open Settings…", self)
        open_settings_action.triggered.connect(self._open_settings_dialog)
        settings_menu.addAction(open_settings_action)

        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _open_settings_dialog(self) -> None:
        previous_input_device = self.config.rx_input_device
        dialog = SettingsDialog(self.config, self)
        if dialog.exec():
            self.tx_panel.set_manual_keying_available(self.config.tx_backend != "hamlib")
            self.qso_panel.set_frequency_editable(self.config.tx_backend != "hamlib")
            # Rig model/port/baud may have changed -- reconnect the monitor
            # against the current config rather than keep polling a now-
            # stale connection (or one for a backend that's no longer
            # configured).
            self._sync_freq_monitor_target()
            # The output device is only read fresh at send time (see
            # _build_audio_tx_backend), so nothing else to apply for it, but
            # a changed *input* device needs the already-running capture
            # pipeline actually restarted onto it.
            if self.config.rx_input_device != previous_input_device:
                self._start_audio_pipeline(self.config.rx_input_device)

    def _show_about(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            self,
            "About",
            "CW Robot - v1.0.3\n\nDeveloped by HG7WHD\n\nhttps://github.com/dacrhu/cwrobot",
        )

    # -- Audio pipeline (capture -> ring buffer -> CwDecoder -> waterfall) --

    def _on_target_pitch_changed(self, hz: int) -> None:
        if self._decoder is not None:
            self._decoder.target_pitch_hz = hz

    def _on_bandwidth_changed(self, bin_span: int) -> None:
        self.config.detection_bin_span = bin_span
        if self._decoder is not None:
            self._decoder.detection_bin_span = bin_span
            self._apply_effective_bandwidth()

    def _on_squelch_ratio_changed(self, ratio: int) -> None:
        self.config.squelch_ratio = float(ratio)
        if self._decoder is not None:
            self._decoder.squelch_ratio = float(ratio)

    def _on_auto_bandwidth_changed(self, enabled: bool) -> None:
        self.config.auto_bandwidth = enabled
        if self._decoder is not None:
            self._decoder.auto_bandwidth = enabled
            self._apply_effective_bandwidth()

    def _apply_effective_bandwidth(self) -> None:
        if self._decoder is None:
            return
        hz = self._decoder.effective_bandwidth_hz
        self.rx_panel.set_bandwidth_label(hz)
        self.rx_panel.waterfall.set_signal_width(hz)

    def _on_rx_wpm_changed(self, wpm: float) -> None:
        self.rx_panel.set_wpm(wpm)
        self._last_rx_wpm = wpm
        self.tx_panel.match_rx_speed_button.setEnabled(True)
        # wpm_changed fires on the same ~0.25s cadence the decoder uses to
        # retune the adaptive window (see CwDecoderCore.process_block), so
        # this is the natural place to keep the bandwidth label/waterfall
        # marker live as the window changes in auto mode. A no-op-looking
        # refresh in manual mode, where the value doesn't change on its own.
        self._apply_effective_bandwidth()

    def _start_audio_pipeline(self, device_name: str | None) -> None:
        self._stop_audio_pipeline()

        # A saved/selected name can resolve to a real PortAudio device (used
        # directly) or a PipeWire source with no PortAudio equivalent (e.g.
        # another app's sink monitor) -- routed onto the stream after it
        # opens on the system default. See audio/pipewire_route.py.
        pa_device, pipewire_source = audio_devices.resolve_input_device(device_name)
        if device_name is not None and pa_device is None and pipewire_source is None:
            self.statusBar().showMessage(
                f"Audio input not found ({device_name}) — using default device."
            )

        try:
            capture = AudioCapture(device=pa_device, route_to_pipewire_source=pipewire_source)
            capture.start()
        except Exception as exc:
            self.statusBar().showMessage(f"Audio error ({device_name or 'default device'}): {exc}")
            return

        self._audio_capture = capture

        decoder = CwDecoder(
            ring_buffer=capture.ring_buffer,
            sample_rate=capture.samplerate,
            target_pitch_hz=self.rx_panel.pitch_spinbox.value(),
            waterfall_half_span_hz=self.config.waterfall_half_span_hz,
        )
        decoder.detection_bin_span = self.config.detection_bin_span
        decoder.squelch_ratio = self.config.squelch_ratio
        decoder.auto_bandwidth = self.config.auto_bandwidth

        thread = QThread(self)
        decoder.moveToThread(thread)
        thread.started.connect(decoder.run)
        decoder.spectrum_column.connect(self.rx_panel.waterfall.push_spectrum_slice)
        decoder.character_decoded.connect(self.rx_panel.append_decoded_text)
        decoder.wpm_changed.connect(self._on_rx_wpm_changed)
        decoder.squelch_state_changed.connect(self.rx_panel.set_squelch_open)
        decoder.squelch_state_changed.connect(self.rx_panel.waterfall.set_squelch_open)
        thread.start()

        self._decoder = decoder
        self._decoder_thread = thread

        self._apply_effective_bandwidth()

        label = device_name or "default device"
        self.statusBar().showMessage(f"Audio: capture running ({label}, {capture.samplerate} Hz)")

    def _stop_audio_pipeline(self) -> None:
        if self._decoder is not None:
            self._decoder.stop()
        if self._decoder_thread is not None:
            self._decoder_thread.quit()
            self._decoder_thread.wait(2000)
        self._decoder = None
        self._decoder_thread = None

        if self._audio_capture is not None:
            self._audio_capture.stop()
        self._audio_capture = None

    # -- TX pipeline (encoder -> backend, on its own QThread) --

    def _on_match_rx_speed_clicked(self) -> None:
        if self._last_rx_wpm is not None:
            self.tx_panel.speed_spinbox.setValue(round(self._last_rx_wpm))

    def _on_macro_activated(self, template: str) -> None:
        # QsoPanel's field values are read fresh here (not cached anywhere)
        # so a macro always reflects whatever's currently in the QSO panel,
        # not whatever it was when the button/editor was last touched.
        qso_field_values = {key: edit.text() for key, edit in self.qso_panel.fields.items()}
        variables = macros.build_variables(self.config, qso_field_values)
        resolved = macros.resolve_macro_text(template, variables)

        # A macro replaces whatever's currently in the TX field rather than
        # inserting at the cursor -- these are canned whole messages, not
        # snippets meant to be spliced into other text.
        self.tx_panel.tx_text_edit.clear()
        cursor = self.tx_panel.tx_text_edit.textCursor()
        cursor.insertText(resolved)
        self.tx_panel.tx_text_edit.setTextCursor(cursor)
        self.tx_panel.tx_text_edit.setFocus()

    # -- QSO logging (ADIF file or UDP, see models.qso/models.adif and
    # ui.settings_dialog's "Logging" tab) --

    def _on_log_qso_clicked(self) -> None:
        callsign = self.qso_panel.fields["callsign"].text().strip()
        freq_hz = self.qso_panel.frequency_hz()

        # Both required fields get checked (and highlighted) together, not
        # one-at-a-time -- so an operator missing both sees both flagged red
        # right away, instead of fixing the callsign only to be told about
        # the frequency next.
        self.qso_panel.set_callsign_error(not callsign)
        self.qso_panel.set_frequency_error(freq_hz is None)
        if not callsign:
            self.statusBar().showMessage("Enter the other station's callsign before logging.")
            return
        if freq_hz is None:
            # Without a frequency there's no BAND either (see models.adif's
            # band_for_frequency_hz) -- both are technically optional per
            # the bare ADIF spec, but real-world logging software routinely
            # rejects an import missing them, so block here rather than
            # produce a record that silently fails to import later.
            self.statusBar().showMessage(
                "Enter or confirm the frequency before logging (needed for BAND)."
            )
            return
        if not self.config.operator_callsign:
            self.statusBar().showMessage("Set your own callsign first -- Settings → My Data.")
            return

        record = QsoRecord(
            callsign=callsign,
            freq_hz=freq_hz,
            mode="CW",
            rx_start=self.qso_panel.start_time_edit.dateTime().toPython(),
            rx_end=self.qso_panel.end_time_edit.dateTime().toPython(),
            rst_sent=self.qso_panel.fields["rst_sent"].text().strip(),
            rst_rcvd=self.qso_panel.fields["rst_rcvd"].text().strip(),
            qth=self.qso_panel.fields["qth"].text().strip(),
            locator=self.qso_panel.fields["locator"].text().strip(),
            name=self.qso_panel.fields["name"].text().strip(),
            station_callsign=self.config.operator_callsign,
        )
        try:
            build_qso_logger(self.config).log_qso(record)
        except QsoLoggingError as exc:
            self.statusBar().showMessage(f"Logging failed: {exc}")
            return
        self.statusBar().showMessage(f"Logged QSO with {record.callsign}.")
        self.qso_panel.clear_all()

    def _build_audio_tx_backend(self) -> TxBackend | None:
        device_name = self.config.tx_output_device
        pa_device, pipewire_sink = audio_devices.resolve_output_device(device_name)
        if device_name is not None and pa_device is None and pipewire_sink is None:
            self.statusBar().showMessage(
                f"Audio output not found ({device_name}) — using default device."
            )
        # The audio TX tone shares the same target pitch as the RX tuning
        # marker (Specifikacio.md: one fixed, pre-configured sidetone pitch
        # for both directions), read live from the pitch spinbox rather than
        # self.config (which is only synced back to it on close).
        frequency_hz = self.rx_panel.pitch_spinbox.value()
        backend = AudioTxBackend(
            frequency_hz=frequency_hz, device=pa_device, route_to_pipewire_sink=pipewire_sink
        )
        try:
            backend.start()
        except Exception as exc:
            self.statusBar().showMessage(f"TX audio error ({device_name or 'default device'}): {exc}")
            return None
        return backend

    def _build_hamlib_tx_backend(self) -> TxBackend | None:
        if self.config.hamlib_rig_model_id is None:
            self.statusBar().showMessage(
                "No rig model selected -- set one on the Settings → TX tab."
            )
            return None

        # Free the port first -- see the frequency-monitor setup comment in
        # __init__ for why this and the TX connection below must never both
        # be open at once. _stop_tx_pipeline resumes it once this send's own
        # connection is closed.
        self._freq_monitor_pause.emit()

        backend = HamlibTxBackend(
            model_id=self.config.hamlib_rig_model_id,
            port_path=self.config.hamlib_port_path,
            baud_rate=self.config.hamlib_baud_rate,
        )
        try:
            backend.start()
        except (HamlibError, HamlibUnavailableError) as exc:
            self.statusBar().showMessage(f"TX CAT error: {exc}")
            return None
        return backend

    def _on_tx_send_clicked(self) -> None:
        if self._tx_thread is not None:
            return  # a send is already in progress; the button is disabled but guard anyway

        # Kept unstripped: TxWorker.progress reports character indices into
        # this exact string, which set_tx_progress then uses directly as
        # tx_text_edit cursor positions -- stripping here would desync the
        # highlight from the widget's actual (unstripped) content.
        text = self.tx_panel.tx_text_edit.toPlainText()
        if not text.strip():
            return

        backend_name = self.config.tx_backend
        if backend_name == "hamlib":
            backend = self._build_hamlib_tx_backend()
        else:
            backend = self._build_audio_tx_backend()
        if backend is None:
            return  # a status bar message explaining why was already shown

        wpm = self.tx_panel.speed_spinbox.value()
        # Manual-keying jitter only means anything for the audio backend --
        # over CAT the rig's own keyer paces the real timing regardless of
        # what we pass (see tx.hamlib_backend), and the checkbox/slider are
        # disabled in that mode anyway (ui.tx_panel.set_manual_keying_available).
        if backend_name != "hamlib" and self.tx_panel.manual_keying_checkbox.isChecked():
            jitter = manual_keying_jitter_from_level(self.tx_panel.manual_keying_slider.value())
        else:
            jitter = 0.0
        worker = TxWorker(backend=backend, text=text, wpm=wpm, jitter=jitter)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_tx_finished)
        worker.error.connect(self._on_tx_error)
        worker.progress.connect(self.tx_panel.set_tx_progress)
        thread.start()

        self._tx_backend = backend
        self._tx_worker = worker
        self._tx_thread = thread

        self.tx_panel.set_sending(True)
        self.statusBar().showMessage(f"Sending ({wpm} WPM)…")

    def _on_tx_stop_clicked(self) -> None:
        if self._tx_worker is not None:
            self._tx_worker.stop()

    def _on_tx_error(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _on_tx_finished(self) -> None:
        self._stop_tx_pipeline()
        self.tx_panel.set_sending(False)
        self.statusBar().showMessage("Send complete.")

    def _stop_tx_pipeline(self) -> None:
        if self._tx_worker is not None:
            self._tx_worker.stop()
        if self._tx_thread is not None:
            self._tx_thread.quit()
            self._tx_thread.wait(2000)
        if self._tx_backend is not None:
            self._tx_backend.close()
        self._tx_backend = None
        self._tx_worker = None
        self._tx_thread = None
        # The port is free again (whether or not this was a Hamlib CAT send
        # -- resuming when it was never paused is a harmless no-op) -- see
        # _build_hamlib_tx_backend's own pause().
        self._freq_monitor_resume.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._stop_audio_pipeline()
        self._stop_tx_pipeline()
        self._freq_monitor_stop.emit()
        self._freq_monitor_thread.quit()
        self._freq_monitor_thread.wait(2000)

        # Persist current UI state back into the config before closing.
        self.config.target_pitch_hz = self.rx_panel.pitch_spinbox.value()
        self.config.tx_speed_wpm = self.tx_panel.speed_spinbox.value()
        self.config.manual_keying_emulation = self.tx_panel.manual_keying_checkbox.isChecked()
        self.config.manual_keying_intensity = self.tx_panel.manual_keying_slider.value()
        self.config.save()
        super().closeEvent(event)
