import tempfile
import unittest

from yugioh_editor.models.entities import (
    ExecutableManifest,
    ProjectFileRecord,
    ProjectManifest,
)
from yugioh_editor.repositories.project.repository import ProjectRepository


def physical(relative="bin#/card_id.bin", workspace="data/bin#/card_id.bin"):
    return ProjectFileRecord(
        source_file="Data.dat",
        relative_path=relative,
        workspace_path=workspace,
        file_kind="table",
        storage_format="table",
    )


class ManifestValidationTests(unittest.TestCase):
    def test_optional_project_icon_is_backward_compatible_and_relative(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = ProjectManifest.from_dict(
                {
                    "name": "Legacy icon",
                    "root_path": directory,
                    "version_prefix": "mai",
                }
            )
            self.assertIsNone(legacy.icon_path)
            self.assertNotIn("icon_path", legacy.to_dict())

            configured = ProjectManifest(
                "Configured icon",
                directory,
                version_prefix="mai",
                icon_path="project.ico",
            )
            configured.validate()
            self.assertEqual(
                ProjectManifest.from_dict(configured.to_dict()).icon_path,
                "project.ico",
            )

            legacy_icon = ProjectManifest(
                "Legacy configured icon",
                directory,
                version_prefix="mai",
                icon_path="project.icon",
            )
            legacy_icon.validate()
            self.assertEqual(
                ProjectManifest.from_dict(legacy_icon.to_dict()).icon_path,
                "project.icon",
            )

            for invalid in (
                "",
                "../project.icon",
                "/absolute/project.icon",
                "C:/absolute/project.icon",
                "C:drive-relative.icon",
                r"\\server\share\project.icon",
            ):
                with self.subTest(icon_path=invalid):
                    manifest = ProjectManifest(
                        "Invalid icon",
                        directory,
                        version_prefix="mai",
                        icon_path=invalid,
                    )
                    with self.assertRaises(ValueError):
                        manifest.validate()

    def test_application_workspace_preference_is_not_manifest_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            serialized = ProjectManifest(
                "No application preferences",
                directory,
                version_prefix="mai",
            ).to_dict()

            self.assertNotIn("workspace", serialized)
            self.assertNotIn("workspace/last_folder", serialized)

    def test_executable_capacity_count_is_derived_and_not_manifest_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = ProjectManifest(
                "Executable metadata",
                directory,
                version_prefix="mai",
                executable=ExecutableManifest(
                    source_name="joey_pc.exe",
                    relative_path="mai/mai_pc.exe",
                ),
            )

            serialized = manifest.to_dict()

            self.assertEqual(
                serialized["executable"],
                {
                    "source_name": "joey_pc.exe",
                    "relative_path": "mai/mai_pc.exe",
                },
            )
            self.assertNotIn("card_record_count", serialized)
            self.assertNotIn("card_record_count", serialized["executable"])

    def test_legacy_codec_and_generator_metadata_is_migrated(self):
        removed_generator_key = "gene" + "rator"
        with tempfile.TemporaryDirectory() as directory:
            manifest = ProjectManifest.from_dict(
                {
                    "name": "Legacy",
                    "root_path": directory,
                    "version_prefix": "mai",
                    "files": [
                        {
                            "source_file": "Data.dat",
                            "relative_path": "bin#/card_id.bin",
                            "workspace_path": "data/bin#/card_id.bin",
                            "file_kind": "table",
                            "storage_format": "table",
                            "codec_name": "card_id",
                            removed_generator_key: "old_generator",
                        }
                    ],
                }
            )
            record = manifest.files[0]
            self.assertFalse(hasattr(record, "codec_name"))
            self.assertFalse(hasattr(record, "generator"))
            serialized = manifest.to_dict()["files"][0]
            self.assertNotIn("codec_name", serialized)
            self.assertNotIn("generator", serialized)

    def test_missing_or_empty_manifest_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in (None, "", "   ", "null"):
                raw = {
                    "name": "Missing prefix",
                    "root_path": directory,
                }
                if value == "null":
                    raw["version_prefix"] = None
                elif value is not None:
                    raw["version_prefix"] = value
                with self.subTest(prefix=value):
                    with self.assertRaises(ValueError) as raised:
                        ProjectManifest.from_dict(raw)
                    self.assertEqual(
                        str(raised.exception),
                        "Version prefix is required.",
                    )

    def test_rejects_invalid_prefix_absolute_and_traversal_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                ProjectManifest(
                    "Demo",
                    directory,
                    version_prefix="../bad",
                ),
                ProjectManifest(
                    "Demo",
                    directory,
                    version_prefix="mai",
                    files=[physical("../escape.bin", "data/escape.bin")],
                ),
                ProjectManifest(
                    "Demo",
                    directory,
                    version_prefix="mai",
                    files=[physical("C:drive-relative.bin", "data/escape.bin")],
                ),
                ProjectManifest(
                    "Demo",
                    directory,
                    version_prefix="mai",
                    files=[physical(r"\\server\share\escape.bin", "data/escape.bin")],
                ),
                ProjectManifest(
                    "Demo",
                    directory,
                    version_prefix="mai",
                    files=[physical(workspace="C:/absolute/file.bin")],
                ),
                ProjectManifest(
                    "Demo",
                    directory,
                    version_prefix="mai",
                    files=[physical(workspace="C:drive-relative.bin")],
                ),
                ProjectManifest(
                    "Demo",
                    directory,
                    version_prefix="mai",
                    executable=ExecutableManifest(
                        "game_pc.exe",
                        "../game_pc.exe",
                    ),
                ),
                ProjectManifest(
                    "Demo",
                    directory,
                    version_prefix="mai",
                    game_files={"data.dat": "../Data.dat"},
                ),
                ProjectManifest(
                    "Demo",
                    directory,
                    version_prefix="mai",
                    files=[
                        physical(),
                        ProjectFileRecord(
                            "../Voice.dat",
                            "sound.wav",
                            "voice/sound.wav",
                            "audio",
                            "binary",
                        ),
                    ],
                ),
            )
            for manifest in cases:
                with self.subTest(manifest=manifest):
                    with self.assertRaises(ValueError):
                        manifest.validate()

    def test_rejects_invalid_physical_and_virtual_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                ProjectFileRecord(
                    "Data.dat",
                    "a.bin",
                    None,
                    "binary",
                    "binary",
                ),
                ProjectFileRecord(
                    "Data.dat",
                    "a.bin",
                    "data/a.bin",
                    "virtual",
                    "virtual",
                    generated_on_pack=True,
                    virtual=True,
                ),
                ProjectFileRecord(
                    "Data.dat",
                    "a.bin",
                    None,
                    "virtual",
                    "virtual",
                    virtual=True,
                ),
            )
            for record in cases:
                manifest = ProjectManifest(
                    "Demo",
                    directory,
                    version_prefix="mai",
                    files=[record],
                )
                with self.subTest(record=record):
                    with self.assertRaises(ValueError):
                        manifest.validate()

    def test_rejects_normalized_duplicates_and_empty_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate_resources = ProjectManifest(
                "Demo",
                directory,
                version_prefix="mai",
                files=[
                    physical("A/B.bin", "data/a.bin"),
                    physical("a\\b.BIN", "data/b.bin"),
                ],
            )
            duplicate_sources = ProjectManifest(
                "Demo",
                directory,
                version_prefix="mai",
                game_files={
                    "data.dat": "Data.dat",
                    "voice.dat": "data.DAT",
                },
            )
            empty_path = ProjectManifest(
                "Demo",
                directory,
                version_prefix="mai",
                files=[physical("", "data/a.bin")],
            )
            duplicate_workspaces = ProjectManifest(
                "Demo",
                directory,
                version_prefix="mai",
                files=[
                    physical("a.bin", "Data/A.bin"),
                    physical("b.bin", "data\\a.BIN"),
                ],
            )
            for manifest in (
                duplicate_resources,
                duplicate_sources,
                empty_path,
                duplicate_workspaces,
            ):
                with self.subTest(manifest=manifest):
                    with self.assertRaises(ValueError):
                        manifest.validate()

    def test_rejects_physical_resources_with_virtual_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            for record in (
                ProjectFileRecord(
                    "Data.dat",
                    "a.bin",
                    "data/a.bin",
                    "binary",
                    "virtual",
                ),
                ProjectFileRecord(
                    "Data.dat",
                    "a.bin",
                    "data/a.bin",
                    "virtual",
                    "binary",
                ),
            ):
                with self.subTest(record=record):
                    with self.assertRaisesRegex(
                        ValueError,
                        "Physical resources cannot use virtual metadata",
                    ):
                        ProjectManifest(
                            "Demo",
                            directory,
                            version_prefix="mai",
                            files=[record],
                        ).validate()

    def test_repository_rejects_virtual_rule_mismatch_in_both_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                ProjectFileRecord(
                    "Data.dat",
                    "bin#/card_id.bin",
                    None,
                    "virtual",
                    "virtual",
                    generated_on_pack=True,
                    virtual=True,
                ),
                ProjectFileRecord(
                    "Data.dat",
                    "bin#/card_indxeng.bin",
                    "data/bin#/card_indxeng.bin",
                    "table",
                    "table",
                    language="eng",
                ),
            )
            expected = (
                "manifest=True, rule=False",
                "manifest=False, rule=True",
            )
            for record, message in zip(cases, expected, strict=True):
                with self.subTest(record=record):
                    manifest = ProjectManifest(
                        "Mismatch",
                        directory,
                        version_prefix="mai",
                        files=[record],
                        game_files={"data.dat": "Data.dat"},
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        ProjectRepository(manifest).save()

    def test_preserves_source_file_casing(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = ProjectManifest(
                "Demo",
                directory,
                version_prefix="mai",
                files=[physical()],
                game_files={"data.dat": "Data.dat"},
            )
            manifest.validate()
            self.assertEqual(manifest.game_files["data.dat"], "Data.dat")
            self.assertEqual(manifest.files[0].source_file, "Data.dat")

    def test_rejects_negative_duplicate_and_noncontiguous_orders_per_source(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                (
                    [
                        ProjectFileRecord(
                            "Data.dat",
                            "only.bin",
                            "data/only.bin",
                            "binary",
                            "binary",
                            order=5,
                        ),
                    ],
                    "must be contiguous",
                ),
                (
                    [
                        physical(),
                        ProjectFileRecord(
                            "Data.dat",
                            "b.bin",
                            "data/b.bin",
                            "binary",
                            "binary",
                            order=-1,
                        ),
                    ],
                    "must not be negative",
                ),
                (
                    [
                        physical(),
                        ProjectFileRecord(
                            "Data.dat",
                            "b.bin",
                            "data/b.bin",
                            "binary",
                            "binary",
                            order=0,
                        ),
                    ],
                    "Duplicate resource order 0",
                ),
                (
                    [
                        physical(),
                        ProjectFileRecord(
                            "Data.dat",
                            "b.bin",
                            "data/b.bin",
                            "binary",
                            "binary",
                            order=2,
                        ),
                    ],
                    "must be contiguous",
                ),
            )
            for records, message in cases:
                with self.subTest(message=message):
                    manifest = ProjectManifest(
                        "Invalid orders",
                        directory,
                        version_prefix="mai",
                        files=records,
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        manifest.validate()

    def test_orders_are_independent_between_source_files(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = ProjectManifest(
                "Independent orders",
                directory,
                version_prefix="mai",
                files=[
                    physical(),
                    ProjectFileRecord(
                        "Voice.dat",
                        "voice/a.wav",
                        "voice/a.wav",
                        "audio",
                        "binary",
                        order=0,
                    ),
                ],
            )
            manifest.validate()

    def test_repository_save_rejects_missing_physical_workspace_file(self):
        with tempfile.TemporaryDirectory() as directory:
            record = physical()
            manifest = ProjectManifest(
                "Missing physical file",
                directory,
                version_prefix="mai",
                files=[record],
                game_files={"data.dat": "Data.dat"},
            )
            with self.assertRaisesRegex(FileNotFoundError, record.workspace_path):
                ProjectRepository(manifest).save()
