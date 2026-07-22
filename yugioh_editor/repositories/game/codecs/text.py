from __future__ import annotations


class TextCodec:
    """Encode and decode text with caller-selected character settings."""

    def decode(
        self,
        data: bytes,
        *,
        encoding: str,
        errors: str = "strict",
    ) -> str:
        return data.decode(encoding, errors=errors)

    def encode(
        self,
        value: str,
        *,
        encoding: str,
        errors: str = "strict",
    ) -> bytes:
        return value.encode(encoding, errors=errors)
