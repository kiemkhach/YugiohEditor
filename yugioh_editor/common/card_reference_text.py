from __future__ import annotations

import html
import re
import unicodedata

_NO_BREAK_SPACES = str.maketrans(
    {
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
    }
)
_HORIZONTAL_WHITESPACE = re.compile(r"[ \t\f\v]+")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([,.;:!?，。；：！？])")

_RENAME_NAME_SUFFIX = re.compile(
    r"\s+\(\s*(?:"
    r"Actualis[eé]e?\s+de|"
    r"Actualizad[oa]\s+de|"
    r"Aggiornat[oa]\s+da|"
    r"Ge[aä]ndert\s+von"
    r")\s*:\s*.+\)\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)

_QUOTE_OPEN = r'["“”„«»]'
_QUOTE_CLOSE = _QUOTE_OPEN
_DATE = r"\d{1,2}-\d{1,2}-\d{4}"
_RENAME_DESCRIPTION_NOTICES = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        rf"Nom\s+de\s+carte\s+actualis[eé]e?\s+de\s+{_QUOTE_OPEN}[^\r\n]+?"
        rf"{_QUOTE_CLOSE}\s+le\s+{_DATE}\.\s*$",
        rf"Nombre\s+de\s+Carta\s+actualizad[oa]\s+de\s+{_QUOTE_OPEN}[^\r\n]+?"
        rf"{_QUOTE_CLOSE}\s+en\s+{_DATE}\.\s*$",
        rf"Nome\s+della\s+carta\s+aggiornat[oa]\s+da\s+{_QUOTE_OPEN}[^\r\n]+?"
        rf"{_QUOTE_CLOSE}\s+il\s+{_DATE}\.\s*$",
        rf"Kartenname\s+ge[aä]ndert\s+von\s+{_QUOTE_OPEN}[^\r\n]+?"
        rf"{_QUOTE_CLOSE}\s+am\s+{_DATE}\.\s*$",
    )
)


def normalize_reference_card_name(value: str) -> str:
    """Normalize provider text and remove a verified trailing rename notice."""

    normalized = _normalize_reference_text(value)
    match = _RENAME_NAME_SUFFIX.search(normalized)
    if match is None:
        return normalized
    primary_name = normalized[: match.start()].rstrip()
    return primary_name or normalized


def normalize_reference_card_description(value: str) -> str:
    """Normalize effect text and remove complete provider rename sentences."""

    normalized = _normalize_reference_text(value)
    while normalized:
        match = next(
            (
                candidate
                for pattern in _RENAME_DESCRIPTION_NOTICES
                if (candidate := pattern.search(normalized)) is not None
            ),
            None,
        )
        if match is None or not _is_independent_sentence(normalized, match.start()):
            break
        normalized = normalized[: match.start()].rstrip()
    return normalized


def _normalize_reference_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"reference text must be a string, got {type(value).__name__}.")
    normalized = value
    while True:
        decoded = html.unescape(normalized)
        if decoded == normalized:
            break
        normalized = decoded
    normalized = unicodedata.normalize("NFC", normalized)
    normalized = normalized.translate(_NO_BREAK_SPACES)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _HORIZONTAL_WHITESPACE.sub(" ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", normalized)
    return normalized.strip()


def _is_independent_sentence(value: str, start: int) -> bool:
    if start == 0:
        return True
    preceding = value[:start]
    if preceding.endswith("\n"):
        return True
    return preceding.rstrip().endswith((".", "!", "?"))
