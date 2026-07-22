import re
import unittest

from yugioh_editor.repositories.game.codecs.card import (
    FixedHexListCodec,
    FixedStringListCodec,
    IntegerListCodec,
    NibbleStatisticsCodec,
    OffsetStringTableCodec,
    RecordTableCodec,
    RegexRecordCodec,
    TerminatedStringListCodec,
    build_indexed_string_layout,
    calculate_string_layout,
    decode_aligned_string,
    encode_aligned_string,
    pack_fixed_string_list,
    pack_int_list,
    pack_offset_strings,
    unpack_fixed_string_list,
    unpack_int_list,
    unpack_offset_strings,
)
from yugioh_editor.repositories.game.codecs.text import TextCodec


def indexed_records(
    texts: list[str],
    *,
    reserved: set[int] | None = None,
) -> list[dict[str, object]]:
    reserved = reserved or set()
    return [
        {"text": text, "is_reserved": index in reserved}
        for index, text in enumerate(texts)
    ]


class GenericCodecTests(unittest.TestCase):
    @staticmethod
    def property_row(**changes):
        row = {
            "attack": 0,
            "defense": 0,
            "monster_type_code": 0x10,
            "monster_type": "winged_beast",
            "card_category_code": 0x00,
            "card_category": "normal",
            "attribute_code": 0x07,
            "attribute": "divine",
            "level": 0,
            "requires_two_tributes": False,
        }
        row.update(changes)
        return row

    def test_integer_width_sign_and_byte_order(self):
        codec = IntegerListCodec()
        cases = (
            ([0, 1, 255], 1, False),
            ([0, 1, 65535], 2, False),
            ([0, 1, 2**32 - 1], 4, False),
            ([-128, 0, 127], 1, True),
            ([-32768, 0, 32767], 2, True),
        )
        for values, width, signed in cases:
            with self.subTest(width=width, signed=signed):
                encoded = codec.encode(
                    values,
                    byte_width=width,
                    signed=signed,
                    byte_order="little",
                )
                self.assertEqual(
                    codec.decode(
                        encoded,
                        byte_width=width,
                        signed=signed,
                        byte_order="little",
                    ),
                    values,
                )
        with self.assertRaises(OverflowError):
            codec.encode([65536], byte_width=2, signed=False)

    def test_fixed_hex_records_preserve_raw_order_and_sentinel_values(self):
        codec = FixedHexListCodec()
        raw = bytes.fromhex("00 00 00 01 FF FF FF FF 12 34 56 78")
        values = ["00000001", "FFFFFFFF", "12345678"]
        self.assertEqual(codec.decode(raw, byte_width=4), values)
        self.assertEqual(codec.encode(values, byte_width=4), raw)

    def test_fixed_hex_records_require_exact_uppercase_strings(self):
        codec = FixedHexListCodec()
        for value in ("1234567", "123456789", "abcdef12", "12345G78", 12345678):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "exactly 8 uppercase hexadecimal characters",
                ):
                    codec.encode([value], byte_width=4)
        with self.assertRaisesRegex(ValueError, "not aligned"):
            codec.decode(b"\x00\x01\x02", byte_width=4)

    def test_fixed_strings_support_padding_and_terminators(self):
        codec = FixedStringListCodec()
        values = ["Alpha", "Béta"]
        encoded = codec.encode(
            values,
            record_size=12,
            encoding="cp1252",
            terminator=b"\x00",
            padding=b"\xff",
        )
        self.assertEqual(len(encoded), 24)
        self.assertEqual(
            codec.decode(
                encoded,
                record_size=12,
                encoding="cp1252",
            ),
            values,
        )

    def test_card_name_fixed_width_uses_encoded_byte_limit(self):
        codec = FixedStringListCodec()
        latin = codec.encode(["x" * 63], record_size=64, encoding="cp1252")
        self.assertEqual(len(latin), 64)
        self.assertEqual(latin[-1:], b"\x00")
        with self.assertRaisesRegex(ValueError, "exceeds 63 bytes"):
            codec.encode(["x" * 64], record_size=64, encoding="cp1252")

        japanese = codec.encode(["漢" * 31 + "a"], record_size=64, encoding="cp932")
        self.assertEqual(len(japanese), 64)
        self.assertEqual(
            codec.decode(japanese, record_size=64, encoding="cp932"), ["漢" * 31 + "a"]
        )
        with self.assertRaisesRegex(ValueError, "exceeds 63 bytes"):
            codec.encode(["漢" * 32], record_size=64, encoding="cp932")

    def test_offset_string_table_round_trip(self):
        codec = OffsetStringTableCodec()
        values = indexed_records(["Alpha", "Beta", "Gamma"])
        blob, offsets = codec.encode(values, encoding="utf-8")
        self.assertEqual(
            codec.decode(blob, offsets, encoding="utf-8"),
            values,
        )
        calculated, records = calculate_string_layout(
            values,
            encoding="utf-8",
            terminator=b"\x00",
            minimum_padding=2,
            alignment=2,
        )
        self.assertEqual(calculated, offsets)
        self.assertEqual(b"".join(records), blob)
        self.assertEqual(
            codec.calculate_offsets(values, encoding="utf-8"),
            offsets,
        )

    def test_aligned_string_padding_uses_encoded_byte_length(self):
        for length in range(6):
            with self.subTest(length=length):
                record = encode_aligned_string(
                    "x" * length,
                    encoding="cp1252",
                )
                zero_count = 3 if length % 2 else 2
                self.assertEqual(record, b"x" * length + b"\x00" * zero_count)
                self.assertEqual(len(record) % 2, 0)
                self.assertEqual(
                    decode_aligned_string(record, encoding="cp1252"),
                    "x" * length,
                )

    def test_indexed_string_known_raw_padding_vectors(self):
        profiles = (
            (
                "card_description",
                2,
                (
                    ("AB", bytes.fromhex("41 42 00 00")),
                    ("ABC", bytes.fromhex("41 42 43 00 00 00")),
                    ("", bytes.fromhex("00 00")),
                ),
            ),
            (
                "dialog",
                1,
                (
                    ("AB", bytes.fromhex("41 42 00 00")),
                    ("ABC", bytes.fromhex("41 42 43 00")),
                    ("", bytes.fromhex("00 00")),
                ),
            ),
        )
        for profile, minimum_padding, vectors in profiles:
            for text, raw in vectors:
                with self.subTest(profile=profile, text=text):
                    self.assertEqual(
                        decode_aligned_string(
                            raw,
                            encoding="cp1252",
                            terminator=b"\x00",
                            alignment=2,
                            minimum_padding=minimum_padding,
                        ),
                        text,
                    )
                    self.assertEqual(
                        encode_aligned_string(
                            text,
                            encoding="cp1252",
                            terminator=b"\x00",
                            alignment=2,
                            minimum_padding=minimum_padding,
                        ),
                        raw,
                    )

    def test_indexed_layout_uses_cp932_byte_parity_and_profile_specific_offsets(self):
        text = "日A"
        self.assertEqual(len(text), 2)
        self.assertEqual(len(text.encode("cp932")), 3)
        self.assertEqual(
            encode_aligned_string(
                text,
                encoding="cp932",
                terminator=b"\x00",
                alignment=2,
                minimum_padding=2,
            ),
            text.encode("cp932") + b"\x00\x00\x00",
        )
        self.assertEqual(
            encode_aligned_string(
                text,
                encoding="cp932",
                terminator=b"\x00",
                alignment=2,
                minimum_padding=1,
            ),
            text.encode("cp932") + b"\x00",
        )

        card_layout = build_indexed_string_layout(
            indexed_records(["A", "B"]),
            encoding="cp932",
            terminator=b"\x00",
            alignment=2,
            minimum_padding=2,
        )
        dialog_layout = build_indexed_string_layout(
            indexed_records(["A", "B"]),
            encoding="cp932",
            terminator=b"\x00",
            alignment=2,
            minimum_padding=1,
        )
        self.assertEqual(card_layout.offsets, (0, 4))
        self.assertEqual(dialog_layout.offsets, (0, 2))

    def test_real_card_description_record_one_vector(self):
        raw = bytes.fromhex(
            "41 20 76 65 6e 67 65 66 75 6c 20 63 72 65 61 74 75 72 "
            "65 20 66 6f 72 6d 65 64 20 62 79 20 74 68 65 20 73 70 "
            "69 72 69 74 73 20 6f 66 20 66 61 6c 6c 65 6e 20 77 61 "
            "72 72 69 6f 72 73 2c 20 69 74 20 64 72 61 67 73 20 61 "
            "6e 79 20 77 68 6f 20 64 61 72 65 20 61 70 70 72 6f 61 "
            "63 68 20 69 74 20 69 6e 74 6f 20 74 68 65 20 64 65 65 "
            "70 65 73 74 20 62 6f 77 65 6c 73 20 6f 66 20 74 68 65 "
            "20 65 61 72 74 68 2e 00 00 00"
        )
        expected = (
            "A vengeful creature formed by the spirits of fallen warriors, "
            "it drags any who dare approach it into the deepest bowels of the earth."
        )
        self.assertEqual(len(raw), 136)
        self.assertEqual(
            decode_aligned_string(raw, encoding="cp1252"),
            expected,
        )
        self.assertEqual(
            encode_aligned_string(expected, encoding="cp1252"),
            raw,
        )
        with self.assertRaisesRegex(ValueError, "padding policy"):
            decode_aligned_string(
                raw,
                encoding="cp1252",
                minimum_padding=1,
            )

    def test_pointer_bounded_padding_accepts_opaque_bytes_at_exact_length(self):
        raw = bytes.fromhex("41 42 00 75")
        self.assertEqual(
            decode_aligned_string(
                raw,
                encoding="cp1252",
                input_padding_policy="pointer_bounded",
            ),
            "AB",
        )
        with self.assertRaisesRegex(ValueError, "canonical_zero"):
            decode_aligned_string(raw, encoding="cp1252")
        with self.assertRaisesRegex(ValueError, "expected record length 4, got 5"):
            decode_aligned_string(
                raw + b"\x75",
                encoding="cp1252",
                input_padding_policy="pointer_bounded",
            )

    def test_pointer_bounded_offsets_reject_one_byte_shift(self):
        with self.assertRaisesRegex(
            ValueError,
            r"offset 5 is not aligned to 2 bytes",
        ):
            OffsetStringTableCodec().decode(
                bytes.fromhex("41 00 00 00 42 00 00 00"),
                [0, 5],
                encoding="cp1252",
                input_padding_policy="pointer_bounded",
            )

    def test_indexed_strings_preserve_reserved_offsets_and_do_not_deduplicate(self):
        codec = OffsetStringTableCodec()
        values = indexed_records(
            ["First", "", "Same", "", "Same"],
            reserved={1, 3},
        )
        blob, offsets = codec.encode(values, encoding="cp1252")
        self.assertEqual(offsets[0], 0)
        self.assertEqual(offsets[1], 0)
        self.assertEqual(offsets[3], 0)
        self.assertNotEqual(offsets[2], offsets[4])
        self.assertEqual(
            codec.decode(
                blob,
                offsets,
                encoding="cp1252",
            ),
            values,
        )

    def test_active_empty_and_reserved_records_are_explicit(self):
        codec = OffsetStringTableCodec()
        first_empty_blob, first_empty_offsets = codec.encode(
            indexed_records(["", "A"]), encoding="cp1252"
        )
        self.assertEqual(first_empty_blob, b"\x00\x00A\x00\x00\x00")
        self.assertEqual(first_empty_offsets, [0, 2])
        self.assertEqual(
            codec.decode(first_empty_blob, first_empty_offsets, encoding="cp1252"),
            indexed_records(["", "A"]),
        )

        later_empty_blob, later_empty_offsets = codec.encode(
            indexed_records(["A", "", "", "B"], reserved={2}),
            encoding="cp1252",
        )
        self.assertEqual(later_empty_offsets, [0, 4, 0, 6])
        self.assertEqual(
            later_empty_blob,
            b"A\x00\x00\x00\x00\x00B\x00\x00\x00",
        )

        active_empty_at_end = indexed_records(
            ["A", "", "", ""],
            reserved={1, 2},
        )
        end_blob, end_offsets = codec.encode(
            active_empty_at_end,
            encoding="cp1252",
        )
        self.assertEqual(end_blob, b"A\x00\x00\x00\x00\x00")
        self.assertEqual(end_offsets, [0, 0, 0, 4])
        self.assertEqual(
            codec.decode(end_blob, end_offsets, encoding="cp1252"),
            active_empty_at_end,
        )

        normalized_blob, normalized_offsets = codec.encode(
            [
                {"text": "", "is_reserved": True},
                {"text": "B", "is_reserved": True},
            ],
            encoding="cp1252",
        )
        self.assertEqual(normalized_offsets, [0, 2])
        self.assertEqual(normalized_blob, b"\x00\x00B\x00\x00\x00")

    def test_reserved_offsets_do_not_create_string_boundaries(self):
        codec = OffsetStringTableCodec()
        records = [
            encode_aligned_string("A" * 8, encoding="cp1252"),
            encode_aligned_string("B" * 88, encoding="cp1252"),
            encode_aligned_string("C" * 398, encoding="cp1252"),
            encode_aligned_string("D", encoding="cp1252"),
        ]
        blob = b"".join(records)
        decoded = codec.decode(
            blob,
            [0, 10, 100, 0, 500],
            encoding="cp1252",
        )
        self.assertEqual(
            decoded,
            indexed_records(
                ["A" * 8, "B" * 88, "C" * 398, "", "D"],
                reserved={3},
            ),
        )

    def test_multiple_reserved_offsets_and_invalid_indexed_data(self):
        codec = OffsetStringTableCodec()
        blob = b"".join(
            (
                encode_aligned_string("A" * 18, encoding="cp1252"),
                encode_aligned_string("B" * 28, encoding="cp1252"),
                encode_aligned_string("C", encoding="cp1252"),
            )
        )
        decoded = codec.decode(
            blob,
            [0, 0, 0, 20, 0, 50],
            encoding="cp1252",
        )
        self.assertEqual(
            decoded,
            indexed_records(
                ["A" * 18, "", "", "B" * 28, "", "C"],
                reserved={1, 2, 4},
            ),
        )
        invalid_cases = (
            (blob, [0, len(blob) + 1]),
            (blob, [0, 20, 10]),
            (b"A", [0]),
            (b"A\x00\x00", [0]),
        )
        for invalid_blob, invalid_offsets in invalid_cases:
            with self.subTest(offsets=invalid_offsets, blob=invalid_blob):
                with self.assertRaises(ValueError):
                    codec.decode(
                        invalid_blob,
                        invalid_offsets,
                        encoding="cp1252",
                    )

    def test_malformed_indexed_record_reports_offsets_and_hex_context(self):
        codec = OffsetStringTableCodec()
        with self.assertRaisesRegex(
            ValueError,
            r"record_index=1.*start_offset=2.*end_offset=4.*"
            r"next_active_index=None.*first_null_position=1.*"
            r"bytes_after_terminator=0.*zero_bytes_after_terminator=0.*"
            r"tail_hex=''.*slice_head_hex='41 00'",
        ):
            codec.decode(
                b"\x00\x00A\x00",
                [0, 2],
                encoding="cp1252",
            )

    def test_indexed_layout_is_immutable_and_reports_encoding_record(self):
        layout = build_indexed_string_layout(
            indexed_records(["A"]),
            encoding="cp1252",
        )
        self.assertEqual(layout.blob, b"A\x00\x00\x00")
        self.assertEqual(layout.offsets, (0,))
        with self.assertRaisesRegex(
            ValueError,
            r"record 1 using cp1252.*position 0",
        ):
            build_indexed_string_layout(
                indexed_records(
                    ["A", "漢"],
                ),
                encoding="cp1252",
            )
        with self.assertRaisesRegex(ValueError, "require.*is_reserved"):
            build_indexed_string_layout([{"text": "A"}], encoding="cp1252")

    def test_terminated_string_list_round_trip_and_validation(self):
        codec = TerminatedStringListCodec()
        values = ["Alpha", "Béta", "Gamma"]
        encoded = codec.encode(
            values,
            encoding="cp1252",
            terminator=b"\x00",
        )
        self.assertEqual(
            codec.decode(
                encoded,
                encoding="cp1252",
                terminator=b"\x00",
            ),
            values,
        )
        with self.assertRaises(ValueError):
            codec.decode(b"unterminated", encoding="utf-8")

    def test_generic_record_table_round_trip(self):
        codec = RecordTableCodec()

        def decode_row(record):
            return {"left": record[0], "right": record[1]}

        def encode_row(row):
            return bytes((int(row["left"]), int(row["right"])))

        rows = [{"left": 1, "right": 2}, {"left": 3, "right": 4}]
        encoded = codec.encode(
            rows,
            record_size=2,
            row_encoder=encode_row,
        )
        self.assertEqual(
            codec.decode(
                encoded,
                record_size=2,
                row_decoder=decode_row,
            ),
            rows,
        )

    def test_regex_records_and_text_use_caller_selected_syntax(self):
        records = RegexRecordCodec()
        rows = [{"label": "Alpha", "count": 2}]
        encoded = records.encode(
            rows,
            template="{label}:{count}\n",
            encoding="utf-8",
        )
        self.assertEqual(
            records.decode(
                encoded,
                pattern=re.compile(r"(?P<label>[^:]+):(?P<count>\d+)\n"),
                encoding="utf-8",
            ),
            [{"label": "Alpha", "count": "2"}],
        )
        text = TextCodec()
        payload = text.encode("café", encoding="cp1252")
        self.assertEqual(
            text.decode(payload, encoding="cp1252"),
            "café",
        )

    def test_property_record_round_trip(self):
        property_codec = NibbleStatisticsCodec()
        record_codec = RecordTableCodec()
        original = [self.property_row()]
        encoded = record_codec.encode(
            original,
            record_size=4,
            row_encoder=property_codec.encode_record,
        )
        rebuilt = record_codec.decode(
            encoded,
            record_size=4,
            row_decoder=property_codec.decode_record,
        )
        self.assertEqual(rebuilt, original)

    def test_property_known_vectors_decode_and_round_trip_exactly(self):
        codec = NibbleStatisticsCodec()
        cases = {
            "7890314C": {
                "attack": 2000,
                "defense": 1200,
                "monster_type_code": 0x03,
                "monster_type": "fiend",
                "card_category": "normal",
                "attribute": "dark",
                "level": 6,
                "requires_two_tributes": False,
            },
            "82A0F466": {
                "attack": 800,
                "defense": 1300,
                "monster_type_code": 0x0F,
                "monster_type": "warrior",
                "card_category": "effect",
                "attribute": "water",
                "level": 3,
            },
            "BEC2596C": {
                "attack": 2250,
                "defense": 1900,
                "monster_type_code": 0x05,
                "monster_type": "sea_serpent",
                "card_category": "fusion",
                "attribute": "water",
                "level": 6,
            },
            "00007001": {
                "monster_type_code": 0x17,
                "monster_type": "non_game_card",
                "card_category": "",
                "level": 0,
            },
            "00008041": {
                "monster_type_code": 0x18,
                "monster_type": "divine",
                "card_category": "",
                "attribute": "dark",
                "level": 0,
            },
        }
        for raw_hex, expected in cases.items():
            with self.subTest(raw_hex=raw_hex):
                raw = bytes.fromhex(raw_hex)
                decoded = codec.decode_record(raw)
                for field, value in expected.items():
                    self.assertEqual(decoded[field], value)
                self.assertEqual(codec.encode_record(decoded), raw)

    def test_property_all_categories_and_monster_types_round_trip(self):
        codec = NibbleStatisticsCodec()
        for category_code, category in (
            (0x00, "normal"),
            (0x01, "effect"),
            (0x02, "fusion"),
            (0x03, "ritual"),
        ):
            with self.subTest(category=category):
                row = self.property_row(
                    card_category_code=category_code,
                    card_category=category,
                )
                self.assertEqual(
                    codec.decode_record(codec.encode_record(row)),
                    row,
                )
        for monster_type_code in range(0x19):
            with self.subTest(monster_type_code=monster_type_code):
                row = self.property_row()
                row.pop("monster_type")
                row["monster_type_code"] = monster_type_code
                if not 1 <= monster_type_code <= 20:
                    row["attack"] = row["defense"] = row["level"] = 0
                    row["card_category_code"] = 0
                    row["card_category"] = ""
                decoded = codec.decode_record(codec.encode_record(row))
                self.assertEqual(
                    decoded["monster_type_code"],
                    monster_type_code,
                )
                self.assertEqual(
                    decoded["monster_type"],
                    "" if monster_type_code == 0 else decoded["monster_type"],
                )

    def test_property_attack_defense_and_level_boundaries(self):
        codec = NibbleStatisticsCodec()
        boundaries = (0, 10, 1270, 1280, 2550, 2560, 3830, 3840, 5110)
        for value in boundaries:
            with self.subTest(value=value):
                decoded = codec.decode_record(
                    codec.encode_record(self.property_row(attack=value, defense=value))
                )
                self.assertEqual(decoded["attack"], value)
                self.assertEqual(decoded["defense"], value)
        for level in (0, 1, 7, 8, 12, 15):
            with self.subTest(level=level):
                row = self.property_row(
                    level=level,
                    requires_two_tributes=level >= 8,
                )
                encoded = codec.encode_record(row)
                self.assertEqual(bool((encoded[3] >> 4) & 1), level >= 8)
                self.assertEqual(codec.decode_record(encoded)["level"], level)

    def test_property_category_and_monster_type_are_editable(self):
        codec = NibbleStatisticsCodec()
        effect = codec.decode_record(bytes.fromhex("82A0F466"))
        effect["card_category"] = "ritual"
        encoded = codec.encode_record(effect)
        self.assertEqual(encoded, bytes.fromhex("82A0FC66"))
        self.assertEqual(encoded[2] & 0x03, 0)

        summoned_skull = codec.decode_record(bytes.fromhex("7890314C"))
        summoned_skull["monster_type"] = "divine"
        summoned_skull["card_category"] = ""
        summoned_skull["card_category_code"] = 0
        encoded = codec.encode_record(summoned_skull)
        self.assertEqual(encoded, bytes.fromhex("00008041"))
        self.assertEqual(codec.decode_record(encoded)["level"], 0)

    def test_generic_codec_validation(self):
        integers = IntegerListCodec()
        for width in (0, 3):
            with self.assertRaises(ValueError):
                integers.decode(b"", byte_width=width)
            with self.assertRaises(ValueError):
                integers.encode([], byte_width=width)
        with self.assertRaises(ValueError):
            integers.decode(b"", byte_width=2, byte_order="middle")
        with self.assertRaises(ValueError):
            integers.encode([], byte_width=2, byte_order="middle")
        with self.assertRaises(ValueError):
            integers.decode(b"\x00", byte_width=2)

        strings = FixedStringListCodec()
        with self.assertRaises(ValueError):
            strings.decode(b"x", record_size=0, encoding="utf-8")
        with self.assertRaises(ValueError):
            strings.decode(
                b"xxxx",
                record_size=4,
                encoding="utf-8",
                terminator=b"",
            )
        with self.assertRaises(ValueError):
            strings.encode(["x"], record_size=0, encoding="utf-8")
        with self.assertRaises(ValueError):
            strings.encode(
                ["x"],
                record_size=4,
                encoding="utf-8",
                terminator=b"",
            )
        with self.assertRaises(ValueError):
            strings.encode(["toolong"], record_size=4, encoding="utf-8")

        offsets = OffsetStringTableCodec()
        with self.assertRaises(ValueError):
            offsets.decode(b"abc", [2, 1], encoding="utf-8")
        with self.assertRaises(ValueError):
            offsets.decode(
                b"abc",
                [0],
                encoding="utf-8",
                terminator=b"",
            )
        with self.assertRaises(ValueError):
            offsets.encode(
                indexed_records(["a"]),
                encoding="utf-8",
                alignment=0,
            )

        records = RecordTableCodec()
        with self.assertRaises(ValueError):
            records.decode(
                b"x",
                record_size=2,
                row_decoder=lambda value: {},
            )
        with self.assertRaises(ValueError):
            records.encode(
                [{}],
                record_size=2,
                row_encoder=lambda row: b"x",
            )

    def test_property_validation_and_functional_helpers(self):
        codec = NibbleStatisticsCodec()
        with self.assertRaises(ValueError):
            codec.decode_record(b"\x00")
        for field in ("attack", "defense"):
            for value in (-10, 5, 5120):
                row = self.property_row(**{field: value})
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        codec.encode_record(row)
        invalid_rows = (
            self.property_row(monster_type="unknown"),
            self.property_row(attribute="unknown"),
            self.property_row(card_category="unknown"),
            self.property_row(level=16),
            self.property_row(level=8, requires_two_tributes=False),
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    codec.encode_record(row)
        missing_category = self.property_row()
        missing_category.pop("card_category")
        missing_category.pop("card_category_code")
        with self.assertRaisesRegex(ValueError, "card_category"):
            codec.encode_record(missing_category)
        with self.assertRaisesRegex(ValueError, "row 1.*Attack"):
            RecordTableCodec().encode(
                [
                    self.property_row(),
                    self.property_row(attack=5),
                ],
                record_size=4,
                row_encoder=codec.encode_record,
            )

        integer_bytes = pack_int_list([1, 2], 2)
        self.assertEqual(unpack_int_list(integer_bytes, 2), [1, 2])
        string_bytes = pack_fixed_string_list(["A"], 4, "utf-8")
        self.assertEqual(
            unpack_fixed_string_list(string_bytes, 4, "utf-8"),
            ["A"],
        )
        records = indexed_records(["A", "B"])
        blob, index_bytes = pack_offset_strings(records, "utf-8")
        self.assertEqual(
            unpack_offset_strings(blob, index_bytes, "utf-8"),
            records,
        )
