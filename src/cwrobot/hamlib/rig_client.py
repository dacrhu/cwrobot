"""Pythonic Hamlib rig control, built on top of ctypes_bindings's raw
declarations.

`HamlibRig` owns one rig handle's lifecycle (open/close) and the handful of
operations cwrobot needs (CW keying, PTT, keyer speed). `list_rig_models`
enumerates every backend Hamlib was built with, for a Settings-dialog combo
box.
"""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass

import serial

from cwrobot.hamlib.ctypes_bindings import (
    RIG_CAPS_MFG_NAME_CPTR,
    RIG_CAPS_MODEL_NAME_CPTR,
    RIG_EIO,
    RIG_LEVEL_KEYSPD,
    RIG_LIST_FOREACH_CB,
    RIG_MODE_CW,
    RIG_OK,
    RIG_PASSBAND_NORMAL,
    RIG_PTT_OFF,
    RIG_PTT_ON,
    RIG_VFO_CURR,
    HamlibUnavailableError,
    ValueT,
    _RigCapsHead,
    get_library,
)

__all__ = [
    "HamlibError",
    "HamlibUnavailableError",
    "RigModel",
    "HamlibRig",
    "list_rig_models",
]

logger = logging.getLogger(__name__)

# Short, operator-facing messages for the Hamlib error codes we're actually
# likely to hit (see enum rig_errcode_e in rig.h). Deliberately not calling
# rigerror() for these -- with debug logging off its messages are fine, but
# relying on it means every string is only as good as whatever a given
# backend author wrote, and in some Hamlib builds it can return far more
# (leftover trace output) than a one-line status message should show.
_ERROR_MESSAGES: dict[int, str] = {
    1: "Invalid parameter",
    2: "Bad port/serial configuration",
    3: "Out of memory",
    4: "Function not implemented in this Hamlib version",
    5: "Timed out communicating with the rig",
    6: "IO error -- check the port path and permissions",
    7: "Internal Hamlib error",
    8: "Protocol error communicating with the rig",
    9: "Rig rejected the command",
    10: "Command performed, but the response was truncated",
    11: "This rig model/backend doesn't support this function",
    12: "VFO not targetable on this rig",
    13: "Error talking on the CAT bus",
    14: "Collision on the CAT bus -- another command was in flight at the "
    "same time (e.g. the frequency poll and a TX send overlapped)",
    15: "Invalid internal parameter",
    16: "Invalid VFO",
    17: "Parameter out of range",
    18: "Function deprecated in this Hamlib version",
    19: "Security error",
    20: "Rig is not powered on",
    21: "Limit exceeded",
    22: "Port already in use",
}


def _error_message(code: int) -> str:
    return _ERROR_MESSAGES.get(-code, f"Hamlib error ({code})")


def _diagnose_port_open_failure(port_path: str) -> str | None:
    """Best-effort extra detail for a RIG_EIO failure. Hamlib's own message
    for that code is a deliberately generic catch-all ("IO error, including
    open failed") covering everything from a nonexistent device to a
    permissions problem to another process already holding the port open --
    on a stock Fedora/most modern desktop Linux setups, ModemManager
    auto-probing newly-plugged USB-serial adapters as potential cellular
    modems is a very common culprit, and looks identical to a permissions
    issue from Hamlib's side. A quick pyserial probe of the same path
    surfaces the OS's own errno/message, which is a much more actionable
    diagnostic than Hamlib's wording alone -- exactly the same open() any
    other serial-aware program on the system would be making, so if this
    fails the same way something else would too. Returns None if the probe
    itself doesn't fail (nothing extra to add -- the real cause was
    something Hamlib-internal, not the OS-level open) or if pyserial raises
    something unexpected (never let a diagnostic aid crash/mask the real
    error)."""
    if not port_path:
        return None
    try:
        probe = serial.Serial(port_path, timeout=0)
    except Exception as exc:
        return str(exc)
    probe.close()
    return None


class HamlibError(RuntimeError):
    """A Hamlib call returned an error code. `.code` is the raw (negative)
    Hamlib return value, for callers that want to branch on it. `detail`,
    when given, is appended in parens -- e.g. the extra OS-level reason
    _diagnose_port_open_failure dug up for a RIG_EIO."""

    def __init__(self, action: str, code: int, detail: str | None = None) -> None:
        self.code = code
        message = f"{action}: {_error_message(code)}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


@dataclass(frozen=True)
class RigModel:
    model_id: int
    mfg_name: str
    model_name: str

    def __str__(self) -> str:
        return f"{self.mfg_name} {self.model_name}".strip()


_model_cache: list[RigModel] | None = None


def list_rig_models(force_refresh: bool = False) -> list[RigModel]:
    """Every rig model Hamlib was built with, sorted by manufacturer then
    model name. Cached in-process after the first call (rig_list_foreach
    walks ~300 backends -- cheap in absolute terms, but no reason to redo it
    every time a Settings dialog opens); pass force_refresh=True to bypass.

    Raises HamlibUnavailableError if libhamlib.so isn't present.
    """
    global _model_cache
    if _model_cache is not None and not force_refresh:
        return _model_cache

    lib = get_library()
    lib.rig_load_all_backends()

    models: list[RigModel] = []

    def _collect(caps_ptr: int, _data: int) -> int:
        head = ctypes.cast(caps_ptr, ctypes.POINTER(_RigCapsHead)).contents
        model_id = head.rig_model
        mfg = lib.rig_get_caps_cptr(model_id, RIG_CAPS_MFG_NAME_CPTR)
        name = lib.rig_get_caps_cptr(model_id, RIG_CAPS_MODEL_NAME_CPTR)
        models.append(
            RigModel(
                model_id=model_id,
                mfg_name=(mfg or b"").decode("utf-8", errors="replace"),
                model_name=(name or b"").decode("utf-8", errors="replace"),
            )
        )
        return 1  # non-zero: keep iterating

    callback = RIG_LIST_FOREACH_CB(_collect)
    lib.rig_list_foreach(callback, None)

    models.sort(key=lambda m: (m.mfg_name.lower(), m.model_name.lower()))
    _model_cache = models
    return models


class HamlibRig:
    """Owns one `RIG *` handle. Not thread-safe by itself -- like
    AudioTxBackend/SidetonePlayback, callers keep it to a single worker
    thread at a time (see tx.hamlib_backend.HamlibTxBackend)."""

    def __init__(self, model_id: int, port_path: str, baud_rate: int | None = None) -> None:
        self._model_id = model_id
        self._port_path = port_path
        self._baud_rate = baud_rate
        self._handle: ctypes.c_void_p | None = None

    def open(self) -> None:
        lib = get_library()
        handle = lib.rig_init(self._model_id)
        if not handle:
            raise HamlibError(
                f"Unknown rig model (id={self._model_id})", -1
            )
        self._handle = handle

        self._set_conf("rig_pathname", self._port_path)
        if self._baud_rate:
            # Best-effort: backends without a serial port (e.g. network
            # rigs) simply won't offer this token -- _set_conf no-ops then,
            # which is correct, not an error.
            self._set_conf("serial_speed", str(self._baud_rate))

        ret = lib.rig_open(self._handle)
        if ret != RIG_OK:
            self._cleanup_handle()
            detail = _diagnose_port_open_failure(self._port_path) if ret == -RIG_EIO else None
            raise HamlibError(f"Could not open the rig ({self._port_path})", ret, detail)

        # CW keying via rig_send_morse requires the rig to actually be in
        # CW mode -- confirmed against a live Hamlib dummy rig, where
        # rig_send_morse fails with "rig is in mode FM, not in CW/CWR mode"
        # otherwise. Since this app exists to send CW, switching the rig
        # into CW mode on connect is the expected behavior here (the same
        # thing e.g. fldigi/CQRLOG do), not a surprising side effect.
        ret = lib.rig_set_mode(self._handle, RIG_VFO_CURR, RIG_MODE_CW, RIG_PASSBAND_NORMAL)
        if ret != RIG_OK:
            # Non-fatal: some backends/rigs don't support rig_set_mode over
            # CAT for the current VFO, or are already in CW -- log and let
            # the actual rig_send_morse call be the authoritative check.
            logger.warning("rig_set_mode(CW) failed (%s), continuing anyway", _error_message(ret))

    def close(self) -> None:
        if self._handle is None:
            return
        lib = get_library()
        lib.rig_close(self._handle)
        self._cleanup_handle()

    def _cleanup_handle(self) -> None:
        if self._handle is None:
            return
        get_library().rig_cleanup(self._handle)
        self._handle = None

    def _set_conf(self, token_name: str, value: str) -> bool:
        assert self._handle is not None
        lib = get_library()
        token = lib.rig_token_lookup(self._handle, token_name.encode("ascii"))
        if not token:
            return False
        ret = lib.rig_set_conf(self._handle, token, value.encode("utf-8"))
        return ret == RIG_OK

    def set_keyer_speed(self, wpm: float) -> None:
        assert self._handle is not None
        lib = get_library()
        ret = lib.rig_set_level(self._handle, RIG_VFO_CURR, RIG_LEVEL_KEYSPD, ValueT(i=round(wpm)))
        if ret != RIG_OK:
            raise HamlibError("Could not set the TX speed on the rig", ret)

    def send_morse(self, text: str) -> None:
        """Queues `text` on the rig's own internal keyer (paced by whatever
        speed set_keyer_speed last configured); returns as soon as it's
        queued, not once sending is done -- see tx.hamlib_backend for how
        completion/Stop is tracked."""
        assert self._handle is not None
        lib = get_library()
        ret = lib.rig_send_morse(self._handle, RIG_VFO_CURR, text.encode("utf-8"))
        if ret != RIG_OK:
            raise HamlibError("Could not send the text to the rig's CW keyer", ret)

    def wait_morse(self) -> bool:
        """Best-effort call into `rig_wait_morse` (blocks until the rig's
        keyer queue is flushed, on backends that implement it). Not used by
        HamlibTxBackend's main send/Stop path -- verified against a live
        Hamlib dummy rig that this can return -RIG_ENAVAIL immediately on
        backends that don't implement it at all, which would make it useless
        as the sole way to detect "done" or wait out; tx.hamlib_backend uses
        its own local timing estimate plus rig_stop_morse for that instead.
        Kept as a real, callable wrapper (rather than omitted) since some
        backends do implement it usefully. Returns True if Hamlib reports
        the wait actually completed, False otherwise (logged, not raised)."""
        if self._handle is None:
            return False
        ret = get_library().rig_wait_morse(self._handle, RIG_VFO_CURR)
        if ret != RIG_OK:
            logger.debug("rig_wait_morse: %s", _error_message(ret))
            return False
        return True

    def stop_morse(self) -> None:
        """Best-effort: interrupts whatever send_morse queued. Errors are
        logged, not raised -- this is itself typically called while already
        handling a Stop request, so it shouldn't surface a second failure on
        top of that."""
        if self._handle is None:
            return
        ret = get_library().rig_stop_morse(self._handle, RIG_VFO_CURR)
        if ret != RIG_OK:
            logger.warning("rig_stop_morse failed: %s", _error_message(ret))

    def set_ptt(self, is_on: bool) -> None:
        assert self._handle is not None
        lib = get_library()
        ret = lib.rig_set_ptt(self._handle, RIG_VFO_CURR, RIG_PTT_ON if is_on else RIG_PTT_OFF)
        if ret != RIG_OK:
            raise HamlibError("Could not control PTT", ret)

    def get_freq(self) -> float:
        """The current VFO frequency in Hz, queried live from the rig."""
        assert self._handle is not None
        lib = get_library()
        freq = ctypes.c_double()
        ret = lib.rig_get_freq(self._handle, RIG_VFO_CURR, ctypes.byref(freq))
        if ret != RIG_OK:
            raise HamlibError("Could not read the frequency from the rig", ret)
        return freq.value

    def __enter__(self) -> "HamlibRig":
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
