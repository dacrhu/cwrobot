"""App-wide QSS stylesheet: section colors and button roles.

Deliberately narrow in scope -- this does *not* reskin the whole app (no
QWidget/background overrides), only adds colored accents on top of whatever
base Qt/system theme is active (light or dark, see the screenshot in the
session this was written for, which happened to be dark): QGroupBox borders/
titles get one accent color per section (so "RX"/"QSO Data"/"TX" read
as clearly separate at a glance, not one running-together block), and a
handful of buttons get a color that matches their role (Send=green,
Stop=red, TX quick-buttons=teal, secondary/utility buttons=neutral outline).

Colors are from the "Flat UI Colors" palette (https://flatuicolors.com/), the
same family cwrobot already drew the squelch-open green (#2ecc71) and RX
progress highlight from -- extending an established, coherent palette rather
than inventing a new one.

Widgets opt in via objectName (one per panel -- see RxPanel/QsoPanel/TxPanel
__init__) or a `role`/`error` dynamic property (see MacroBar, the
"secondary"-role utility buttons in rx_panel.py/tx_panel.py/
settings_dialog.py, and QsoPanel's required-field highlight). Qt only
re-reads a property-based selector on the widget's next style *polish* --
for properties set once during __init__ (every case here except the
"error" one) that polish happens naturally the first time the widget is
shown, so nothing extra is needed; toggling one live afterwards (as
QsoPanel._set_error_highlight does) needs an explicit
`style().unpolish(widget); style().polish(widget)` to force that re-read.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QWidget


def make_separator(
    parent: QWidget | None = None, orientation: Qt.Orientation = Qt.Orientation.Horizontal
) -> QFrame:
    """A thin rule, used to visually break up a panel's own sub-sections
    (e.g. RxPanel's tuning/device/detection clusters, or TxPanel's
    Send/Stop-vs-settings-column split when `orientation` is Vertical)
    without the heaviness of a nested QGroupBox for each one. Styled via QSS
    (QFrame#separatorLine, see STYLESHEET) rather than the native sunken-
    bevel look -- that bevel is just two 1px highlight/shadow hairlines,
    which all but disappear against a dark theme; a single solid-colored
    line at the *same* thickness reads as a much clearer divider."""
    line = QFrame(parent)
    line.setObjectName("separatorLine")
    if orientation == Qt.Orientation.Horizontal:
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(3)  # matches the native Sunken-HLine height this replaces
    else:
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(3)
    return line


# Section accents (one per top-level QGroupBox).
_RX_ACCENT = "#3498db"  # Peter River -- cool blue, echoes the waterfall's own hues
_QSO_ACCENT = "#7f8c8d"  # Asbestos -- neutral, no urgency (it's just data entry)
_TX_ACCENT = "#e67e22"  # Carrot -- warm, reads as "active/transmit" against RX's cool blue

# Separators (ui.style.make_separator): a neutral tone deliberately lighter
# than the section accents above and _QSO_ACCENT's Asbestos, so a divider
# reads clearly against a dark theme without competing with any section's
# own color language.
_SEPARATOR_COLOR = "#95a5a6"  # Concrete

# Button roles.
_SEND_COLOR = "#2ecc71"  # Emerald -- matches the pre-existing squelch-open green
_SEND_HOVER = "#27ae60"  # Nephritis
_STOP_COLOR = "#e74c3c"  # Alizarin -- universal stop/danger
_STOP_HOVER = "#c0392b"  # Pomegranate
_DISABLED_BG = "#7f8c8d"
_DISABLED_FG = "#dcdcdc"

# MacroBar quick-buttons: one hue per group (cq / reply_cq / report / bye),
# "short" in a light tint and "long" in a markedly darker shade of the same
# hue. Each pair is Flat UI's own official two-tone pairing (e.g. Turquoise/
# Green Sea) -- a first attempt widened that gap to stay more distinct, but
# it read as two unrelated colors rather than a "short/long" pair, so this
# went back to the official, tighter pairing (the same gap Send/Stop's own
# Alizarin/Pomegranate already uses). "bye" was the odd one out entirely --
# a custom slate/near-black navy that didn't belong to this palette family
# at all -- swapped for the official Wet Asphalt/Midnight Blue pair so it's
# built from the same rule as the other three.
_MACRO_GROUPS: dict[str, dict[str, str]] = {
    "cq": {"light": "#1abc9c", "dark": "#107c66", "fg": "#ffffff"},  # Turquoise / Green Sea
    "reply_cq": {"light": "#a95fc7", "dark": "#7b3b97", "fg": "#ffffff"},  # Amethyst / Wisteria
    "report": {"light": "#f1c40f", "dark": "#f39c12", "fg": "#2c3e50"},  # Sun Flower / Orange
    "bye": {"light": "#5f6fff", "dark": "#3542b9", "fg": "#ffffff"},  # Wet Asphalt / Midnight Blue
}

def _macro_button_rules() -> str:
    """One QSS rule per (group, shade) combination in _MACRO_GROUPS --
    generated rather than spelled out by hand so adding a 5th macro group
    later is a one-line change in _MACRO_GROUPS, not an easy-to-forget
    fourth place to edit. Hover feedback is a shared rule (see STYLESHEET,
    a light border rather than a background swap) so it doesn't need a
    third hand-picked shade per group just for hovering."""
    rules = []
    for group, shades in _MACRO_GROUPS.items():
        for shade in ("light", "dark"):
            selector = f'QPushButton[macroGroup="{group}"][macroShade="{shade}"]'
            rules.append(f"{selector} {{ background-color: {shades[shade]}; color: {shades['fg']}; }}")
    return "\n".join(rules)


STYLESHEET = f"""
/* ---- Section group boxes: colored border + bold title ---- */
QGroupBox {{
    font-weight: 600;
    border: 1px solid palette(mid);
    border-radius: 6px;
    margin-top: 1.4em;
    padding-top: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
}}

QGroupBox#rxPanel {{ border-color: {_RX_ACCENT}; }}
QGroupBox#rxPanel::title {{ color: {_RX_ACCENT}; }}

QGroupBox#qsoPanel {{ border-color: {_QSO_ACCENT}; }}
QGroupBox#qsoPanel::title {{ color: {_QSO_ACCENT}; }}

QGroupBox#txPanel {{ border-color: {_TX_ACCENT}; }}
QGroupBox#txPanel::title {{ color: {_TX_ACCENT}; }}

/* ---- Sub-section separators (ui.style.make_separator) ---- */
QFrame#separatorLine {{
    background-color: {_SEPARATOR_COLOR};
    border: none;
}}

/* ---- Primary action buttons ---- */
QPushButton#sendButton {{
    background-color: {_SEND_COLOR};
    color: #ffffff;
    font-weight: 600;
    border: none;
    border-radius: 4px;
    padding: 6px 16px;
}}
QPushButton#sendButton:hover {{ background-color: {_SEND_HOVER}; }}
QPushButton#sendButton:disabled {{ background-color: {_DISABLED_BG}; color: {_DISABLED_FG}; }}

QPushButton#stopButton {{
    background-color: {_STOP_COLOR};
    color: #ffffff;
    font-weight: 600;
    border: none;
    border-radius: 4px;
    padding: 6px 16px;
}}
QPushButton#stopButton:hover {{ background-color: {_STOP_HOVER}; }}
QPushButton#stopButton:disabled {{ background-color: {_DISABLED_BG}; color: {_DISABLED_FG}; }}

/* ---- TX quick-buttons (MacroBar): one hue per group (cq/reply_cq/report/
   bye), light shade = short, dark shade = long -- see _MACRO_GROUPS. ---- */
QPushButton[role="macro"] {{
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px 10px;
}}
QPushButton[role="macro"]:hover {{ border-color: rgba(255, 255, 255, 160); }}
{_macro_button_rules()}

/* ---- Secondary/utility buttons (rescan, refresh, test connection, "match
   RX speed", ...): deliberately understated relative to Send/Stop/macro
   buttons, but still has to read as a *button* -- a transparent fill plus a
   palette(mid) border (the previous approach) turned out to blend almost
   completely into the panel on at least one real dark theme, the same
   failure mode palette(mid) already caused for field-label text (see
   below): it's meant for bezel/shadow work, not guaranteed contrast against
   a themed window background. palette(button) is the role Qt's own native
   buttons use for exactly this "look like a button" job, and the border
   reuses _SEPARATOR_COLOR, already verified visible against a dark theme. ---- */
QPushButton[role="secondary"] {{
    background-color: palette(button);
    border: 1px solid {_SEPARATOR_COLOR};
    border-radius: 4px;
    padding: 4px 10px;
}}
QPushButton[role="secondary"]:hover {{ background-color: palette(midlight); }}
QPushButton[role="secondary"]:disabled {{
    background-color: {_DISABLED_BG};
    border-color: {_DISABLED_BG};
    color: {_DISABLED_FG};
}}

/* ---- Settings-column/macro-bar caption labels (ui.form_helpers.labeled_field,
   MacroBar's own "Gyorsgombok" caption): a small muted line identifying the
   control below/beside it. placeholderText (not mid -- mid is meant for
   bezel/shadow work, and on at least one real dark theme it resolved to a
   near-black that was flatly illegible against a dark window background)
   is the palette role Qt itself already uses for exactly this "de-emphasized
   but still legible" job (e.g. QLineEdit's own placeholder text), so it's
   guaranteed to stay legible whichever theme (light or dark) is active. ---- */
QLabel[role="fieldLabel"] {{
    color: palette(placeholdertext);
    font-size: 0.92em;
}}

/* ---- Squelch open/closed indicator (ui.rx_panel.RxPanel): a fixed
   12x12 QLabel, rounded into a dot -- fill color is set per-state in
   set_squelch_open(). ---- */
QLabel#squelchStateDot {{
    border-radius: 6px;
}}

/* ---- Required-field validation highlight (ui.qso_panel.QsoPanel's "Log
   QSO" flow: an empty callsign/frequency field at click time gets this,
   cleared the moment the operator edits it -- see
   QsoPanel._set_error_highlight). Reuses Stop's own red (border + a faint
   matching tint) rather than a full block fill, so it reads as "fix this"
   without fighting the rest of the row for attention. ---- */
QLineEdit[error="true"] {{
    border: 1px solid {_STOP_COLOR};
    background-color: rgba(231, 76, 60, 35);
}}
"""
