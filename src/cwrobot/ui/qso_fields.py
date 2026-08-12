"""Shared definition of the QSO data fields (see ui.qso_panel).

A tiny, dependency-free module of its own so both QsoPanel (which owns the
actual input widgets) and RxPanel (which offers them as selection-popup
destinations, see ui.selection_popup) can agree on the same set of fields
in the same order without importing each other's UI internals.

Field keys deliberately match models.qso.QsoRecord's attribute names where
they overlap (callsign, rst_sent, rst_rcvd) plus qth/locator/name -- so a
future QsoLogger integration can build a QsoRecord straight from these
values by key, with no renaming step.
"""

from __future__ import annotations

QSO_FIELDS: list[tuple[str, str]] = [
    ("callsign", "Callsign"),
    ("rst_sent", "RSTs"),
    ("rst_rcvd", "RSTr"),
    ("qth", "QTH"),
    ("locator", "Locator"),
    ("name", "Name"),
]
