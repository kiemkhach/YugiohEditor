from __future__ import annotations

import struct

from yugioh_editor.common.errors import InvalidFileFormatError
from yugioh_editor.models.entities import DeckFile


class DeckCodec:
    """Codec for an eight-byte header followed by 16-bit identifiers."""

    def decode(self, data: bytes) -> DeckFile:
        if len(data) < 9:
            raise InvalidFileFormatError("Deck data is smaller than nine bytes.")
        count = data[8]
        required = 9 + count * 2
        if len(data) < required:
            raise InvalidFileFormatError(
                "Deck data does not contain all declared identifiers."
            )
        card_ids = list(struct.unpack_from(f"<{count}H", data, 9)) if count else []
        return DeckFile(header=data[:8], card_ids=card_ids)

    def encode(self, deck: DeckFile) -> bytes:
        if len(deck.card_ids) > 255:
            raise ValueError("Deck data supports at most 255 identifiers.")
        invalid = [value for value in deck.card_ids if not 0 <= int(value) <= 0xFFFF]
        if invalid:
            raise ValueError(
                "All deck card IDs must fit in an unsigned 16-bit integer."
            )
        header = bytes(deck.header[:8]).ljust(8, b"\x00")
        payload = (
            struct.pack(f"<{len(deck.card_ids)}H", *deck.card_ids)
            if deck.card_ids
            else b""
        )
        return header + bytes((len(deck.card_ids),)) + payload
