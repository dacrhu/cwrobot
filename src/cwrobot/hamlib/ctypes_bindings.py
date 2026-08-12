"""Raw ctypes declarations for the Hamlib C API (libhamlib.so).

Design note -- why there's no struct-offset probing here (as an earlier
version of the plan called for): modern Hamlib (>= ~4.1, i.e. what Fedora
and every other current distro ships) exposes ABI-stable accessor functions
for everything this app needs to read out of Hamlib's internal structs:

  - `rig_get_caps_cptr(model_id, which)` returns a string field (model name,
    manufacturer, ...) straight out of `struct rig_caps` for any registered
    model -- no need to know that struct's field layout/offsets at all.
  - `rig_token_lookup` + `rig_set_conf` let the frontend port path
    ("rig_pathname") and serial speed ("serial_speed") be set by name,
    without touching `hamlib_port_t`'s layout.
  - `RIG *` and the `const struct rig_caps *` handed to the
    `rig_list_foreach` callback are otherwise treated as fully opaque
    (`c_void_p`) -- we never read or write into them directly. The one
    exception, reading `rig_model` out of the caps struct's *first* field in
    that callback, is safe because Hamlib's own header explicitly promises
    that field's position never moves ("Don't move or add fields around
    without bumping version numbers -- DLL/shared library replacement
    depends on order").

So the whole 6. Milestone C-probe/gcc/offset-cache subsystem originally
planned turns out to be unnecessary: this module only needs libhamlib.so at
*runtime* (no gcc, no hamlib-devel headers, no per-version cache file).
hamlib-devel is still handy for a developer reading rig.h, but nothing here
depends on it being installed.

See cwrobot.hamlib.rig_client for the Pythonic wrapper built on top of this;
nothing outside the hamlib package should import this module directly.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

logger = logging.getLogger(__name__)

# Sonames to try, in order, if ctypes.util.find_library("hamlib") comes up
# empty (it can, depending on how ldconfig's cache is set up) -- covers the
# current major version plus one either side so a future Hamlib 5 doesn't
# immediately break this.
_CANDIDATE_SONAMES = ["libhamlib.so.4", "libhamlib.so", "libhamlib.so.5", "libhamlib.so.3"]

RIG_OK = 0

# -- Constants mirrored from <hamlib/rig.h> (values are part of Hamlib's
# public, stable ABI -- these are #define's/enum ordinals, not struct
# offsets, so they don't share the fragility struct layouts would have). --
RIG_EIO = 6  # enum rig_errcode_e: "IO error, including open failed"
RIG_BUSBUSY = 14  # enum rig_errcode_e: "Collision on the bus" -- transient, worth retrying
RIG_MODEL_DUMMY = 1  # RIG_MAKE_MODEL(RIG_DUMMY=0, 1)
RIG_VFO_CURR = 1 << 29  # RIG_VFO_N(29)
RIG_PTT_OFF = 0
RIG_PTT_ON = 1
RIG_MODE_CW = 1 << 1  # CONSTANT_64BIT_FLAG(1)
RIG_PASSBAND_NORMAL = 0  # width sentinel: "use the mode's normal passband"
RIG_LEVEL_KEYSPD = 1 << 14  # CONSTANT_64BIT_FLAG(14): keyer speed, arg int (WPM)
RIG_DEBUG_NONE = 0

# enum rig_caps_cptr_e ordinals (only the two we use)
RIG_CAPS_MFG_NAME_CPTR = 1
RIG_CAPS_MODEL_NAME_CPTR = 2


class ValueT(ctypes.Union):
    """Mirrors `value_t` (a plain C union of int/unsigned/float/char* --
    small and stable, unlike the big structs this module otherwise avoids
    touching). Only `.i` (int args, e.g. RIG_LEVEL_KEYSPD) is used so far."""

    _fields_ = [
        ("i", ctypes.c_int),
        ("u", ctypes.c_uint),
        ("f", ctypes.c_float),
    ]


class _RigCapsHead(ctypes.Structure):
    """Just the first field of `struct rig_caps` (`rig_model_t rig_model`),
    for reading the model id out of the pointer rig_list_foreach's callback
    receives. See the module docstring for why this one field is safe to
    hardcode despite not probing the rest of the struct."""

    _fields_ = [("rig_model", ctypes.c_uint32)]


# int (*)(const struct rig_caps *, rig_ptr_t)
RIG_LIST_FOREACH_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


class HamlibUnavailableError(RuntimeError):
    """Raised when libhamlib.so can't be loaded at all. Callers should treat
    this as "CAT keying is off the table this session" and disable/hide the
    Hamlib TX option with the message here, while leaving audio TX and RX
    unaffected."""


def _load_library() -> ctypes.CDLL:
    name = ctypes.util.find_library("hamlib")
    names_to_try = [name] if name else []
    names_to_try += [n for n in _CANDIDATE_SONAMES if n not in names_to_try]

    last_error: OSError | None = None
    for soname in names_to_try:
        try:
            return ctypes.CDLL(soname)
        except OSError as exc:
            last_error = exc
            continue

    raise HamlibUnavailableError(
        "The Hamlib shared library (libhamlib.so) was not found on this "
        "system -- CAT TX is unavailable because of this. Audio TX and RX "
        "work fine regardless. "
        f"(Last error: {last_error})"
    )


def _configure(lib: ctypes.CDLL) -> ctypes.CDLL:
    """Set argtypes/restype for every entry point this app calls, and
    silence Hamlib's (very chatty by default) debug logging."""
    lib.rig_set_debug.argtypes = [ctypes.c_int]
    lib.rig_set_debug.restype = None
    lib.rig_set_debug(RIG_DEBUG_NONE)

    lib.rig_load_all_backends.argtypes = []
    lib.rig_load_all_backends.restype = ctypes.c_int

    lib.rig_init.argtypes = [ctypes.c_uint32]
    lib.rig_init.restype = ctypes.c_void_p

    lib.rig_open.argtypes = [ctypes.c_void_p]
    lib.rig_open.restype = ctypes.c_int

    lib.rig_close.argtypes = [ctypes.c_void_p]
    lib.rig_close.restype = ctypes.c_int

    lib.rig_cleanup.argtypes = [ctypes.c_void_p]
    lib.rig_cleanup.restype = ctypes.c_int

    lib.rig_set_mode.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_long]
    lib.rig_set_mode.restype = ctypes.c_int

    lib.rig_set_level.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint64, ValueT]
    lib.rig_set_level.restype = ctypes.c_int

    lib.rig_set_ptt.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
    lib.rig_set_ptt.restype = ctypes.c_int

    # freq_t is `double` (see <hamlib/rig.h>). rig_get_freq/rig_set_freq are
    # wrapped in a `#if BUILTINFUNC` macro upstream that -- only when that
    # constant is nonzero -- appends an extra `const char *` (calling
    # function name, for enhanced debug tracing) to the real exported
    # symbol's signature. Confirmed via the installed hamlib-devel headers
    # that this build defines `BUILTINFUNC 0` unconditionally, so the actual
    # libhamlib.so.4 export here is the plain 3-arg form.
    lib.rig_get_freq.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_double)]
    lib.rig_get_freq.restype = ctypes.c_int

    lib.rig_send_morse.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p]
    lib.rig_send_morse.restype = ctypes.c_int

    lib.rig_stop_morse.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.rig_stop_morse.restype = ctypes.c_int

    lib.rig_wait_morse.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.rig_wait_morse.restype = ctypes.c_int

    lib.rig_token_lookup.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.rig_token_lookup.restype = ctypes.c_long

    lib.rig_set_conf.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_char_p]
    lib.rig_set_conf.restype = ctypes.c_int

    lib.rig_list_foreach.argtypes = [RIG_LIST_FOREACH_CB, ctypes.c_void_p]
    lib.rig_list_foreach.restype = ctypes.c_int

    lib.rig_get_caps_cptr.argtypes = [ctypes.c_uint32, ctypes.c_int]
    lib.rig_get_caps_cptr.restype = ctypes.c_char_p

    lib.rigerror.argtypes = [ctypes.c_int]
    lib.rigerror.restype = ctypes.c_char_p

    return lib


_library: ctypes.CDLL | None = None
_load_failed: HamlibUnavailableError | None = None


def get_library() -> ctypes.CDLL:
    """Lazily load and configure libhamlib.so, caching both success and
    failure for the life of the process (there's no scenario where the
    library would appear/disappear mid-run, and retrying a failed dlopen on
    every call would just be noise)."""
    global _library, _load_failed
    if _library is not None:
        return _library
    if _load_failed is not None:
        raise _load_failed
    try:
        _library = _configure(_load_library())
    except HamlibUnavailableError as exc:
        _load_failed = exc
        raise
    return _library
