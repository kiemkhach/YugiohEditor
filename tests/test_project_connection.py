import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from yugioh_editor.models.entities import ProjectManifest
from yugioh_editor.repositories.project.connection import ProjectFolderConnection


class ProjectConnectionTests(unittest.TestCase):
    def test_csv_row_rewrite_preserves_unrelated_records_and_csv_values(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = ProjectFolderConnection(directory)
            connection.ensure_root()
            relative_path = "data/edge.csv"
            payload = (
                "name,note,value\r\n"
                '"Alpha","quoted ""note""",1\r\n'
                '"Beta, B","multi\nline",2\r\n'
                "Gamma,,3"
            ).encode("utf-8-sig")
            connection.write_bytes(relative_path, payload)

            connection.rewrite_csv_rows(
                relative_path,
                {
                    1: {"name": "Unicode é 日本", "note": 'new, "note"\nline'},
                    2: {"value": ""},
                },
                expected_rows={
                    1: {"name": "Beta, B", "note": "multi\nline", "value": 2},
                    2: {"name": "Gamma", "note": "", "value": 3},
                },
                expected_columns=("name", "note", "value"),
                expected_row_count=3,
            )

            serialized = connection.read_bytes(relative_path)
            self.assertTrue(serialized.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b'"Alpha","quoted ""note""",1\r\n', serialized)
            inspection = connection.inspect_csv_table(
                relative_path,
                expected_columns=("name", "note", "value"),
                expected_row_count=3,
            )
            self.assertEqual(
                inspection.row(1),
                {
                    "name": "Unicode é 日本",
                    "note": 'new, "note"\nline',
                    "value": "2",
                },
            )
            self.assertEqual(inspection.row(2)["value"], "")

    def test_csv_row_rewrite_rejects_shape_target_and_stale_values(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = ProjectFolderConnection(directory)
            connection.ensure_root()
            relative_path = "data/values.csv"
            connection.write_bytes(
                relative_path,
                "value\nfirst\nsecond\n".encode("utf-8-sig"),
            )
            before = connection.read_bytes(relative_path)

            cases = (
                {"expected_columns": ("wrong",)},
                {"expected_row_count": 3},
                {"updates": {2: {"value": "missing"}}},
                {"expected_rows": {0: {"value": "stale"}}},
            )
            for options in cases:
                with (
                    self.subTest(options=options),
                    self.assertRaises((ValueError, IndexError)),
                ):
                    connection.rewrite_csv_rows(
                        relative_path,
                        options.get("updates", {0: {"value": "changed"}}),
                        expected_rows=options.get(
                            "expected_rows", {0: {"value": "first"}}
                        ),
                        expected_columns=options.get("expected_columns", ("value",)),
                        expected_row_count=options.get("expected_row_count", 2),
                    )
                self.assertEqual(connection.read_bytes(relative_path), before)

    def test_csv_row_rewrite_is_atomic_and_breaks_staging_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            live = ProjectFolderConnection(Path(directory) / "project")
            live.ensure_root()
            relative_path = "data/values.csv"
            live.write_bytes(
                relative_path,
                "value\nfirst\nsecond\n".encode("utf-8-sig"),
            )
            staging = live.create_staging_clone("rows")
            live_before = live.read_bytes(relative_path)

            staging.rewrite_csv_rows(
                relative_path,
                {1: {"value": "changed"}},
                expected_rows={1: {"value": "second"}},
                expected_columns=("value",),
                expected_row_count=2,
            )
            self.assertEqual(live.read_bytes(relative_path), live_before)
            self.assertNotEqual(staging.read_bytes(relative_path), live_before)

            staged_before = staging.read_bytes(relative_path)
            with (
                patch(
                    "yugioh_editor.repositories.project.connection.os.replace",
                    side_effect=OSError("controlled replace failure"),
                ),
                self.assertRaises(OSError),
            ):
                staging.rewrite_csv_rows(
                    relative_path,
                    {0: {"value": "failure"}},
                    expected_rows={0: {"value": "first"}},
                    expected_columns=("value",),
                    expected_row_count=2,
                )
            self.assertEqual(staging.read_bytes(relative_path), staged_before)

    def test_text_round_trip_preserves_newline_sequences_and_trailing_state(self):
        cases = {
            "crlf_trailing": "alpha\r\nbeta\r\n",
            "crlf_no_trailing": "alpha\r\nbeta",
            "lf_trailing": "alpha\nbeta\n",
            "lf_no_trailing": "alpha\nbeta",
            "cr_trailing": "alpha\rbeta\r",
            "cr_no_trailing": "alpha\rbeta",
            "mixed_trailing": "alpha\r\nbeta\ngamma\r",
            "mixed_no_trailing": "alpha\r\nbeta\ngamma",
        }
        with tempfile.TemporaryDirectory() as directory:
            connection = ProjectFolderConnection(directory)
            connection.ensure_root()

            for name, value in cases.items():
                with self.subTest(name=name):
                    relative_path = f"data/{name}.txt"
                    connection.write_text(relative_path, value)

                    self.assertEqual(connection.read_text(relative_path), value)
                    self.assertEqual(
                        connection.read_bytes(relative_path),
                        value.encode("utf-8"),
                    )

    def test_typed_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = ProjectFolderConnection(directory)
            connection.ensure_root()
            connection.write_int_list("data/card_id.bin", [1, 2, 3])
            connection.write_string_list("data/card_nameeng.bin", ["Alpha", "Beta"])
            connection.write_table(
                "data/card_prop.bin",
                pd.DataFrame(
                    {
                        "attack": [800],
                        "defense": [1300],
                        "monster_type_code": [0x0F],
                        "monster_type": ["warrior"],
                        "card_category_code": [0x04],
                        "card_category": ["effect"],
                        "attribute_code": [0x06],
                        "attribute": ["water"],
                        "level": [3],
                        "requires_two_tributes": [False],
                    }
                ),
            )
            connection.write_manifest(
                ProjectManifest("Demo", directory, version_prefix="mai")
            )

            self.assertEqual(connection.read_int_list("data/card_id.bin"), [1, 2, 3])
            self.assertEqual(
                connection.read_string_list("data/card_nameeng.bin"), ["Alpha", "Beta"]
            )
            self.assertEqual(connection.read_manifest().name, "Demo")
            properties = connection.read_table("data/card_prop.bin")
            self.assertEqual(properties.iloc[0]["attack"], "800")
            self.assertEqual(properties.iloc[0]["card_category"], "effect")
            self.assertEqual(properties.iloc[0]["monster_type_code"], "15")
