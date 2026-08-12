"""Unit tests for cwrobot.macros: {VAR} substitution and variable
assembly. Pure logic, no Qt involved."""

from __future__ import annotations

from cwrobot.config import AppConfig
from cwrobot.macros import (
    ALL_VARIABLES,
    DEFAULT_MACROS,
    OWN_VARIABLES,
    QSO_VARIABLES,
    build_variables,
    get_macro_text,
    resolve_macro_text,
    set_macro_text,
)


def test_resolve_substitutes_known_variables():
    result = resolve_macro_text("DE {MY_CALLSIGN} K", {"MY_CALLSIGN": "HA1ABC"})
    assert result == "DE HA1ABC K"


def test_resolve_multiple_and_repeated_variables():
    result = resolve_macro_text(
        "{CALLSIGN} DE {MY_CALLSIGN} {MY_CALLSIGN}",
        {"CALLSIGN": "DL1XYZ", "MY_CALLSIGN": "HA1ABC"},
    )
    assert result == "DL1XYZ DE HA1ABC HA1ABC"


def test_resolve_known_variable_with_empty_value_becomes_empty_string():
    result = resolve_macro_text("UR RST {RST_SENT} K", {"RST_SENT": ""})
    assert result == "UR RST  K"


def test_resolve_unknown_token_is_left_untouched():
    # A typo (or a stray "{X}" someone typed on purpose) must stay visible
    # rather than silently disappearing from the sent text.
    result = resolve_macro_text("DE {MY_CALSIGN} K", {"MY_CALLSIGN": "HA1ABC"})
    assert result == "DE {MY_CALSIGN} K"


def test_resolve_is_case_insensitive_on_the_token():
    result = resolve_macro_text("de {my_callsign} k", {"MY_CALLSIGN": "HA1ABC"})
    assert result == "de HA1ABC k"


def test_resolve_text_without_any_variables_is_unchanged():
    assert resolve_macro_text("TNX 73", {"MY_CALLSIGN": "HA1ABC"}) == "TNX 73"


def test_build_variables_maps_own_and_qso_data():
    config = AppConfig(
        operator_callsign="HA1ABC",
        operator_name="Elek",
        operator_locator="JN86ii",
        operator_qth="Budapest",
    )
    qso_field_values = {
        "callsign": "DL1XYZ",
        "rst_sent": "599",
        "rst_rcvd": "579",
        "qth": "Berlin",
        "locator": "JO62",
        "name": "Hans",
    }
    variables = build_variables(config, qso_field_values)
    assert variables == {
        "MY_CALLSIGN": "HA1ABC",
        "MY_NAME": "Elek",
        "MY_LOCATOR": "JN86ii",
        "MY_QTH": "Budapest",
        "CALLSIGN": "DL1XYZ",
        "RST_SENT": "599",
        "RST_RCVD": "579",
        "QTH": "Berlin",
        "LOCATOR": "JO62",
        "NAME": "Hans",
    }


def test_build_variables_tolerates_missing_qso_fields():
    config = AppConfig()
    variables = build_variables(config, {})
    assert variables["CALLSIGN"] == ""
    assert variables["MY_CALLSIGN"] == ""


def test_every_default_macro_only_references_known_variables():
    known = {token for token, _ in ALL_VARIABLES}
    for macro in DEFAULT_MACROS:
        resolved = resolve_macro_text(macro.default_text, dict.fromkeys(known, "X"))
        assert "{" not in resolved, f"{macro.key} references an unknown variable: {macro.default_text!r}"


def test_all_variables_is_own_then_qso_with_no_overlap():
    assert ALL_VARIABLES == OWN_VARIABLES + QSO_VARIABLES
    own_tokens = {token for token, _ in OWN_VARIABLES}
    qso_tokens = {token for token, _ in QSO_VARIABLES}
    assert own_tokens.isdisjoint(qso_tokens)


def test_get_macro_text_falls_back_to_default_when_unconfigured():
    config = AppConfig()
    macro = DEFAULT_MACROS[0]
    assert get_macro_text(config, macro) == macro.default_text


def test_set_then_get_macro_text_returns_override():
    config = AppConfig()
    macro = DEFAULT_MACROS[0]
    set_macro_text(config, macro.key, "CUSTOM TEXT")
    assert get_macro_text(config, macro) == "CUSTOM TEXT"
    # Other macros stay on their defaults.
    other = DEFAULT_MACROS[1]
    assert get_macro_text(config, other) == other.default_text


def test_macro_keys_are_unique():
    keys = [macro.key for macro in DEFAULT_MACROS]
    assert len(keys) == len(set(keys))


def test_eight_default_macros_match_the_requested_set():
    labels = [macro.label for macro in DEFAULT_MACROS]
    assert labels == [
        "CQ short",
        "CQ long",
        "Reply to CQ short",
        "Reply to CQ long",
        "Report short",
        "Report long",
        "Bye short",
        "Bye long",
    ]
