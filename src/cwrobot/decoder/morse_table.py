"""The single source of truth for Morse <-> character mapping.

Both the decoder (dot/dash sequence -> character) and the TX encoder
(character -> dot/dash sequence, milestone 5) use this table, so the two
directions can never drift apart.
"""

from __future__ import annotations

CHAR_TO_MORSE: dict[str, str] = {
    "A": ".-",
    "B": "-...",
    "C": "-.-.",
    "D": "-..",
    "E": ".",
    "F": "..-.",
    "G": "--.",
    "H": "....",
    "I": "..",
    "J": ".---",
    "K": "-.-",
    "L": ".-..",
    "M": "--",
    "N": "-.",
    "O": "---",
    "P": ".--.",
    "Q": "--.-",
    "R": ".-.",
    "S": "...",
    "T": "-",
    "U": "..-",
    "V": "...-",
    "W": ".--",
    "X": "-..-",
    "Y": "-.--",
    "Z": "--..",
    "0": "-----",
    "1": ".----",
    "2": "..---",
    "3": "...--",
    "4": "....-",
    "5": ".....",
    "6": "-....",
    "7": "--...",
    "8": "---..",
    "9": "----.",
    ".": ".-.-.-",
    ",": "--..--",
    "?": "..--..",
    "/": "-..-.",
    "=": "-...-",
    "+": ".-.-.",
    "-": "-....-",
    "(": "-.--.",
    ")": "-.--.-",
    ":": "---...",
    "'": ".----.",
    '"': ".-..-.",
    "@": ".--.-.",
}
# Note: CW prosigns (<AR>, <SK>, <BT>, ...) are deliberately not included --
# on the wire they share identical dot/dash patterns with ordinary
# characters/punctuation (e.g. <AR> == "+"), distinguished only by being
# keyed as one run-together unit. Disambiguating that is future scope, not
# needed for this version.

MORSE_TO_CHAR: dict[str, str] = {morse: char for char, morse in CHAR_TO_MORSE.items()}
