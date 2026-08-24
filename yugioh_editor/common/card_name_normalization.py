from __future__ import annotations

import re
import unicodedata
from typing import Protocol

from yugioh_editor.common.card_errors import JapaneseReadingNotFoundError
from yugioh_editor.common.constants import (
    JAPANESE_LANGUAGE,
    normalize_language_code,
)


class CardReferenceDataProvider(Protocol):
    def get_japanese_reading(
        self,
        display_name_jpn: str,
        *,
        allow_crawl: bool = True,
    ) -> str: ...


_LATIN_SEQUENCES = {
    "DNA": "ディーエヌエー",
    "UFO": "ユーフォー",
    "AM": "エーエム",
    "SB": "エスビー",
    "TM": "ティーエム",
}
_LATIN_LETTERS = dict(
    zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        (
            "エー",
            "ビー",
            "シー",
            "ディー",
            "イー",
            "エフ",
            "ジー",
            "エイチ",
            "アイ",
            "ジェー",
            "ケー",
            "エル",
            "エム",
            "エヌ",
            "オー",
            "ピー",
            "キュー",
            "アール",
            "エス",
            "ティー",
            "ユー",
            "ブイ",
            "ダブリュー",
            "エックス",
            "ワイ",
            "ゼット",
        ),
        strict=True,
    )
)
_GREEK_NAMES = {
    "α": "アルファ",
    "Α": "アルファ",
    "β": "ベータ",
    "Β": "ベータ",
    "γ": "ガンマ",
    "Γ": "ガンマ",
}
_DIGIT_NAMES = {
    0: "ゼロ",
    1: "イチ",
    2: "ニ",
    3: "サン",
    4: "ヨン",
    5: "ゴ",
    6: "ロク",
    7: "ナナ",
    8: "ハチ",
    9: "キユウ",
}
_SMALL_KANA = str.maketrans(
    {
        "ァ": "ア",
        "ィ": "イ",
        "ゥ": "ウ",
        "ェ": "エ",
        "ォ": "オ",
        "ャ": "ヤ",
        "ュ": "ユ",
        "ョ": "ヨ",
        "ッ": "ツ",
        "ヮ": "ワ",
        "ヵ": "カ",
        "ヶ": "ケ",
    }
)
_SEPARATORS = frozenset(
    {
        "・",
        "･",
        "-",
        "－",
        "‐",
        "―",
        "「",
        "」",
        "『",
        "』",
        "(",
        ")",
        "<",
        ">",
        "（",
        "）",
    }
)
_VOWEL_BY_KANA = {
    **dict.fromkeys("ァアカサタナハマャヤラヮワガザダバパヵ", "ア"),
    **dict.fromkeys("ィイキシチニヒミリヰギジヂビピ", "イ"),
    **dict.fromkeys("ゥウェウクスツヌフムュユルグズヅブプヴ", "ウ"),
    **dict.fromkeys("ェエケセテネヘメレヱゲゼデベペヶ", "エ"),
    **dict.fromkeys("ォオコソトノホモョヨロヲゴゾドボポ", "オ"),
}
_ASCII_LETTER = re.compile(r"[A-Z]", re.IGNORECASE)
_ASCII_DIGITS = re.compile(r"[0-9]+")


class CardNameNormalizer:
    def __init__(
        self,
        card_reference_data_service: CardReferenceDataProvider | None,
    ) -> None:
        self._card_reference_data_service = card_reference_data_service

    def normalize(self, name: str, language: str) -> str:
        if not isinstance(name, str):
            raise TypeError("Card name must be a string.")
        normalized_language = normalize_language_code(language)
        if normalized_language == JAPANESE_LANGUAGE:
            if name == "":
                return ""
            if self._card_reference_data_service is None:
                raise RuntimeError(
                    "Japanese card-name normalization requires a reading service."
                )
            try:
                reading = self._card_reference_data_service.get_japanese_reading(name)
            except JapaneseReadingNotFoundError:
                return name
            return normalize_japanese_reading(reading)
        return normalize_non_japanese_name(name)


def normalize_non_japanese_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("Card name must be a string.")
    value = name.lower().replace("œ", "oe").replace("æ", "ae")
    value = value.replace("«", "(").replace("»", ")")
    value = unicodedata.normalize("NFD", value)
    value = "".join(
        character for character in value if unicodedata.category(character) != "Mn"
    )
    return value.replace("-", "").replace("'", "").replace("’", "")


def normalize_japanese_reading(reading_jpn: str) -> str:
    if not isinstance(reading_jpn, str) or not reading_jpn.strip():
        raise ValueError("Japanese card reading must be a non-empty string.")
    value = unicodedata.normalize("NFKC", reading_jpn)
    value = _replace_latin(value)
    value = "".join(_GREEK_NAMES.get(character, character) for character in value)
    value = _ASCII_DIGITS.sub(
        lambda match: _read_japanese_number(int(match.group(0))),
        value,
    )
    value = _hiragana_to_katakana(value)
    value = _expand_prolonged_sound_marks(value)
    value = value.replace("ヴ", "フ")
    value = unicodedata.normalize("NFD", value)
    value = "".join(
        character for character in value if unicodedata.category(character) != "Mn"
    )
    value = unicodedata.normalize("NFC", value).translate(_SMALL_KANA)
    value = "".join(
        character
        for character in value
        if not character.isspace() and character not in _SEPARATORS
    )
    _validate_japanese_sort_key(value, reading_jpn)
    return value


def _replace_latin(value: str) -> str:
    for sequence in sorted(_LATIN_SEQUENCES, key=len, reverse=True):
        value = re.sub(
            re.escape(sequence),
            _LATIN_SEQUENCES[sequence],
            value,
            flags=re.IGNORECASE,
        )
    return _ASCII_LETTER.sub(
        lambda match: _LATIN_LETTERS[match.group(0).upper()],
        value,
    )


def _read_japanese_number(value: int) -> str:
    if value < 0 or value >= 10000:
        raise ValueError(f"Unsupported Japanese card-reading number: {value}.")
    if value < 10:
        return _DIGIT_NAMES[value]
    units = (
        (1000, "セン"),
        (100, "ヒヤク"),
        (10, "ジユウ"),
    )
    remaining = value
    parts: list[str] = []
    for unit, label in units:
        digit, remaining = divmod(remaining, unit)
        if digit:
            if digit != 1:
                parts.append(_DIGIT_NAMES[digit])
            parts.append(label)
    if remaining:
        parts.append(_DIGIT_NAMES[remaining])
    return "".join(parts)


def _hiragana_to_katakana(value: str) -> str:
    return "".join(
        chr(ord(character) + 0x60) if 0x3041 <= ord(character) <= 0x3096 else character
        for character in value
    )


def _expand_prolonged_sound_marks(value: str) -> str:
    result: list[str] = []
    for character in value:
        if character != "ー":
            result.append(character)
            continue
        if not result or result[-1] not in _VOWEL_BY_KANA:
            previous = result[-1] if result else "<start>"
            raise ValueError(
                "Japanese prolonged sound mark has no supported preceding mora: "
                f"{previous!r} in {value!r}."
            )
        result.append(_VOWEL_BY_KANA[result[-1]])
    return "".join(result)


def _validate_japanese_sort_key(value: str, reading_jpn: str) -> None:
    for character in value:
        if not 0x30A1 <= ord(character) <= 0x30FA:
            raise ValueError(
                f"Unsupported character {character!r} remains in Japanese "
                f"reading {reading_jpn!r}."
            )
