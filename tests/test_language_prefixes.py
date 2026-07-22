import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tests.pipeline_support import (
    decode_description_resource,
    encode_description_resources,
    encode_sort_resource,
)
from yugioh_editor.common.constants import (
    LANGUAGE_ENCODINGS,
    LANGUAGE_PREFIXES,
)
from yugioh_editor.models.card_editing import CARD_CSV_COLUMNS
from yugioh_editor.models.entities import (
    ContainerArchive,
    ContainerEntry,
    DeckFile,
    ProjectManifest,
)
from yugioh_editor.repositories.game.connection import GameFolderConnection
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.project.repository import ProjectRepository
from yugioh_editor.services.project_service import ProjectService


class LanguagePrefixTests(unittest.TestCase):
    def test_language_registry_contains_only_canonical_prefixes(self):
        self.assertEqual(LANGUAGE_PREFIXES, tuple(LANGUAGE_ENCODINGS))
        self.assertEqual(LANGUAGE_ENCODINGS["jpn"], "cp932")
        self.assertTrue(
            all(
                encoding == "cp1252"
                for language, encoding in LANGUAGE_ENCODINGS.items()
                if language != "jpn"
            )
        )
        self.assertIn("spa", LANGUAGE_PREFIXES)

    def test_card_csv_columns_follow_the_language_registry(self):
        base = (
            "card_index",
            "card_id",
            "password",
            "level",
            "attack",
            "defense",
            "attribute",
            "card_type",
            "card_category",
            "pack",
            "image_name",
        )
        localized = tuple(
            column
            for language in LANGUAGE_PREFIXES
            for column in (f"name_{language}", f"desc_{language}")
        )
        self.assertEqual(CARD_CSV_COLUMNS, base + localized)
        for column in localized:
            self.assertEqual(CARD_CSV_COLUMNS.count(column), 1)

    def test_spanish_resource_names_use_spa_case_insensitively(self):
        for name in (
            "bin#/card_namespa.bin",
            "bin#/CARD_DESCSPA.BIN",
            "bin#/card_indxspa.bin",
            "bin#/CARD_SORTSPA.BIN",
            "Card_NameSpa.bin",
            "Card_DescSpa.bin",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(GameRepository.from_root(".").find_rule(name))

        self.assertEqual(
            GameRepository.find_bin_codec("bin#/card_namespa.bin"),
            "fixed_string_list",
        )
        repository = GameRepository.from_root(".")
        self.assertEqual(
            [
                step.method_name
                for step in repository.virtual_subfile_rule(
                    "bin#/card_indxspa.bin"
                ).pre_encode
            ],
            [
                "load_dependency_table",
                "dataframe_to_indexed_text_records",
                "generate_string_offsets",
                "pad_integer_sequence",
            ],
        )
        self.assertEqual(
            repository.virtual_subfile_rule("bin#/card_sortspa.bin")
            .pre_encode[1]
            .method_name,
            "generate_sort_indices",
        )

    def test_spanish_name_description_index_and_sort_round_trip(self):
        names = pd.DataFrame({"value": ["Ángel", "Zorro", "Baraja"]})
        encoded_names = GameRepository.encode_binary_resource(
            "card_namespa.bin",
            names,
            "spa",
        )
        decoded_names = GameRepository.decode_binary_resource(
            "card_namespa.bin",
            encoded_names,
            "spa",
        )
        self.assertEqual(decoded_names["value"].tolist(), names["value"].tolist())

        descriptions = pd.DataFrame({"value": ["Descripción uno", "Descripción dos"]})
        blob, indexes = encode_description_resources(
            descriptions,
            "spa",
        )
        decoded_descriptions = decode_description_resource(
            blob,
            indexes[: 2 * 4],
            "spa",
        )
        self.assertEqual(
            decoded_descriptions["text"].tolist(),
            descriptions["value"].tolist(),
        )
        self.assertEqual(len(indexes), 8192)
        sort = encode_sort_resource(
            names["value"].astype(str).tolist(),
            "spa",
        )
        self.assertEqual(len(sort), 8)

    def test_manifest_accepts_canonical_and_rejects_unknown_language(self):
        with tempfile.TemporaryDirectory() as directory:
            base = {
                "name": "Demo",
                "root_path": directory,
                "version_prefix": "mai",
                "files": [
                    {
                        "source_file": "Data.dat",
                        "relative_path": "bin#/card_namespa.bin",
                        "workspace_path": "data/bin#/card_namespa.bin",
                        "file_kind": "table",
                        "storage_format": "csv",
                        "language": "spa",
                    }
                ],
            }
            manifest = ProjectManifest.from_dict(base)
            self.assertEqual(manifest.files[0].language, "spa")

            base["files"][0]["relative_path"] = "bin#/card_namezzz.bin"
            with self.assertRaisesRegex(
                ValueError,
                r"zzz.*card_namezzz",
            ):
                ProjectManifest.from_dict(base)

            base["files"][0]["relative_path"] = "bin#/card_namespa.bin"
            base["files"][0]["language"] = "zzz"
            with self.assertRaisesRegex(
                ValueError,
                r"zzz.*card_namespa",
            ):
                ProjectManifest.from_dict(base)

    def test_spanish_project_unpack_edit_and_pack_regenerates_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = GameFolderConnection(root / "game")
            names = pd.DataFrame({"value": ["", "Ángel", "Zorro"]})
            descriptions = pd.DataFrame(
                {"value": ["Back", "Descripción uno", "Descripción dos"]}
            )
            description_blob, description_index = encode_description_resources(
                descriptions,
                "spa",
            )
            entries = [
                ContainerEntry(
                    "bin#/card_id.bin",
                    data=b"\xff\xff\x01\x00\x03\x00",
                    order=0,
                ),
                ContainerEntry(
                    "bin#/card_intid.bin",
                    data=b"\x00" * 4096,
                    order=1,
                ),
                ContainerEntry(
                    "bin#/card_namespa.bin",
                    data=GameRepository.encode_binary_resource(
                        "card_namespa.bin",
                        names,
                        "spa",
                    ),
                    order=2,
                ),
                ContainerEntry(
                    "bin#/card_sortspa.bin",
                    data=b"\x00" * 4096,
                    order=3,
                ),
                ContainerEntry(
                    "bin#/card_descspa.bin",
                    data=description_blob,
                    order=4,
                ),
                ContainerEntry(
                    "bin#/card_indxspa.bin",
                    data=description_index,
                    order=5,
                ),
            ]
            game.write_container(
                "Data.dat",
                ContainerArchive("Data.dat", entries=entries),
                "never",
            )
            game.write_container(
                "Voice.dat",
                ContainerArchive("Voice.dat", entries=[]),
                "never",
            )
            game.write_binary("Region.dat", b"REGION")
            game.write_deck("deck.ydc", DeckFile())

            service = ProjectService()
            manifest = service.create_project(
                "Spanish",
                root / "workspace",
                root / "game",
                "mai",
            )
            spanish_records = [
                item
                for item in manifest.files
                if item.relative_path.casefold().endswith(
                    (
                        "card_namespa.bin",
                        "card_sortspa.bin",
                        "card_descspa.bin",
                        "card_indxspa.bin",
                    )
                )
            ]
            self.assertEqual(len(spanish_records), 4)
            self.assertTrue(all(item.language == "spa" for item in spanish_records))

            project = ProjectRepository(manifest.root)
            name_record = next(
                item
                for item in spanish_records
                if item.relative_path.casefold().endswith("card_namespa.bin")
            )
            description_record = next(
                item
                for item in spanish_records
                if item.relative_path.casefold().endswith("card_descspa.bin")
            )
            name_table = project.read_table(name_record.workspace_path or "")
            name_table.loc[1, "value"] = "Águila"
            project.write_table(name_record.workspace_path or "", name_table)
            description_table = project.read_table(
                description_record.workspace_path or ""
            )
            description_table.loc[1, "text"] = "Descripción actualizada"
            project.write_table(
                description_record.workspace_path or "",
                description_table,
            )

            output = service.pack_project(manifest)
            packed = GameFolderConnection(output).read_container("Data.dat")
            payloads = {
                item.relative_path.replace("\\", "/").casefold(): item.data
                for item in packed.entries
            }
            self.assertIn("bin#/card_namespa.bin", payloads)
            self.assertIn("bin#/card_descspa.bin", payloads)
            self.assertIn("bin#/card_indxspa.bin", payloads)
            self.assertIn("bin#/card_sortspa.bin", payloads)
            decoded_names = GameRepository.decode_binary_resource(
                "card_namespa.bin",
                payloads["bin#/card_namespa.bin"],
                "spa",
            )
            self.assertEqual(decoded_names.loc[1, "value"], "Águila")
            decoded_descriptions = decode_description_resource(
                payloads["bin#/card_descspa.bin"],
                payloads["bin#/card_indxspa.bin"][: 3 * 4],
                "spa",
            )
            self.assertEqual(
                decoded_descriptions.loc[1, "text"],
                "Descripción actualizada",
            )
            self.assertEqual(
                len(payloads["bin#/card_indxspa.bin"]),
                8192,
            )
            self.assertEqual(
                len(payloads["bin#/card_sortspa.bin"]),
                8,
            )


if __name__ == "__main__":
    unittest.main()
