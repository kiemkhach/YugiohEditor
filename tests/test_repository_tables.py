import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from PIL import Image

from yugioh_editor.models.entities import (
    CardImageVariant,
    NamedCardImagePair,
    ProjectFileRecord,
    ProjectManifest,
    ProjectResource,
)
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.game.subfile_rule_factory import SubfileRuleFactory
from yugioh_editor.repositories.project.repository import ProjectRepository
from yugioh_editor.services.card_service import CardService


class ProjectTableFixture:
    @staticmethod
    def build(root: Path) -> tuple[ProjectManifest, ProjectRepository]:
        manifest = ProjectManifest(
            "Cards",
            str(root),
            version_prefix="mai",
            game_files={"data.dat": "Data.dat"},
        )
        values = (
            (
                "bin#/card_id.bin",
                "data/bin#/card_id.bin",
                pd.DataFrame({"value": [-1, 2]}),
            ),
            (
                "bin#/card_pass.bin",
                "data/bin#/card_pass.bin",
                pd.DataFrame({"value": ["64000000", "C8000000"]}),
            ),
            (
                "bin#/card_pack.bin",
                "data/bin#/card_pack.bin",
                pd.DataFrame({"value": ["disabled", "joey"]}),
            ),
            (
                "bin#/card_prop.bin",
                "data/bin#/card_prop.bin",
                pd.DataFrame(
                    {
                        "attack": [0, 1600],
                        "defense": [0, 1200],
                        "monster_type_code": [0x10, 0x01],
                        "monster_type": ["winged_beast", "dragon"],
                        "card_category_code": [0x00, 0x01],
                        "card_category": ["normal", "effect"],
                        "attribute_code": [0x07, 0x02],
                        "attribute": ["divine", "dark"],
                        "level": [0, 4],
                        "requires_two_tributes": [False, False],
                    }
                ),
            ),
            (
                "bin#/card_nameeng.bin",
                "data/bin#/card_nameeng.bin",
                pd.DataFrame({"value": ["", "Dragon"]}),
            ),
            (
                "bin#/card_desceng.bin",
                "data/bin#/card_desceng.bin",
                pd.DataFrame(
                    {
                        "text": ["Back", "Description"],
                        "is_reserved": [False, False],
                    }
                ),
            ),
            (
                "card/list_card.txt",
                "data/card/list_card.txt",
                pd.DataFrame(
                    {
                        "name": ["Back", "Dragon"],
                        "index": [0, 1],
                        "card_id": [0, 1],
                        "image_name": ["", ""],
                        "note": ["Back", ""],
                    }
                ),
            ),
            (
                "mini/list_card.txt",
                "data/mini/list_card.txt",
                pd.DataFrame(
                    {
                        "name": ["Mini Back", "Mini Dragon"],
                        "index": [0, 1],
                        "card_id": [0, 1],
                        "image_name": ["", ""],
                        "note": ["Mini", ""],
                    }
                ),
            ),
        )
        resources = []
        for order, (relative, workspace, table) in enumerate(values):
            resources.append(
                ProjectResource(
                    ProjectFileRecord(
                        source_file="Data.dat",
                        relative_path=relative,
                        workspace_path=workspace,
                        file_kind="table",
                        storage_format="table",
                        language=(
                            "eng"
                            if "nameeng" in relative or "desceng" in relative
                            else None
                        ),
                        order=order,
                    ),
                    table,
                )
            )
        virtual = (
            "bin#/card_intid.bin",
            "bin#/card_sorteng.bin",
            "bin#/card_indxeng.bin",
        )
        for position, relative in enumerate(
            virtual,
            start=len(resources),
        ):
            resources.append(
                ProjectResource(
                    ProjectFileRecord(
                        source_file="Data.dat",
                        relative_path=relative,
                        workspace_path=None,
                        file_kind="virtual",
                        storage_format="virtual",
                        language="eng" if "eng" in relative else None,
                        generated_on_pack=True,
                        virtual=True,
                        order=position,
                    )
                )
            )
        repository = ProjectRepository(manifest)
        repository.ensure_root()
        manifest.files.extend(repository.import_resources(resources))
        repository.save(manifest)
        return manifest, repository


class RepositoryTableTests(unittest.TestCase):
    def test_version_two_properties_and_passcodes_migrate_once_to_schema_four(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, repository = ProjectTableFixture.build(root)
            legacy = pd.DataFrame(
                {
                    "attack": [2000, 1280],
                    "defense": [1200, 900],
                    "monster_type_code": [3, 21],
                    "monster_type": ["fiend", "trap_card"],
                    "card_category_code": [8, 4],
                    "card_category": ["fusion", "effect"],
                    "attribute_code": [4, 6],
                    "attribute": ["dark", "water"],
                    "level": [8, 7],
                    "requires_two_tributes": [True, False],
                }
            )
            repository._connection.write_table("data/bin#/card_prop.bin", legacy)
            repository._connection.write_table(
                "data/bin#/card_pass.bin",
                pd.DataFrame({"value": [2018915346, 4294967295]}),
            )
            manifest.version = 2
            repository.save(manifest)

            loaded_repository = ProjectRepository(root)
            loaded = loaded_repository.load()
            self.assertEqual(loaded.version, 4)
            migrated = loaded_repository.get_table("card_properties")
            self.assertEqual(migrated["attribute_code"].tolist(), [2, 3])
            self.assertEqual(migrated["card_category_code"].tolist(), [2, 2])
            self.assertEqual(migrated["card_category"].tolist(), ["fusion", "field"])
            self.assertEqual(
                migrated.loc[1, ["attack", "defense", "level"]].tolist(), [0, 0, 0]
            )
            self.assertEqual(
                loaded_repository.get_table("card_passcodes")["value"].tolist(),
                ["12345678", "FFFFFFFF"],
            )
            migrated_bytes = (root / "data/bin#/card_prop.bin").read_bytes()
            migrated_password_bytes = (root / "data/bin#/card_pass.bin").read_bytes()

            reloaded_repository = ProjectRepository(root)
            self.assertEqual(reloaded_repository.load().version, 4)
            self.assertEqual(
                (root / "data/bin#/card_prop.bin").read_bytes(), migrated_bytes
            )
            self.assertEqual(
                (root / "data/bin#/card_pass.bin").read_bytes(),
                migrated_password_bytes,
            )

    def test_version_three_passcodes_migrate_without_rewriting_properties(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, repository = ProjectTableFixture.build(root)
            property_bytes = (root / "data/bin#/card_prop.bin").read_bytes()
            repository._connection.write_table(
                "data/bin#/card_pass.bin",
                pd.DataFrame({"value": [1, 0]}),
            )
            manifest.version = 3
            repository.save(manifest)

            loaded_repository = ProjectRepository(root)
            self.assertEqual(loaded_repository.load().version, 4)
            self.assertEqual(
                loaded_repository.get_table("card_passcodes")["value"].tolist(),
                ["01000000", "00000000"],
            )
            self.assertEqual(
                (root / "data/bin#/card_prop.bin").read_bytes(),
                property_bytes,
            )

    def test_passcode_migration_failure_keeps_version_three_project_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, repository = ProjectTableFixture.build(root)
            repository._connection.write_table(
                "data/bin#/card_pass.bin",
                pd.DataFrame({"value": [0, 4294967296]}),
            )
            manifest.version = 3
            repository.save(manifest)
            original_passcodes = (root / "data/bin#/card_pass.bin").read_bytes()

            with self.assertRaisesRegex(
                ValueError,
                "card_passcodes row 1.*outside unsigned 32-bit range",
            ):
                ProjectRepository(root).load()

            self.assertEqual(
                (root / "data/bin#/card_pass.bin").read_bytes(),
                original_passcodes,
            )
            self.assertEqual(ProjectRepository(root).read_manifest().version, 3)

    def test_version_two_passcode_failure_rolls_back_staged_property_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, repository = ProjectTableFixture.build(root)
            legacy_properties = pd.DataFrame(
                {
                    "attack": [2000, 1280],
                    "defense": [1200, 900],
                    "monster_type_code": [3, 21],
                    "monster_type": ["fiend", "trap_card"],
                    "card_category_code": [8, 4],
                    "card_category": ["fusion", "effect"],
                    "attribute_code": [4, 6],
                    "attribute": ["dark", "water"],
                    "level": [8, 7],
                    "requires_two_tributes": [True, False],
                }
            )
            repository._connection.write_table(
                "data/bin#/card_prop.bin",
                legacy_properties,
            )
            repository._connection.write_table(
                "data/bin#/card_pass.bin",
                pd.DataFrame({"value": [0, 4294967296]}),
            )
            manifest.version = 2
            repository.save(manifest)
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

            with self.assertRaisesRegex(
                ValueError,
                "card_passcodes row 1.*outside unsigned 32-bit range",
            ):
                ProjectRepository(root).load()

            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertEqual(ProjectRepository(root).read_manifest().version, 2)
            self.assertEqual(
                list(root.parent.glob(f".{root.name}.cards.*.tmp")),
                [],
            )

    def test_list_physical_language_and_composite_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            _, repository = ProjectTableFixture.build(Path(directory))
            self.assertTrue(
                {
                    "card_ids",
                    "card_passcodes",
                    "card_packs",
                    "card_properties",
                    "card_names",
                    "card_descriptions",
                    "cards",
                }.issubset(repository.list_tables())
            )
            self.assertEqual(
                repository.get_table("card_ids")["value"].astype(int).tolist(),
                [-1, 2],
            )
            repository.save_table(
                "card_ids",
                pd.DataFrame({"value": [-1, 1]}),
            )
            self.assertEqual(
                repository.get_table("card_ids")["value"].astype(int).tolist(),
                [-1, 1],
            )
            self.assertEqual(
                repository.get_table(
                    "card_names",
                    language="eng",
                )["value"].tolist(),
                ["", "Dragon"],
            )
            cards = repository.get_table("cards", language="eng")
            self.assertEqual(cards["passcode"].tolist(), ["64000000", "C8000000"])
            self.assertEqual(cards["name"].tolist(), ["", "Dragon"])
            self.assertEqual(
                cards["description"].tolist(),
                ["Back", "Description"],
            )
            self.assertNotIn("description_is_reserved", cards.columns)
            self.assertFalse(
                any(column.startswith("desc_reserved_") for column in cards.columns)
            )

    def test_card_passcode_table_serializes_canonical_hex_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            _, repository = ProjectTableFixture.build(Path(directory))
            repository.save_table(
                "card_passcodes",
                pd.DataFrame({"value": [" 00000001 ", "ffffffff"]}),
            )
            self.assertEqual(
                repository.get_table("card_passcodes")["value"].tolist(),
                ["00000001", "FFFFFFFF"],
            )
            workspace = Path(directory) / "data/bin#/card_pass.bin"
            self.assertIn("00000001", workspace.read_text(encoding="utf-8-sig"))
            for invalid in (12345678, "1234567", "12345G78"):
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(
                        ValueError,
                        "card_passcodes row 0",
                    ):
                        repository.save_table(
                            "card_passcodes",
                            pd.DataFrame({"value": [invalid]}),
                        )

    def test_invalid_composite_passcode_does_not_partially_write_physical_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, repository = ProjectTableFixture.build(root)
            cards = repository.get_table("cards", language="eng")
            cards.loc[1, "card_id"] = 9
            cards.loc[1, "passcode"] = "BAD"
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

            with self.assertRaisesRegex(
                ValueError,
                "card_passcodes row 1.*exactly 8 hexadecimal characters",
            ):
                repository.save_table("cards", cards, language="eng")

            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertEqual(
                repository.get_table("card_ids")["value"].astype(int).tolist(),
                [-1, 2],
            )

    def test_save_composite_splits_physical_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, repository = ProjectTableFixture.build(Path(directory))
            for image_variant in CardImageVariant:
                catalog = repository.read_card_image_list(
                    image_variant=image_variant,
                )
                catalog.loc[0, "name"] = "Generated by Getallcard.exe"
                repository.write_card_image_list(
                    catalog,
                    image_variant=image_variant,
                )
            cards = repository.get_table("cards", language="eng")
            cards.loc[1, "name"] = "Updated"
            cards.loc[1, "name_eng"] = "Updated"
            cards.loc[1, "description"] = ""
            cards.loc[1, "attack"] = 2400
            cards.loc[1, "card_category"] = "Ritual"
            cards.loc[1, "monster_type"] = "Fiend"
            cards.loc[1, "passcode"] = "00abcdef"
            repository.save_table("cards", cards, language="eng")
            self.assertEqual(
                repository.get_table(
                    "card_names",
                    language="eng",
                ).loc[1, "value"],
                "Updated",
            )
            self.assertEqual(
                int(repository.get_table("card_properties").loc[1, "attack"]),
                2400,
            )
            properties = repository.get_table("card_properties")
            self.assertEqual(properties.loc[1, "card_category"], "ritual")
            self.assertEqual(int(properties.loc[1, "card_category_code"]), 0x03)
            self.assertEqual(properties.loc[1, "monster_type"], "fiend")
            self.assertEqual(int(properties.loc[1, "monster_type_code"]), 0x03)
            self.assertEqual(
                repository.get_table("card_passcodes")["value"].tolist(),
                ["64000000", "00ABCDEF"],
            )
            descriptions = repository.get_table(
                "card_descriptions",
                language="eng",
            )
            self.assertEqual(list(descriptions.columns), ["text", "is_reserved"])
            self.assertEqual(descriptions["text"].tolist(), ["Back", ""])
            self.assertEqual(descriptions["is_reserved"].tolist(), [False, False])
            self.assertFalse((Path(directory) / "data/bin#/card_indxeng.bin").exists())

            for image_variant in CardImageVariant:
                catalog = repository.read_card_image_list(
                    image_variant=image_variant,
                )
                self.assertEqual(
                    catalog["name"].tolist(),
                    ["Generated by Getallcard.exe", "Updated"],
                )

            resources = repository.export_resources(
                repository.list_resources(manifest, include_virtual=True)
            )
            archive = GameRepository.from_root(directory).encode_archive(
                "Data.dat",
                resources,
            )
            index_data = next(
                entry.data
                for entry in archive.entries
                if entry.relative_path.replace("\\", "/").endswith(
                    "bin#/card_indxeng.bin"
                )
            )
            self.assertEqual(
                index_data[:8],
                b"\x00\x00\x00\x00\x06\x00\x00\x00",
            )
            password_data = next(
                entry.data
                for entry in archive.entries
                if entry.relative_path.replace("\\", "/").endswith("bin#/card_pass.bin")
            )
            self.assertEqual(password_data, bytes.fromhex("64000000 00ABCDEF"))
            encoded_catalogs = {
                entry.relative_path.replace("\\", "/"): (
                    GameRepository.decode_binary_resource(
                        entry.relative_path,
                        entry.data,
                    )
                )
                for entry in archive.entries
                if entry.relative_path.replace("\\", "/")
                in {"card/list_card.txt", "mini/list_card.txt"}
            }
            self.assertEqual(
                set(encoded_catalogs),
                {"card/list_card.txt", "mini/list_card.txt"},
            )
            for catalog in encoded_catalogs.values():
                self.assertEqual(
                    catalog["name"].tolist(),
                    ["Generated by Getallcard.exe", "Updated"],
                )

    def test_legacy_card_properties_are_normalized_before_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, repository = ProjectTableFixture.build(root)
            old_properties = pd.DataFrame(
                {
                    "attack": [0, 1600],
                    "defense": [0, 1200],
                    "attribute": ["", "dark"],
                    "card_type": ["", "dragon"],
                    "level": [0, 4],
                }
            )
            repository._connection.write_table(
                "data/bin#/card_prop.bin",
                old_properties,
            )
            with self.assertLogs(level="WARNING") as warnings:
                cards = repository.get_table("cards")
            self.assertEqual(cards["card_category"].tolist(), ["", "normal"])
            self.assertEqual(
                cards["monster_type"].tolist(),
                ["", "dragon"],
            )
            self.assertTrue(
                any(
                    "card_properties legacy schema normalized" in message
                    for message in warnings.output
                )
            )
            records = [
                item for item in manifest.files if item.source_file == "Data.dat"
            ]
            resources = repository.export_resources(records)
            archive = GameRepository.from_root(root).encode_archive(
                "Data.dat",
                resources,
            )
            packed = next(
                entry.data
                for entry in archive.entries
                if entry.relative_path.replace("\\", "/").endswith("bin#/card_prop.bin")
            )
            decoded = GameRepository.decode_binary_resource(
                "card_prop.bin",
                packed,
            )
            self.assertEqual(decoded["card_category"].tolist(), ["", "normal"])

    def test_unknown_table_and_record_mismatch_are_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            _, repository = ProjectTableFixture.build(Path(directory))
            with self.assertRaisesRegex(
                KeyError,
                "Unknown table 'not_a_table'.*card_ids",
            ):
                repository.get_table("not_a_table")
            repository.save_table(
                "card_properties",
                repository.get_table("card_properties").iloc[:1],
            )
            with self.assertRaisesRegex(
                ValueError,
                "card_prop.bin has 1 records, but card_id.bin has 2",
            ):
                repository.get_table("cards")

    def test_table_handlers_are_the_only_canonical_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            _, repository = ProjectTableFixture.build(Path(directory))
            self.assertTrue(
                all(
                    callable(handler.reader)
                    and (handler.writer is None or callable(handler.writer))
                    for handler in repository._table_handlers.values()
                )
            )
            self.assertEqual(
                repository.list_tables(),
                tuple(repository._table_handlers),
            )
            self.assertFalse(hasattr(repository, "_table_readers"))
            self.assertFalse(hasattr(repository, "_table_writers"))
            self.assertFalse(hasattr(ProjectRepository, "TABLE_NAMES"))

    def test_physical_table_parameters_are_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            _, repository = ProjectTableFixture.build(Path(directory))
            with self.assertRaisesRegex(
                ValueError,
                "Table 'card_names' requires parameter 'language'",
            ):
                repository.get_table("card_names")
            with self.assertRaisesRegex(
                ValueError,
                "Table 'card_ids' does not accept parameter 'language'",
            ):
                repository.get_table("card_ids", language="eng")
            with self.assertRaisesRegex(ValueError, "Unsupported language prefix"):
                repository.get_table("card_names", language="span")

    def test_test_only_physical_table_registers_from_config_alone(self):
        rules = SubfileRuleFactory().build_rules(
            (
                {
                    "pattern": "sample_values.bin",
                    "table_name": "sample_values",
                    "codec_name": "integer_list",
                    "decode_params": {"byte_width": 2},
                    "encode_params": {"byte_width": 2},
                    "post_decode": (
                        {
                            "method_name": "sequence_to_dataframe",
                            "params": {"column": "value"},
                        },
                    ),
                    "pre_encode": (
                        {
                            "method_name": "dataframe_column_to_list",
                            "params": {"column": "value", "cast": "int"},
                        },
                    ),
                },
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = ProjectFileRecord(
                "Data.dat",
                "bin#/sample_values.bin",
                "data/bin#/sample_values.bin",
                "table",
                "table",
            )
            manifest = ProjectManifest(
                "Config table",
                str(root),
                version_prefix="mai",
                files=[record],
                game_files={"data.dat": "Data.dat"},
            )
            repository = ProjectRepository(
                manifest,
                subfile_rules=rules,
            )
            repository.ensure_root()
            repository.write_table(
                record.workspace_path,
                pd.DataFrame({"value": [1, 2]}),
            )
            self.assertTrue(repository.has_table("sample_values"))
            self.assertEqual(
                repository.get_table("sample_values")["value"].astype(int).tolist(),
                [1, 2],
            )
            repository.save_table(
                "sample_values",
                pd.DataFrame({"value": [3]}),
            )
            self.assertEqual(
                repository.get_table("sample_values")["value"].astype(int).tolist(),
                [3],
            )

    def test_card_tables_select_the_data_source_and_exact_file_name(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, repository = ProjectTableFixture.build(Path(directory))
            decoy = ProjectFileRecord(
                source_file="Voice.dat",
                relative_path="bin#/card_id.bin",
                workspace_path="voice/bin#/card_id.bin",
                file_kind="table",
                storage_format="table",
            )
            repository.import_resources(
                [ProjectResource(decoy, pd.DataFrame({"value": [999]}))]
            )
            manifest.files.insert(0, decoy)
            repository.save(manifest)
            self.assertEqual(
                repository.get_table("card_ids")["value"].astype(int).tolist(),
                [-1, 2],
            )

    def test_duplicate_custom_image_names_are_rejected_before_table_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            _, repository = ProjectTableFixture.build(Path(directory))
            cards = repository.get_table("cards")
            cards["image_name"] = ["CUSTOM0001.bmp", "custom0001.BMP"]
            original_ids = repository.get_table("card_ids")
            with self.assertRaisesRegex(ValueError, "must be unique"):
                repository.save_table("cards", cards)
            pd.testing.assert_frame_equal(
                repository.get_table("card_ids"),
                original_ids,
            )


class CardServiceTests(unittest.TestCase):
    def test_card_save_preserves_sentinel_catalog_names_and_renames_normal_card(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, repository = ProjectTableFixture.build(Path(directory))
            sentinel_names = {
                CardImageVariant.LARGE: "Generated by Getallcard.exe",
                CardImageVariant.MINI: "Mini generated card back",
            }
            for image_variant, sentinel_name in sentinel_names.items():
                catalog = repository.read_card_image_list(
                    image_variant=image_variant,
                )
                catalog.loc[0, "name"] = sentinel_name
                repository.write_card_image_list(
                    catalog,
                    image_variant=image_variant,
                )

            service = CardService(repository)
            draft = service.get_card_detail(None, 1).to_draft()
            draft.localized_text.names["eng"] = "Renamed Dragon"
            draft.dirty = True
            service.save_card_changes(None, [draft])

            reloaded = ProjectRepository(manifest)
            for image_variant, sentinel_name in sentinel_names.items():
                self.assertEqual(
                    reloaded.read_card_image_list(
                        image_variant=image_variant,
                    )["name"].tolist(),
                    [sentinel_name, "Renamed Dragon"],
                )

    def test_card_image_list_selector_reads_and_writes_only_requested_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            _, repository = ProjectTableFixture.build(Path(directory))
            large = repository.read_card_image_list(
                image_variant=CardImageVariant.LARGE,
            )
            mini = repository.read_card_image_list(image_variant="mini")
            self.assertEqual(large.loc[0, "name"], "Back")
            self.assertEqual(mini.loc[0, "name"], "Mini Back")

            large.loc[0, "note"] = "large-only"
            repository.write_card_image_list(
                large,
                image_variant="large",
            )
            self.assertEqual(
                repository.read_card_image_list(image_variant="large").loc[0, "note"],
                "large-only",
            )
            self.assertEqual(
                repository.read_card_image_list(image_variant="mini").loc[0, "note"],
                "Mini",
            )

            mini.loc[0, "note"] = "mini-only"
            repository.write_card_image_list(
                mini,
                image_variant=CardImageVariant.MINI,
            )
            self.assertEqual(
                repository.read_card_image_list(image_variant="large").loc[0, "note"],
                "large-only",
            )
            self.assertEqual(
                repository.read_card_image_list(image_variant="mini").loc[0, "note"],
                "mini-only",
            )

    def test_load_save_create_update_and_pack_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, repository = ProjectTableFixture.build(root / "project")
            png = root / "full.png"
            jpeg = root / "replacement.jpg"
            Image.new("RGB", (80, 120), "red").save(png, format="PNG")
            Image.new("RGB", (40, 60), "blue").save(jpeg, format="JPEG")
            service = CardService(repository)

            cards = repository.get_table("cards", language="eng")
            cards.loc[1, "name"] = "Saved"
            repository.save_table("cards", cards, language="eng")
            self.assertEqual(
                repository.get_table("cards", language="eng").loc[1, "name"],
                "Saved",
            )

            draft = service.create_card_draft()
            draft.image_name = "usr000.bmp"
            draft.large_image_source = png
            draft.small_image_source = png
            service.create_card(None, draft)
            added = repository.get_table("cards", language="eng")
            self.assertEqual(len(added), 3)
            image_name = str(added.iloc[-1]["image_name"])
            image_records = [
                item
                for item in manifest.files
                if item.file_kind == "image" and item.relative_path.endswith(image_name)
            ]
            self.assertEqual(len(image_records), 2)
            self.assertTrue(
                all(item.source_file == "Data.dat" for item in image_records)
            )
            for record in image_records:
                payload = repository.get_resource(record)
                self.assertTrue(bytes(payload).startswith(b"BM"))

            edited = service.get_card_detail(None, 2).to_draft()
            edited.large_image_source = jpeg
            service.update_card(None, edited)
            edited = service.get_card_detail(None, 2).to_draft()
            edited.small_image_source = jpeg
            service.update_card(None, edited)
            for record in image_records:
                self.assertTrue(
                    bytes(repository.get_resource(record)).startswith(b"BM")
                )

            resources = repository.export_resources(
                [item for item in manifest.files if item.source_file == "Data.dat"]
            )
            archive = GameRepository.from_root(root).encode_archive(
                "Data.dat",
                resources,
            )
            packed_paths = {
                item.relative_path.replace("\\", "/"): item.data
                for item in archive.entries
            }
            self.assertIn(f"card/{image_name}", packed_paths)
            self.assertIn(f"mini/{image_name}", packed_paths)
            self.assertIn("bin#/card_prop.bin", packed_paths)
            self.assertTrue(packed_paths[f"card/{image_name}"].startswith(b"BM"))
            self.assertTrue(packed_paths[f"mini/{image_name}"].startswith(b"BM"))
            packed_properties = GameRepository.decode_binary_resource(
                "card_prop.bin",
                packed_paths["bin#/card_prop.bin"],
            )
            self.assertEqual(
                packed_properties["card_category"].tolist(),
                ["normal", "effect", ""],
            )
            self.assertEqual(
                packed_properties["monster_type"].tolist(),
                ["winged_beast", "dragon", "non_game_card"],
            )

    def test_card_service_validation_and_repository_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, repository = ProjectTableFixture.build(Path(directory))
            service = CardService(repository)
            with self.assertRaisesRegex(IndexError, "999"):
                service.get_card_detail(None, 999)
            draft = service.get_card_detail(None, 1).to_draft()
            draft.card_id = 999
            with self.assertRaisesRegex(Exception, "immutable"):
                service.update_card(None, draft)

            self.assertEqual(
                len(service.load_card_details(manifest)),
                2,
            )
            self.assertEqual(
                len(CardService().load_card_details(manifest)),
                2,
            )
            with self.assertRaisesRegex(ValueError, "manifest or repository"):
                CardService().load_card_details()


class UnknownBinaryTests(unittest.TestCase):
    def test_language_like_unknown_binary_names_round_trip_raw(self):
        from yugioh_editor.models.entities import ContainerArchive, ContainerEntry

        archive = ContainerArchive(
            "Data.dat",
            entries=[
                ContainerEntry("bin#/customeng.bin", data=b"\x00\xff", order=0),
                ContainerEntry("bin#/unknownspa.bin", data=b"\x80\x81", order=1),
                ContainerEntry("bin#/abc.bin", data=b"\x10\x11", order=2),
                ContainerEntry(
                    "bin#/customcard_id.bin",
                    data=b"\x12\x13\x14",
                    order=3,
                ),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = GameRepository.from_root(root / "game")
            resources = game.decode_archive(archive, "data")
            self.assertTrue(
                all(item.record.storage_format == "binary" for item in resources)
            )
            manifest = ProjectManifest(
                "Raw",
                str(root / "project"),
                version_prefix="mai",
                files=[item.record for item in resources],
                game_files={"data.dat": "Data.dat"},
            )
            project = ProjectRepository(manifest)
            project.ensure_root()
            project.import_resources(resources)
            rebuilt = game.encode_archive(
                "Data.dat",
                project.export_resources(manifest.files),
            )
            self.assertEqual(
                [item.data for item in rebuilt.entries],
                [item.data for item in archive.entries],
            )


class ProjectRepositoryResourceTests(unittest.TestCase):
    def test_resource_crud_lists_paths_and_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = ProjectManifest(
                "Resources",
                str(root / "project"),
                version_prefix="mai",
                game_files={"data.dat": "Data.dat"},
            )
            resources = [
                ProjectResource(
                    ProjectFileRecord(
                        "Data.dat",
                        "docs/readme.txt",
                        "data/docs/readme.txt",
                        "text",
                        "text",
                        order=0,
                    ),
                    "hello",
                ),
                ProjectResource(
                    ProjectFileRecord(
                        "Data.dat",
                        "raw/value.bin",
                        "data/raw/value.bin",
                        "binary",
                        "binary",
                        order=1,
                    ),
                    b"\x00\x01\x02",
                ),
            ]
            repository = ProjectRepository(manifest)
            repository.ensure_root()
            manifest.files.extend(repository.import_resources(resources))
            repository.save()

            loaded = ProjectRepository(manifest.root).load()
            self.assertEqual(loaded.name, "Resources")
            repository = ProjectRepository(loaded)
            text_record, binary_record = loaded.files
            self.assertEqual(repository.get_resource(text_record), "hello")
            repository.save_resource(text_record, "updated")
            self.assertEqual(repository.get_resource("docs/readme.txt"), "updated")
            self.assertEqual(
                repository.get_binary_preview(binary_record, 2),
                (b"\x00\x01", 3),
            )
            self.assertEqual(
                repository.resource_path(binary_record).name,
                "value.bin",
            )
            repository.save_resource(binary_record, b"\x03")
            self.assertEqual(repository.get_resource(binary_record), b"\x03")

            replacement = root / "replacement.bin"
            replacement.write_bytes(b"new")
            repository.replace_resource(binary_record, replacement)
            self.assertEqual(repository.get_resource(binary_record), b"new")
            with self.assertRaises(TypeError):
                repository.get_binary_preview(text_record, 1)
            with self.assertRaises(KeyError):
                repository.get_resource("missing.bin")

            visible = repository.list_visible_resources(loaded)
            self.assertEqual(len(visible), 2)
            self.assertEqual(
                repository.find_records(loaded, source_file="data.dat"),
                visible,
            )
            self.assertEqual(
                repository.find_records(loaded, suffix="value.bin"),
                [binary_record],
            )
            self.assertEqual(
                repository.get_game_file_name("DATA.DAT"),
                "Data.dat",
            )

    def test_deck_table_language_errors_and_repository_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = ProjectManifest(
                "Deck",
                str(root / "project"),
                version_prefix="mai",
                files=[
                    ProjectFileRecord(
                        "deck.ydc",
                        "deck.ydc",
                        "deck/deck.ydc",
                        "table",
                        "table",
                    )
                ],
                game_files={"deck.ydc": "deck.ydc"},
            )
            repository = ProjectRepository(manifest)
            repository.ensure_root()
            repository.write_table(
                "deck/deck.ydc",
                pd.DataFrame({"card_id": [1, 2]}),
            )
            repository.save()
            self.assertEqual(
                repository.get_table("deck_cards")["card_id"].astype(int).tolist(),
                [1, 2],
            )
            repository.save_table(
                "deck_cards",
                pd.DataFrame({"card_id": [3]}),
            )
            self.assertEqual(
                repository.get_table("deck_cards")["card_id"].astype(int).tolist(),
                [3],
            )
            with self.assertRaises(ValueError):
                repository.get_table("card_names", language="zzz")
            with self.assertRaises(KeyError):
                repository.get_game_file_name("missing.dat")

            final = ProjectRepository(root / "final")
            staging = final.begin_create()
            staging.ensure_root()
            staging.write_text("ready.txt", "yes")
            final.commit_create(staging)
            self.assertTrue((final.root / "ready.txt").exists())

            pack_staging = final.begin_pack()
            pack_staging.write_text("output.txt", "packed")
            output = final.commit_pack(pack_staging)
            self.assertEqual(output.name, "bin")
            self.assertTrue((output / "output.txt").exists())

    def test_image_resource_conversion_and_virtual_access_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_record = ProjectFileRecord(
                "Data.dat",
                "card/image.bmp",
                "data/card/image.bmp",
                "image",
                "binary",
            )
            virtual_record = ProjectFileRecord(
                "Data.dat",
                "bin#/card_intid.bin",
                None,
                "virtual",
                "virtual",
                generated_on_pack=True,
                virtual=True,
                order=1,
            )
            manifest = ProjectManifest(
                "Images",
                str(root / "project"),
                version_prefix="mai",
                files=[image_record, virtual_record],
                game_files={"data.dat": "Data.dat"},
            )
            repository = ProjectRepository(manifest)
            repository.ensure_root()
            repository.write_image(image_record.workspace_path, b"initial")
            source = root / "source.png"
            Image.new("RGB", (10, 10), "purple").save(source, format="PNG")
            repository.replace_image_resource(image_record, source)
            self.assertTrue(
                bytes(repository.get_resource(image_record)).startswith(b"BM")
            )
            with self.assertRaises(ValueError):
                repository.get_resource(virtual_record)
            with self.assertRaises(ValueError):
                repository.save_resource(virtual_record, b"x")
            with self.assertRaises(ValueError):
                repository.replace_resource(virtual_record, source)
            with self.assertRaises(ValueError):
                repository.resource_path(virtual_record)

    def test_add_card_images_uses_filesystem_names_and_rolls_back_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = ProjectManifest(
                "Images",
                str(root / "project"),
                version_prefix="mai",
                game_files={"data.dat": "Data.dat"},
            )
            repository = ProjectRepository(manifest)
            repository.ensure_root()
            repository.write_image("data/card/CUSTOM0000.BMP", b"BM")
            source = root / "source.png"
            Image.new("RGB", (10, 10), "purple").save(source, format="PNG")

            image_name = repository.add_card_images(source)
            self.assertEqual(image_name, "CUSTOM0001.bmp")
            repository.delete_card_images(image_name)

            original_records = list(manifest.files)
            with self.assertRaises(FileNotFoundError):
                repository.add_card_images(
                    source,
                    root / "missing.png",
                )
            self.assertEqual(manifest.files, original_records)
            self.assertFalse(
                repository.exists("data/card/CUSTOM0001.bmp"),
            )
            self.assertFalse(
                repository.exists("data/mini/CUSTOM0001.bmp"),
            )

    def test_named_card_image_batch_sorts_data_records_and_writes_manifest_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            records = [
                ProjectFileRecord(
                    "DATA.DAT",
                    "bin#/before.bin",
                    "data/bin#/before.bin",
                    "binary",
                    "binary",
                    order=0,
                ),
                ProjectFileRecord(
                    "DATA.DAT",
                    "Card\\tp4013.bmp",
                    "data/Card/tp4013.bmp",
                    "image",
                    "binary",
                    compressed=True,
                    order=1,
                ),
                ProjectFileRecord(
                    "DATA.DAT",
                    "Card/ZZZ001.bmp",
                    "data/Card/ZZZ001.bmp",
                    "image",
                    "binary",
                    compressed=True,
                    order=2,
                ),
                ProjectFileRecord(
                    "DATA.DAT",
                    "misc/middle.bin",
                    "data/misc/middle.bin",
                    "binary",
                    "binary",
                    order=3,
                ),
                ProjectFileRecord(
                    "DATA.DAT",
                    "Mini/tp4013.bmp",
                    "data/Mini/tp4013.bmp",
                    "image",
                    "binary",
                    compressed=True,
                    order=4,
                ),
                ProjectFileRecord(
                    "DATA.DAT",
                    "Mini\\ZZZ001.bmp",
                    "data/Mini/ZZZ001.bmp",
                    "image",
                    "binary",
                    compressed=True,
                    order=5,
                ),
                ProjectFileRecord(
                    "Voice.dat",
                    "voice/a.wav",
                    "voice/a.wav",
                    "audio",
                    "binary",
                    order=0,
                ),
                ProjectFileRecord(
                    "Voice.dat",
                    "voice/b.wav",
                    "voice/b.wav",
                    "audio",
                    "binary",
                    order=1,
                ),
                ProjectFileRecord(
                    "Region.dat",
                    "Region.dat",
                    "Region.dat",
                    "binary",
                    "binary",
                    order=0,
                ),
            ]
            manifest = ProjectManifest(
                "Batch images",
                str(project_root),
                version_prefix="mai",
                files=records,
                game_files={
                    "data.dat": "DATA.DAT",
                    "voice.dat": "Voice.dat",
                    "region.dat": "Region.dat",
                },
            )
            repository = ProjectRepository(manifest)
            repository.ensure_root()
            sample_image = BytesIO()
            Image.new("RGB", (10, 10), "black").save(sample_image, format="BMP")
            for record in records:
                repository.write_bytes(
                    record.workspace_path,
                    sample_image.getvalue() if record.file_kind == "image" else b"raw",
                )
            first = root / "first.png"
            second = root / "second.png"
            Image.new("RGB", (20, 30), "red").save(first)
            Image.new("RGB", (20, 30), "blue").save(second)
            original_manifest_write = repository._connection.write_manifest
            physical_before_manifest: list[bool] = []

            def write_manifest_after_files(current):
                physical_before_manifest.append(
                    all(
                        record.virtual
                        or (
                            record.workspace_path is not None
                            and repository.exists(record.workspace_path)
                        )
                        for record in current.files
                    )
                )
                return original_manifest_write(current)

            with (
                patch.object(
                    repository,
                    "existing_card_image_names",
                    wraps=repository.existing_card_image_names,
                ) as inventory,
                patch.object(
                    repository,
                    "_get_card_catalog",
                    wraps=repository._get_card_catalog,
                ) as catalog_load,
                patch.object(
                    repository._connection,
                    "write_manifest",
                    side_effect=write_manifest_after_files,
                ) as manifest_write,
                patch(
                    "yugioh_editor.repositories.project.repository."
                    "estimate_available_memory_bytes",
                    return_value=512 * 1024 * 1024,
                ),
            ):
                added = repository.add_named_card_images_batch(
                    (
                        NamedCardImagePair("zzz002.bmp", first, first),
                        NamedCardImagePair("aaa002.bmp", second, second),
                        NamedCardImagePair("usr000.bmp", first, first),
                    ),
                    save_manifest=True,
                )

            self.assertEqual(inventory.call_count, 1)
            self.assertEqual(catalog_load.call_count, 2)
            self.assertEqual(manifest_write.call_count, 1)
            self.assertEqual(physical_before_manifest, [True])
            self.assertEqual(len(added), 6)
            self.assertTrue(all(not record.compressed for record in added))
            self.assertTrue(all(record.source_file == "DATA.DAT" for record in added))
            self.assertEqual(
                [record.relative_path for record in added],
                [
                    "Card/zzz002.bmp",
                    "Card/aaa002.bmp",
                    "Card/usr000.bmp",
                    "Mini/zzz002.bmp",
                    "Mini/aaa002.bmp",
                    "Mini/usr000.bmp",
                ],
            )
            data_records = sorted(
                (
                    record
                    for record in manifest.files
                    if record.source_file.casefold() == "data.dat"
                ),
                key=lambda record: record.order,
            )
            self.assertEqual(
                [record.relative_path for record in data_records],
                [
                    "bin#/before.bin",
                    "Card/aaa002.bmp",
                    "Card\\tp4013.bmp",
                    "Card/usr000.bmp",
                    "Card/ZZZ001.bmp",
                    "Card/zzz002.bmp",
                    "Mini/aaa002.bmp",
                    "Mini/tp4013.bmp",
                    "Mini/usr000.bmp",
                    "Mini\\ZZZ001.bmp",
                    "Mini/zzz002.bmp",
                    "misc/middle.bin",
                ],
            )
            self.assertEqual(
                [record.order for record in data_records],
                list(range(len(data_records))),
            )
            self.assertEqual(
                [
                    (record.relative_path, record.order)
                    for record in manifest.files
                    if record.source_file == "Voice.dat"
                ],
                [("voice/a.wav", 0), ("voice/b.wav", 1)],
            )
            region = next(
                record
                for record in manifest.files
                if record.source_file == "Region.dat"
            )
            self.assertEqual((region.relative_path, region.order), ("Region.dat", 0))
            for record in added:
                self.assertTrue(repository.exists(record.workspace_path))
                self.assertTrue(
                    repository.read_bytes(record.workspace_path).startswith(b"BM")
                )

    def test_card_image_order_plan_normalizes_complete_path_case_insensitively(self):
        def record(path: str, order: int = 0) -> ProjectFileRecord:
            return ProjectFileRecord(
                "Data.dat",
                path,
                f"data/{path}",
                "image" if path.casefold().endswith(".bmp") else "binary",
                "binary",
                order=order,
            )

        existing = [
            record("misc/middle.bin", 0),
            record("Mini\\ZZZ001.bmp", 1),
            record("Card/ZZZ001.bmp", 2),
            record("bin#/before.bin", 3),
            record("Mini/tp4013.bmp", 4),
            record("Card\\tp4013.bmp", 5),
        ]
        new_large = [
            record("Card/zzz002.bmp"),
            record("Card/aaa002.bmp"),
            record("Card/usr000.bmp"),
        ]
        new_mini = [
            record("Mini/zzz002.bmp"),
            record("Mini/aaa002.bmp"),
            record("Mini/usr000.bmp"),
        ]

        planned = ProjectRepository._plan_card_image_record_order(
            existing,
            new_large,
            new_mini,
        )

        self.assertEqual(
            [item.relative_path for item in planned],
            [
                "bin#/before.bin",
                "Card/aaa002.bmp",
                "Card\\tp4013.bmp",
                "Card/usr000.bmp",
                "Card/ZZZ001.bmp",
                "Card/zzz002.bmp",
                "Mini/aaa002.bmp",
                "Mini/tp4013.bmp",
                "Mini/usr000.bmp",
                "Mini\\ZZZ001.bmp",
                "Mini/zzz002.bmp",
                "misc/middle.bin",
            ],
        )

    def test_card_image_order_plan_uses_windows_separator_prefix_order(self):
        def record(
            path: str,
            *,
            virtual: bool = False,
        ) -> ProjectFileRecord:
            return ProjectFileRecord(
                "Data.dat",
                path,
                None if virtual else f"data/{path}",
                "virtual" if virtual else "binary",
                "virtual" if virtual else "binary",
                generated_on_pack=virtual,
                virtual=virtual,
            )

        source_records = [
            record("reaction01.txt"),
            record("reaction02.txt"),
            record("reaction\\reaction01.bmp"),
            record("start.txt"),
            record("start\\kage.yga", virtual=True),
            record("summon.txt"),
            record("summon2.txt"),
            record("summon\\mask.bmp"),
        ]

        planned = ProjectRepository._plan_card_image_record_order(
            source_records,
            (),
            (),
        )

        self.assertEqual(
            [item.relative_path for item in planned],
            [
                "reaction01.txt",
                "reaction02.txt",
                "reaction\\reaction01.bmp",
                "start.txt",
                "start\\kage.yga",
                "summon.txt",
                "summon2.txt",
                "summon\\mask.bmp",
            ],
        )

    def test_named_card_image_batch_validates_all_inputs_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, repository = ProjectTableFixture.build(root / "project")
            source = root / "source.png"
            Image.new("RGB", (10, 10), "purple").save(source)
            original_files = list(manifest.files)
            original_orders = [record.order for record in manifest.files]

            with self.assertRaisesRegex(ValueError, "Duplicate card image name"):
                repository.add_named_card_images_batch(
                    (
                        NamedCardImagePair("usr000.bmp", source, source),
                        NamedCardImagePair("USR000.BMP", source, source),
                    )
                )

            self.assertEqual(manifest.files, original_files)
            self.assertEqual(
                [record.order for record in manifest.files],
                original_orders,
            )
            self.assertFalse(repository.exists("data/card/usr000.bmp"))
            self.assertFalse(repository.exists("data/mini/usr000.bmp"))

            with self.assertRaisesRegex(ValueError, "requires a complete pair"):
                repository.add_named_card_images_batch(
                    (NamedCardImagePair("usr002.bmp", None, source),)
                )
            self.assertFalse(repository.exists("data/card/usr002.bmp"))

    def test_named_card_image_batch_rejects_catalog_only_name_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, repository = ProjectTableFixture.build(root / "project")
            source = root / "source.png"
            Image.new("RGB", (10, 10), "purple").save(source)
            catalog = repository.read_card_image_list(
                image_variant=CardImageVariant.LARGE
            )
            catalog.loc[0, "image_name"] = "CatalogOnly.BMP"
            repository.write_card_image_list(
                catalog,
                image_variant=CardImageVariant.LARGE,
            )

            with self.assertRaisesRegex(ValueError, "already exists"):
                repository.add_named_card_images_batch(
                    (NamedCardImagePair("catalogonly.bmp", source, source),)
                )

            self.assertFalse(repository.exists("data/card/catalogonly.bmp"))
            self.assertFalse(repository.exists("data/mini/catalogonly.bmp"))

    def test_named_card_image_batch_prepare_and_write_failure_roll_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, repository = ProjectTableFixture.build(root / "project")
            source = root / "source.png"
            Image.new("RGB", (10, 10), "purple").save(source)
            original_files = list(manifest.files)
            original_orders = [record.order for record in manifest.files]

            with self.assertRaises(FileNotFoundError):
                repository.add_named_card_images_batch(
                    (
                        NamedCardImagePair("usr000.bmp", source, source),
                        NamedCardImagePair("usr001.bmp", root / "missing.png", source),
                    )
                )
            self.assertFalse(repository.exists("data/card/usr000.bmp"))

            original_write = repository._connection.write_image

            def fail_second_pair(path, data):
                if str(path).casefold().endswith("usr001.bmp"):
                    raise OSError("write failed")
                return original_write(path, data)

            with (
                patch.object(
                    repository._connection,
                    "write_image",
                    side_effect=fail_second_pair,
                ),
                self.assertRaisesRegex(OSError, "write failed"),
            ):
                repository.add_named_card_images_batch(
                    (
                        NamedCardImagePair("usr000.bmp", source, source),
                        NamedCardImagePair("usr001.bmp", source, source),
                    )
                )

            self.assertEqual(manifest.files, original_files)
            self.assertEqual(
                [record.order for record in manifest.files],
                original_orders,
            )
            for folder in ("card", "mini"):
                for name in ("usr000.bmp", "usr001.bmp"):
                    self.assertFalse(repository.exists(f"data/{folder}/{name}"))

    def test_named_card_image_batch_manifest_failure_rolls_back_files_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, repository = ProjectTableFixture.build(root / "project")
            source = root / "source.png"
            Image.new("RGB", (10, 10), "purple").save(source)
            original_manifest = repository.read_bytes("project.json")
            original_files = list(manifest.files)
            original_orders = [record.order for record in manifest.files]

            with (
                patch.object(
                    repository._connection,
                    "write_manifest",
                    side_effect=OSError("manifest write failed"),
                ),
                self.assertRaisesRegex(OSError, "manifest write failed"),
            ):
                repository.add_named_card_images_batch(
                    (NamedCardImagePair("usr000.bmp", source, source),),
                    save_manifest=True,
                )

            self.assertEqual(manifest.files, original_files)
            self.assertEqual(
                [record.order for record in manifest.files],
                original_orders,
            )
            self.assertEqual(repository.read_bytes("project.json"), original_manifest)
            self.assertFalse(repository.exists("data/card/usr000.bmp"))
            self.assertFalse(repository.exists("data/mini/usr000.bmp"))

    def test_named_card_image_batch_rejects_workspace_name_case_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, repository = ProjectTableFixture.build(root / "project")
            source = root / "source.png"
            Image.new("RGB", (10, 10), "purple").save(source)
            repository.write_bytes("data/card/UsR000.BMP", b"existing")

            with self.assertRaisesRegex(ValueError, "already exists: usr000.bmp"):
                repository.add_named_card_images_batch(
                    (NamedCardImagePair("usr000.bmp", source, source),)
                )

            self.assertEqual(
                repository.read_bytes("data/card/UsR000.BMP"),
                b"existing",
            )
            self.assertFalse(repository.exists("data/mini/usr000.bmp"))

    def test_compatibility_resource_wrappers_and_table_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = ProjectRepository(root / "project")
            repository.ensure_root()
            repository.write_bytes("raw.bin", b"raw")
            self.assertEqual(repository.read_bytes("raw.bin"), b"raw")
            repository.write_text("text.txt", "text")
            self.assertEqual(repository.read_text("text.txt"), "text")
            repository.write_audio("audio.wav", b"audio")
            self.assertEqual(repository.read_audio("audio.wav"), b"audio")
            repository.write_executable("game.exe", b"MZ")
            self.assertEqual(repository.read_executable("game.exe"), b"MZ")
            repository.write_binary("other.bin", b"other")
            self.assertEqual(repository.read_binary("other.bin"), b"other")
            repository.write_image("image.bmp", b"BM")
            self.assertEqual(repository.read_image("image.bmp"), b"BM")
            copied = root / "copied.bin"
            copied.write_bytes(b"copy")
            repository.copy_file(copied, "copy.bin")
            self.assertTrue(repository.exists("copy.bin"))
            repository.delete_file("copy.bin")
            self.assertFalse(repository.exists("copy.bin"))

            manifest = ProjectManifest(
                "Created",
                str(root / "created"),
                version_prefix="mai",
            )
            created = repository.create(root / "created", manifest)
            self.assertEqual(created.name, "Created")
            repository = ProjectRepository(root / "created")
            repository.write_manifest(manifest)
            self.assertEqual(repository.read_manifest().name, "Created")
            self.assertTrue(repository.has_table("cards"))
            self.assertFalse(repository.has_table("missing"))
            with self.assertRaisesRegex(KeyError, "Unknown table 'missing'"):
                repository.save_table(
                    "missing",
                    pd.DataFrame(),
                )
