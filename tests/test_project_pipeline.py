import inspect
import multiprocessing
import queue
import tempfile
import unittest
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from PIL import Image

from tests.pipeline_support import encode_description_resources
from yugioh_editor.common import subfile_rules_config
from yugioh_editor.common.errors import ProjectValidationError, RulePipelineError
from yugioh_editor.models.entities import (
    ContainerArchive,
    ContainerEntry,
    DeckFile,
    ExecutableManifest,
    ProjectFileRecord,
    ProjectManifest,
    ProjectResource,
)
from yugioh_editor.repositories.game.connection import GameFolderConnection
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.project.repository import ProjectRepository
from yugioh_editor.services.card_service import CardService
from yugioh_editor.services.project_service import ProjectService


def _pack_project_process(project_root: str, result_queue) -> None:
    try:
        service = ProjectService()
        manifest = service.load_project(project_root)
        output = service.pack_project(manifest)
        result_queue.put(("ok", str(output)))
    except Exception as error:
        result_queue.put(("error", f"{type(error).__name__}: {error}"))


class ProjectPipelineTests(unittest.TestCase):
    @staticmethod
    def _synthetic_executable_rule_config() -> tuple[bytes, dict, tuple[dict, ...]]:
        profile = deepcopy(subfile_rules_config.EXECUTABLE_CARD_CAPACITY_PROFILE)
        sites = (
            *profile["integer_patch_sites"],
            *profile["conditional_patch_sites"],
        )
        source_size = max(site["offset"] + len(site["expected"]) for site in sites)
        source = bytearray(b"\xcc" * (source_size + 16))
        for site in sites:
            offset = site["offset"]
            expected = site["expected"]
            source[offset : offset + len(expected)] = expected
        for site in profile["conditional_patch_sites"]:
            source[site["offset"] - 2 : site["offset"]] = b"\xf3\xa5"
        source_bytes = bytes(source)
        profile["source_sha256"] = sha256(source_bytes).hexdigest()
        profile["known_output_sha256"] = {}

        configs = deepcopy(subfile_rules_config.SUBFILE_RULE_CONFIGS)
        executable_rule = next(
            config for config in configs if config["pattern"] == "*_pc.exe"
        )
        patch_step = next(
            step
            for step in executable_rule["pre_encode"]
            if step["method_name"] == "patch_executable_card_capacity"
        )
        patch_step["params"]["profile"] = profile
        return source_bytes, profile, configs

    @staticmethod
    def _write_required_files(game):
        game.write_container(
            "data.dat",
            ContainerArchive(
                source_name="data.dat",
                entries=[
                    ContainerEntry(
                        "bin#/card_id.bin",
                        data=GameRepository.encode_binary_resource(
                            "card_id.bin",
                            pd.DataFrame({"value": [-1]}),
                        ),
                        order=0,
                    ),
                    ContainerEntry(
                        "misc/raw.yga",
                        data=b"RAW",
                        compressed=True,
                        order=1,
                    ),
                ],
            ),
            "preserve",
        )
        game.write_container(
            "Voice.dat",
            ContainerArchive("Voice.dat", entries=[]),
            "never",
        )
        game.write_deck("deck.ydc", DeckFile(card_ids=[10, 20, 20]))
        game.write_binary("Region.dat", b"REGION")

    def test_create_and_pack_renames_executable_with_required_prefix(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game_path = root_path / "game"
            game = GameFolderConnection(game_path)
            self._write_required_files(game)
            game.write_executable("joey_pc.exe", b"MZ-demo")

            service = ProjectService()
            manifest = service.create_project(
                "Demo",
                root_path / "workspace",
                game_path,
                "mai",
            )

            self.assertEqual(manifest.version_prefix, "mai")
            self.assertEqual(manifest.executable.source_name, "joey_pc.exe")
            self.assertEqual(
                manifest.executable.relative_path,
                "mai/mai_pc.exe",
            )
            workspace_executable = manifest.root / "mai" / "mai_pc.exe"
            self.assertEqual(workspace_executable.read_bytes(), b"MZ-demo")

            reloaded = service.load_project(manifest.root)
            self.assertEqual(
                reloaded.executable.relative_path,
                "mai/mai_pc.exe",
            )
            with patch.object(
                GameFolderConnection,
                "update_executable_icon",
                autospec=True,
            ) as update_icon:
                output = service.pack_project(reloaded)
            update_icon.assert_not_called()
            self.assertEqual((output / "mai_pc.exe").read_bytes(), b"MZ-demo")

    def test_project_icon_is_copied_and_only_updates_pack_staging(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game_path = root_path / "game"
            game = GameFolderConnection(game_path)
            self._write_required_files(game)
            source_executable = b"MZ-icon-demo"
            game.write_executable("joey_pc.exe", source_executable)
            external_icon = root_path / "selected.ico"
            Image.new("RGBA", (32, 32), "red").save(external_icon, format="ICO")
            selected_icon_bytes = external_icon.read_bytes()

            service = ProjectService()
            manifest = service.create_project(
                "Icon project",
                root_path / "workspace",
                game_path,
                "mai",
                external_icon,
            )
            self.assertEqual(manifest.icon_path, "project.ico")
            project_icon = manifest.root / "project.ico"
            self.assertEqual(project_icon.read_bytes(), selected_icon_bytes)
            external_icon.unlink()

            workspace_executable = manifest.root / "mai" / "mai_pc.exe"
            observed_updates = []

            def observe_update(connection, relative_path, icon_data):
                staged_path = connection.resolve(relative_path)
                observed_updates.append(
                    (staged_path, bytes(icon_data), staged_path.read_bytes())
                )
                return staged_path

            with patch.object(
                GameFolderConnection,
                "update_executable_icon",
                autospec=True,
                side_effect=observe_update,
            ):
                output = service.pack_project(manifest)

            self.assertEqual(len(observed_updates), 1)
            staged_path, observed_icon, staged_executable = observed_updates[0]
            self.assertIn(".pack.", staged_path.parent.name)
            self.assertTrue(staged_path.parent.name.endswith(".tmp"))
            self.assertEqual(observed_icon, selected_icon_bytes)
            self.assertEqual(staged_executable, source_executable)
            self.assertEqual(workspace_executable.read_bytes(), source_executable)
            self.assertEqual(
                (game_path / "joey_pc.exe").read_bytes(),
                source_executable,
            )
            self.assertEqual((output / "mai_pc.exe").read_bytes(), source_executable)

            previous_output = {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output.rglob("*")
                if path.is_file()
            }
            with (
                patch.object(
                    GameFolderConnection,
                    "update_executable_icon",
                    autospec=True,
                    side_effect=OSError("controlled icon update failure"),
                ),
                self.assertRaisesRegex(OSError, "icon update failure"),
            ):
                service.pack_project(manifest)
            self.assertEqual(
                {
                    path.relative_to(output).as_posix(): path.read_bytes()
                    for path in output.rglob("*")
                    if path.is_file()
                },
                previous_output,
            )
            self.assertEqual(
                list(manifest.root.parent.glob(f".{manifest.root.name}.pack.*.tmp")),
                [],
            )

            legacy_project_icon = manifest.root / "project.icon"
            project_icon.rename(legacy_project_icon)
            manifest.icon_path = "project.icon"
            ProjectRepository(manifest).save(manifest)
            observed_updates.clear()
            with patch.object(
                GameFolderConnection,
                "update_executable_icon",
                autospec=True,
                side_effect=observe_update,
            ):
                service.pack_project(manifest)
            self.assertEqual(len(observed_updates), 1)
            self.assertEqual(observed_updates[0][1], selected_icon_bytes)

            legacy_project_icon.unlink()
            with self.assertRaisesRegex(
                FileNotFoundError,
                "(?i)configured project icon",
            ):
                service.pack_project(manifest)
            self.assertEqual(
                {
                    path.relative_to(output).as_posix(): path.read_bytes()
                    for path in output.rglob("*")
                    if path.is_file()
                },
                previous_output,
            )

    def test_create_project_rejects_invalid_icon_without_partial_project(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game_path = root_path / "game"
            game = GameFolderConnection(game_path)
            self._write_required_files(game)
            game.write_executable("joey_pc.exe", b"MZ-icon-demo")
            invalid_icon = root_path / "invalid.ico"
            invalid_icon.write_bytes(b"not-an-icon")
            workspace = root_path / "workspace"

            with self.assertRaisesRegex(ValueError, "ICO"):
                ProjectService().create_project(
                    "Invalid icon project",
                    workspace,
                    game_path,
                    "mai",
                    invalid_icon,
                )

            self.assertFalse((workspace / "Invalid icon project").exists())
            self.assertEqual(list(workspace.glob(".*.create.*.tmp")), [])

    def test_pack_patches_executable_from_dynamic_card_id_count_only(self):
        source, profile, configs = self._synthetic_executable_rule_config()
        with (
            tempfile.TemporaryDirectory() as root,
            patch(
                "yugioh_editor.repositories.game.repository.SUBFILE_RULE_CONFIGS",
                configs,
            ),
        ):
            root_path = Path(root)
            game_path = root_path / "game"
            game = GameFolderConnection(game_path)
            self._write_required_files(game)
            game.write_executable("joey_pc.exe", source)

            service = ProjectService()
            manifest = service.create_project(
                "Dynamic executable",
                root_path / "workspace",
                game_path,
                "mai",
            )
            workspace_executable = manifest.root / "mai" / "mai_pc.exe"
            self.assertEqual(workspace_executable.read_bytes(), source)

            project = ProjectRepository(manifest)
            project.save_table(
                "card_ids",
                pd.DataFrame({"value": [-1, *range(1115)]}),
            )
            observed_counts: list[int] = []
            original_patch = GameRepository.patch_executable_card_capacity

            def observe_patch(value, *, context, profile):
                observed_counts.append(context.metadata["card_record_count"])
                return original_patch(value, context=context, profile=profile)

            with patch.object(
                GameRepository,
                "patch_executable_card_capacity",
                new=staticmethod(observe_patch),
            ):
                first_output = service.pack_project(manifest)
                first_packed = (first_output / "mai_pc.exe").read_bytes()
                second_output = service.pack_project(manifest)
                second_packed = (second_output / "mai_pc.exe").read_bytes()

            self.assertEqual(observed_counts, [1116, 1116])
            self.assertNotEqual(first_packed, source)
            self.assertEqual(
                sum(before != after for before, after in zip(source, first_packed)),
                20,
            )
            self.assertEqual(second_packed, first_packed)
            self.assertEqual(workspace_executable.read_bytes(), source)
            self.assertEqual((game_path / "joey_pc.exe").read_bytes(), source)

            derived = GameRepository._calculate_executable_card_capacity_values(
                1116,
                profile,
            )
            for site in profile["integer_patch_sites"]:
                expected = bytearray(site["expected"])
                start = site["value_offset"]
                width = site["value_width"]
                expected[start : start + width] = int(
                    derived[site["value_name"]]
                ).to_bytes(width, "little")
                offset = site["offset"]
                self.assertEqual(
                    first_packed[offset : offset + len(expected)],
                    bytes(expected),
                    site["description"],
                )
            for site in profile["conditional_patch_sites"]:
                offset = site["offset"]
                self.assertEqual(first_packed[offset - 2 : offset], b"\xf3\xa5")
                self.assertEqual(
                    first_packed[offset : offset + len(site["expected"])],
                    site["even_record_bytes"],
                    site["description"],
                )

    def test_create_project_uses_exact_required_prefix(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game = GameFolderConnection(root_path / "game")
            self._write_required_files(game)
            game.write_executable("joey_pc.exe", b"MZ-demo")

            manifest = ProjectService().create_project(
                "Demo",
                root_path / "workspace",
                root_path / "game",
                "eng",
            )

            self.assertEqual(manifest.version_prefix, "eng")
            self.assertEqual(
                manifest.executable.relative_path,
                "eng/eng_pc.exe",
            )

    def test_create_project_version_prefix_has_no_default(self):
        parameter = inspect.signature(ProjectService.create_project).parameters[
            "version_prefix"
        ]
        self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_first_matching_executable_is_selected_deterministically(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game = GameFolderConnection(root_path / "game")
            self._write_required_files(game)
            for name in (
                "zeta_pc.exe",
                "alpha_pc.exe",
                "beta_pc.exe",
                "launcher.exe",
            ):
                game.write_executable(name, name.encode("ascii"))

            manifest = ProjectService().create_project(
                "Demo",
                root_path / "workspace",
                root_path / "game",
                "mai",
            )

            self.assertEqual(
                manifest.executable.source_name,
                "alpha_pc.exe",
            )
            self.assertEqual(
                (manifest.root / "mai" / "mai_pc.exe").read_bytes(),
                b"alpha_pc.exe",
            )

    def test_create_project_without_matching_executable(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game = GameFolderConnection(root_path / "game")
            self._write_required_files(game)
            game.write_executable("launcher.exe", b"MZ-launcher")

            service = ProjectService()
            manifest = service.create_project(
                "Demo",
                root_path / "workspace",
                root_path / "game",
                "mai",
            )

            self.assertIsNone(manifest.executable)
            output = service.pack_project(manifest)
            self.assertEqual(list(output.glob("*.exe")), [])

    def test_load_edit_and_pack_replaces_previous_output(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game_path = root_path / "game"
            game = GameFolderConnection(game_path)
            self._write_required_files(game)
            service = ProjectService()
            manifest = service.create_project(
                "Demo",
                root_path / "workspace",
                game_path,
                "mai",
            )
            first_output = service.pack_project(manifest)
            region_name = manifest.game_files["region.dat"]
            self.assertEqual((first_output / region_name).read_bytes(), b"REGION")

            reloaded = service.load_project(manifest.root)
            region_record = next(
                item
                for item in reloaded.files
                if item.source_file.casefold() == region_name.casefold()
            )
            service.write_project_binary(
                reloaded,
                region_record,
                b"REGION-UPDATED",
            )
            second_output = service.pack_project(reloaded)
            self.assertEqual(
                (second_output / region_name).read_bytes(),
                b"REGION-UPDATED",
            )

    def test_invalid_prefixes_are_rejected_before_project_creation(self):
        invalid_values = (
            "",
            "   ",
            "../mai",
            "mai/test",
            "mai_pc.exe",
            "mai:test",
        )
        for value in invalid_values:
            with self.subTest(prefix=value), tempfile.TemporaryDirectory() as root:
                root_path = Path(root)
                workspace = root_path / "workspace"
                with self.assertRaises(ProjectValidationError):
                    ProjectService().create_project(
                        "Demo",
                        workspace,
                        root_path / "game",
                        value,
                    )
                self.assertFalse((workspace / "Demo").exists())

    def test_empty_prefixes_have_the_required_validation_message(self):
        for value in ("", "   "):
            with self.subTest(prefix=value), tempfile.TemporaryDirectory() as root:
                with self.assertRaises(ProjectValidationError) as raised:
                    ProjectService().create_project(
                        "Demo",
                        Path(root) / "workspace",
                        Path(root) / "game",
                        value,
                    )
                self.assertEqual(
                    str(raised.exception),
                    "Version prefix is required.",
                )

    def test_create_failure_removes_staging_directory(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game_path = root_path / "game"
            game = GameFolderConnection(game_path)
            self._write_required_files(game)
            game.write_binary("data.dat", b"invalid-container")
            workspace = root_path / "workspace"
            with self.assertRaises(Exception):
                ProjectService().create_project(
                    "Demo",
                    workspace,
                    game_path,
                    "mai",
                )
            self.assertFalse((workspace / "Demo").exists())
            self.assertEqual(
                list(workspace.glob(".Demo.create.*.tmp")),
                [],
            )

    def test_pack_failure_preserves_previous_output_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game_path = root_path / "game"
            game = GameFolderConnection(game_path)
            self._write_required_files(game)
            game.write_executable("joey_pc.exe", b"MZ-rollback")
            card_ids = GameRepository.encode_binary_resource(
                "card_id.bin",
                pd.DataFrame({"value": [-1, 0, 1, 2068]}),
            )
            game.write_container(
                "data.dat",
                ContainerArchive(
                    "data.dat",
                    entries=[
                        ContainerEntry(
                            "bin#/card_id.bin",
                            data=card_ids,
                            order=0,
                        ),
                        ContainerEntry(
                            "bin#/card_intid.bin",
                            data=b"\x00" * 4096,
                            order=1,
                        ),
                    ],
                ),
                "never",
            )
            service = ProjectService()
            manifest = service.create_project(
                "Demo",
                root_path / "workspace",
                game_path,
                "mai",
            )
            output = service.pack_project(manifest)
            packed_before = GameFolderConnection(output).read_container("data.dat")
            reverse_before = next(
                entry.data
                for entry in packed_before.entries
                if entry.relative_path.casefold().endswith("card_intid.bin")
            )
            self.assertEqual(len(reverse_before), 8192)
            project_before = {
                path.relative_to(manifest.root).as_posix(): path.read_bytes()
                for path in manifest.root.rglob("*")
                if path.is_file()
            }

            def fail_executable_encode(
                value,
                *,
                context,
                profile,
            ):
                del value, context, profile
                raise OSError("controlled executable encode failure")

            failures = (
                (
                    "encode",
                    patch.object(
                        GameRepository,
                        "encode_archive",
                        side_effect=OSError("controlled encode failure"),
                    ),
                    OSError,
                ),
                (
                    "container write",
                    patch.object(
                        GameRepository,
                        "write_container",
                        side_effect=OSError("controlled container write failure"),
                    ),
                    OSError,
                ),
                (
                    "executable encode",
                    patch.object(
                        GameRepository,
                        "patch_executable_card_capacity",
                        new=staticmethod(fail_executable_encode),
                    ),
                    RulePipelineError,
                ),
            )
            for label, failure, expected_error in failures:
                with (
                    self.subTest(failure=label),
                    failure,
                    self.assertLogs(level="ERROR"),
                    self.assertRaisesRegex(expected_error, "controlled"),
                ):
                    service.pack_project(manifest)

                project_after = {
                    path.relative_to(manifest.root).as_posix(): path.read_bytes()
                    for path in manifest.root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(project_after, project_before)
                packed_after = GameFolderConnection(output).read_container("data.dat")
                reverse_after = next(
                    entry.data
                    for entry in packed_after.entries
                    if entry.relative_path.casefold().endswith("card_intid.bin")
                )
                self.assertEqual(reverse_after, reverse_before)
                self.assertEqual(
                    (game_path / "joey_pc.exe").read_bytes(),
                    b"MZ-rollback",
                )
                self.assertEqual(
                    list(manifest.root.parent.glob(".Demo.pack.*.tmp")),
                    [],
                )

            project = ProjectRepository(manifest)
            staging = project.begin_update()
            try:
                staging.save_table(
                    "card_ids",
                    pd.DataFrame({"value": [-1, 0, 1, 2068, 2389]}),
                )
                with (
                    patch.object(
                        staging._connection,
                        "write_manifest",
                        side_effect=OSError("controlled manifest write failure"),
                    ),
                    self.assertRaisesRegex(OSError, "controlled manifest"),
                ):
                    staging.save()
            finally:
                staging.discard()

            project_after_manifest_failure = {
                path.relative_to(manifest.root).as_posix(): path.read_bytes()
                for path in manifest.root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(project_after_manifest_failure, project_before)
            self.assertEqual(
                list(manifest.root.parent.glob(".Demo.cards.*.tmp")),
                [],
            )

    def test_saved_card_ids_reload_and_pack_complete_reverse_lookup(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game_path = root_path / "game"
            game = GameFolderConnection(game_path)
            self._write_required_files(game)
            initial_ids = pd.DataFrame({"value": [-1, *range(9)]})
            encoded_ids = GameRepository.encode_binary_resource(
                "card_id.bin",
                initial_ids,
            )
            game.write_container(
                "data.dat",
                ContainerArchive(
                    "data.dat",
                    entries=[
                        ContainerEntry(
                            "bin#/card_id.bin",
                            data=encoded_ids,
                            order=0,
                        ),
                        ContainerEntry(
                            "bin#/card_intid.bin",
                            data=b"\x00" * 4096,
                            order=1,
                        ),
                    ],
                ),
                "never",
            )
            service = ProjectService()
            manifest = service.create_project(
                "Reverse",
                root_path / "workspace",
                game_path,
                "mai",
            )

            saved_values = [-1, 0, 1, 100, 2000, 2040, 2047, 2048, 2068, 2389]
            project = ProjectRepository(manifest)
            project.save_table(
                "card_ids",
                pd.DataFrame({"value": saved_values}),
            )
            project.save(manifest)
            reloaded = service.load_project(manifest.root)
            reloaded_project = ProjectRepository(reloaded)
            self.assertEqual(
                reloaded_project.get_table("card_ids")["value"].astype(int).tolist(),
                saved_values,
            )

            first_output = service.pack_project(reloaded)
            second_output = service.pack_project(reloaded)

            self.assertEqual(first_output, second_output)
            packed = GameFolderConnection(second_output).read_container("data.dat")
            reverse_payload = next(
                entry.data
                for entry in packed.entries
                if entry.relative_path.casefold().endswith("card_intid.bin")
            )
            reverse = GameRepository.decode_binary_resource(
                "card_intid.bin",
                reverse_payload,
            )["value"].astype(int)
            reverse_rule = GameRepository.from_root(game_path).find_rule(
                "card_intid.bin"
            )
            record_size = int(reverse_rule.encode_params["byte_width"])
            expected_record_count = 1 << max(saved_values).bit_length()
            self.assertEqual(
                len(reverse_payload),
                expected_record_count * record_size,
            )
            self.assertEqual(len(reverse), expected_record_count)
            self.assertEqual(reverse[0], 1)
            self.assertEqual(reverse[1], 2)
            self.assertEqual(reverse[100], 3)
            self.assertEqual(reverse[2000], 4)
            self.assertEqual(reverse[2040], 5)
            self.assertEqual(reverse[2047], 6)
            self.assertEqual(reverse[2048], 7)
            self.assertEqual(reverse[2068], 8)
            self.assertEqual(reverse[2389], 9)
            expected = [0] * expected_record_count
            for card_index, card_id in enumerate(saved_values):
                if card_id >= 0:
                    expected[card_id] = card_index
            self.assertEqual(reverse.tolist(), expected)

    def test_production_pack_flow_has_a_process_timeout_guard(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game_path = root_path / "game"
            game = GameFolderConnection(game_path)
            self._write_required_files(game)
            service = ProjectService()
            manifest = service.create_project(
                "Finite Pack",
                root_path / "workspace",
                game_path,
                "mai",
            )
            context = multiprocessing.get_context("spawn")
            result_queue = context.Queue()
            process = context.Process(
                target=_pack_project_process,
                args=(str(manifest.root), result_queue),
            )
            process.start()
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(5)
                self.fail("Pack did not complete within the test timeout.")
            self.assertEqual(process.exitcode, 0)
            try:
                status, details = result_queue.get(timeout=1)
            except queue.Empty:
                self.fail("Pack process exited without returning a result.")
            finally:
                result_queue.close()
                result_queue.join_thread()
            self.assertEqual(status, "ok", details)
            self.assertEqual(Path(details), manifest.root / "bin")

    def test_project_resource_service_methods_and_run(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            project_root = root_path / "project"
            records = [
                ProjectFileRecord(
                    "Data.dat",
                    "docs/a.txt",
                    "data/docs/a.txt",
                    "text",
                    "text",
                ),
                ProjectFileRecord(
                    "Data.dat",
                    "table/a.bin",
                    "data/table/a.bin",
                    "table",
                    "table",
                    order=1,
                ),
                ProjectFileRecord(
                    "Data.dat",
                    "raw/a.bin",
                    "data/raw/a.bin",
                    "binary",
                    "binary",
                    order=2,
                ),
                ProjectFileRecord(
                    "Data.dat",
                    "image/a.bmp",
                    "data/image/a.bmp",
                    "image",
                    "binary",
                    order=3,
                ),
                ProjectFileRecord(
                    "game_pc.exe",
                    "mai/mai_pc.exe",
                    "mai/mai_pc.exe",
                    "exe",
                    "binary",
                    order=0,
                ),
            ]
            manifest = ProjectManifest(
                "Resources",
                str(project_root),
                version_prefix="mai",
                files=records,
                executable=ExecutableManifest(
                    "game_pc.exe",
                    "mai/mai_pc.exe",
                ),
                game_files={"data.dat": "Data.dat"},
            )
            repository = ProjectRepository(manifest)
            repository.ensure_root()
            repository.import_resources(
                [
                    ProjectResource(records[0], "hello"),
                    ProjectResource(
                        records[1],
                        pd.DataFrame({"value": [1]}),
                    ),
                    ProjectResource(records[2], b"raw"),
                    ProjectResource(records[3], b"BMraw"),
                    ProjectResource(records[4], b"MZ"),
                ]
            )
            repository.save(manifest)
            service = ProjectService()
            self.assertEqual(
                service.read_project_text(manifest, records[0]),
                "hello",
            )
            service.write_project_text(manifest, records[0], "updated")
            self.assertEqual(
                service.read_project_text(manifest, records[0]),
                "updated",
            )
            table = service.read_project_table(manifest, records[1])
            table.loc[0, "value"] = 2
            service.write_project_table(manifest, records[1], table)
            self.assertEqual(
                service.read_project_binary(manifest, records[2]),
                b"raw",
            )
            service.write_project_binary(manifest, records[2], b"changed")
            self.assertEqual(
                service.read_project_binary_preview(
                    manifest,
                    records[2],
                    3,
                ),
                (b"cha", 7),
            )

            replacement = root_path / "replacement.bin"
            replacement.write_bytes(b"replacement")
            service.replace_project_file(
                manifest,
                records[2],
                replacement,
            )
            image = root_path / "image.png"
            Image.new("RGB", (4, 4), "red").save(image, format="PNG")
            service.replace_project_image(manifest, records[3], image)
            self.assertTrue(
                service.read_project_binary(
                    manifest,
                    records[3],
                ).startswith(b"BM")
            )
            self.assertEqual(
                service.project_resource_path(manifest, records[2]).name,
                "a.bin",
            )
            self.assertEqual(
                service.load_project(project_root).name,
                "Resources",
            )

            bin_root = project_root / "bin"
            bin_root.mkdir()
            (bin_root / "mai_pc.exe").write_bytes(b"MZ")
            with patch("subprocess.Popen") as popen:
                service.run_packed_game(manifest)
                resolved_bin = bin_root.resolve()
                popen.assert_called_once_with(
                    [str(resolved_bin / "mai_pc.exe"), "-full", "-speedy"],
                    cwd=str(resolved_bin),
                )

    def test_generic_cp932_text_preserves_newlines_through_project_pipeline(self):
        fixtures = {
            "j/bust_up/draw.txt": "日本語\t一行目\r\n次の行\t終わり\r\n".encode(
                "cp932"
            ),
            "j/bust_up/draw_lf.text": "日本語\t一行目\n次の行\t終わり\n".encode(
                "cp932"
            ),
        }
        archive = ContainerArchive(
            "Data.dat",
            entries=[
                ContainerEntry(relative_path, data=data, order=order)
                for order, (relative_path, data) in enumerate(fixtures.items())
            ],
        )

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game_repository = GameRepository.from_root(root_path / "game")
            decoded = game_repository.decode_archive(archive, "data")
            manifest = ProjectManifest(
                "Text Round Trip",
                root_path / "project",
                version_prefix="mai",
                files=[resource.record for resource in decoded],
            )
            project_repository = ProjectRepository(manifest)
            project_repository.ensure_root()
            project_repository.import_resources(decoded)

            exported = project_repository.export_resources(manifest.files)
            rebuilt = game_repository.encode_archive("Data.dat", exported)

        rebuilt_by_path = {entry.relative_path: entry.data for entry in rebuilt.entries}
        self.assertEqual(rebuilt_by_path, fixtures)

    def test_add_card_images_survive_full_project_pack_with_source_casing(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            game_path = root_path / "game"
            game = GameFolderConnection(game_path)
            names = pd.DataFrame({"value": ["", "Dragon"]})
            descriptions = pd.DataFrame({"value": ["Back", "Description"]})
            description_blob, description_index = encode_description_resources(
                descriptions,
                "eng",
            )
            existing_image = root_path / "existing.bmp"
            Image.new("RGB", (32, 48), "green").save(
                existing_image,
                format="BMP",
            )
            existing_image_bytes = existing_image.read_bytes()
            entries = [
                ContainerEntry(
                    "bin#/card_id.bin",
                    data=GameRepository.encode_binary_resource(
                        "card_id.bin",
                        pd.DataFrame({"value": [-1, 2]}),
                    ),
                    order=0,
                ),
                ContainerEntry(
                    "bin#/card_intid.bin",
                    data=b"\x00" * 4096,
                    order=1,
                ),
                ContainerEntry(
                    "bin#/card_pass.bin",
                    data=GameRepository.encode_binary_resource(
                        "card_pass.bin",
                        pd.DataFrame({"value": ["00000000", "7B000000"]}),
                    ),
                    order=2,
                ),
                ContainerEntry(
                    "bin#/card_pack.bin",
                    data=GameRepository.encode_binary_resource(
                        "card_pack.bin",
                        pd.DataFrame({"value": ["disabled", "joey"]}),
                    ),
                    order=3,
                ),
                ContainerEntry(
                    "bin#/card_prop.bin",
                    data=GameRepository.encode_binary_resource(
                        "card_prop.bin",
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
                    order=4,
                ),
                ContainerEntry(
                    "bin#/card_nameeng.bin",
                    data=GameRepository.encode_binary_resource(
                        "card_nameeng.bin",
                        names,
                        "eng",
                    ),
                    order=5,
                ),
                ContainerEntry(
                    "bin#/card_sorteng.bin",
                    data=b"\x00" * 4096,
                    order=6,
                ),
                ContainerEntry(
                    "bin#/card_desceng.bin",
                    data=description_blob,
                    order=7,
                ),
                ContainerEntry(
                    "bin#/card_indxeng.bin",
                    data=description_index,
                    order=8,
                ),
                ContainerEntry(
                    "card/list_card.txt",
                    data=GameRepository.encode_binary_resource(
                        "list_card.txt",
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
                    order=9,
                ),
                ContainerEntry(
                    "card/tp4013.bmp",
                    data=existing_image_bytes,
                    order=10,
                ),
                ContainerEntry(
                    "card/zzz001.bmp",
                    data=existing_image_bytes,
                    order=11,
                ),
                ContainerEntry(
                    "mini/tp4013.bmp",
                    data=existing_image_bytes,
                    order=12,
                ),
                ContainerEntry(
                    "mini/zzz001.bmp",
                    data=existing_image_bytes,
                    order=13,
                ),
                ContainerEntry(
                    "misc/repeated.bin",
                    data=b"A" * 4096,
                    compressed=True,
                    order=14,
                ),
            ]
            game.write_container(
                "Data.dat",
                ContainerArchive("Data.dat", entries=entries),
                "preserve",
            )
            game.write_container(
                "Voice.dat",
                ContainerArchive(
                    "Voice.dat",
                    entries=[
                        ContainerEntry(
                            "sound/voice.wav",
                            data=b"ORIGINAL VOICE",
                            order=0,
                        )
                    ],
                ),
                "never",
            )
            game.write_binary("Region.dat", b"REGION")
            game.write_deck("deck.ydc", DeckFile())

            project_service = ProjectService()
            manifest = project_service.create_project(
                "Cards",
                root_path / "workspace",
                game_path,
                "mai",
            )
            png = root_path / "new.png"
            jpeg = root_path / "replacement.jpg"
            Image.new("RGB", (80, 120), "red").save(png, format="PNG")
            Image.new("RGB", (40, 60), "blue").save(jpeg, format="JPEG")
            cards = CardService()
            draft = cards.create_card_draft(manifest)
            draft.image_name = "usr000.bmp"
            draft.large_image_source = png
            draft.small_image_source = png
            cards.create_card(manifest, draft)
            image_name = cards.get_card_detail(manifest, 2).image_name
            edited = cards.get_card_detail(manifest, 2).to_draft()
            edited.large_image_source = jpeg
            cards.update_card(manifest, edited)
            edited = cards.get_card_detail(manifest, 2).to_draft()
            edited.small_image_source = jpeg
            cards.update_card(manifest, edited)

            voice_record = next(
                record
                for record in manifest.files
                if record.source_file.casefold() == "voice.dat"
            )
            project_service.write_project_binary(
                manifest,
                voice_record,
                b"EDITED VOICE",
            )

            reloaded = project_service.load_project(manifest.root)
            data_records = sorted(
                (
                    record
                    for record in reloaded.files
                    if record.source_file.casefold() == "data.dat"
                ),
                key=lambda record: record.order,
            )
            data_paths = [
                record.relative_path.replace("\\", "/") for record in data_records
            ]
            self.assertEqual(
                [record.order for record in data_records],
                list(range(len(data_records))),
            )
            self.assertEqual(
                data_paths,
                sorted(
                    data_paths,
                    key=lambda path: path.replace("/", "\\").casefold(),
                ),
            )

            export_root = root_path / "export"
            export_root.mkdir()
            unrelated = export_root / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(
                ProjectValidationError,
                "editable project root",
            ):
                project_service.export_project_files(reloaded, reloaded.root)
            exported = project_service.export_project_files(
                reloaded,
                export_root,
            )
            self.assertEqual(exported, export_root.resolve())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

            output = project_service.pack_project(reloaded)
            packed = GameFolderConnection(output).read_container("Data.dat")
            packed_paths = [
                item.relative_path.replace("\\", "/") for item in packed.entries
            ]
            self.assertEqual(
                packed_paths,
                sorted(
                    packed_paths,
                    key=lambda path: path.replace("/", "\\").casefold(),
                ),
            )
            expected_image_paths = [
                "card/tp4013.bmp",
                "card/usr000.bmp",
                "card/zzz001.bmp",
                "mini/tp4013.bmp",
                "mini/usr000.bmp",
                "mini/zzz001.bmp",
            ]
            expected_image_keys = {path.casefold() for path in expected_image_paths}
            self.assertEqual(
                [
                    path
                    for path in packed_paths
                    if path.casefold() in expected_image_keys
                ],
                expected_image_paths,
            )
            payloads = {
                item.relative_path.replace("\\", "/"): item.data
                for item in packed.entries
            }
            packed_entries = {
                item.relative_path.replace("\\", "/"): item for item in packed.entries
            }
            self.assertEqual(
                {
                    path.relative_to(export_root / "data").as_posix(): (
                        path.read_bytes()
                    )
                    for path in (export_root / "data").rglob("*")
                    if path.is_file()
                },
                payloads,
            )
            packed_voice = GameFolderConnection(output).read_container("Voice.dat")
            voice_payloads = {
                item.relative_path.replace("\\", "/"): item.data
                for item in packed_voice.entries
            }
            self.assertEqual(
                {
                    path.relative_to(export_root / "voice").as_posix(): (
                        path.read_bytes()
                    )
                    for path in (export_root / "voice").rglob("*")
                    if path.is_file()
                },
                voice_payloads,
            )
            self.assertEqual(voice_payloads, {"sound/voice.wav": b"EDITED VOICE"})
            self.assertEqual(
                (export_root / "deck" / "deck.ydc").read_bytes(),
                (output / "deck.ydc").read_bytes(),
            )
            self.assertEqual(
                (export_root / "region" / "Region.dat").read_bytes(),
                (output / "Region.dat").read_bytes(),
            )
            self.assertEqual(
                payloads["bin#/card_id.bin"],
                b"\xff\xff\x02\x00\x00\x00",
            )
            self.assertEqual(
                GameRepository.decode_binary_resource(
                    "card_id.bin",
                    payloads["bin#/card_id.bin"],
                )["value"].tolist(),
                [-1, 2, 0],
            )
            self.assertIn(f"card/{image_name}", payloads)
            self.assertIn(f"mini/{image_name}", payloads)
            self.assertIn("bin#/card_intid.bin", payloads)
            self.assertIn("bin#/card_sorteng.bin", payloads)
            self.assertIn("bin#/card_indxeng.bin", payloads)
            self.assertEqual(payloads["misc/repeated.bin"], b"A" * 4096)
            self.assertTrue(packed_entries["misc/repeated.bin"].compressed)
            self.assertLess(
                packed_entries["misc/repeated.bin"].stored_size,
                packed_entries["misc/repeated.bin"].full_size,
            )
            self.assertTrue(payloads[f"card/{image_name}"].startswith(b"BM"))
            self.assertTrue(payloads[f"mini/{image_name}"].startswith(b"BM"))
            output_names = {path.name for path in output.iterdir()}
            self.assertIn("Data.dat", output_names)
            self.assertNotIn("data.dat", output_names)
