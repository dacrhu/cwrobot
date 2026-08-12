"""TX quick-button macros: template definitions, the {VARIABLE}
substitution engine, and the variable catalog resolved from the operator's
own data (config.AppConfig) and the current QSO's data (ui.qso_panel).

Kept Qt-free and independent of any specific widget -- mirrors the
decoder/encoder split used elsewhere in this codebase -- so it's directly
unit-testable and so ui.macro_bar / ui.macro_editor_dialog stay thin Qt
shells around it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cwrobot.config import AppConfig

# Each entry: (variable token without braces, human-readable description
# for the macro editor's variable list). "Own" variables come from the
# operator's own config.AppConfig.operator_* fields (see ui.settings_dialog);
# "QSO" variables come from the current contact's ui.qso_panel.QsoPanel
# fields (see ui.qso_fields.QSO_FIELDS) at the moment a macro is activated.
OWN_VARIABLES: list[tuple[str, str]] = [
    ("MY_CALLSIGN", "My callsign"),
    ("MY_NAME", "My name"),
    ("MY_LOCATOR", "My locator"),
    ("MY_QTH", "My QTH"),
]

QSO_VARIABLES: list[tuple[str, str]] = [
    ("CALLSIGN", "QSO callsign"),
    ("RST_SENT", "Sent report (RSTs)"),
    ("RST_RCVD", "Received report (RSTr)"),
    ("QTH", "QSO QTH"),
    ("LOCATOR", "QSO locator"),
    ("NAME", "QSO name"),
]

ALL_VARIABLES: list[tuple[str, str]] = OWN_VARIABLES + QSO_VARIABLES

# Case-insensitive: resolve_macro_text below normalizes to upper case before
# lookup, so a hand-typed "{my_callsign}" still resolves -- only the display
# convention (in ALL_VARIABLES / the editor) is upper case.
_VAR_TOKEN_RE = re.compile(r"\{([A-Za-z_]+)\}")


@dataclass(frozen=True)
class MacroDefinition:
    key: str
    label: str
    default_text: str
    # Which of the 4 short/long pairs this belongs to -- ui.macro_bar uses
    # this (plus whether `key` ends in "_short"/"_long") to color-code the
    # quick-buttons: one hue per group, a lighter shade for "short" and a
    # darker one for "long", so the 8 buttons read as 4 related pairs
    # instead of one undifferentiated row.
    group: str


DEFAULT_MACROS: list[MacroDefinition] = [
    MacroDefinition("cq_short", "CQ short", "CQ CQ DE {MY_CALLSIGN} {MY_CALLSIGN} K", group="cq"),
    MacroDefinition(
        "cq_long",
        "CQ long",
        "CQ CQ CQ DE {MY_CALLSIGN} {MY_CALLSIGN} {MY_CALLSIGN} CQ DE {MY_CALLSIGN} K",
        group="cq",
    ),
    MacroDefinition(
        "reply_cq_short", "Reply to CQ short", "{CALLSIGN} DE {MY_CALLSIGN} K", group="reply_cq"
    ),
    MacroDefinition(
        "reply_cq_long",
        "Reply to CQ long",
        "{CALLSIGN} {CALLSIGN} DE {MY_CALLSIGN} {MY_CALLSIGN} GE TNX FER CALL "
        "UR RST {RST_SENT} {RST_SENT} NAME {MY_NAME} {MY_NAME} QTH {MY_QTH} {MY_QTH} K",
        group="reply_cq",
    ),
    MacroDefinition("report_short", "Report short", "UR RST {RST_SENT} K", group="report"),
    MacroDefinition(
        "report_long",
        "Report long",
        "UR RST {RST_SENT} {RST_SENT} NAME {MY_NAME} QTH {MY_QTH} BK",
        group="report",
    ),
    MacroDefinition(
        "bye_short", "Bye short", "TNX QSO 73 {CALLSIGN} DE {MY_CALLSIGN} SK", group="bye"
    ),
    MacroDefinition(
        "bye_long",
        "Bye long",
        "TNX FER NICE QSO {NAME} 73 AND GL {CALLSIGN} DE {MY_CALLSIGN} {MY_CALLSIGN} SK",
        group="bye",
    ),
]


def resolve_macro_text(template: str, variables: dict[str, str]) -> str:
    """Substitute {VAR} tokens with their current values.

    A *known* variable with no value yet (e.g. the QSO callsign field is
    still empty) resolves to an empty string; an *unknown* token (a typo,
    or a stray "{X}" the operator typed on purpose) is left untouched
    rather than silently dropped, so the mistake stays visible instead of
    quietly vanishing from the sent text.
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1).upper()
        if name in variables:
            return variables[name]
        return match.group(0)

    return _VAR_TOKEN_RE.sub(_replace, template)


def build_variables(config: AppConfig, qso_field_values: dict[str, str]) -> dict[str, str]:
    """Assemble the current {VAR: value} map. `qso_field_values` is keyed
    exactly like ui.qso_fields.QSO_FIELDS (callsign/rst_sent/rst_rcvd/qth/
    locator/name) -- callers typically pass QsoPanel.fields' current text,
    read fresh at macro-activation time so this never goes stale."""
    return {
        "MY_CALLSIGN": config.operator_callsign,
        "MY_NAME": config.operator_name,
        "MY_LOCATOR": config.operator_locator,
        "MY_QTH": config.operator_qth,
        "CALLSIGN": qso_field_values.get("callsign", ""),
        "RST_SENT": qso_field_values.get("rst_sent", ""),
        "RST_RCVD": qso_field_values.get("rst_rcvd", ""),
        "QTH": qso_field_values.get("qth", ""),
        "LOCATOR": qso_field_values.get("locator", ""),
        "NAME": qso_field_values.get("name", ""),
    }


def get_macro_text(config: AppConfig, macro: MacroDefinition) -> str:
    """The macro's current text: the operator's saved override if there is
    one, otherwise its built-in default."""
    return config.macro_texts.get(macro.key, macro.default_text)


def set_macro_text(config: AppConfig, macro_key: str, text: str) -> None:
    config.macro_texts[macro_key] = text
