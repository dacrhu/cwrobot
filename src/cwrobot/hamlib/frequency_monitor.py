"""FrequencyMonitor: a QObject worker, run on a dedicated QThread, that
polls a Hamlib-connected rig's VFO frequency once a second for
ui.qso_panel's frequency field.

Runs entirely off the GUI thread on purpose: `HamlibRig.open()`/`get_freq()`
are ordinary blocking calls (Hamlib's own retry/timeout logic can take
several seconds *per attempt* when the rig doesn't answer -- e.g. it's
simply powered off), and this poll would otherwise retry once a second,
forever, each time blocking the entire UI for that long -- which is exactly
what running it straight from a QTimer on MainWindow did (a real freeze
report: "if the rig is off and Hamlib is configured, the whole app locks
up").

MainWindow talks to this instance only via signal/slot connections (see its
own _freq_monitor_* signals): once moveToThread has parked this object on
its own thread, calling its methods *directly* from the GUI thread would
just run them synchronously *on the GUI thread* (defeating the whole point)
and race the timer callback over `self._rig`. Going through Qt's
signal/slot mechanism is what makes each request auto-queue onto the
worker thread's own event loop instead -- which is also exactly why run()
below sets up a QTimer and returns immediately rather than looping with
time.sleep() like decoder.CwDecoder's worker does: a thread stuck in a
blocking Python while-loop never actually runs its own Qt event loop, so
nothing queued for it (set_target/pause/resume/stop) would ever be
dispatched. A QTimer's callback still runs synchronously (so a hung
get_freq() call blocks *this* thread until it returns/errors), but that's
fine and expected here -- it's a dedicated background thread, and blocking
it doesn't touch the GUI thread at all, which is the actual goal.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from cwrobot.hamlib.ctypes_bindings import HamlibUnavailableError
from cwrobot.hamlib.rig_client import HamlibError, HamlibRig

POLL_INTERVAL_MS = 1000

# Extra settle time after closing our rig handle, before the caller (see
# pause()) is allowed to proceed to open a *new* one on the same port.
# rig_close() returning doesn't necessarily mean Hamlib's own internal
# state for that port has fully quiesced -- newer Hamlib versions run
# morse sending through an internal async handler thread (per-rig
# "morse_busy" state in Hamlib's own src/rig.c), and a rapid close-then-
# reopen cycle on the same port can still trip RIG_BUSBUSY ("collision on
# the bus") if that internal teardown hasn't caught up yet -- even though,
# from our own Python/Qt side, the two connections are already correctly
# serialized (see main_window's BlockingQueuedConnection pause/resume).
# Confirmed against a real rig/AppImage build that 0.2s alone wasn't
# enough -- the very first command or two issued right after the *new*
# handle's own rig_open() could still trip RIG_BUSBUSY, well past this
# delay. rig_client's own retry budget (see _retry_on_bus_collision) is
# what actually absorbs that startup race now; this settle delay just
# gives the old handle's teardown a head start so retry doesn't have to
# do all the work.
_PORT_SETTLE_S = 0.3


class FrequencyMonitor(QObject):
    frequency_changed = Signal(float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # `_paused` tracks *only* "a TX send currently owns the port" (see
        # pause()/resume()) -- whether there's anything configured to poll
        # at all is `_model_id`'s job (see set_target), independently of
        # this, so starting unpaused here is correct: with no target set
        # yet, _poll() no-ops on the `_model_id is None` check regardless.
        self._paused = False
        self._model_id: int | None = None
        self._port_path = ""
        self._baud_rate: int | None = None
        self._rig: HamlibRig | None = None
        self._timer: QTimer | None = None

    # -- Slots, meant to be driven only via queued signal connections from
    # the GUI thread (see module docstring) -- never called directly. --

    def set_target(self, model_id: int | None, port_path: str, baud_rate: int | None) -> None:
        """`model_id=None` means "nothing configured to poll" (e.g. the TX
        backend isn't Hamlib CAT, or no rig model is set yet)."""
        self._close_rig()
        self._model_id = model_id
        self._port_path = port_path
        self._baud_rate = baud_rate

    def pause(self) -> None:
        """Called right before a TX send opens its own Hamlib connection --
        see main_window's own comment on why the two must never be open on
        the port at once. Runs via a BlockingQueuedConnection, so the
        caller (on the GUI thread) doesn't proceed to its own open() until
        this -- including the settle delay below -- has actually finished."""
        self._paused = True
        had_rig = self._rig is not None
        self._close_rig()
        if had_rig:
            # Only worth the wait if there was an actual connection to
            # settle from -- a pause() with nothing open yet (e.g. the
            # very first send of the session) has nothing to wait out.
            time.sleep(_PORT_SETTLE_S)

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._close_rig()

    def run(self) -> None:
        """Starts polling; intended to run via `QThread.started.connect`
        (after `moveToThread`). Deliberately non-blocking -- see module
        docstring for why a QTimer, not a while-loop, is what makes the
        other slots above actually reachable at all."""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_INTERVAL_MS)

    def _poll(self) -> None:
        if self._paused or self._model_id is None:
            return

        if self._rig is None:
            rig = HamlibRig(model_id=self._model_id, port_path=self._port_path, baud_rate=self._baud_rate)
            try:
                rig.open()
            except (HamlibError, HamlibUnavailableError):
                return
            self._rig = rig

        try:
            freq_hz = self._rig.get_freq()
        except HamlibError:
            # The rig went away mid-session (powered off, unplugged, ...) --
            # drop the connection so the next poll attempts a fresh
            # reconnect rather than keep hammering a dead one.
            self._close_rig()
            return
        self.frequency_changed.emit(freq_hz)

    def _close_rig(self) -> None:
        if self._rig is not None:
            self._rig.close()
        self._rig = None
