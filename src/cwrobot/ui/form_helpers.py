"""Small layout-composition helpers shared by RxPanel/TxPanel's settings
columns (and MacroBar's caption) -- a uniform 'caption label above its
control' block, replacing QFormLayout's label-beside-control pattern.

QFormLayout computes ONE shared label-column width across every row in the
form; once the form's natural width exceeds whatever container it's in, Qt
compresses that shared column toward its *narrowest* row's needs and every
wider label -- which has no word-wrap/elide set -- gets silently clipped at
paint time. Stacking caption above control sidesteps the shared-column
concept entirely: each block's width is governed by its own control, and
the caption (word-wrapped as a defensive fallback) never competes with any
other row for space. Echoes QsoPanel's own row-0-label/row-1-field shape
(ui/qso_panel.py), just stacked in a column instead of a grid.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QLayout, QVBoxLayout, QWidget

# Fixed width for RxPanel/TxPanel's settings columns. Chosen from the
# widest real content that has to fit without compression -- measured via
# sizeHint() under a +6pt font bump (double the +3pt bump that was already
# enough to reproduce the original clipping bug), back when the widest
# single element was a device-rescan button here (that control has since
# moved to the Settings dialog, see ui.settings_dialog). 320px leaves
# headroom beyond that tested worst case; if a future control needs more,
# widen this constant -- do not reintroduce a squeeze-prone cap.
SETTINGS_COLUMN_WIDTH = 320

# Vertical gap between a block's caption and its control (tight -- reads as
# one unit), and between consecutive blocks in a settings column (looser --
# reads as related-but-separate). Its own tier, between the panels'
# section-level layout.setSpacing(8) and column-level
# content_row.setSpacing(14): 8 < 10 < 14.
FIELD_LABEL_SPACING = 2
FIELD_BLOCK_SPACING = 10


def labeled_field(label_text: str, field: QWidget | QLayout, parent: QWidget) -> QVBoxLayout:
    """A caption QLabel stacked directly above `field` (a widget, or a
    layout such as a QHBoxLayout pairing a slider with its readout label).
    `parent` is the owning panel (matches make_separator()'s convention in
    style.py), not `field` itself."""
    caption = QLabel(label_text, parent)
    caption.setWordWrap(True)
    caption.setToolTip(label_text)  # defense in depth if it ever *does* wrap
    caption.setProperty("role", "fieldLabel")

    block = QVBoxLayout()
    block.setSpacing(FIELD_LABEL_SPACING)
    block.addWidget(caption)
    if isinstance(field, QLayout):
        block.addLayout(field)
    else:
        block.addWidget(field)
    return block
