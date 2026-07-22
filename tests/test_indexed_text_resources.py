import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tests.pipeline_support import (
    decode_description_resource,
    decode_dialog_resource,
    encode_description_resources,
    encode_dialog_resources,
    indexed_text_table,
)
from yugioh_editor.common.constants import validate_language_resource_path
from yugioh_editor.common.errors import PackResourceError, RulePipelineError
from yugioh_editor.models.entities import (
    ContainerArchive,
    ContainerEntry,
    ProjectManifest,
)
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.project.repository import ProjectRepository

REAL_CARD_DESC_RECORD_ONE = bytes.fromhex(
    "41 20 76 65 6e 67 65 66 75 6c 20 63 72 65 61 74 75 72 "
    "65 20 66 6f 72 6d 65 64 20 62 79 20 74 68 65 20 73 70 "
    "69 72 69 74 73 20 6f 66 20 66 61 6c 6c 65 6e 20 77 61 "
    "72 72 69 6f 72 73 2c 20 69 74 20 64 72 61 67 73 20 61 "
    "6e 79 20 77 68 6f 20 64 61 72 65 20 61 70 70 72 6f 61 "
    "63 68 20 69 74 20 69 6e 74 6f 20 74 68 65 20 64 65 65 "
    "70 65 73 74 20 62 6f 77 65 6c 73 20 6f 66 20 74 68 65 "
    "20 65 61 72 74 68 2e 00 00 00"
)
REAL_CARD_DESC_TEXT_ONE = (
    "A vengeful creature formed by the spirits of fallen warriors, "
    "it drags any who dare approach it into the deepest bowels of the earth."
)
FRENCH_RECORD_369 = bytes.fromhex(
    "53 61 63 72 69 66 69 65 7a 20 31 20 6d 6f 6e 73 "
    "74 72 65 20 54 c9 4e c8 42 52 45 53 20 61 76 65 "
    "63 20 75 6e 65 20 41 54 4b 20 64 65 20 31 30 30 "
    "30 20 70 6f 69 6e 74 73 20 6f 75 20 6d 6f 69 6e "
    "73 20 64 65 20 76 6f 74 72 65 54 65 72 72 61 69 "
    "6e 2e 20 54 6f 75 73 20 6c 65 73 20 6d 6f 6e 73 "
    "74 72 65 73 20 61 76 65 63 20 75 6e 65 20 41 54 "
    "4b 20 64 65 20 31 35 30 30 20 70 6f 69 6e 74 73 "
    "20 6f 75 20 70 6c 75 73 20 73 75 72 20 6c 65 54 "
    "65 72 72 61 69 6e 20 64 65 20 76 6f 74 72 65 20 "
    "61 64 76 65 72 73 61 69 72 65 2c 20 64 61 6e 73 "
    "20 73 61 20 6d 61 69 6e 2c 20 65 74 20 63 65 75 "
    "78 20 71 75 27 69 6c 2f 65 6c 6c 65 20 70 69 6f "
    "63 68 65 20 64 75 72 61 6e 74 73 65 73 20 33 20 "
    "70 72 6f 63 68 61 69 6e 73 20 74 6f 75 72 73 20 "
    "73 6f 6e 74 20 64 e9 74 72 75 69 74 73 2e 00 75"
)
FRENCH_RECORD_370 = bytes.fromhex(
    "44 e9 74 72 75 69 74 20 6c 27 75 6e 20 64 65 73 "
    "20 6d 6f 6e 73 74 72 65 73 20 64 65 20 76 6f 74 "
    "72 65 20 61 64 76 65 72 73 61 69 72 65 20 65 74 "
    "20 76 6f 75 73 20 64 6f 6e 6e 65 20 6c 65 20 63 "
    "6f 6e 74 72 f4 6c 65 20 64 65 20 6c 27 75 6e 20 "
    "64 65 20 76 6f 73 20 6d 6f 6e 73 74 72 65 73 20 "
    "73 75 72 20 6c 65 20 74 65 72 72 61 69 6e 20 61 "
    "64 76 65 72 73 65 2e 00 00 00"
)
FRENCH_TEXT_369 = (
    "Sacrifiez 1 monstre TÉNÈBRES avec une ATK de 1000 points ou moins de "
    "votreTerrain. Tous les monstres avec une ATK de 1500 points ou plus sur "
    "leTerrain de votre adversaire, dans sa main, et ceux qu'il/elle pioche "
    "durantses 3 prochains tours sont détruits."
)
FRENCH_TEXT_370 = (
    "Détruit l'un des monstres de votre adversaire et vous donne le contrôle "
    "de l'un de vos monstres sur le terrain adverse."
)


def uint32_table(*values: int) -> bytes:
    return b"".join(value.to_bytes(4, "little") for value in values)


class IndexedTextResourceTests(unittest.TestCase):
    def test_french_real_records_keep_dirty_padding_out_of_both_strings(self):
        raw_blob = FRENCH_RECORD_369 + FRENCH_RECORD_370
        raw_index = uint32_table(0, len(FRENCH_RECORD_369))
        table = decode_description_resource(raw_blob, raw_index, "fra")
        self.assertEqual(table["text"].tolist(), [FRENCH_TEXT_369, FRENCH_TEXT_370])
        self.assertEqual(list(table.columns), ["text", "is_reserved"])
        self.assertEqual(table["is_reserved"].tolist(), [False, False])
        self.assertTrue(FRENCH_RECORD_369.endswith(bytes.fromhex("00 75")))
        self.assertTrue(FRENCH_RECORD_370.startswith(bytes.fromhex("44 e9")))

        rebuilt_blob, rebuilt_index = encode_description_resources(table, "fra")
        self.assertEqual(
            rebuilt_blob,
            FRENCH_RECORD_369[:-1] + b"\x00" + FRENCH_RECORD_370,
        )
        self.assertEqual(rebuilt_index[:8], raw_index)

    def test_card_description_raw_vectors_cover_all_languages(self):
        cases = (
            ("jpn", bytes.fromhex("93 fa 96 7b 00 00"), "日本"),
            ("eng", bytes.fromhex("41 42 00 00"), "AB"),
            ("fra", bytes.fromhex("e9 00 00 00"), "é"),
            ("ger", bytes.fromhex("d6 00 00 00"), "Ö"),
            ("spa", bytes.fromhex("f1 00 00 00"), "ñ"),
            ("ita", bytes.fromhex("e0 00 00 00"), "à"),
        )
        for language, raw_blob, expected in cases:
            with self.subTest(language=language):
                table = decode_description_resource(
                    raw_blob,
                    uint32_table(0),
                    language,
                )
                self.assertEqual(table["text"].tolist(), [expected])
                rebuilt_blob, rebuilt_index = encode_description_resources(
                    table,
                    language,
                )
                self.assertEqual(rebuilt_blob, raw_blob)
                self.assertEqual(rebuilt_index[:4], uint32_table(0))

    def test_real_card_description_raw_vector_decodes_and_packs_exactly(self):
        raw_blob = bytes.fromhex("00 00") + REAL_CARD_DESC_RECORD_ONE
        raw_index = uint32_table(0, 2)
        table = decode_description_resource(raw_blob, raw_index, "eng")
        self.assertEqual(
            table.to_dict("records"),
            [
                {"text": "", "is_reserved": False},
                {"text": REAL_CARD_DESC_TEXT_ONE, "is_reserved": False},
            ],
        )

        rebuilt_blob, rebuilt_index = encode_description_resources(table, "eng")
        self.assertEqual(rebuilt_blob, raw_blob)
        self.assertEqual(rebuilt_index[:8], raw_index)
        self.assertEqual(rebuilt_index[8:], b"\x00" * (2048 * 4 - 8))

    def test_dialog_raw_vectors_use_the_same_padding_and_offsets(self):
        raw_blob = bytes.fromhex("00 00 41 42 43 00")
        raw_index = uint32_table(0, 2)
        table = decode_dialog_resource(raw_blob, raw_index, "eng")
        self.assertEqual(table["text"].tolist(), ["", "ABC"])
        self.assertEqual(list(table.columns), ["text", "is_reserved"])
        self.assertEqual(table["is_reserved"].tolist(), [False, False])

        rebuilt_blob, rebuilt_index = encode_dialog_resources(table, "eng")
        self.assertEqual(rebuilt_blob, raw_blob)
        self.assertEqual(rebuilt_index, raw_index)

    def test_description_and_dialog_exact_round_trip_through_project_persistence(self):
        card_blob = bytes.fromhex("00 00 41 42 43 00 00 00")
        card_index = uint32_table(0, 2) + b"\x00" * (2048 * 4 - 8)
        dialog_blob = bytes.fromhex("00 00 41 42 43 00")
        dialog_index = uint32_table(0, 2)
        archive = ContainerArchive(
            "Data.dat",
            entries=[
                ContainerEntry("bin#/card_id.bin", data=b"\x00\x00" * 2, order=0),
                ContainerEntry(
                    "bin#/card_desceng.bin",
                    data=card_blob,
                    order=1,
                ),
                ContainerEntry(
                    "bin#/card_indxeng.bin",
                    data=card_index,
                    order=2,
                ),
                ContainerEntry(
                    "bin#/dlg_texteng.bin",
                    data=dialog_blob,
                    order=3,
                ),
                ContainerEntry(
                    "bin#/dlg_indxeng.bin",
                    data=dialog_index,
                    order=4,
                ),
            ],
        )
        game = GameRepository.from_root(".")
        resources = game.decode_archive(archive, "data")

        with tempfile.TemporaryDirectory() as directory:
            manifest = ProjectManifest(
                "Indexed text persistence",
                str(Path(directory) / "project"),
                version_prefix="mai",
                game_files={"data.dat": "Data.dat"},
            )
            project = ProjectRepository(manifest)
            project.ensure_root()
            manifest.files.extend(project.import_resources(resources))
            project.save(manifest)

            for suffix in ("card_desceng.bin", "dlg_texteng.bin"):
                record = next(
                    item
                    for item in manifest.files
                    if item.relative_path.casefold().endswith(suffix)
                )
                csv_path = manifest.root / str(record.workspace_path)
                self.assertEqual(
                    csv_path.read_text(encoding="utf-8-sig").splitlines()[0],
                    "text,is_reserved",
                )

            reopened_manifest = ProjectRepository(manifest.root).load()
            reopened = ProjectRepository(reopened_manifest)
            for table_name in ("card_descriptions", "dialog_texts"):
                frame = reopened.get_table(table_name, language="eng")
                self.assertEqual(list(frame.columns), ["text", "is_reserved"])
                self.assertEqual(frame["text"].tolist(), ["", "ABC"])
                self.assertEqual(frame["is_reserved"].tolist(), [False, False])
            exported = reopened.export_resources(
                ProjectRepository.list_resources(
                    reopened_manifest,
                    include_virtual=True,
                )
            )
            rebuilt = game.encode_archive("Data.dat", exported)

        payloads = {entry.relative_path: entry.data for entry in rebuilt.entries}
        self.assertEqual(payloads["bin#/card_desceng.bin"], card_blob)
        self.assertEqual(payloads["bin#/card_indxeng.bin"], card_index)
        self.assertEqual(payloads["bin#/dlg_texteng.bin"], dialog_blob)
        self.assertEqual(payloads["bin#/dlg_indxeng.bin"], dialog_index)

    def test_single_column_edit_pack_preserves_row_identity_and_reserved_offsets(self):
        original_values = ["", "A", "", "", "Same", "Same", "Final"]
        card_blob = bytes.fromhex(
            "00 00 41 00 00 00 "
            "53 61 6d 65 00 00 53 61 6d 65 00 00 "
            "46 69 6e 61 6c 00 00 00"
        )
        card_offsets = [0, 2, 0, 0, 6, 12, 18]
        card_index = uint32_table(*card_offsets) + b"\x00" * (2048 * 4 - 28)
        dialog_blob = bytes.fromhex(
            "00 00 41 00 53 61 6d 65 00 00 53 61 6d 65 00 00 46 69 6e 61 6c 00"
        )
        dialog_offsets = [0, 2, 0, 0, 4, 10, 16]
        dialog_index = uint32_table(*dialog_offsets)
        archive = ContainerArchive(
            "Data.dat",
            entries=[
                ContainerEntry("bin#/card_id.bin", data=b"\x00\x00" * 7, order=0),
                ContainerEntry("bin#/card_desceng.bin", data=card_blob, order=1),
                ContainerEntry("bin#/card_indxeng.bin", data=card_index, order=2),
                ContainerEntry("bin#/dlg_texteng.bin", data=dialog_blob, order=3),
                ContainerEntry("bin#/dlg_indxeng.bin", data=dialog_index, order=4),
            ],
        )
        game = GameRepository.from_root(".")
        decoded_resources = game.decode_archive(archive, "data")

        with tempfile.TemporaryDirectory() as directory:
            manifest = ProjectManifest(
                "Single column indexed text",
                str(Path(directory) / "project"),
                version_prefix="mai",
                game_files={"data.dat": "Data.dat"},
            )
            project = ProjectRepository(manifest)
            project.ensure_root()
            manifest.files.extend(project.import_resources(decoded_resources))
            project.save(manifest)

            reopened_manifest = ProjectRepository(manifest.root).load()
            reopened = ProjectRepository(reopened_manifest)
            descriptions = reopened.get_table("card_descriptions", language="eng")
            dialogs = reopened.get_table("dialog_texts", language="eng")
            self.assertEqual(list(descriptions.columns), ["text", "is_reserved"])
            self.assertEqual(list(dialogs.columns), ["text", "is_reserved"])
            self.assertEqual(descriptions["text"].tolist(), original_values)
            self.assertEqual(dialogs["text"].tolist(), original_values)
            self.assertEqual(
                descriptions["is_reserved"].tolist(),
                [False, False, True, True, False, False, False],
            )
            self.assertEqual(
                dialogs["is_reserved"].tolist(),
                [False, False, True, True, False, False, False],
            )

            descriptions.loc[1, "text"] = "Edited"
            reopened.save_table(
                "card_descriptions",
                descriptions,
                language="eng",
            )
            exported = reopened.export_resources(
                reopened.list_resources(reopened_manifest, include_virtual=True)
            )
            packed = game.encode_archive("Data.dat", exported)

        payloads = {entry.relative_path: entry.data for entry in packed.entries}
        expected_card_values = ["", "Edited", "", "", "Same", "Same", "Final"]
        expected_card_offsets = [0, 2, 0, 0, 10, 16, 22]
        self.assertEqual(
            payloads["bin#/card_desceng.bin"],
            bytes.fromhex(
                "00 00 45 64 69 74 65 64 00 00 "
                "53 61 6d 65 00 00 53 61 6d 65 00 00 "
                "46 69 6e 61 6c 00 00 00"
            ),
        )
        self.assertEqual(
            payloads["bin#/card_indxeng.bin"],
            uint32_table(*expected_card_offsets) + b"\x00" * (2048 * 4 - 28),
        )
        self.assertEqual(payloads["bin#/dlg_texteng.bin"], dialog_blob)
        self.assertEqual(payloads["bin#/dlg_indxeng.bin"], dialog_index)

        reopened_resources = game.decode_archive(
            ContainerArchive(
                "Data.dat",
                entries=[
                    ContainerEntry("bin#/card_id.bin", data=b"\x00\x00" * 7, order=0),
                    *[
                        ContainerEntry(path, data=payloads[path], order=order)
                        for order, path in enumerate(
                            (
                                "bin#/card_desceng.bin",
                                "bin#/card_indxeng.bin",
                                "bin#/dlg_texteng.bin",
                                "bin#/dlg_indxeng.bin",
                            ),
                            start=1,
                        )
                    ],
                ],
            ),
            "data",
        )
        reopened_tables = {
            Path(resource.record.relative_path.replace("\\", "/")).name: resource.value
            for resource in reopened_resources
            if Path(resource.record.relative_path.replace("\\", "/")).name
            in {"card_desceng.bin", "dlg_texteng.bin"}
        }
        self.assertEqual(
            reopened_tables["card_desceng.bin"]["text"].tolist(),
            expected_card_values,
        )
        self.assertEqual(
            reopened_tables["dlg_texteng.bin"]["text"].tolist(),
            original_values,
        )

    def test_description_dependencies_pair_indexes_by_language(self):
        repository = GameRepository.from_root(".")
        archive = ContainerArchive(
            "Data.dat",
            entries=[
                ContainerEntry(
                    "bin#/card_id.bin",
                    data=b"\x00\x00" * 2,
                    order=0,
                ),
                ContainerEntry(
                    "bin#/card_descjpn.bin",
                    data=bytes.fromhex("00 00") + "日本".encode("cp932") + b"\x00\x00",
                    order=1,
                ),
                ContainerEntry(
                    "bin#/card_indxeng.bin",
                    data=uint32_table(0, 2),
                    order=2,
                ),
                ContainerEntry(
                    "bin#/card_desceng.bin",
                    data=bytes.fromhex("00 00 45 4e 47 00 00 00"),
                    order=3,
                ),
                ContainerEntry(
                    "bin#/card_indxjpn.bin",
                    data=uint32_table(0, 2),
                    order=4,
                ),
            ],
        )
        resources = repository.decode_archive(archive, "data")
        tables = {
            resource.record.language: resource.value
            for resource in resources
            if "card_desc" in resource.record.relative_path.casefold()
        }
        self.assertEqual(tables["eng"]["text"].tolist(), ["", "ENG"])
        self.assertEqual(tables["jpn"]["text"].tolist(), ["", "日本"])

    def test_malformed_description_reports_record_boundaries(self):
        with self.assertRaisesRegex(
            RulePipelineError,
            r"card_desceng\.bin.*record_index=1.*start_offset=2.*"
            r"end_offset=4.*first_null_position=1.*"
            r"bytes_after_terminator=0.*tail_hex=''",
        ):
            decode_description_resource(
                bytes.fromhex("00 00 41 00"),
                uint32_table(0, 2),
                "eng",
            )

    def test_card_description_reserved_entry_round_trip(self):
        table = indexed_text_table(
            ["First", "", "Same", "", "Same"],
            reserved={1, 3},
        )
        blob, index_bytes = encode_description_resources(table, "eng")
        offsets = [
            int.from_bytes(index_bytes[position : position + 4], "little")
            for position in range(0, 20, 4)
        ]
        self.assertEqual(offsets[0], 0)
        self.assertEqual(offsets[1], 0)
        self.assertEqual(offsets[3], 0)
        self.assertNotEqual(offsets[2], offsets[4])
        rebuilt = decode_description_resource(blob, index_bytes[:20], "eng")
        pd.testing.assert_frame_equal(rebuilt, table)

    def test_indexed_text_reserved_boolean_is_canonical_and_strict(self):
        canonical = pd.DataFrame({"text": ["A", ""], "is_reserved": ["False", "True"]})
        blob, index_bytes = encode_description_resources(canonical, "eng")
        self.assertEqual(blob, b"A\x00\x00\x00")
        self.assertEqual(index_bytes[:8], uint32_table(0, 0))

        for invalid in ("false", "", float("nan")):
            with self.subTest(invalid=invalid):
                table = pd.DataFrame(
                    {"text": ["A", ""], "is_reserved": [False, invalid]}
                )
                with self.assertRaisesRegex(
                    PackResourceError,
                    r"is_reserved must be the canonical boolean True or False",
                ):
                    encode_description_resources(table, "eng")

    def test_dialog_english_control_tokens_reserved_and_pack_generation(self):
        table = indexed_text_table(
            ["@0 Draw %d cards", "", "Repeat %s", "Repeat %s"],
            reserved={1},
        )
        blob, index_bytes = encode_dialog_resources(table, "eng")
        self.assertEqual(len(index_bytes), len(table) * 4)
        offsets = [
            int.from_bytes(index_bytes[position : position + 4], "little")
            for position in range(0, len(index_bytes), 4)
        ]
        self.assertEqual(offsets[1], 0)
        self.assertNotEqual(offsets[2], offsets[3])
        rebuilt = decode_dialog_resource(blob, index_bytes, "eng")
        pd.testing.assert_frame_equal(rebuilt, table)

    def test_dialog_japanese_uses_cp932_and_round_trips(self):
        table = indexed_text_table(
            ["@2日本語%s", "終了"],
        )
        blob, index_bytes = encode_dialog_resources(table, "jpn")
        self.assertIn("日本語".encode("cp932"), blob)
        rebuilt = decode_dialog_resource(blob, index_bytes, "jpn")
        pd.testing.assert_frame_equal(rebuilt, table)

    def test_dialog_invalid_index_width_and_encoding_error_have_context(self):
        with self.assertRaisesRegex(
            RulePipelineError,
            r"dlg_indxeng\.bin.*not aligned.*item width",
        ):
            GameRepository.decode_binary_resource(
                "dlg_indxeng.bin",
                b"\x00\x00\x00",
                "eng",
            )
        table = indexed_text_table(
            ["valid", "漢"],
        )
        with self.assertRaisesRegex(
            PackResourceError,
            r"dlg_texteng\.bin.*language='eng'.*cp1252.*record 1",
        ):
            encode_dialog_resources(table, "eng")

    def test_dialog_rules_are_language_dependent_physical_and_virtual(self):
        repository = GameRepository.from_root(".")
        text_rule = repository.find_rule("bin#/DLG_TEXTSPA.BIN")
        index_rule = repository.find_rule("bin#/dlg_indxspa.bin")
        self.assertEqual(text_rule.table_name, "dialog_texts")
        self.assertEqual(text_rule.table_parameters, ("language",))
        self.assertFalse(text_rule.virtual)
        self.assertTrue(index_rule.virtual)
        self.assertEqual(index_rule.codec_name, "integer_list")
        self.assertEqual(text_rule.decode_params["minimum_padding"], 1)
        self.assertEqual(
            text_rule.decode_params["input_padding_policy"], "pointer_bounded"
        )
        self.assertEqual(text_rule.encode_params["minimum_padding"], 1)
        offset_step = next(
            step
            for step in index_rule.pre_encode
            if step.method_name == "generate_string_offsets"
        )
        self.assertEqual(offset_step.params["minimum_padding"], 1)

        description_rule = repository.find_rule("bin#/card_desceng.bin")
        description_index_rule = repository.find_rule("bin#/card_indxeng.bin")
        self.assertEqual(description_rule.decode_params["minimum_padding"], 2)
        self.assertEqual(
            description_rule.decode_params["input_padding_policy"],
            "pointer_bounded",
        )
        self.assertEqual(description_rule.encode_params["minimum_padding"], 2)
        description_offset_step = next(
            step
            for step in description_index_rule.pre_encode
            if step.method_name == "generate_string_offsets"
        )
        self.assertEqual(description_offset_step.params["minimum_padding"], 2)

        with self.assertRaisesRegex(ValueError, "span"):
            validate_language_resource_path("bin#/dlg_textspan.bin")


if __name__ == "__main__":
    unittest.main()
