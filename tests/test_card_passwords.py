from __future__ import annotations

import unittest

from yugioh_editor.common.card_passwords import (
    CARD_PASSWORD_BYTE_WIDTH,
    CARD_PASSWORD_HEX_WIDTH,
    MISSING_CARD_PASSWORD,
    is_missing_card_password,
    legacy_card_password_to_hex,
    normalize_card_password,
)


class CardPasswordTests(unittest.TestCase):
    def test_normalize_requires_one_record_and_returns_uppercase(self):
        self.assertEqual(CARD_PASSWORD_BYTE_WIDTH, 4)
        self.assertEqual(CARD_PASSWORD_HEX_WIDTH, 8)
        self.assertEqual(normalize_card_password(" 00abcdef "), "00ABCDEF")
        self.assertEqual(normalize_card_password("00000000"), "00000000")
        for value in (None, True, 12345678, "1234567", "123456789", "12345G78"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_card_password(value)

    def test_missing_sentinel_requires_a_valid_normalized_password(self):
        self.assertEqual(MISSING_CARD_PASSWORD, "FFFFFFFF")
        self.assertTrue(is_missing_card_password("ffffffff"))
        self.assertTrue(is_missing_card_password(" FFFFFFFF "))
        for value in (None, 4294967295, "FFFFFFF", "not a password"):
            with self.subTest(value=value):
                self.assertFalse(is_missing_card_password(value))

    def test_legacy_unsigned_integer_recovers_original_little_endian_bytes(self):
        vectors = {
            0: "00000000",
            1: "01000000",
            int.from_bytes(bytes.fromhex("00503000"), "little"): "00503000",
            2018915346: "12345678",
            4294967295: "FFFFFFFF",
            "2018915346": "12345678",
        }
        for value, expected in vectors.items():
            with self.subTest(value=value):
                self.assertEqual(legacy_card_password_to_hex(value), expected)
        for value in (-1, 4294967296, True, "1.0", "FFFFFFFF"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    legacy_card_password_to_hex(value)


if __name__ == "__main__":
    unittest.main()
