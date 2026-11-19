"""Conservative normalisation used only for candidate alignment."""
from __future__ import annotations

import re
import unicodedata

_NUMBER_WORDS = {
    "ноль": 0, "нуль": 0,
    "один": 1, "одна": 1, "одно": 1, "одну": 1,
    "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30,
    "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400,
    "пятьсот": 500, "шестьсот": 600, "семьсот": 700,
    "восемьсот": 800, "девятьсот": 900,
}
_BOUNDARY_RE = re.compile(r"[.,;:!?]+|[0-9a-zа-я]+")


def _normalise_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").replace("\n", ".")
    return normalized.replace("‐", "-").replace("‑", "-").replace("‒", "-").replace("–", "-").replace("—", "-").replace("―", "-").replace("-", " ")


def _canonical_groups(text: str) -> list[list[str]]:
    """Token groups separated by punctuation that changes numeric meaning."""
    groups: list[list[str]] = [[]]
    for match in _BOUNDARY_RE.finditer(_normalise_text(text)):
        token = match.group(0)
        if token[0] in ".,;:!?":
            if groups[-1]:
                groups.append([])
            continue
        groups[-1].append(token)
    return [group for group in groups if group]


def _parse_cardinal(tokens: list[str], index: int) -> tuple[str, int]:
    """Parse one grammatically ordered Russian cardinal up to 999."""
    value = _NUMBER_WORDS[tokens[index]]
    if 100 <= value <= 900:
        total = value
        index += 1
        if index < len(tokens) and tokens[index] in _NUMBER_WORDS:
            next_value = _NUMBER_WORDS[tokens[index]]
            if 10 <= next_value <= 19:
                return str(total + next_value), index + 1
            if 20 <= next_value <= 90 and next_value % 10 == 0:
                total += next_value
                index += 1
                if index < len(tokens) and tokens[index] in _NUMBER_WORDS and 1 <= _NUMBER_WORDS[tokens[index]] <= 9:
                    total += _NUMBER_WORDS[tokens[index]]
                    index += 1
                return str(total), index
            if 1 <= next_value <= 9:
                return str(total + next_value), index + 1
        return str(total), index
    if 20 <= value <= 90 and value % 10 == 0:
        total = value
        index += 1
        if index < len(tokens) and tokens[index] in _NUMBER_WORDS and 1 <= _NUMBER_WORDS[tokens[index]] <= 9:
            total += _NUMBER_WORDS[tokens[index]]
            index += 1
        return str(total), index
    return str(value), index + 1


def _normalise_cardinals(tokens: list[str]) -> list[str]:
    output: list[str] = []
    index = 0
    while index < len(tokens):
        if tokens[index] not in _NUMBER_WORDS:
            output.append(tokens[index])
            index += 1
            continue
        value, index = _parse_cardinal(tokens, index)
        output.append(value)
    return output


def normalize_for_alignment(text: str) -> str:
    """Return a conservative comparison form; original strings stay untouched."""
    tokens: list[str] = []
    for group in _canonical_groups(text):
        tokens.extend(_normalise_cardinals(group))
    return " ".join(tokens)


def alignment_tokens(text: str) -> tuple[str, ...]:
    return tuple(normalize_for_alignment(text).split())
