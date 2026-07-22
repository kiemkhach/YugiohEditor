from __future__ import annotations

import unittest
from unittest.mock import Mock

import pandas as pd

from yugioh_editor.common.card_name_normalization import (
    CardNameNormalizer,
    normalize_japanese_reading,
    normalize_non_japanese_name,
)
from yugioh_editor.services.card_reference_data_service import (
    CardReferenceDataService,
)


class CardNameNormalizationTests(unittest.TestCase):
    def test_non_japanese_algorithm(self):
        fixtures = {
            "Dark-Piercing Light": "darkpiercing light",
            "Blue-Eyed Silver Zombie": "blueeyed silver zombie",
            "L'Amazone hostile": "lamazone hostile",
            "Bœuf de Combat": "boeuf de combat",
            "Chœur du Sanctuaire": "choeur du sanctuaire",
            "Électrofouet": "electrofouet",
            "Message spirituel « A »": "message spirituel ( a )",
            "A, B. & C": "a, b. & c",
        }
        for source, expected in fixtures.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_non_japanese_name(source), expected)

    def test_japanese_normalization_stages(self):
        fixtures = {
            "ＴＭ－１": "テイイエムイチ",
            "DNA": "テイイエヌエエ",
            "Aβ": "エエヘエタ",
            "１３": "シユウサン",
            "ひらがな": "ヒラカナ",
            "カー": "カア",
            "キャー": "キヤア",
            "ヴァ": "フア",
            "ガジバパ": "カシハハ",
            "ァィゥェォャュョッヮヵヶ": "アイウエオヤユヨツワカケ",
            "「A・B－C」": "エエヒイシイ",
        }
        for source, expected in fixtures.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_japanese_reading(source), expected)
        with self.assertRaisesRegex(ValueError, "Unsupported character.*漢"):
            normalize_japanese_reading("漢")

    def test_card_name_normalizer_uses_reference_data_abstraction(self):
        card_reference_data_service = Mock()
        card_reference_data_service.get_japanese_reading.return_value = "ヨロイ・トカゲ"
        normalizer = CardNameNormalizer(card_reference_data_service)
        self.assertEqual(normalizer.normalize("鎧蜥蜴", "jpn"), "ヨロイトカケ")
        card_reference_data_service.get_japanese_reading.assert_called_once_with(
            "鎧蜥蜴"
        )
        for alias in ("ja", "jp"):
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                normalizer.normalize("鎧蜥蜴", alias)

    def test_default_resource_normalizes_without_remote_api(self):
        mocked_ygocdb_card_client = Mock()
        service = CardReferenceDataService(ygocdb_client=mocked_ygocdb_card_client)
        normalizer = CardNameNormalizer(service)
        dataframe = pd.read_csv(
            service.japanese_reading_resource_path,
            dtype=str,
            encoding="utf-8-sig",
            keep_default_na=False,
        )
        keys = [
            normalizer.normalize(name, "jpn") for name in dataframe["display_name_jpn"]
        ]
        self.assertEqual(len(keys), len(dataframe))
        self.assertTrue(all(keys))
        mocked_ygocdb_card_client.fetch_japanese_reading.assert_not_called()


if __name__ == "__main__":
    unittest.main()
