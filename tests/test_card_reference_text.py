from __future__ import annotations

import unittest

from yugioh_editor.common.card_reference_text import (
    normalize_reference_card_description,
    normalize_reference_card_name,
)


class CardReferenceTextTests(unittest.TestCase):
    def test_obnoxious_celtic_guard_localized_rename_notices_are_removed(self):
        values = {
            "fra": (
                "Guardien Celtique Recyclé (Actualisé de : Gardien Celtique Recyclé)",
                "Guardien Celtique Recyclé",
                "Non destructible au combat avec un monstre qui a min. 1900 ATK. "
                'Nom de carte actualisé de "Gardien Celtique Recyclé" le 22-07-2005.',
                "Non destructible au combat avec un monstre qui a min. 1900 ATK.",
            ),
            "spa": (
                "Guardia Celta Convertido (Actualizado de: Guardián Celta Convertido)",
                "Guardia Celta Convertido",
                "No puede ser destruida en batalla por un monstruo que tenga "
                "1900 ATK o más. Nombre de Carta actualizado de "
                '"Guardián Celta Convertido" en 20-10-2016.',
                "No puede ser destruida en batalla por un monstruo que tenga "
                "1900 ATK o más.",
            ),
            "ita": (
                "Guardia Celtica Riaddestrata "
                "(Aggiornato da: Guardiano Celtico Riaddestrato)",
                "Guardia Celtica Riaddestrata",
                "Non può essere distrutto in battaglia con un mostro che ha ATK "
                "di 1900 o "
                'superiore. Nome della carta aggiornato da "Guardiano Celtico '
                'Riaddestrato" il 20-10-2016.',
                "Non può essere distrutto in battaglia con un mostro che ha ATK "
                "di 1900 o "
                "superiore.",
            ),
            "ger": (
                "Ausgebildeter Keltischer Wärter "
                "(Geändert von: Ausgebildete Keltischer Wärter )",
                "Ausgebildeter Keltischer Wärter",
                "Kann nicht durch Kampf mit einem Monster zerstört werden, "
                "das 1900 oder mehr ATK hat. Kartenname geändert von "
                '„Ausgebildete Keltischer Wärter " '
                "am 20-10-2016.",
                "Kann nicht durch Kampf mit einem Monster zerstört werden, "
                "das 1900 oder mehr ATK hat.",
            ),
        }
        for language, (
            name,
            expected_name,
            description,
            expected_description,
        ) in values.items():
            with self.subTest(language=language):
                self.assertEqual(normalize_reference_card_name(name), expected_name)
                self.assertEqual(
                    normalize_reference_card_description(description),
                    expected_description,
                )

    def test_english_text_and_valid_parenthetical_are_preserved(self):
        name = "Obnoxious Celtic Guard"
        description = (
            "Cannot be destroyed by battle with a monster that has 1900 or more ATK."
        )
        self.assertEqual(normalize_reference_card_name(name), name)
        self.assertEqual(normalize_reference_card_description(description), description)
        self.assertEqual(
            normalize_reference_card_name("Card Name (Anime Artwork)"),
            "Card Name (Anime Artwork)",
        )

    def test_whitespace_entities_unicode_and_paragraphs_are_normalized(self):
        decomposed = "Cafe\u0301"
        value = f"  {decomposed}&amp;Name\u00a0 \t!\r\n\r\nSecond   line  "
        self.assertEqual(
            normalize_reference_card_description(value),
            "Café&Name!\n\nSecond line",
        )

    def test_similar_effect_wording_is_not_removed(self):
        values = (
            "This card's name becomes updated while it is face-up.",
            "If this card changed its name, draw 1 card. Then update the effect.",
            'Kartenname geändert von "Old" am 20-10-2016 without a full stop',
            'The effect continues. Name updated from "Old" on 20-10-2016.',
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(normalize_reference_card_description(value), value)

    def test_nested_rename_value_and_curly_quotes_are_supported(self):
        self.assertEqual(
            normalize_reference_card_name(
                "Primary (Actualisé de: Ancien nom (édition limitée))"
            ),
            "Primary",
        )
        self.assertEqual(
            normalize_reference_card_description(
                "Effect text. Kartenname geändert von „Alter Name“ am 2-7-2005."
            ),
            "Effect text.",
        )

    def test_normalizers_are_idempotent(self):
        values = (
            " Nom\u00a0 (Actualisé de : Ancien) ",
            "Effect. Nom de carte actualisé de “Ancien” le 2-7-2005.",
            "A&amp;amp;B\r\n\r\nParagraph",
        )
        normalizers = (
            normalize_reference_card_name,
            normalize_reference_card_description,
        )
        for normalizer in normalizers:
            for value in values:
                with self.subTest(normalizer=normalizer.__name__, value=value):
                    normalized = normalizer(value)
                    self.assertEqual(normalizer(normalized), normalized)


if __name__ == "__main__":
    unittest.main()
