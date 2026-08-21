import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import pandas as pd

from tests.pipeline_support import (
    decode_description_resource,
    encode_description_resources,
)
from yugioh_editor.common.card_errors import JapaneseReadingNotFoundError
from yugioh_editor.common.errors import (
    InvalidFileFormatError,
    PackResourceError,
    ProjectValidationError,
)
from yugioh_editor.models.entities import (
    ContainerArchive,
    ContainerEntry,
    DeckFile,
    ProjectFileRecord,
)
from yugioh_editor.repositories.game.connection import GameFolderConnection
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.project.repository import ProjectRepository
from yugioh_editor.services.card_reference_data_service import (
    CardReferenceDataService,
)
from yugioh_editor.services.project_service import ProjectService
from yugioh_editor.services.subfile_service import SubfileService


class DatDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _write_game(
        root: Path,
        data_name: str = "Data.dat",
        voice_name: str = "Voice.dat",
        region_name: str = "Region.dat",
    ) -> GameFolderConnection:
        game = GameFolderConnection(root)
        game.write_container(
            data_name,
            ContainerArchive(data_name, entries=[]),
            "never",
        )
        game.write_container(
            voice_name,
            ContainerArchive(voice_name, entries=[]),
            "never",
        )
        game.write_binary(region_name, b"REGION-RAW")
        game.write_deck("deck.ydc", DeckFile())
        return game

    def test_dat_names_are_case_insensitive_and_casing_is_preserved(self):
        variants = (
            ("Data.dat", "Voice.dat", "Region.dat"),
            ("data.dat", "voice.dat", "region.dat"),
            ("DATA.DAT", "VOICE.DAT", "REGION.DAT"),
            ("Data.DAT", "Voice.DAT", "Region.DAT"),
        )
        for data_name, voice_name, region_name in variants:
            with self.subTest(names=(data_name, voice_name, region_name)):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self._write_game(
                        root / "game",
                        data_name,
                        voice_name,
                        region_name,
                    )
                    manifest = ProjectService().create_project(
                        "Demo",
                        root / "workspace",
                        root / "game",
                        "mai",
                    )
                    self.assertEqual(manifest.game_files["data.dat"], data_name)
                    self.assertEqual(manifest.game_files["voice.dat"], voice_name)
                    self.assertEqual(manifest.game_files["region.dat"], region_name)
                    self.assertEqual(
                        (manifest.root / "region" / region_name).read_bytes(),
                        b"REGION-RAW",
                    )
                    output = ProjectService().pack_project(manifest)
                    self.assertTrue((output / data_name).exists())
                    self.assertTrue((output / voice_name).exists())
                    self.assertEqual(
                        (output / region_name).read_bytes(),
                        b"REGION-RAW",
                    )

    def test_top_level_bin_files_are_not_dat_containers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = GameFolderConnection(root / "game")
            game.write_container(
                "data.bin",
                ContainerArchive("data.bin", entries=[]),
                "never",
            )
            game.write_container(
                "voice.bin",
                ContainerArchive("voice.bin", entries=[]),
                "never",
            )
            game.write_binary("Region.dat", b"raw")
            game.write_deck("deck.ydc", DeckFile())
            with self.assertRaises(ProjectValidationError):
                ProjectService().create_project(
                    "Demo",
                    root / "workspace",
                    root / "game",
                    "mai",
                )

    def test_data_and_voice_require_signature_but_region_does_not(self):
        for invalid_name in ("Data.dat", "Voice.dat"):
            with self.subTest(file_name=invalid_name):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    game = self._write_game(root / "game")
                    game.write_binary(invalid_name, b"not-a-container")
                    with self.assertRaisesRegex(
                        InvalidFileFormatError,
                        "KCEJYUGI",
                    ):
                        ProjectService().create_project(
                            "Demo",
                            root / "workspace",
                            root / "game",
                            "mai",
                        )


class VirtualResourceTests(unittest.TestCase):
    def test_japanese_sort_pack_retries_not_found_without_csv_pollution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reading_resource = root / "card_reading_jpn.csv"
            pd.DataFrame(
                [("既存", "ワ")],
                columns=["display_name_jpn", "reading_jpn"],
            ).to_csv(
                reading_resource,
                index=False,
                encoding="utf-8-sig",
                lineterminator="\n",
            )
            original_reading_bytes = reading_resource.read_bytes()
            ygocdb_client = Mock()
            ygocdb_client.fetch_japanese_reading.side_effect = (
                JapaneseReadingNotFoundError("未登録"),
                JapaneseReadingNotFoundError("未登録"),
                "ア",
            )
            reading_service = CardReferenceDataService(
                japanese_reading_resource_path=reading_resource,
                ygocdb_client=ygocdb_client,
            )
            service = ProjectService(reading_service)

            game = GameFolderConnection(root / "game")
            entries = [
                ContainerEntry(
                    "bin#/card_id.bin",
                    data=GameRepository.encode_binary_resource(
                        "card_id.bin",
                        pd.DataFrame({"value": [-1, 2, 1]}),
                    ),
                    order=0,
                ),
                ContainerEntry(
                    "bin#/card_namejpn.bin",
                    data=GameRepository.encode_binary_resource(
                        "card_namejpn.bin",
                        pd.DataFrame({"value": ["", "既存", "未登録"]}),
                        "jpn",
                    ),
                    order=1,
                ),
                ContainerEntry(
                    "bin#/card_sortjpn.bin",
                    data=b"\x00" * 8,
                    order=2,
                ),
                ContainerEntry("misc/raw.bin", data=b"RAW", order=3),
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
            manifest = service.create_project(
                "Japanese sort",
                root / "workspace",
                root / "game",
                "mai",
            )

            def pack_data():
                output = service.pack_project(manifest)
                container_bytes = (output / "Data.dat").read_bytes()
                packed = GameFolderConnection(output).read_container("Data.dat")
                payloads = {
                    item.relative_path.replace("\\", "/"): item.data
                    for item in packed.entries
                }
                return container_bytes, payloads

            fallback_sort = b"".join(
                value.to_bytes(2, "little") for value in (0, 0, 1, 0)
            )
            resolved_sort = b"".join(
                value.to_bytes(2, "little") for value in (0, 1, 0, 0)
            )
            sort_path = "bin#/card_sortjpn.bin"

            with patch.object(
                reading_service,
                "add_japanese_reading_mapping",
                wraps=reading_service.add_japanese_reading_mapping,
            ) as persist_mapping:
                first_bytes, first_payloads = pack_data()
                self.assertEqual(first_payloads[sort_path], fallback_sort)
                self.assertEqual(reading_resource.read_bytes(), original_reading_bytes)

                second_bytes, second_payloads = pack_data()
                self.assertEqual(second_bytes, first_bytes)
                self.assertEqual(second_payloads, first_payloads)
                self.assertEqual(reading_resource.read_bytes(), original_reading_bytes)
                self.assertEqual(
                    ygocdb_client.fetch_japanese_reading.call_args_list,
                    [call("未登録"), call("未登録")],
                )
                persist_mapping.assert_not_called()

                third_bytes, third_payloads = pack_data()
                self.assertEqual(third_payloads[sort_path], resolved_sort)
                self.assertNotEqual(third_bytes, first_bytes)
                self.assertEqual(
                    {
                        path: payload
                        for path, payload in third_payloads.items()
                        if path != sort_path
                    },
                    {
                        path: payload
                        for path, payload in first_payloads.items()
                        if path != sort_path
                    },
                )
                persist_mapping.assert_called_once_with("未登録", "ア")
                persisted_reading_bytes = reading_resource.read_bytes()
                persisted = pd.read_csv(
                    reading_resource,
                    dtype=str,
                    encoding="utf-8-sig",
                    keep_default_na=False,
                )
                self.assertEqual(
                    persisted[["display_name_jpn", "reading_jpn"]].values.tolist(),
                    [["既存", "ワ"], ["未登録", "ア"]],
                )
                self.assertNotIn(
                    ["未登録", "未登録"],
                    persisted[["display_name_jpn", "reading_jpn"]].values.tolist(),
                )

                fourth_bytes, fourth_payloads = pack_data()
                self.assertEqual(fourth_bytes, third_bytes)
                self.assertEqual(fourth_payloads, third_payloads)
                self.assertEqual(
                    reading_resource.read_bytes(),
                    persisted_reading_bytes,
                )
                self.assertEqual(
                    ygocdb_client.fetch_japanese_reading.call_args_list,
                    [call("未登録"), call("未登録"), call("未登録")],
                )
                persist_mapping.assert_called_once_with("未登録", "ア")

    def test_virtual_sidecars_are_hidden_and_regenerated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = GameFolderConnection(root / "game")
            names = pd.DataFrame({"value": ["", "Zulu", "Alpha"]})
            descriptions = pd.DataFrame(
                {"value": ["Back description", "Old Zulu", "Old Alpha"]}
            )
            description_blob, description_index = encode_description_resources(
                descriptions,
                "eng",
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
                    "bin#/card_nameeng.bin",
                    data=GameRepository.encode_binary_resource(
                        "card_nameeng.bin",
                        names,
                        "eng",
                    ),
                    order=2,
                ),
                ContainerEntry(
                    "bin#/card_sorteng.bin",
                    data=b"\x00" * 4096,
                    order=3,
                ),
                ContainerEntry(
                    "bin#/card_desceng.bin",
                    data=description_blob,
                    order=4,
                ),
                ContainerEntry(
                    "bin#/card_indxeng.bin",
                    data=description_index,
                    order=5,
                ),
                ContainerEntry("misc/raw.bin", data=b"RAW", order=6),
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
                "Demo",
                root / "workspace",
                root / "game",
                "mai",
            )
            virtual = [item for item in manifest.files if item.virtual]
            self.assertEqual(
                {
                    next(
                        step.method_name
                        for step in GameRepository.from_root(".")
                        .find_rule(item.relative_path)
                        .pre_encode
                        if step.method_name.startswith("generate_")
                    )
                    for item in virtual
                },
                {
                    "generate_string_offsets",
                    "generate_sort_indices",
                    "generate_reverse_lookup",
                },
            )
            for resource in virtual:
                self.assertIsNone(resource.workspace_path)
            self.assertFalse((manifest.root / "data/bin#/card_indxeng.bin").exists())
            self.assertFalse((manifest.root / "data/bin#/card_sorteng.bin").exists())
            self.assertFalse((manifest.root / "data/bin#/card_intid.bin").exists())
            self.assertTrue((manifest.root / "data/misc/raw.bin").exists())
            self.assertTrue(
                all(
                    not item.virtual
                    for item in service.list_visible_resources(manifest)
                )
            )

            repository = ProjectRepository(manifest.root)
            description_record = next(
                item
                for item in manifest.files
                if "card_desceng.bin" in item.relative_path.casefold()
            )
            updated = repository.read_table(description_record.workspace_path)
            updated.loc[1, "text"] = "Updated Zulu"
            repository.write_table(description_record.workspace_path, updated)

            output = service.pack_project(manifest)
            packed = GameFolderConnection(output).read_container("Data.dat")
            payloads = {
                item.relative_path.replace("\\", "/").casefold(): item.data
                for item in packed.entries
            }
            rebuilt = decode_description_resource(
                payloads["bin#/card_desceng.bin"],
                payloads["bin#/card_indxeng.bin"][: 3 * 4],
                "eng",
            )
            self.assertEqual(
                rebuilt["text"].tolist(),
                ["Back description", "Updated Zulu", "Old Alpha"],
            )
            self.assertEqual(len(payloads["bin#/card_indxeng.bin"]), 8192)
            self.assertEqual(len(payloads["bin#/card_sorteng.bin"]), 8)
            self.assertEqual(len(payloads["bin#/card_intid.bin"]), 8)
            self.assertEqual(
                payloads["bin#/card_intid.bin"],
                b"\x00\x00\x01\x00\x00\x00\x02\x00",
            )

    def test_virtual_resource_without_rule_fails_early(self):
        with tempfile.TemporaryDirectory() as directory:
            record = ProjectFileRecord(
                source_file="Data.dat",
                relative_path="bin#/unknown_virtual.bin",
                workspace_path=None,
                file_kind="virtual",
                storage_format="virtual",
                generated_on_pack=True,
                virtual=True,
            )
            with self.assertRaisesRegex(
                PackResourceError,
                "unknown_virtual.bin.*manifest=True.*rule=False",
            ):
                SubfileService().pack_archive(
                    "Data.dat",
                    [record],
                    ProjectRepository(directory),
                )


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_services_and_views_do_not_import_connections(self):
        repository_root = Path(__file__).resolve().parents[1]
        forbidden = {
            "yugioh_editor.repositories.game.connection",
            "yugioh_editor.repositories.project.connection",
        }
        violations = []
        for folder in ("services", "views"):
            for path in (repository_root / "yugioh_editor" / folder).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module in forbidden:
                        violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual(violations, [])
