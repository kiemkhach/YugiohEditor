from __future__ import annotations

import operator
import re

CARD_PASSWORD_BYTE_WIDTH = 4
CARD_PASSWORD_HEX_WIDTH = CARD_PASSWORD_BYTE_WIDTH * 2
MISSING_CARD_PASSWORD = "FFFFFFFF"

_CARD_PASSWORD_PATTERN = re.compile(rf"[0-9A-Fa-f]{{{CARD_PASSWORD_HEX_WIDTH}}}")
_LEGACY_DECIMAL_PATTERN = re.compile(r"[0-9]+")


def normalize_card_password(value: object) -> str:
    """Return one canonical uppercase raw-order password record."""

    if not isinstance(value, str):
        raise ValueError("Card password must be a hexadecimal string.")
    normalized = value.strip()
    if _CARD_PASSWORD_PATTERN.fullmatch(normalized) is None:
        raise ValueError("Card password must contain exactly 8 hexadecimal characters.")
    return normalized.upper()


def is_missing_card_password(value: object) -> bool:
    """Return whether a valid password is the game's missing-value sentinel."""

    try:
        return normalize_card_password(value) == MISSING_CARD_PASSWORD
    except ValueError:
        return False


def legacy_card_password_to_hex(value: object) -> str:
    """Recover raw byte order from the legacy unsigned-u32 workspace value."""

    if isinstance(value, bool):
        raise ValueError("Legacy card password must be an unsigned 32-bit integer.")
    if isinstance(value, str):
        text = value.strip()
        if _LEGACY_DECIMAL_PATTERN.fullmatch(text) is None:
            raise ValueError("Legacy card password must be an unsigned 32-bit integer.")
        integer = int(text, 10)
    else:
        try:
            integer = operator.index(value)
        except TypeError as error:
            raise ValueError(
                "Legacy card password must be an unsigned 32-bit integer."
            ) from error
    maximum = (1 << (CARD_PASSWORD_BYTE_WIDTH * 8)) - 1
    if not 0 <= integer <= maximum:
        raise ValueError("Legacy card password is outside unsigned 32-bit range.")
    return (
        integer.to_bytes(
            CARD_PASSWORD_BYTE_WIDTH,
            byteorder="little",
            signed=False,
        )
        .hex()
        .upper()
    )
