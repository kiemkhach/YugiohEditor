import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from PIL import Image

from yugioh_editor.common.constants import CODEC_OPERATIONS
from yugioh_editor.models.entities import ContainerArchive, ContainerEntry
from yugioh_editor.repositories.game.connection import GameFolderConnection
from yugioh_editor.repositories.project.connection import ProjectFolderConnection


class GameConnectionExtendedTests(unittest.TestCase):
    def test_codec_registries_match_the_canonical_operation_set(self):
        connection = GameFolderConnection(".")
        self.assertEqual(set(connection._decoders), set(CODEC_OPERATIONS))
        self.assertEqual(set(connection._encoders), set(CODEC_OPERATIONS))

    def test_filesystem_container_and_subfile_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = GameFolderConnection(root)
            connection.write_binary("top.bin", b"top")
            connection.write_binary("nested/item.bin", b"nested")
            archive = ContainerArchive(
                "Data.dat",
                entries=[ContainerEntry("a.bin", data=b"A", order=0)],
            )
            connection.write_container("Data.dat", archive, "never")

            self.assertEqual(connection.read_binary("top.bin"), b"top")
            self.assertEqual(len(connection.list_files()), 2)
            self.assertEqual(len(connection.list_files(recursive=True)), 3)
            self.assertEqual(
                [path.name for path in connection.list_binary_files()],
                ["top.bin"],
            )
            self.assertEqual(
                [path.name for path in connection.list_container_files()],
                ["Data.dat"],
            )
            with self.assertRaises(ValueError):
                connection.resolve("../outside")

            rebuilt = connection.read_container("Data.dat")
            self.assertEqual(
                connection.list_container_subfiles(rebuilt),
                ["a.bin"],
            )
            self.assertEqual(
                connection.read_container_subfile(rebuilt, "A.BIN"),
                b"A",
            )
            connection.replace_container_subfile(rebuilt, "a.bin", b"B")
            self.assertEqual(rebuilt.entries[0].data, b"B")
            added = connection.add_container_subfile(
                rebuilt,
                "folder/b.bin",
                b"C",
                compressed=False,
            )
            self.assertEqual(added.order, 1)
            with self.assertRaises(ValueError):
                connection.add_container_subfile(
                    rebuilt,
                    "FOLDER/B.BIN",
                    b"D",
                )
            connection.delete_container_subfile(rebuilt, "a.bin")
            self.assertEqual(rebuilt.entries[0].order, 0)
            self.assertEqual(
                connection.use_root(root / "other").root,
                (root / "other").resolve(),
            )

    def test_generic_structured_operations_and_errors(self):
        connection = GameFolderConnection(".")
        integer_data = connection.write_integer_list(
            [-1, 2],
            byte_width=2,
            signed=True,
        )
        self.assertEqual(
            connection.read_integer_list(
                integer_data,
                byte_width=2,
                signed=True,
            ),
            [-1, 2],
        )
        raw_hex = connection.write_fixed_hex_list(
            ["00000001", "FFFFFFFF"],
            byte_width=4,
        )
        self.assertEqual(raw_hex, bytes.fromhex("00 00 00 01 FF FF FF FF"))
        self.assertEqual(
            connection.read_fixed_hex_list(raw_hex, byte_width=4),
            ["00000001", "FFFFFFFF"],
        )
        string_data = connection.write_fixed_string_list(
            ["A", "B"],
            record_size=4,
            encoding="utf-8",
        )
        self.assertEqual(
            connection.read_fixed_string_list(
                string_data,
                record_size=4,
                encoding="utf-8",
            ),
            ["A", "B"],
        )
        records = [
            {"text": "A", "is_reserved": False},
            {"text": "B", "is_reserved": False},
        ]
        blob = connection.write_offset_string_table(
            records,
            encoding="utf-8",
        )
        self.assertEqual(
            connection.read_offset_string_table(
                blob,
                [0, 4],
                encoding="utf-8",
            ),
            records,
        )
        terminated = connection.write_terminated_string_list(
            ["A", "B"],
            encoding="utf-8",
        )
        self.assertEqual(
            connection.read_terminated_string_list(
                terminated,
                encoding="utf-8",
            ),
            ["A", "B"],
        )
        rows = [
            {
                "attack": 1600,
                "defense": 1200,
                "monster_type_code": 0x01,
                "monster_type": "dragon",
                "card_category_code": 0x00,
                "card_category": "normal",
                "attribute_code": 0x02,
                "attribute": "dark",
                "level": 4,
                "requires_two_tributes": False,
            }
        ]
        records = connection.write_record_table(
            rows,
            record_size=4,
            row_encoder="nibble_statistics",
        )
        self.assertEqual(
            connection.read_record_table(
                records,
                record_size=4,
                row_decoder="nibble_statistics",
            ),
            rows,
        )
        with self.assertRaisesRegex(ValueError, "Unknown row decoder"):
            connection.read_record_table(
                records,
                record_size=4,
                row_decoder="missing",
            )
        with self.assertRaisesRegex(ValueError, "Unknown row encoder"):
            connection.write_record_table(
                rows,
                record_size=4,
                row_encoder="missing",
            )

        rows = [{"label": "Alpha", "count": 2}]
        catalog_data = connection.write_regex_record_table(
            rows,
            template="{label}:{count}\n",
            encoding="utf-8",
        )
        self.assertEqual(
            connection.read_regex_record_table(
                catalog_data,
                pattern=re.compile(r"(?P<label>[^:]+):(?P<count>\d+)\n"),
                encoding="utf-8",
            ),
            [{"label": "Alpha", "count": "2"}],
        )
        text = connection.encode_text_data(
            "café",
            encoding="cp1252",
        )
        self.assertEqual(
            connection.decode_text_data(
                text,
                encoding="cp1252",
            ),
            "café",
        )
        self.assertFalse(hasattr(connection, "sort_order"))


class ProjectConnectionExtendedTests(unittest.TestCase):
    def test_files_lists_previews_and_typed_wrappers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = ProjectFolderConnection(root)
            connection.ensure_root()
            connection.write_bytes("folder/data.bin", b"012345")
            connection.write_text("folder/text.txt", "hello")
            self.assertTrue(connection.exists("folder/data.bin"))
            self.assertEqual(len(connection.list_files()), 2)
            self.assertEqual(
                [item.name for item in connection.list_directories()],
                ["folder"],
            )
            self.assertEqual(
                connection.read_bytes_preview("folder/data.bin", 3),
                (b"012", 6),
            )
            with self.assertRaises(ValueError):
                connection.read_bytes_preview("folder/data.bin", 0)
            with self.assertRaises(ValueError):
                connection.resolve("../outside")

            source = root / "source.bin"
            source.write_bytes(b"COPY")
            connection.copy_file(source, "folder/copy.bin")
            self.assertEqual(connection.read_binary("folder/copy.bin"), b"COPY")
            with patch(
                "yugioh_editor.repositories.project.connection.shutil.copy2",
                side_effect=OSError("copy failed"),
            ):
                with self.assertRaisesRegex(OSError, "copy failed"):
                    connection.copy_file(source, "folder/copy.bin")
            self.assertEqual(connection.read_binary("folder/copy.bin"), b"COPY")
            connection.write_audio("folder/audio.wav", b"WAVE")
            connection.write_executable("folder/game.exe", b"MZ")
            self.assertEqual(connection.read_audio("folder/audio.wav"), b"WAVE")
            self.assertEqual(
                connection.read_executable("folder/game.exe"),
                b"MZ",
            )
            connection.delete_file("folder/copy.bin")
            self.assertFalse(connection.exists("folder/copy.bin"))

    def test_tables_images_and_staging_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = ProjectFolderConnection(root / "project")
            connection.ensure_root()
            connection.write_table(
                "table.csv",
                pd.DataFrame({"a": [1]}),
                columns=("a", "b"),
            )
            table = connection.read_table("table.csv")
            self.assertEqual(list(table.columns), ["a", "b"])
            connection.write_bytes("empty.csv", b"")
            self.assertTrue(connection.read_table("empty.csv").empty)
            self.assertEqual(connection.read_int_list("table.csv", "missing"), [])
            self.assertEqual(
                connection.read_string_list("table.csv", "missing"),
                [],
            )
            connection.write_fixed_string_list("strings.csv", ["A", "B"])
            self.assertEqual(
                connection.read_fixed_string_list("strings.csv"),
                ["A", "B"],
            )

            source = root / "source.png"
            Image.new("RGB", (12, 18), "green").save(source, format="PNG")
            connection.convert_image_to_bmp(
                source,
                "images/card.bmp",
                size=(6, 9),
            )
            self.assertTrue(connection.read_image("images/card.bmp").startswith(b"BM"))
            self.assertEqual(connection.image_size("images/card.bmp"), (6, 9))

            final = ProjectFolderConnection(root / "final")
            staging = final.create_staging_sibling("create")
            staging.write_text("value.txt", "ready")
            final.commit_staging_root(staging)
            self.assertEqual(final.read_text("value.txt"), "ready")

            output = final.create_staging_sibling("pack")
            output.write_text("new.txt", "new")
            old = final.resolve("bin")
            old.mkdir()
            (old / "old.txt").write_text("old", encoding="utf-8")
            final.replace_directory(output, "bin")
            self.assertTrue((final.root / "bin/new.txt").exists())

            disposable = final.create_staging_sibling("discard")
            disposable.write_text("temp.txt", "temp")
            disposable.discard_root()
            self.assertFalse(disposable.root.exists())

    def test_prepared_bmp_bytes_are_validated_without_reencoding(self):
        output = BytesIO()
        Image.new("RGB", (12, 18), "green").save(output, format="BMP")
        payload = output.getvalue()

        self.assertIs(
            ProjectFolderConnection.convert_image_to_bmp_bytes(payload),
            payload,
        )
        self.assertIs(
            ProjectFolderConnection.convert_image_to_bmp_bytes(
                payload,
                size=(12, 18),
            ),
            payload,
        )
        resized = ProjectFolderConnection.convert_image_to_bmp_bytes(
            payload,
            size=(6, 9),
        )
        self.assertNotEqual(resized, payload)
        with Image.open(BytesIO(resized)) as image:
            self.assertEqual(image.size, (6, 9))
