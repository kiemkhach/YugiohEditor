import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from tests.pipeline_support import (
    decode_description_resource,
    encode_description_resources,
    indexed_text_table,
)
from yugioh_editor.common.constants import EXECUTABLE_PATTERN
from yugioh_editor.common.errors import PackResourceError
from yugioh_editor.models.entities import (
    ContainerArchive,
    ContainerEntry,
    DeckFile,
    ProjectFileRecord,
    ProjectResource,
)
from yugioh_editor.repositories.game.connection import GameFolderConnection
from yugioh_editor.repositories.game.repository import GameRepository


class GameConnectionTests(unittest.TestCase):
    def test_arbitrary_binary_files_and_container_subfiles_are_raw_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = GameFolderConnection(directory)
            values = {
                "unknown_table.bin": b"\x00\x01",
                "custom_data.bin": b"\x02\x03",
                "abc.bin": b"\x04\x05",
            }
            for name, data in values.items():
                connection.write_binary_file(name, data)
                self.assertEqual(connection.read_binary_file(name), data)
            self.assertEqual(
                {path.name for path in connection.list_binary_files()},
                set(values),
            )

            archive = ContainerArchive(
                "custom.dat",
                entries=[ContainerEntry("folder/abc.bin", data=b"raw")],
            )
            self.assertEqual(
                connection.read_container_subfile(archive, "folder/abc.bin"),
                b"raw",
            )


class GameRepositoryTests(unittest.TestCase):
    @staticmethod
    def _reverse_lookup_resources(values: list[int]) -> list[ProjectResource]:
        return [
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    "bin#/card_id.bin",
                    "data/bin#/card_id.bin",
                    "table",
                    "table",
                    order=0,
                ),
                pd.DataFrame({"value": values}),
            ),
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    "bin#/card_intid.bin",
                    None,
                    "virtual",
                    "virtual",
                    generated_on_pack=True,
                    virtual=True,
                    order=1,
                )
            ),
        ]

    @staticmethod
    def _virtual_pipeline_resources() -> list[ProjectResource]:
        physical = (
            ("bin#/card_id.bin", pd.DataFrame({"value": [-1, 7, 3]}), None),
            (
                "bin#/card_nameeng.bin",
                pd.DataFrame({"value": ["", "Zulu", "Alpha"]}),
                "eng",
            ),
            (
                "bin#/card_desceng.bin",
                indexed_text_table(
                    ["A", "BB", "CCC"],
                ),
                "eng",
            ),
        )
        resources = [
            ProjectResource(
                ProjectFileRecord(
                    source_file="Data.dat",
                    relative_path=path,
                    workspace_path=f"data/{path}",
                    file_kind="table",
                    storage_format="table",
                    language=language,
                    order=order,
                ),
                table,
            )
            for order, (path, table, language) in enumerate(physical)
        ]
        for order, (path, language) in enumerate(
            (
                ("bin#/card_intid.bin", None),
                ("bin#/card_sorteng.bin", "eng"),
                ("bin#/card_indxeng.bin", "eng"),
            ),
            start=len(resources),
        ):
            resources.append(
                ProjectResource(
                    ProjectFileRecord(
                        source_file="Data.dat",
                        relative_path=path,
                        workspace_path=None,
                        file_kind="virtual",
                        storage_format="virtual",
                        language=language,
                        generated_on_pack=True,
                        virtual=True,
                        order=order,
                    )
                )
            )
        return resources

    @staticmethod
    def _dynamic_sort_resources(
        card_ids: list[int],
        names: list[str],
    ) -> list[ProjectResource]:
        resources = [
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    "bin#/card_id.bin",
                    "data/bin#/card_id.bin",
                    "table",
                    "table",
                    order=0,
                ),
                pd.DataFrame({"value": card_ids}),
            ),
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    "bin#/card_nameeng.bin",
                    "data/bin#/card_nameeng.bin",
                    "table",
                    "table",
                    language="eng",
                    order=1,
                ),
                pd.DataFrame({"value": names}),
            ),
        ]
        for path, language, order in (
            ("bin#/card_sorteng.bin", "eng", 2),
            ("bin#/card_intid.bin", None, 3),
        ):
            resources.append(
                ProjectResource(
                    ProjectFileRecord(
                        "Data.dat",
                        path,
                        None,
                        "virtual",
                        "virtual",
                        language=language,
                        generated_on_pack=True,
                        virtual=True,
                        order=order,
                    )
                )
            )
        return resources

    def test_duplicate_logical_dat_files_warn_and_select_stably(self):
        connection = Mock()
        connection.list_files.return_value = [
            Path("data.dat"),
            Path("Data.dat"),
            Path("Voice.dat"),
            Path("Region.dat"),
        ]
        with self.assertLogs(level="WARNING") as messages:
            selected = GameRepository(connection).find_logical_dat_files()
        self.assertEqual(selected["data.dat"].name, "Data.dat")
        self.assertTrue(
            any("Duplicate logical game file" in item for item in messages.output)
        )

    def test_repository_selects_registered_bin_codecs_and_raw_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = GameFolderConnection(directory)
            connection.write_binary_file(
                "card_id.bin",
                b"\xff\xff\x02\x00",
            )
            connection.write_binary_file(
                "card_prop.bin",
                b"\x00\x00\x00\x00",
            )
            connection.write_binary_file("unknown_table.bin", b"raw")
            repository = GameRepository(connection)

            card_ids = repository.read_binary_resource("card_id.bin")
            properties = repository.read_binary_resource("card_prop.bin")
            unknown = repository.read_binary_resource("unknown_table.bin")

            self.assertIsInstance(card_ids, pd.DataFrame)
            self.assertEqual(card_ids["value"].tolist(), [-1, 2])
            repository.write_binary_resource("card_id.bin", card_ids)
            self.assertEqual(
                connection.read_binary_file("card_id.bin"),
                b"\xff\xff\x02\x00",
            )
            self.assertEqual(
                GameRepository.encode_binary_resource(
                    "card_id.bin",
                    pd.DataFrame({"value": [-1, 2]}),
                ),
                b"\xff\xff\x02\x00",
            )
            self.assertIsInstance(properties, pd.DataFrame)
            self.assertEqual(
                list(properties.columns),
                [
                    "attack",
                    "defense",
                    "monster_type_code",
                    "monster_type",
                    "card_category_code",
                    "card_category",
                    "attribute_code",
                    "attribute",
                    "level",
                    "requires_two_tributes",
                ],
            )
            self.assertEqual(unknown, b"raw")

    def test_executable_pattern(self):
        valid = (
            "joey_pc.exe",
            "mai_pc.exe",
            "YUGI_PC.EXE",
            "version-2_pc.exe",
        )
        invalid = (
            "game.exe",
            "launcher.exe",
            "_pc.exe",
            "pc.exe",
            "joey_pc.dll",
        )
        self.assertTrue(all(EXECUTABLE_PATTERN.fullmatch(name) for name in valid))
        self.assertTrue(all(not EXECUTABLE_PATTERN.fullmatch(name) for name in invalid))

    def test_repository_top_level_read_write_and_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = GameRepository.from_root(directory)
            repository.write_binary("Region.dat", b"REGION")
            repository.write_deck("deck.ydc", DeckFile(card_ids=[1, 2]))
            repository.write_executable("game_pc.exe", b"MZ")
            repository.write_container(
                "Data.dat",
                ContainerArchive(
                    "Data.dat",
                    entries=[ContainerEntry("raw.bin", data=b"RAW", order=0)],
                ),
                "never",
            )
            self.assertEqual(repository.read_binary("Region.dat"), b"REGION")
            self.assertEqual(repository.read_deck("deck.ydc").card_ids, [1, 2])
            self.assertEqual(repository.read_executable("game_pc.exe"), b"MZ")
            self.assertIsInstance(
                repository.read_file("Data.dat"),
                ContainerArchive,
            )
            self.assertEqual(repository.read_file("Region.dat"), b"REGION")
            self.assertEqual(
                repository.require_file_path("Region.dat").name,
                "Region.dat",
            )
            with self.assertRaises(FileNotFoundError):
                repository.require_file_path("missing.dat")
            self.assertEqual(
                repository.subfile_rule("unknown.bin").codec_name,
                "binary",
            )
            self.assertEqual(
                repository.subfile_rule("customcard_id.bin").codec_name,
                "binary",
            )
            self.assertEqual(
                repository.subfile_rule("mylist_card.txt").codec_name,
                "text",
            )
            self.assertIsNone(repository.virtual_subfile_rule("unknown.bin"))
            self.assertEqual(
                repository.use_root(Path(directory)).read_binary("Region.dat"),
                b"REGION",
            )

    def test_unregistered_media_text_and_structured_write_paths(self):
        archive = ContainerArchive(
            "Data.dat",
            entries=[
                ContainerEntry("image/a.bmp", data=b"BMraw", order=0),
                ContainerEntry("audio/a.wav", data=b"RIFFraw", order=1),
                ContainerEntry("docs/a.txt", data=b"hello", order=2),
                ContainerEntry("raw/a.yga", data=b"raw", order=3),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = GameRepository.from_root(directory)
            resources = repository.decode_archive(archive, "data")
            self.assertEqual(
                [item.record.file_kind for item in resources],
                ["image", "audio", "text", "binary"],
            )
            rebuilt = repository.encode_archive("Data.dat", resources)
            self.assertEqual(
                [item.data for item in rebuilt.entries],
                [item.data for item in archive.entries],
            )

            ids = pd.DataFrame({"value": [1, 2]})
            repository.write_binary_resource("card_id.bin", ids)
            self.assertEqual(
                repository.read_binary_resource("card_id.bin")["value"].tolist(),
                [1, 2],
            )
            repository.write_binary_resource("unknown.bin", b"unknown")
            self.assertEqual(
                repository.read_binary_resource("unknown.bin"),
                b"unknown",
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "card_id.bin.*pre_encode.*dataframe_column_to_list",
            ):
                repository.write_binary_resource("card_id.bin", b"bad")
            repository.write_binary("card_desceng.bin", b"")
            with self.assertRaises(ValueError):
                repository.read_binary_resource("card_desceng.bin", "eng")

    def test_generic_txt_and_text_use_fixed_cp932_strictly(self):
        text = "日本語"
        raw = text.encode("cp932")
        archive = ContainerArchive(
            "Data.dat",
            entries=[
                ContainerEntry("docs/notes.txt", data=raw, order=0),
                ContainerEntry("docs/notes.text", data=raw, order=1),
            ],
        )
        repository = GameRepository.from_root(".")
        resources = repository.decode_archive(archive, "data")
        self.assertEqual([resource.value for resource in resources], [text, text])
        self.assertEqual(
            [resource.record.language for resource in resources],
            [None, None],
        )
        rebuilt = repository.encode_archive("Data.dat", resources)
        self.assertEqual([entry.data for entry in rebuilt.entries], [raw, raw])

        for file_name in ("notes.txt", "notes.text"):
            with self.subTest(file_name=file_name):
                rule = repository.find_rule(file_name)
                self.assertEqual(rule.codec_name, "text")
                self.assertEqual(rule.decode_params, {"encoding": "cp932"})
                self.assertEqual(rule.encode_params, {"encoding": "cp932"})
                with self.assertRaisesRegex(
                    RuntimeError,
                    rf"{re.escape(file_name)}.*encoding='cp932'",
                ):
                    GameRepository.decode_binary_resource(file_name, b"\x81")

    def test_virtual_generator_errors_and_pack_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = GameRepository.from_root(directory)
            invalid_virtual = ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    "bin#/card_intid.bin",
                    None,
                    "virtual",
                    "virtual",
                    generated_on_pack=True,
                    virtual=True,
                    order=0,
                )
            )
            invalid_virtual.record.relative_path = "bin#/unknown_virtual.bin"
            with self.assertRaisesRegex(
                PackResourceError,
                "manifest=True.*rule=False",
            ):
                repository.encode_archive("Data.dat", [invalid_virtual])

            invalid_table = ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    "bin#/card_id.bin",
                    "data/bin#/card_id.bin",
                    "table",
                    "table",
                    order=0,
                ),
                b"not-a-table",
            )
            with self.assertRaisesRegex(PackResourceError, "pre_encode"):
                repository.encode_archive("Data.dat", [invalid_table])

            bad_pack = pd.DataFrame({"value": ["unsupported"]})
            with self.assertRaisesRegex(
                RuntimeError,
                "card_pack.bin.*apply_reverse_value_map.*unsupported",
            ):
                repository.encode_binary_resource(
                    "card_pack.bin",
                    bad_pack,
                )

    def test_reverse_id_generator_omits_card_back(self):
        repository = GameRepository.from_root(".")
        resources = self._reverse_lookup_resources([-1, 7])

        archive = repository.encode_archive("Data.dat", resources)
        reverse_ids = next(
            entry.data
            for entry in archive.entries
            if entry.relative_path.casefold().endswith("card_intid.bin")
        )

        self.assertEqual(reverse_ids[7 * 2 : 7 * 2 + 2], b"\x01\x00")
        self.assertEqual(len(reverse_ids), 16)

    def test_reverse_lookup_encodes_complete_natural_table(self):
        repository = GameRepository.from_root(".")
        values = [-1, 0, 1, 100, 2000, 2047, 2048, 2068]
        rule = repository.find_rule("card_intid.bin")
        record_size = int(rule.encode_params["byte_width"])
        expected_record_count = 1 << max(values).bit_length()

        archive = repository.encode_archive(
            "Data.dat",
            self._reverse_lookup_resources(values),
        )

        payload = next(
            entry.data
            for entry in archive.entries
            if entry.relative_path.casefold().endswith("card_intid.bin")
        )
        decoded = repository.decode_binary_resource("card_intid.bin", payload)
        reverse = decoded["value"].astype(int).tolist()
        self.assertEqual(len(payload), expected_record_count * record_size)
        self.assertEqual(len(reverse), expected_record_count)
        self.assertEqual(reverse[0], 1)
        self.assertEqual(reverse[1], 2)
        self.assertEqual(reverse[100], 3)
        self.assertEqual(reverse[2000], 4)
        self.assertEqual(reverse[2047], 5)
        self.assertEqual(reverse[2048], 6)
        self.assertEqual(reverse[2068], 7)
        expected = [0] * expected_record_count
        for card_index, card_id in enumerate(values):
            if card_id >= 0:
                expected[card_id] = card_index
        self.assertEqual(reverse, expected)

    def test_reverse_lookup_duplicate_ids_use_last_card_index(self):
        repository = GameRepository.from_root(".")

        archive = repository.encode_archive(
            "Data.dat",
            self._reverse_lookup_resources([0, 5, 0]),
        )
        payload = archive.entries[-1].data
        reverse = repository.decode_binary_resource("card_intid.bin", payload)
        self.assertEqual(reverse.loc[0, "value"], 2)
        self.assertEqual(reverse.loc[5, "value"], 1)

    def test_configured_table_pipeline_regressions(self):
        names = pd.DataFrame({"value": ["Card Back", "Blue-Eyes"]})
        encoded_names = GameRepository.encode_binary_resource(
            "card_nameeng.bin",
            names,
            "eng",
        )
        decoded_names = GameRepository.decode_binary_resource(
            "card_nameeng.bin",
            encoded_names,
            "eng",
        )
        pd.testing.assert_frame_equal(decoded_names, names)

        property_data = bytes.fromhex("7890314C 82A0F466 BEC2596C 00007001 00008041")
        properties = GameRepository.decode_binary_resource(
            "card_prop.bin",
            property_data,
        )
        self.assertIsInstance(properties, pd.DataFrame)
        self.assertEqual(
            properties["card_category"].tolist(),
            ["normal", "effect", "fusion", "", ""],
        )
        self.assertEqual(
            properties["monster_type_code"].tolist(),
            [0x03, 0x0F, 0x05, 0x17, 0x18],
        )
        with self.assertLogs(level="DEBUG") as encoded_logs:
            encoded_properties = GameRepository.encode_binary_resource(
                "card_prop.bin",
                properties,
            )
        self.assertEqual(encoded_properties, property_data)
        self.assertTrue(
            any(
                "card_prop.bin" in message
                and "card_category" in message
                and "effect" in message
                and "divine" in message
                for message in encoded_logs.output
            )
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "card_prop.bin cannot be encoded.*card_category",
        ):
            GameRepository.encode_binary_resource(
                "card_prop.bin",
                properties.drop(columns="card_category"),
            )

        pack_data = b"\x00\x00\x04\x00\x07\x00"
        packs = GameRepository.decode_binary_resource("card_pack.bin", pack_data)
        self.assertEqual(
            packs["value"].tolist(),
            ["disabled", "joey", "yugi_kaiba_joey"],
        )
        self.assertEqual(
            GameRepository.encode_binary_resource("card_pack.bin", packs),
            pack_data,
        )

        card_list = pd.DataFrame(
            {
                "name": ["Card Back", "Blue-Eyes"],
                "index": [0, 1],
                "card_id": [0, 1],
                "image_name": ["CARD0000.bmp", "CARD0001.bmp"],
                "note": ["Back", ""],
            }
        )
        encoded_list = GameRepository.encode_binary_resource(
            "list_card.txt",
            card_list,
        )
        decoded_list = GameRepository.decode_binary_resource(
            "list_card.txt",
            encoded_list,
        )
        pd.testing.assert_frame_equal(decoded_list, card_list)

    def test_description_dependency_pipeline_and_virtual_index_regression(self):
        descriptions = indexed_text_table(
            ["Back", "Second"],
        )
        blob, indexes = encode_description_resources(
            descriptions,
            "eng",
        )
        archive = ContainerArchive(
            "Data.dat",
            entries=[
                ContainerEntry(
                    "bin#/card_id.bin",
                    data=b"\x00\x00\x01\x00",
                    order=0,
                ),
                ContainerEntry(
                    "bin#/card_desceng.bin",
                    data=blob,
                    order=1,
                ),
                ContainerEntry(
                    "bin#/card_indxeng.bin",
                    data=indexes,
                    order=2,
                ),
            ],
        )
        repository = GameRepository.from_root(".")
        resources = repository.decode_archive(archive, "data")
        decoded = next(
            resource.value
            for resource in resources
            if resource.record.relative_path.endswith("card_desceng.bin")
        )
        pd.testing.assert_frame_equal(decoded, descriptions)

        rebuilt = repository.encode_archive("Data.dat", resources)
        rebuilt_payloads = {
            entry.relative_path: entry.data for entry in rebuilt.entries
        }
        self.assertEqual(rebuilt_payloads["bin#/card_desceng.bin"], blob)
        self.assertEqual(len(rebuilt_payloads["bin#/card_indxeng.bin"]), 8192)
        round_trip = decode_description_resource(
            rebuilt_payloads["bin#/card_desceng.bin"],
            rebuilt_payloads["bin#/card_indxeng.bin"][:8],
            "eng",
        )
        pd.testing.assert_frame_equal(round_trip, descriptions)

    def test_indexed_text_pre_encode_normalizes_missing_values_to_empty_strings(self):
        values = GameRepository.dataframe_column_to_list(
            pd.DataFrame({"text": ["A", None, float("nan"), "B"]}),
            context=Mock(),
            column="text",
            fill_value="",
            cast="str",
        )
        self.assertEqual(values, ["A", "", "", "B"])

    def test_virtual_pipeline_generates_once_and_preserves_baseline_bytes(self):
        repository = GameRepository.from_root(".")
        resources = self._virtual_pipeline_resources()
        with (
            patch.object(
                GameRepository,
                "generate_string_offsets",
                wraps=GameRepository.generate_string_offsets,
            ) as offsets,
            patch.object(
                GameRepository,
                "generate_sort_indices",
                wraps=GameRepository.generate_sort_indices,
            ) as sort_indices,
            patch.object(
                GameRepository,
                "generate_reverse_lookup",
                wraps=GameRepository.generate_reverse_lookup,
            ) as reverse_lookup,
        ):
            archive = repository.encode_archive("Data.dat", resources)
        self.assertEqual(offsets.call_count, 1)
        self.assertEqual(sort_indices.call_count, 1)
        self.assertEqual(reverse_lookup.call_count, 1)

        payloads = {
            entry.relative_path.replace("\\", "/"): entry.data
            for entry in archive.entries
        }
        self.assertEqual(len(payloads["bin#/card_indxeng.bin"]), 8192)
        expected_offsets = [0, 4, 8] + [0] * 2045
        self.assertEqual(
            payloads["bin#/card_indxeng.bin"],
            b"".join(item.to_bytes(4, "little") for item in expected_offsets),
        )
        self.assertEqual(len(payloads["bin#/card_sorteng.bin"]), 8)
        expected_sort = [0, 1, 0, 0]
        self.assertEqual(
            payloads["bin#/card_sorteng.bin"],
            b"".join(item.to_bytes(2, "little") for item in expected_sort),
        )
        self.assertEqual(len(payloads["bin#/card_intid.bin"]), 16)
        expected_reverse = [0] * 8
        expected_reverse[3] = 2
        expected_reverse[7] = 1
        self.assertEqual(
            payloads["bin#/card_intid.bin"],
            b"".join(item.to_bytes(2, "little") for item in expected_reverse),
        )

    def test_virtual_sort_uses_card_ids_for_ranking_and_not_card_intid(self):
        repository = GameRepository.from_root(".")
        resources = self._dynamic_sort_resources(
            [-1, 5, 2, 12],
            ["", "Zulu", "Alpha", "Beta"],
        )
        with patch.object(
            GameRepository,
            "generate_reverse_lookup",
            wraps=GameRepository.generate_reverse_lookup,
        ) as reverse_lookup:
            archive = repository.encode_archive("Data.dat", resources)
        self.assertEqual(reverse_lookup.call_count, 1)

        payloads = {entry.relative_path: entry.data for entry in archive.entries}
        reverse = (
            repository.decode_binary_resource(
                "card_intid.bin",
                payloads["bin#/card_intid.bin"],
            )["value"]
            .astype(int)
            .tolist()
        )
        sort = (
            repository.decode_binary_resource(
                "card_sorteng.bin",
                payloads["bin#/card_sorteng.bin"],
                "eng",
            )["value"]
            .astype(int)
            .tolist()
        )
        self.assertEqual(len(reverse), 16)
        self.assertEqual(reverse[2], 2)
        self.assertEqual(reverse[5], 1)
        self.assertEqual(reverse[12], 3)
        self.assertEqual(sort[:4], [0, 2, 0, 1])
        self.assertEqual(len(sort), 4)
        self.assertFalse(any(sort[4:]))
        self.assertEqual(len(payloads["bin#/card_intid.bin"]), len(reverse) * 2)

        alternative = repository.encode_archive(
            "Data.dat",
            self._dynamic_sort_resources(
                [-1, 14, 13, 12],
                ["", "Zulu", "Alpha", "Beta"],
            ),
        )
        alternative_sort = next(
            entry.data
            for entry in alternative.entries
            if entry.relative_path.endswith("card_sorteng.bin")
        )
        self.assertEqual(alternative_sort, payloads["bin#/card_sorteng.bin"])

    def test_virtual_sort_padding_uses_card_count_not_card_id_range(self):
        repository = GameRepository.from_root(".")
        archive = repository.encode_archive(
            "Data.dat",
            self._dynamic_sort_resources(
                [-1, 2000, 2047, 2048, 2389],
                ["", "D", "C", "B", "A"],
            ),
        )
        payloads = {entry.relative_path: entry.data for entry in archive.entries}
        reverse = (
            repository.decode_binary_resource(
                "card_intid.bin",
                payloads["bin#/card_intid.bin"],
            )["value"]
            .astype(int)
            .tolist()
        )
        sort = (
            repository.decode_binary_resource(
                "card_sorteng.bin",
                payloads["bin#/card_sorteng.bin"],
                "eng",
            )["value"]
            .astype(int)
            .tolist()
        )
        self.assertEqual(len(reverse), 4096)
        self.assertEqual(reverse[2000], 1)
        self.assertEqual(reverse[2047], 2)
        self.assertEqual(reverse[2048], 3)
        self.assertEqual(reverse[2389], 4)
        self.assertEqual(len(sort), 8)
        self.assertEqual(sort[:5], [0, 3, 2, 1, 0])
        self.assertFalse(any(sort[5:]))

    def test_card_sort_target_length_uses_card_count(self):
        repository = GameRepository.from_root(".")
        archive = repository.encode_archive(
            "Data.dat",
            self._dynamic_sort_resources([-1, 2389], ["", "B"]),
        )
        sort_payload = next(
            entry.data
            for entry in archive.entries
            if entry.relative_path.endswith("card_sorteng.bin")
        )
        self.assertEqual(len(sort_payload), 4)

    def test_physical_and_virtual_resources_use_same_encode_method(self):
        repository = GameRepository.from_root(".")
        resources = self._virtual_pipeline_resources()
        with patch.object(
            repository,
            "_encode_rule_value",
            wraps=repository._encode_rule_value,
        ) as encode:
            repository.encode_archive("Data.dat", resources)
        self.assertEqual(encode.call_count, len(resources))
        virtual_inputs = [
            call.args[1] for call in encode.call_args_list if call.args[0].virtual
        ]
        self.assertEqual(virtual_inputs, [None, None, None])
