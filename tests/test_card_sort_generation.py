from __future__ import annotations

import unittest
from unittest.mock import Mock

from yugioh_editor.common.card_errors import JapaneseReadingNotFoundError
from yugioh_editor.common.card_name_normalization import CardNameNormalizer
from yugioh_editor.common.constants import LANGUAGE_ENCODINGS
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.game.subfile_rule import RuleProcessingContext


class CardSortGenerationTests(unittest.TestCase):
    @staticmethod
    def _context(
        repository: GameRepository,
        language: str = "eng",
    ) -> RuleProcessingContext:
        rule = repository.find_rule(f"card_sort{language}.bin")
        return RuleProcessingContext(
            repository=repository,
            rule=rule,
            relative_path=f"bin#/card_sort{language}.bin",
            language=language,
            decode_params=dict(rule.decode_params),
            encode_params=dict(rule.encode_params),
            metadata={},
        )

    @staticmethod
    def _records(names: list[str], card_ids: list[int]):
        return [
            {"card_index": index, "name": name, "card_id": card_ids[index]}
            for index, name in enumerate(names)
        ]

    def test_normalized_sort_card_id_tiebreak_and_stable_equal_key(self):
        repository = GameRepository.from_root(".")
        result = repository.generate_sort_indices(
            self._records(
                ["", "Same", "same", "Alpha", "S-a-m-e"],
                [-1, 20, 10, 30, 10],
            ),
            context=self._context(repository),
        )
        self.assertEqual(result[:5], [0, 3, 1, 0, 2])
        self.assertEqual(sorted(result[1:5]), [0, 1, 2, 3])
        self.assertEqual(len(result), 8)
        self.assertFalse(any(result[5:]))

    def test_padding_uses_card_count_not_maximum_card_id(self):
        repository = GameRepository.from_root(".")
        result = repository.generate_sort_indices(
            self._records(["", "Alpha", "Beta"], [-1, 2, 10]),
            context=self._context(repository),
        )
        self.assertEqual(len(result), 4)
        self.assertEqual(result[:3], [0, 0, 1])
        self.assertFalse(any(result[3:]))

    def test_power_of_two_padding_boundaries(self):
        repository = GameRepository.from_root(".")
        context = self._context(repository)
        for card_count, expected_length in ((4, 4), (5, 8)):
            with self.subTest(card_count=card_count):
                names = [""] + [f"Card {index}" for index in range(1, card_count)]
                card_ids = [-1] + list(range(1, card_count))
                result = repository.generate_sort_indices(
                    self._records(names, card_ids),
                    context=context,
                )
                self.assertEqual(len(result), expected_length)
                self.assertFalse(any(result[card_count:]))

    def test_maximum_joey_record_count_has_4096_valid_inverse_ranks(self):
        repository = GameRepository.from_root(".")
        card_count = 4095
        names = [""] + [f"Card {index:04d}" for index in range(1, card_count)]
        card_ids = [-1, *range(1, card_count)]

        result = repository.generate_sort_indices(
            self._records(names, card_ids),
            context=self._context(repository),
        )

        self.assertEqual(len(result), 4096)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1:card_count], list(range(card_count - 1)))
        self.assertEqual(result[card_count], 0)

    def test_duplicate_low_card_ids_do_not_change_target_length(self):
        repository = GameRepository.from_root(".")
        result = repository.generate_sort_indices(
            self._records(["", "A", "B", "C"], [-1, 1, 1, 1]),
            context=self._context(repository),
        )
        self.assertEqual(len(result), 4)

    def test_empty_invalid_id_and_dummy_order_are_rejected(self):
        repository = GameRepository.from_root(".")
        context = self._context(repository)
        with self.assertRaisesRegex(ValueError, "empty"):
            repository.generate_sort_indices([], context=context)
        for invalid in (True, 1.5, "2", -2):
            with (
                self.subTest(invalid=invalid),
                self.assertRaises((TypeError, ValueError)),
            ):
                repository.generate_sort_indices(
                    self._records(["", "A"], [-1, invalid]),
                    context=context,
                )
        with self.assertRaisesRegex(ValueError, "dummy card index 0"):
            repository.generate_sort_indices(
                self._records(["Zulu", "Alpha"], [-1, 2]),
                context=context,
            )

    def test_japanese_sort_uses_reference_data_service_once_per_name(self):
        card_reference_data_service = Mock()
        card_reference_data_service.get_japanese_reading.side_effect = {
            "地縛霊": "アース・バウンド・スピリット",
            "鎧蜥蜴": "ヨロイ・トカゲ",
        }.__getitem__
        repository = GameRepository.from_root(
            ".",
            CardNameNormalizer(card_reference_data_service),
        )
        result = repository.generate_sort_indices(
            self._records(["", "鎧蜥蜴", "地縛霊"], [-1, 4, 3]),
            context=self._context(repository, "jpn"),
        )
        self.assertEqual(result[:3], [0, 1, 0])
        self.assertEqual(
            [
                call.args[0]
                for call in (
                    card_reference_data_service.get_japanese_reading.call_args_list
                )
            ],
            ["鎧蜥蜴", "地縛霊"],
        )

    def test_japanese_true_not_found_uses_original_name_as_sort_key(self):
        card_reference_data_service = Mock()

        def get_reading(name):
            if name == "既存":
                return "ワ"
            raise JapaneseReadingNotFoundError(name)

        card_reference_data_service.get_japanese_reading.side_effect = get_reading
        repository = GameRepository.from_root(
            ".",
            CardNameNormalizer(card_reference_data_service),
        )
        result = repository.generate_sort_indices(
            self._records(["", "既存", "未登録"], [-1, 2, 1]),
            context=self._context(repository, "jpn"),
        )

        self.assertEqual(result, [0, 0, 1, 0])
        reading_calls = card_reference_data_service.get_japanese_reading.call_args_list
        self.assertEqual(
            [call.args[0] for call in reading_calls],
            ["既存", "未登録"],
        )

    def test_non_japanese_sort_never_requests_japanese_readings(self):
        card_reference_data_service = Mock()
        card_reference_data_service.get_japanese_reading.side_effect = AssertionError(
            "non-Japanese sort requested a Japanese reading"
        )
        repository = GameRepository.from_root(
            ".",
            CardNameNormalizer(card_reference_data_service),
        )

        for language in (code for code in LANGUAGE_ENCODINGS if code != "jpn"):
            with self.subTest(language=language):
                result = repository.generate_sort_indices(
                    self._records(["", "Zulu", "Alpha"], [-1, 2, 1]),
                    context=self._context(repository, language),
                )
                self.assertEqual(result, [0, 1, 0, 0])

        card_reference_data_service.get_japanese_reading.assert_not_called()


if __name__ == "__main__":
    unittest.main()
