from __future__ import annotations

import re
from pathlib import Path

APPLICATION_NAME = "Yu-Gi-Oh! Power of Chaos Editor"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
UI_DIRECTORY = PACKAGE_ROOT / "ui"

CONTAINER_SIGNATURE = b"KCEJYUGI"
CONTAINER_HEADER_SIZE = 12
CONTAINER_ENTRY_HEADER_SIZE = 268
CONTAINER_PATH_SIZE = 256
LOGICAL_DAT_FILES = {
    "data.dat": "data",
    "voice.dat": "voice",
    "region.dat": "region",
}
CONTAINER_LOGICAL_NAMES = {
    "data.dat": "data",
    "voice.dat": "voice",
}

SUPPORTED_GAME_FILES = (
    "data.dat",
    "Voice.dat",
    "deck.ydc",
    "Region.dat",
)
EXECUTABLE_SUFFIX = "_pc.exe"
EXECUTABLE_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]+_pc\.exe$",
    re.IGNORECASE,
)
VERSION_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

LANGUAGE_ENCODINGS = {
    "eng": "cp1252",
    "fra": "cp1252",
    "jpn": "cp932",
    "spa": "cp1252",
    "ita": "cp1252",
    "ger": "cp1252",
}
LANGUAGE_PREFIXES = tuple(LANGUAGE_ENCODINGS)
DEFAULT_LANGUAGE = "eng"
JAPANESE_LANGUAGE = "jpn"
LOCALIZED_CARD_FILE_PATTERN = re.compile(
    r"(?:card_(?:name|desc|indx|sort)|dlg_(?:text|indx))"
    r"(?P<language>[a-z]{3,4})\.bin$",
    re.IGNORECASE,
)

PACK_NAMES = {
    0: "disabled",
    1: "yugi",
    2: "kaiba",
    3: "yugi_kaiba",
    4: "joey",
    5: "yugi_joey",
    6: "kaiba_joey",
    7: "yugi_kaiba_joey",
}
PROJECT_FILE_NAME = "project.json"
PROJECT_BIN_DIRECTORY = "bin"
PROJECT_ICON_FILE_NAME = "project.ico"

IMAGE_EXTENSIONS = {".bmp", ".png", ".jpg", ".jpeg", ".gif"}
AUDIO_EXTENSIONS = {".wav"}

CODEC_OPERATIONS = frozenset(
    {
        "container",
        "binary",
        "text",
        "integer_list",
        "fixed_hex_list",
        "fixed_string_list",
        "offset_string_table",
        "record_table",
        "regex_record_table",
        "image",
        "audio",
    }
)
TABLE_CODEC_OPERATIONS = frozenset(
    {
        "integer_list",
        "fixed_hex_list",
        "fixed_string_list",
        "offset_string_table",
        "record_table",
        "regex_record_table",
    }
)


def normalize_language_code(language: object) -> str:
    code = str(language).strip().casefold()
    if code not in LANGUAGE_ENCODINGS:
        supported = ", ".join(LANGUAGE_ENCODINGS)
        raise ValueError(
            f"Unsupported language {language!r}. Supported languages: {supported}."
        )
    return code


def language_encoding(language: object) -> str:
    return LANGUAGE_ENCODINGS[normalize_language_code(language)]


def validate_language_resource_path(file_name: str) -> None:
    match = LOCALIZED_CARD_FILE_PATTERN.search(Path(file_name).name)
    if match is None:
        return
    try:
        normalize_language_code(match.group("language"))
    except ValueError as error:
        raise ValueError(
            f"Unsupported language prefix "
            f"{match.group('language')!r}. "
            f"Resource path: {file_name!r}."
        ) from error


def ui_path(file_name: str) -> Path:
    return UI_DIRECTORY / file_name
