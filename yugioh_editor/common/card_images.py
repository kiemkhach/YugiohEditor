from __future__ import annotations

import re
from collections.abc import Collection
from io import BytesIO

from PIL import Image

from yugioh_editor.common.card_errors import CardImageNameConflictError

CARD_IMAGE_NAME_PATTERN = re.compile(r"^[a-zA-Z]{3}\d{3}\.bmp$")
TOKEN_CARD_IMAGE_NAME = "token_sl.bmp"
LARGE_CARD_IMAGE_SIZE = (200, 290)
MINI_CARD_IMAGE_SIZE = (50, 72)


def generate_unique_card_image_name(existing_names: Collection[str]) -> str:
    """Return the first available deterministic user-card image name."""

    existing = {str(name).casefold() for name in existing_names}
    first_prefix = _prefix_to_number("usr")
    last_prefix = _prefix_to_number("zzz")
    for prefix_number in range(first_prefix, last_prefix + 1):
        prefix = _number_to_prefix(prefix_number)
        for suffix in range(1000):
            candidate = f"{prefix}{suffix:03d}.bmp"
            if candidate.casefold() not in existing:
                return candidate
    raise CardImageNameConflictError(
        "generate_unique_card_image_name exhausted the usr000.bmp through "
        "zzz999.bmp namespace."
    )


def build_card_image_pair(source: bytes) -> tuple[bytes, bytes]:
    if not isinstance(source, bytes) or not source:
        raise ValueError("Downloaded card image must contain bytes.")
    try:
        with Image.open(BytesIO(source)) as image:
            image.load()
            original = image.convert("RGB")
    except Exception as error:
        raise ValueError(f"Downloaded card image is invalid: {error}") from error
    return (
        _bmp_at_size(original, LARGE_CARD_IMAGE_SIZE),
        _bmp_at_size(original, MINI_CARD_IMAGE_SIZE),
    )


def _bmp_at_size(image: Image.Image, size: tuple[int, int]) -> bytes:
    output = BytesIO()
    image.resize(size, Image.Resampling.LANCZOS).save(output, format="BMP")
    return output.getvalue()


def _prefix_to_number(prefix: str) -> int:
    if len(prefix) != 3 or not prefix.isascii() or not prefix.isalpha():
        raise ValueError(f"Invalid three-letter image prefix: {prefix!r}.")
    value = 0
    for character in prefix.casefold():
        value = value * 26 + (ord(character) - ord("a"))
    return value


def _number_to_prefix(value: int) -> str:
    if not 0 <= value < 26**3:
        raise ValueError(f"Image prefix number is outside base-26 range: {value}.")
    characters = ["a", "a", "a"]
    for position in range(2, -1, -1):
        value, remainder = divmod(value, 26)
        characters[position] = chr(ord("a") + remainder)
    return "".join(characters)
