from __future__ import annotations

import re
from collections.abc import Mapping

CARD_CLASS_LABELS: dict[int, str] = {
    0x00: "",
    0x01: "dragon",
    0x02: "zombie",
    0x03: "fiend",
    0x04: "pyro",
    0x05: "sea_serpent",
    0x06: "rock",
    0x07: "machine",
    0x08: "fish",
    0x09: "dinosaur",
    0x0A: "insect",
    0x0B: "beast",
    0x0C: "beast_warrior",
    0x0D: "plant",
    0x0E: "aqua",
    0x0F: "warrior",
    0x10: "winged_beast",
    0x11: "fairy",
    0x12: "spellcaster",
    0x13: "thunder",
    0x14: "reptile",
    0x15: "trap_card",
    0x16: "spell_card",
    0x17: "non_game_card",
    0x18: "divine",
    **{code: "" for code in range(0x19, 0x20)},
}

MONSTER_CATEGORY_LABELS: dict[int, str] = {
    0: "normal",
    1: "effect",
    2: "fusion",
    3: "ritual",
}

SPELL_TRAP_SUBTYPE_LABELS: dict[int, str] = {
    0: "normal",
    1: "counter",
    2: "field",
    3: "equip",
    4: "continuous",
    5: "quick_play",
    6: "ritual",
    7: "",
}

ATTRIBUTE_LABELS: dict[int, str] = {
    0: "",
    1: "light",
    2: "dark",
    3: "water",
    4: "fire",
    5: "earth",
    6: "wind",
    7: "divine",
}

# Existing callers use these public semantic names. Their values now follow the
# actual five-bit class and two-bit Monster-category codes.
MONSTER_TYPE_LABELS = CARD_CLASS_LABELS
CARD_CATEGORY_LABELS = MONSTER_CATEGORY_LABELS

CARD_PROPERTY_COLUMNS = (
    "attack",
    "defense",
    "monster_type_code",
    "monster_type",
    "card_category_code",
    "card_category",
    "attribute_code",
    "attribute",
    "level",
    "requires_two_tributes",
)

CARD_STAT_MIN = 0
CARD_STAT_MAX = 5110
CARD_STAT_STEP = 10
CARD_LEVEL_MIN = 0
CARD_LEVEL_MAX = 15

_DISPLAY_LABEL_OVERRIDES = {"non_game_card": "Non-Game Card"}


def normalize_property_label(value: object) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "_",
        str(value).strip().casefold(),
    ).strip("_")


def code_for_property_label(
    value: object,
    labels: Mapping[int, str],
    *,
    field: str,
) -> int:
    normalized = normalize_property_label(value)
    matches = [code for code, label in labels.items() if label == normalized]
    if not matches:
        raise ValueError(f"Unsupported {field}: {value}")
    return matches[0]


def property_label_for_code(
    value: object,
    labels: Mapping[int, str],
    *,
    field: str,
) -> str:
    code = parse_property_code(value, field=field)
    try:
        return labels[code]
    except KeyError as error:
        raise ValueError(f"Unsupported {field}: 0x{code:02X}") from error


def parse_property_code(value: object, *, field: str) -> int:
    try:
        if isinstance(value, str):
            stripped = value.strip()
            code = (
                int(stripped, 16)
                if stripped.casefold().startswith("0x")
                else int(stripped)
            )
        else:
            code = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {field}: {value!r}") from error
    return code


def display_property_label(value: object) -> str:
    normalized = normalize_property_label(value)
    return _DISPLAY_LABEL_OVERRIDES.get(
        normalized,
        normalized.replace("_", " ").title(),
    )
