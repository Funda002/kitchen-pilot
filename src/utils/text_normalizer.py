"""Text cleanup for speech sent to Rime."""

from __future__ import annotations

import re
import unicodedata


_FRACTION_DENOMINATORS = {
    2: ("half", "halves"),
    3: ("third", "thirds"),
    4: ("quarter", "quarters"),
    5: ("fifth", "fifths"),
    6: ("sixth", "sixths"),
    8: ("eighth", "eighths"),
    10: ("tenth", "tenths"),
    16: ("sixteenth", "sixteenths"),
}

_UNICODE_FRACTIONS = {
    "½": "1/2",
    "⅓": "1/3",
    "⅔": "2/3",
    "¼": "1/4",
    "¾": "3/4",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}

_UNITS = {
    "tsp": "teaspoon",
    "tsps": "teaspoons",
    "tbsp": "tablespoon",
    "tbsps": "tablespoons",
    "oz": "ounces",
    "lb": "pound",
    "lbs": "pounds",
    "g": "grams",
    "kg": "kilograms",
    "ml": "milliliters",
    "l": "liters",
    "min": "minutes",
    "mins": "minutes",
    "hr": "hour",
    "hrs": "hours",
}

_SMALL_NUMBERS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)


def _number_to_words(value: int) -> str:
    """Return an English cardinal for recipe-sized non-negative integers."""
    if value < 20:
        return _SMALL_NUMBERS[value]
    if value < 100:
        tens, remainder = divmod(value, 10)
        return _TENS[tens] if not remainder else f"{_TENS[tens]} { _SMALL_NUMBERS[remainder]}"
    if value < 1_000:
        hundreds, remainder = divmod(value, 100)
        prefix = f"{_SMALL_NUMBERS[hundreds]} hundred"
        return prefix if not remainder else f"{prefix} {_number_to_words(remainder)}"
    if value < 10_000:
        thousands, remainder = divmod(value, 1_000)
        prefix = f"{_SMALL_NUMBERS[thousands]} thousand"
        return prefix if not remainder else f"{prefix} {_number_to_words(remainder)}"
    return str(value)


def _expand_fraction(match: re.Match[str]) -> str:
    whole_text, numerator_text, denominator_text = match.groups()
    whole = int(whole_text) if whole_text else None
    numerator = int(numerator_text)
    denominator = int(denominator_text)
    names = _FRACTION_DENOMINATORS.get(denominator)

    if not names or numerator >= denominator:
        return match.group(0)

    denominator_name = names[0] if numerator == 1 else names[1]
    fraction = f"{_number_to_words(numerator)} {denominator_name}"
    return f"{_number_to_words(whole)} and {fraction}" if whole is not None else fraction


def _expand_temperature(match: re.Match[str]) -> str:
    value, scale = match.groups()
    unit = "Celsius" if scale.casefold() == "c" else "Fahrenheit"
    return f"{value} degrees {unit}"


def normalize_for_rime(text: str) -> str:
    """Make recipe text natural and safe for Rime's speech input.

    The result keeps ordinary sentence punctuation, expands common recipe notation,
    and removes markup/control symbols that are not useful to a voice listener.
    """
    if not text:
        return ""

    normalized = unicodedata.normalize("NFKC", text).replace("⁄", "/")
    for character, replacement in _UNICODE_FRACTIONS.items():
        normalized = normalized.replace(character, replacement)

    # Do temperatures before removing the degree symbol or expanding units.
    normalized = re.sub(
        r"(?<!\w)(\d+(?:\.\d+)?)\s*(?:°\s*)?([cCfF])\b",
        _expand_temperature,
        normalized,
    )
    normalized = re.sub(
        r"(?<!\w)(?:(\d+)\s+)?(\d+)\s*/\s*(\d+)(?!\w)",
        _expand_fraction,
        normalized,
    )

    unit_pattern = "|".join(sorted(map(re.escape, _UNITS), key=len, reverse=True))
    normalized = re.sub(
        rf"\b({unit_pattern})\.?\b",
        lambda match: _UNITS[match.group(1).casefold()],
        normalized,
        flags=re.IGNORECASE,
    )

    # Keep prose punctuation, but convert Markdown and symbols into readable text.
    normalized = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", normalized)
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[`*_~#|{}<>\\]", " ", normalized)
    normalized = normalized.replace("/", " ")
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith(("C", "S"))
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized
