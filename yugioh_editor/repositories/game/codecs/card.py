from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from yugioh_editor.common.card_properties import (
    ATTRIBUTE_LABELS,
    CARD_LEVEL_MAX,
    CARD_LEVEL_MIN,
    CARD_STAT_MAX,
    CARD_STAT_MIN,
    CARD_STAT_STEP,
    MONSTER_CATEGORY_LABELS,
    MONSTER_TYPE_LABELS,
    SPELL_TRAP_SUBTYPE_LABELS,
    code_for_property_label,
    parse_property_code,
    property_label_for_code,
)


@dataclass(frozen=True, slots=True)
class IndexedStringLayout:
    """An immutable text blob and its matching offset sequence."""

    blob: bytes
    offsets: tuple[int, ...]
    records: tuple[bytes, ...]


def encode_aligned_string(
    value: object,
    *,
    encoding: str,
    terminator: bytes = b"\x00",
    minimum_padding: int = 2,
    alignment: int = 2,
    record_index: int | None = None,
) -> bytes:
    if not terminator:
        raise ValueError("String terminator must not be empty.")
    if alignment <= 0 or minimum_padding < len(terminator):
        raise ValueError("Invalid string-table padding parameters.")
    try:
        encoded = str(value).encode(encoding, errors="strict")
    except UnicodeEncodeError as error:
        location = (
            f"record {record_index}" if record_index is not None else "string record"
        )
        character = error.object[error.start : error.end]
        raise ValueError(
            f"Cannot encode {location} using {encoding}: "
            f"character {character!r} at position {error.start}."
        ) from error
    padding = minimum_padding
    while (len(encoded) + padding) % alignment:
        padding += 1
    return encoded + terminator + b"\x00" * (padding - len(terminator))


def decode_aligned_string(
    data: bytes,
    *,
    encoding: str,
    terminator: bytes = b"\x00",
    minimum_padding: int = 2,
    alignment: int = 2,
    input_padding_policy: str = "canonical_zero",
    record_index: int | None = None,
    errors: str = "strict",
) -> str:
    if not terminator:
        raise ValueError("String terminator must not be empty.")
    if alignment <= 0 or minimum_padding < len(terminator):
        raise ValueError("Invalid string-table padding parameters.")
    if input_padding_policy not in {"canonical_zero", "pointer_bounded"}:
        raise ValueError(
            "Unsupported indexed-string input padding policy "
            f"{input_padding_policy!r}. Use: canonical_zero, pointer_bounded."
        )
    position = data.find(terminator)
    location = f" at record {record_index}" if record_index is not None else ""
    if position < 0:
        raise ValueError(
            f"Malformed indexed string{location}: missing null terminator."
        )
    payload = data[:position]
    padding = minimum_padding
    while (len(payload) + padding) % alignment:
        padding += 1
    expected = payload + terminator + b"\x00" * (padding - len(terminator))
    valid = (
        data == expected
        if input_padding_policy == "canonical_zero"
        else len(data) == len(expected)
    )
    if not valid:
        raise ValueError(
            f"Malformed indexed string{location}: input does not satisfy "
            f"padding policy {input_padding_policy!r} with "
            f"{alignment}-byte alignment; expected record length "
            f"{len(expected)}, got {len(data)}."
        )
    try:
        return payload.decode(encoding, errors=errors)
    except UnicodeDecodeError as error:
        raise ValueError(
            f"Cannot decode indexed string{location} using {encoding}: "
            f"byte position {error.start}."
        ) from error


def build_indexed_string_layout(
    values: Sequence[Mapping[str, object]],
    *,
    encoding: str,
    terminator: bytes = b"\x00",
    minimum_padding: int = 2,
    alignment: int = 2,
) -> IndexedStringLayout:
    offsets: list[int] = []
    records: list[bytes] = []
    blob = bytearray()
    for record_index, value in enumerate(values):
        text, is_reserved = _normalize_indexed_text_record(value, record_index)
        if is_reserved:
            offsets.append(0)
            records.append(b"")
            continue
        offsets.append(len(blob))
        record = encode_aligned_string(
            text,
            encoding=encoding,
            terminator=terminator,
            minimum_padding=minimum_padding,
            alignment=alignment,
            record_index=record_index,
        )
        records.append(record)
        blob.extend(record)
    return IndexedStringLayout(
        blob=bytes(blob),
        offsets=tuple(offsets),
        records=tuple(records),
    )


def calculate_string_layout(
    values: Sequence[Mapping[str, object]],
    *,
    encoding: str,
    terminator: bytes,
    minimum_padding: int,
    alignment: int,
) -> tuple[list[int], list[bytes]]:
    layout = build_indexed_string_layout(
        list(values),
        encoding=encoding,
        terminator=terminator,
        minimum_padding=minimum_padding,
        alignment=alignment,
    )
    return list(layout.offsets), list(layout.records)


def _normalize_indexed_text_record(
    value: object,
    record_index: int,
) -> tuple[str, bool]:
    if not isinstance(value, Mapping):
        raise TypeError(
            "Indexed string values must be mappings containing 'text' and "
            f"'is_reserved': record {record_index} is {type(value).__name__}."
        )
    if "text" not in value or "is_reserved" not in value:
        raise ValueError(
            "Indexed string records require 'text' and 'is_reserved': "
            f"record {record_index}."
        )
    text = value["text"]
    is_reserved = value["is_reserved"]
    if not isinstance(text, str):
        raise TypeError(f"Indexed string record {record_index} text must be a string.")
    if not isinstance(is_reserved, bool):
        raise TypeError(
            f"Indexed string record {record_index} is_reserved must be a bool."
        )
    # Offset zero is the active first record. Non-empty text cannot be reserved.
    return text, bool(is_reserved and record_index > 0 and text == "")


class IntegerListCodec:
    """Encode and decode fixed-width integers without file-specific knowledge."""

    _widths = (1, 2, 4, 8)

    def decode(
        self,
        data: bytes,
        *,
        byte_width: int,
        signed: bool = False,
        byte_order: str = "little",
    ) -> list[int]:
        if byte_width not in self._widths:
            raise ValueError("Integer width must be 1, 2, 4, or 8 bytes.")
        if byte_order not in {"little", "big"}:
            raise ValueError("Byte order must be 'little' or 'big'.")
        if len(data) % byte_width:
            raise ValueError(
                "Integer-list data length is not aligned to its item width."
            )
        return [
            int.from_bytes(
                data[offset : offset + byte_width],
                byteorder=byte_order,
                signed=signed,
            )
            for offset in range(0, len(data), byte_width)
        ]

    def encode(
        self,
        values: Iterable[int],
        *,
        byte_width: int,
        signed: bool = False,
        byte_order: str = "little",
    ) -> bytes:
        if byte_width not in self._widths:
            raise ValueError("Integer width must be 1, 2, 4, or 8 bytes.")
        if byte_order not in {"little", "big"}:
            raise ValueError("Byte order must be 'little' or 'big'.")
        return b"".join(
            int(value).to_bytes(
                byte_width,
                byteorder=byte_order,
                signed=signed,
            )
            for value in values
        )


class FixedHexListCodec:
    """Encode fixed-width byte records as uppercase raw-order hex strings."""

    @staticmethod
    def _validate_byte_width(byte_width: int) -> None:
        if isinstance(byte_width, bool) or not isinstance(byte_width, int):
            raise ValueError("Hex-record byte width must be a positive integer.")
        if byte_width <= 0:
            raise ValueError("Hex-record byte width must be a positive integer.")

    def decode(self, data: bytes, *, byte_width: int) -> list[str]:
        self._validate_byte_width(byte_width)
        if len(data) % byte_width:
            raise ValueError(
                "Fixed-hex data length is not aligned to its record width."
            )
        return [
            data[offset : offset + byte_width].hex().upper()
            for offset in range(0, len(data), byte_width)
        ]

    def encode(self, values: Iterable[str], *, byte_width: int) -> bytes:
        self._validate_byte_width(byte_width)
        character_width = byte_width * 2
        pattern = re.compile(rf"[0-9A-F]{{{character_width}}}")
        output = bytearray()
        for record_index, value in enumerate(values):
            if not isinstance(value, str) or pattern.fullmatch(value) is None:
                raise ValueError(
                    f"Fixed-hex record {record_index} must be exactly "
                    f"{character_width} uppercase hexadecimal characters."
                )
            output.extend(bytes.fromhex(value))
        return bytes(output)


class FixedStringListCodec:
    """Encode and decode fixed-size, padded string records."""

    def decode(
        self,
        data: bytes,
        *,
        record_size: int,
        encoding: str,
        terminator: bytes = b"\x00",
        errors: str = "strict",
    ) -> list[str]:
        if record_size <= 0 or len(data) % record_size:
            raise ValueError("Fixed-string data is not aligned to the record size.")
        if not terminator:
            raise ValueError("String terminator must not be empty.")
        values: list[str] = []
        for offset in range(0, len(data), record_size):
            raw = data[offset : offset + record_size].split(terminator, 1)[0]
            values.append(raw.decode(encoding, errors=errors))
        return values

    def encode(
        self,
        values: Iterable[str],
        *,
        record_size: int,
        encoding: str,
        terminator: bytes = b"\x00",
        padding: bytes = b"\x00",
    ) -> bytes:
        if record_size <= 0:
            raise ValueError("Record size must be positive.")
        if not terminator or len(padding) != 1:
            raise ValueError(
                "Terminator must not be empty and padding must be one byte."
            )
        output = bytearray()
        for record_index, value in enumerate(values):
            try:
                encoded = str(value).encode(encoding, errors="strict")
            except UnicodeEncodeError as error:
                character = error.object[error.start : error.end]
                raise ValueError(
                    f"Cannot encode record {record_index} using {encoding}: "
                    f"character {character!r} at position {error.start}."
                ) from error
            required = len(encoded) + len(terminator)
            if required > record_size:
                raise ValueError(
                    f"Encoded string at record {record_index} exceeds "
                    f"{record_size - len(terminator)} bytes using {encoding}: "
                    f"{value!r}"
                )
            output.extend(encoded)
            output.extend(terminator)
            output.extend(padding * (record_size - required))
        return bytes(output)


class OffsetStringTableCodec:
    """Encode and decode strings whose starts are stored in a separate list."""

    def decode(
        self,
        data: bytes,
        offsets: Sequence[int],
        *,
        encoding: str,
        terminator: bytes = b"\x00",
        alignment: int = 2,
        minimum_padding: int = 2,
        input_padding_policy: str = "canonical_zero",
        errors: str = "strict",
    ) -> list[dict[str, object]]:
        if not terminator:
            raise ValueError("String terminator must not be empty.")
        if alignment <= 0 or minimum_padding < len(terminator):
            raise ValueError("Invalid string-table padding parameters.")
        if input_padding_policy not in {"canonical_zero", "pointer_bounded"}:
            raise ValueError(
                "Unsupported indexed-string input padding policy "
                f"{input_padding_policy!r}. Use: canonical_zero, pointer_bounded."
            )
        normalized = [int(value) for value in offsets]
        if not normalized:
            if data:
                raise ValueError(
                    "An empty offset table cannot address a non-empty text blob."
                )
            return []
        if normalized[0] != 0:
            raise ValueError("The first active string offset must be zero.")
        previous_active = -1
        for record_index, value in enumerate(normalized):
            if record_index > 0 and value == 0:
                continue
            if value < 0 or value > len(data):
                raise ValueError(
                    f"Invalid active offset {value} at record {record_index}: "
                    f"text blob size is {len(data)} bytes."
                )
            if value % alignment:
                raise ValueError(
                    f"Invalid active offset alignment at record {record_index}: "
                    f"offset {value} is not aligned to {alignment} bytes."
                )
            if value < previous_active:
                raise ValueError(
                    "Invalid active offset order: "
                    f"record {record_index} starts at {value} after "
                    f"active offset {previous_active}."
                )
            previous_active = value
        values: list[dict[str, object]] = []
        for position, start in enumerate(normalized):
            if position > 0 and start == 0:
                values.append({"text": "", "is_reserved": True})
                continue
            next_active = next(
                (
                    (next_position, value)
                    for next_position, value in enumerate(
                        normalized[position + 1 :],
                        start=position + 1,
                    )
                    if not (next_position > 0 and value == 0)
                ),
                (None, len(data)),
            )
            next_active_index, end = next_active
            if end < start or end > len(data):
                raise ValueError(
                    "Invalid active offset order: "
                    f"record {position} starts at {start} but the next active "
                    f"record starts at {end}."
                )
            record = data[start:end]
            try:
                text = decode_aligned_string(
                    record,
                    encoding=encoding,
                    terminator=terminator,
                    minimum_padding=minimum_padding,
                    alignment=alignment,
                    input_padding_policy=input_padding_policy,
                    record_index=position,
                    errors=errors,
                )
            except ValueError as error:
                first_null = record.find(terminator)
                payload_length = first_null if first_null >= 0 else len(record)
                tail = (
                    record[first_null + len(terminator) :] if first_null >= 0 else b""
                )
                head = record[:32].hex(" ")
                slice_tail = record[-32:].hex(" ")
                tail_hex = tail[:32].hex(" ")
                raise ValueError(
                    f"{error} Indexed-string diagnostics: "
                    f"record_index={position}, encoding={encoding!r}, "
                    f"start_offset={start}, end_offset={end}, "
                    f"next_active_index={next_active_index!r}, "
                    f"slice_length={len(record)}, "
                    f"terminator_found={first_null >= 0}, "
                    f"first_null_position={first_null}, "
                    f"payload_length={payload_length}, "
                    f"bytes_after_terminator={len(tail)}, "
                    f"zero_bytes_after_terminator={tail.count(0)}, "
                    f"tail_hex='{tail_hex}', "
                    f"minimum_padding={minimum_padding}, alignment={alignment}, "
                    f"input_padding_policy={input_padding_policy!r}, "
                    f"slice_head_hex='{head}', slice_tail_hex='{slice_tail}'."
                ) from error
            values.append({"text": text, "is_reserved": False})
        return values

    def encode(
        self,
        values: Sequence[Mapping[str, object]],
        *,
        encoding: str,
        terminator: bytes = b"\x00",
        alignment: int = 2,
        minimum_padding: int = 2,
    ) -> tuple[bytes, list[int]]:
        layout = build_indexed_string_layout(
            values,
            encoding=encoding,
            terminator=terminator,
            minimum_padding=minimum_padding,
            alignment=alignment,
        )
        return layout.blob, list(layout.offsets)

    @staticmethod
    def calculate_offsets(
        values: Sequence[Mapping[str, object]],
        *,
        encoding: str,
        terminator: bytes = b"\x00",
        alignment: int = 2,
        minimum_padding: int = 2,
    ) -> list[int]:
        layout = build_indexed_string_layout(
            values,
            encoding=encoding,
            terminator=terminator,
            minimum_padding=minimum_padding,
            alignment=alignment,
        )
        return list(layout.offsets)


class TerminatedStringListCodec:
    """Encode and decode sequential delimiter-terminated strings."""

    def decode(
        self,
        data: bytes,
        *,
        encoding: str,
        terminator: bytes = b"\x00",
        errors: str = "strict",
    ) -> list[str]:
        if not terminator:
            raise ValueError("String terminator must not be empty.")
        if data and not data.endswith(terminator):
            raise ValueError("Terminated-string data is missing its final terminator.")
        parts = data.split(terminator)
        if parts and parts[-1] == b"":
            parts.pop()
        return [part.decode(encoding, errors=errors) for part in parts]

    def encode(
        self,
        values: Iterable[str],
        *,
        encoding: str,
        terminator: bytes = b"\x00",
    ) -> bytes:
        if not terminator:
            raise ValueError("String terminator must not be empty.")
        return b"".join(
            str(value).encode(encoding, errors="strict") + terminator
            for value in values
        )


class RecordTableCodec:
    """Apply caller-selected row transforms to fixed-size records."""

    def decode(
        self,
        data: bytes,
        *,
        record_size: int,
        row_decoder: Callable[[bytes], Mapping[str, object]],
    ) -> list[dict[str, object]]:
        if record_size <= 0 or len(data) % record_size:
            raise ValueError("Record data is not aligned to the record size.")
        return [
            dict(row_decoder(data[offset : offset + record_size]))
            for offset in range(0, len(data), record_size)
        ]

    def encode(
        self,
        rows: Iterable[Mapping[str, object]],
        *,
        record_size: int,
        row_encoder: Callable[[Mapping[str, object]], bytes],
    ) -> bytes:
        output = bytearray()
        for row_index, row in enumerate(rows):
            try:
                encoded = bytes(row_encoder(row))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Invalid record at row {row_index}: {error}"
                ) from error
            if len(encoded) != record_size:
                raise ValueError("Row encoder returned an unexpected record size.")
            output.extend(encoded)
        return bytes(output)


@dataclass(frozen=True, slots=True)
class NibbleStatisticsCodec:
    """Transform the four-byte little-endian card-property bitfield."""

    _EMPTY_DETAIL_LABELS = {code: "" for code in range(8)}
    _DETAIL_LABELS_BY_CLASS = {
        21: SPELL_TRAP_SUBTYPE_LABELS,
        22: SPELL_TRAP_SUBTYPE_LABELS,
        23: {0: ""},
        24: {0: ""},
    }

    def decode_record(self, record: bytes) -> dict[str, object]:
        if len(record) != 4:
            raise ValueError("Card property records must contain four bytes.")
        value = int.from_bytes(record, "little", signed=False)
        monster_type_code = (value >> 20) & 0x1F
        is_monster = 1 <= monster_type_code <= 20
        detail_shift = 18 if is_monster else 17
        detail_mask = 0x03 if is_monster else 0x07
        card_category_code = (value >> detail_shift) & detail_mask
        detail_labels = (
            MONSTER_CATEGORY_LABELS
            if is_monster
            else self._DETAIL_LABELS_BY_CLASS.get(
                monster_type_code,
                self._EMPTY_DETAIL_LABELS,
            )
        )
        attack = ((value >> 9) & 0x1FF) * 10 if is_monster else 0
        defense = (value & 0x1FF) * 10 if is_monster else 0
        level = (value >> 25) & 0x0F if is_monster else 0
        attribute_code = (value >> 29) & 0x07
        return {
            "attack": attack,
            "defense": defense,
            "monster_type_code": monster_type_code,
            "monster_type": MONSTER_TYPE_LABELS[monster_type_code],
            "card_category_code": card_category_code,
            "card_category": detail_labels[card_category_code],
            "attribute_code": attribute_code,
            "attribute": ATTRIBUTE_LABELS[attribute_code],
            "level": level,
            "requires_two_tributes": bool(is_monster and level >= 8),
        }

    def encode_record(self, row: Mapping[str, object]) -> bytes:
        attack = self._required_int(row, "attack")
        defense = self._required_int(row, "defense")
        level = self._required_int(row, "level")
        monster_type_code = self._resolve_code_and_label(
            row,
            code_field="monster_type_code",
            label_field="monster_type",
            labels=MONSTER_TYPE_LABELS,
        )
        is_monster = 1 <= monster_type_code <= 20
        detail_labels = (
            MONSTER_CATEGORY_LABELS
            if is_monster
            else self._DETAIL_LABELS_BY_CLASS.get(
                monster_type_code,
                self._EMPTY_DETAIL_LABELS,
            )
        )
        card_category_code = self._resolve_code_and_label(
            row,
            code_field="card_category_code",
            label_field="card_category",
            labels=detail_labels,
        )
        attribute_code = self._resolve_code_and_label(
            row,
            code_field="attribute_code",
            label_field="attribute",
            labels=ATTRIBUTE_LABELS,
        )

        detail_shift = 18 if is_monster else 17
        value = (
            (monster_type_code << 20)
            | (attribute_code << 29)
            | (card_category_code << detail_shift)
        )
        if is_monster:
            self._validate_stat(attack, "Attack")
            self._validate_stat(defense, "Defense")
            if not CARD_LEVEL_MIN <= level <= CARD_LEVEL_MAX:
                raise ValueError(
                    f"Card level must be between {CARD_LEVEL_MIN} and {CARD_LEVEL_MAX}."
                )
            if "requires_two_tributes" in row:
                requires_two_tributes = self._parse_bool(
                    row["requires_two_tributes"],
                    field="requires_two_tributes",
                )
                if requires_two_tributes != (level >= 8):
                    raise ValueError(
                        "requires_two_tributes must equal whether level is at least 8."
                    )
            value |= (defense // 10) & 0x1FF
            value |= ((attack // 10) & 0x1FF) << 9
            value |= (level & 0x0F) << 25
        return value.to_bytes(4, "little", signed=False)

    @staticmethod
    def _validate_stat(value: int, label: str) -> None:
        if not CARD_STAT_MIN <= value <= CARD_STAT_MAX or value % CARD_STAT_STEP:
            raise ValueError(
                f"{label} must be representable in steps of {CARD_STAT_STEP} "
                f"from {CARD_STAT_MIN} to {CARD_STAT_MAX}."
            )

    @staticmethod
    def _required_int(row: Mapping[str, object], field: str) -> int:
        if field not in row:
            raise ValueError(f"Required logical field '{field}' is missing.")
        try:
            return int(row[field])
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Invalid integer field '{field}': {row[field]!r}"
            ) from error

    @staticmethod
    def _resolve_code_and_label(
        row: Mapping[str, object],
        *,
        code_field: str,
        label_field: str,
        labels: Mapping[int, str],
    ) -> int:
        has_code = code_field in row and str(row[code_field]).strip() != ""
        has_label = label_field in row and str(row[label_field]).strip() != ""
        if not has_code and not has_label:
            raise ValueError(
                f"Required logical field '{label_field}' or '{code_field}' is missing."
            )
        label_code = (
            code_for_property_label(
                row[label_field],
                labels,
                field=label_field,
            )
            if has_label
            else None
        )
        code = (
            parse_property_code(row[code_field], field=code_field)
            if has_code
            else label_code
        )
        property_label_for_code(code, labels, field=code_field)
        if label_code is not None:
            return label_code
        return code

    @staticmethod
    def _parse_bool(value: object, *, field: str) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().casefold()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
        raise ValueError(f"Invalid boolean field '{field}': {value!r}")


class RegexRecordCodec:
    """Transform repeated text records from caller-provided syntax."""

    def decode(
        self,
        data: bytes,
        *,
        pattern: re.Pattern[str],
        encoding: str,
        errors: str = "strict",
    ) -> list[dict[str, str]]:
        text = data.decode(encoding, errors=errors)
        return [match.groupdict(default="") for match in pattern.finditer(text)]

    def encode(
        self,
        rows: Iterable[Mapping[str, object]],
        *,
        template: str,
        encoding: str,
        errors: str = "strict",
    ) -> bytes:
        return "".join(template.format_map(dict(row)) for row in rows).encode(
            encoding,
            errors=errors,
        )


# Functional aliases keep the generic operations convenient for callers.
_INTEGER_CODEC = IntegerListCodec()
_FIXED_STRING_CODEC = FixedStringListCodec()
_OFFSET_STRING_CODEC = OffsetStringTableCodec()


def unpack_int_list(
    data: bytes,
    byte_width: int,
    *,
    signed: bool = False,
    byte_order: str = "little",
) -> list[int]:
    return _INTEGER_CODEC.decode(
        data,
        byte_width=byte_width,
        signed=signed,
        byte_order=byte_order,
    )


def pack_int_list(
    values: Iterable[int],
    byte_width: int,
    *,
    signed: bool = False,
    byte_order: str = "little",
) -> bytes:
    return _INTEGER_CODEC.encode(
        values,
        byte_width=byte_width,
        signed=signed,
        byte_order=byte_order,
    )


def unpack_fixed_string_list(
    data: bytes,
    item_size: int,
    encoding: str,
) -> list[str]:
    return _FIXED_STRING_CODEC.decode(
        data,
        record_size=item_size,
        encoding=encoding,
        errors="strict",
    )


def pack_fixed_string_list(
    values: Iterable[str],
    item_size: int,
    encoding: str,
) -> bytes:
    return _FIXED_STRING_CODEC.encode(
        values,
        record_size=item_size,
        encoding=encoding,
    )


def unpack_offset_strings(
    data: bytes,
    indexes: bytes,
    encoding: str,
) -> list[dict[str, object]]:
    offsets = _INTEGER_CODEC.decode(indexes, byte_width=4)
    return _OFFSET_STRING_CODEC.decode(
        data,
        offsets,
        encoding=encoding,
        errors="strict",
    )


def pack_offset_strings(
    values: Iterable[Mapping[str, object]],
    encoding: str,
) -> tuple[bytes, bytes]:
    blob, offsets = _OFFSET_STRING_CODEC.encode(
        list(values),
        encoding=encoding,
    )
    return blob, _INTEGER_CODEC.encode(offsets, byte_width=4)
