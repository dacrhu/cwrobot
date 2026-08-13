# Using CW Robot

Quick reference for the main window's controls. For install/build
instructions, see [README.md](README.md).

## Receive (RX panel)

- Waterfall + decoded text pane at the top. Click a spot on the waterfall
  to tune the decoder there.
- **Pitch (Hz)** — set to match your radio's own sidetone frequency.
- **Bandwidth** — how wide a slice around the pitch the decoder listens
  to; **Automatic** lets it track the signal instead of a fixed width.
- **Squelch sensitivity** — how strong a signal has to be before it's
  decoded; the dot next to it lights up while the squelch is open.
- **RX speed** shows the sender's live estimated WPM.
- Select any decoded text to pop up **Callsign / RSTs / RSTr / QTH /
  Locator / Name** buttons — one click sends the selection straight into
  the matching QSO Data field below.
- Right-click the decoded text for **Clear**.

## QSO Data

Callsign, RSTs/RSTr, QTH, Locator, Name, Frequency, Start/End time for the
contact in progress. Fill by typing, or via the RX selection popup above.
Locator is always forced to upper case.

- **Start** is captured automatically the moment Callsign is first filled
  in; **End** keeps ticking to "now" until you log the QSO. Both are UTC
  (per ADIF convention), not your local time zone.
- **Frequency** — when Hamlib CAT is connected, the current VFO frequency
  is polled once a second and the field becomes read-only; otherwise type
  it in by hand.
- **Log QSO** writes the contact out (file or UDP — see Settings →
  Logging) and clears the panel for the next one. Missing required fields
  (Callsign, Frequency) are highlighted in red instead of silently
  failing.

## Transmit (TX panel)

- **Quick Buttons** — eight macro buttons (CQ, reply to CQ, report, bye —
  short/long variants) that fill in `{MY_CALLSIGN}`, `{CALLSIGN}`,
  `{RST_SENT}`, etc. from your own data (Settings → My Data) and the
  current QSO Data fields. Right-click a button to edit its text or see
  the full variable list.
- Type or edit the text box directly, then **Send**; **■ Stop** aborts
  mid-transmission.
- **Speed (WPM)** sets sending speed; **⇄ RX speed** copies the RX panel's
  currently measured speed over.
- **Manual keying emulation** adds hand-sent-style timing jitter (slider
  controls how much); only available when a Hamlib rig with a working CAT
  connection is configured (see Settings → TX) — the checkbox explains why
  it's disabled otherwise via its tooltip.
- Right-click the text box for **Clear**.
- TX goes out either as an audio sidetone or as Hamlib CAT keying,
  whichever is chosen in Settings → TX.

## Settings (Settings menu → Settings…)

- **My Data** — your own callsign/name/locator/QTH, used by the macro
  variables above.
- **Audio Devices** — input/output device selection.
- **TX** — audio vs. Hamlib CAT mode, rig model/port/baud rate, and a
  connection test button.
- **Logging** — how **Log QSO** delivers contacts: to a local ADIF file
  (one file per your callsign) or over UDP, either as raw ADIF (Log4OM-
  style) or the WSJT-X protocol (QLog, JTAlert, GridTracker, ...).
