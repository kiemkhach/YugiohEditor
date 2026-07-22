import tempfile
import unittest

import pandas as pd

from yugioh_editor.models.entities import ProjectManifest
from yugioh_editor.repositories.project.connection import ProjectFolderConnection


class ProjectConnectionTests(unittest.TestCase):
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
