import ast
import importlib.util
import inspect
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

from yugioh_editor.common.constants import CODEC_OPERATIONS
from yugioh_editor.common.subfile_rules_config import SUBFILE_RULE_CONFIGS
from yugioh_editor.repositories.game import subfile_rule_factory
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.game.subfile_rule_factory import (
    ALLOWED_RULE_METHODS,
    PIPELINE_FIELDS,
    VALID_CODEC_NAMES,
    SubfileRuleFactory,
)

ROOT = Path(__file__).resolve().parents[1] / "yugioh_editor"


def trees(folder):
    for path in (ROOT / folder).rglob("*.py"):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


class ArchitectureTests(unittest.TestCase):
    def test_ui_loading_module_imports_without_pyside6(self):
        test_module = ROOT.parent / "tests" / "test_ui_loading.py"
        real_find_spec = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "PySide6":
                return None
            return real_find_spec(name, package)

        with patch("importlib.util.find_spec", side_effect=find_spec):
            namespace = runpy.run_path(str(test_module))
        self.assertFalse(namespace["PYSIDE_AVAILABLE"])

    def test_production_has_no_version_prefix_default_symbol(self):
        removed_symbol = "DEFAULT_" + "VERSION_PREFIX"
        violations = [
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*.py")
            if removed_symbol in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(violations, [])

    def test_start_view_does_not_override_version_prefix_text(self):
        source = (ROOT / "views" / "start_view.py").read_text(encoding="utf-8")
        self.assertNotIn("_version_prefix.setText(", source)

    def test_views_and_services_do_not_import_connections_or_codecs(self):
        violations = []
        for folder in ("views", "services"):
            for path, tree in trees(folder):
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        names = (
                            [alias.name for alias in node.names]
                            if isinstance(node, ast.Import)
                            else [node.module or ""]
                        )
                        if any(
                            ".connection" in name or ".codecs" in name for name in names
                        ):
                            violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_services_do_not_access_storage_dependencies(self):
        forbidden_attributes = {
            "connection",
            "_connection",
            "codec",
            "_codec",
            "codecs",
            "_codecs",
        }
        forbidden_calls = {
            "read_bytes",
            "write_bytes",
            "read_dataframe",
            "write_dataframe",
            "read_integer_list",
            "read_offset_string_table",
        }
        violations = []
        for path, tree in trees("services"):
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    if node.attr in forbidden_attributes:
                        violations.append(f"{path.name}:{node.lineno}")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in forbidden_calls
                ):
                    violations.append(f"{path.name}:{node.lineno}")
                if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                    for argument in node.args.args[1:]:
                        annotation = ast.unparse(argument.annotation or "")
                        if "Connection" in annotation or "Codec" in annotation:
                            violations.append(f"{path.name}:{argument.lineno}")
        self.assertEqual(violations, [])

    def test_repositories_do_not_import_or_construct_codecs(self):
        violations = []
        for path, tree in trees("repositories"):
            if path.name != "repository.py":
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and ".codecs" in (
                    node.module or ""
                ):
                    violations.append(f"{path.name}:{node.lineno}")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id.endswith("Codec")
                ):
                    violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_codecs_do_not_access_filesystem_or_outer_layers(self):
        violations = []
        codec_root = ROOT / "repositories" / "game" / "codecs"
        for path in codec_root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                    )
                    if any(
                        name in {"os", "pathlib"}
                        or ".services" in name
                        or ".views" in name
                        or name.endswith(".repository")
                        for name in names
                    ):
                        violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_generic_codecs_do_not_receive_file_selection_metadata(self):
        codec_root = ROOT / "repositories" / "game" / "codecs"
        container_source = (codec_root / "container.py").read_text(encoding="utf-8")
        text_source = (codec_root / "text.py").read_text(encoding="utf-8")
        card_source = (codec_root / "card.py").read_text(encoding="utf-8")
        deck_source = (codec_root / "deck.py").read_text(encoding="utf-8")
        self.assertNotIn("source_name", container_source)
        self.assertNotIn("LANGUAGE_", text_source)
        self.assertNotIn("language", text_source.casefold())
        self.assertNotIn("list_card", card_source)
        self.assertNotIn("deck.ydc", deck_source.casefold())

    def test_project_repository_exposes_canonical_table_api(self):
        from yugioh_editor.repositories.project.repository import (
            ProjectRepository,
        )

        for name in (
            "list_tables",
            "has_table",
            "get_table",
            "save_table",
            "plan_existing_card_update",
            "apply_existing_card_update",
        ):
            self.assertTrue(callable(getattr(ProjectRepository, name, None)))

    def test_card_service_uses_repository_card_persistence_contracts(self):
        source = (ROOT / "services" / "card_service.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        table_calls = [
            (
                node.func.attr,
                node.args[0].value,
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get_table", "save_table"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ]
        self.assertIn(("get_table", "cards"), table_calls)
        self.assertIn(("save_table", "cards"), table_calls)
        repository_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("plan_existing_card_update", repository_calls)
        self.assertIn("apply_existing_card_update", repository_calls)
        string_literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertNotIn("card_names", string_literals)
        self.assertNotIn("card_descriptions", string_literals)

    def test_repository_builds_rules_from_the_common_config(self):
        source = (ROOT / "repositories" / "game" / "repository.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        self.assertIn("SUBFILE_RULE_CONFIGS", source)
        self.assertIn("build_rules(", source)
        self.assertNotIn("SUBFILE_RULES =", source)
        constructions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "SubfileRule"
        ]
        self.assertEqual(constructions, [])

    def test_repository_does_not_call_codec_methods_directly(self):
        source = (ROOT / "repositories" / "game" / "repository.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"decode", "encode"}
        ]
        self.assertEqual(calls, [])

    def test_generic_codec_whitelist_is_the_only_codec_name_policy(self):
        removed_name = "SEMANTIC" + "_CODEC_NAMES"
        self.assertFalse(hasattr(subfile_rule_factory, removed_name))
        rules = SubfileRuleFactory().build_rules(SUBFILE_RULE_CONFIGS)
        self.assertTrue(VALID_CODEC_NAMES)
        self.assertEqual(VALID_CODEC_NAMES, CODEC_OPERATIONS)
        self.assertTrue(all(rule.codec_name in VALID_CODEC_NAMES for rule in rules))

    def test_executable_capacity_patch_uses_the_existing_binary_rule_pipeline(self):
        executable_rule = next(
            config for config in SUBFILE_RULE_CONFIGS if config["pattern"] == "*_pc.exe"
        )
        self.assertEqual(executable_rule["codec_name"], "binary")
        self.assertFalse(executable_rule["virtual"])
        self.assertEqual(
            [step["method_name"] for step in executable_rule["pre_encode"]],
            ["patch_executable_card_capacity"],
        )
        self.assertIn("patch_executable_card_capacity", ALLOWED_RULE_METHODS)
        self.assertNotIn("executable", CODEC_OPERATIONS)
        self.assertFalse(
            (ROOT / "repositories" / "game" / "codecs" / "executable.py").exists()
        )

        production_source = "\n".join(
            path.read_text(encoding="utf-8") for path in ROOT.rglob("*.py")
        )
        self.assertNotIn("Executable" + "Patcher", production_source)
        self.assertNotIn("Executable" + "Codec", production_source)

        service_source = (ROOT / "services" / "project_service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('get_table("card_ids")', service_source)
        self.assertIn("card_record_count", service_source)

    def test_removed_helpers_and_duplicate_registries_stay_absent(self):
        game_source = (ROOT / "repositories" / "game" / "repository.py").read_text(
            encoding="utf-8"
        )
        project_source = (
            ROOT / "repositories" / "project" / "repository.py"
        ).read_text(encoding="utf-8")
        for name in (
            "decode_description_table",
            "generate_description_files",
            "generate_sort_sidecar",
            "load_dependency_tables",
            "_TABLE_CODEC_NAMES",
        ):
            self.assertNotIn(name, game_source)
        for name in ("TABLE_NAMES", "_table_readers", "_table_writers"):
            self.assertNotIn(name, project_source)

    def test_removed_card_editing_symbols_stay_absent(self):
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in ROOT.rglob("*.py")
        )
        removed_symbols = (
            "SUPPORTED_" + "CARD_LANGUAGES",
            "CARD_LIST_" + "DERIVED_COLUMNS",
            "CardImage" + "Draft",
            "Card" + "Property",
            "PACK_" + "IDS",
            "TEXT_" + "EXTENSIONS",
            "language_" + "from_name",
            "apply_card_" + "changes",
        )
        for symbol in removed_symbols:
            self.assertNotIn(symbol, source)
        self.assertFalse((ROOT / "common" / "card_reference_data_errors.py").exists())

    def test_configured_pipeline_methods_are_whitelisted_static_methods(self):
        configured = set()
        for config in SUBFILE_RULE_CONFIGS:
            self.assertIsInstance(config, dict)
            for field in PIPELINE_FIELDS:
                pipeline = config.get(field, ())
                self.assertIsInstance(pipeline, (list, tuple))
                for step in pipeline:
                    configured.add(step["method_name"])
        self.assertTrue(configured)
        self.assertTrue(configured.issubset(ALLOWED_RULE_METHODS))
        for method_name in ALLOWED_RULE_METHODS:
            self.assertIsInstance(
                inspect.getattr_static(GameRepository, method_name),
                staticmethod,
            )

    def test_rule_decode_and_encode_are_pipeline_orchestrators(self):
        source = (ROOT / "repositories" / "game" / "repository.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"_decode_rule_value", "_encode_rule_value"}
        }
        self.assertEqual(set(methods), {"_decode_rule_value", "_encode_rule_value"})
        for method in methods.values():
            codec_dispatches = [
                node
                for node in ast.walk(method)
                if isinstance(node, ast.Attribute) and node.attr == "codec_name"
            ]
            self.assertEqual(len(codec_dispatches), 1)
            source_segment = ast.get_source_segment(source, method) or ""
            self.assertIn("_run_rule_pipeline(", source_segment)
            self.assertIn("_connection.", source_segment)

    def test_config_tree_contains_no_callables(self):
        pending = [SUBFILE_RULE_CONFIGS]
        callables = []
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, (list, tuple)):
                pending.extend(value)
            elif callable(value):
                callables.append(value)
        self.assertEqual(callables, [])

    def test_connection_has_no_virtual_generation_responsibility(self):
        source = (ROOT / "repositories" / "game" / "connection.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("_gene" + "rators", source)
        self.assertNotIn("generate_" + "resource", source)
        self.assertNotIn("virtual", source.casefold())

    def test_virtual_rules_construct_values_in_pre_encode(self):
        removed_key = "gene" + "rator"
        virtual_rules = [
            config for config in SUBFILE_RULE_CONFIGS if config.get("virtual")
        ]
        self.assertTrue(virtual_rules)
        for config in SUBFILE_RULE_CONFIGS:
            self.assertNotIn(removed_key, config.get("encode_params", {}))
        for config in virtual_rules:
            pipeline = config.get("pre_encode", ())
            self.assertTrue(pipeline)
            self.assertIn(
                pipeline[0]["method_name"],
                {"load_dependency_table", "load_card_sort_records"},
            )
            self.assertTrue(
                any(
                    step["method_name"]
                    in {
                        "generate_string_offsets",
                        "generate_sort_indices",
                        "generate_reverse_lookup",
                    }
                    for step in pipeline
                )
            )

    def test_card_intid_and_card_sort_rules_are_isolated(self):
        sort_source = inspect.getsource(GameRepository.generate_sort_indices)
        for removed in ("preserve_zero", "NFKD", "casefold"):
            self.assertNotIn(removed, sort_source)

        reverse_config = next(
            config
            for config in SUBFILE_RULE_CONFIGS
            if config["pattern"] == "card_intid.bin"
        )
        sort_config = next(
            config
            for config in SUBFILE_RULE_CONFIGS
            if config["pattern"] == "card_sort[lang].bin"
        )
        self.assertTrue(reverse_config["virtual"])
        self.assertEqual(
            reverse_config["pre_encode"][0]["params"]["table"],
            "card_id.bin",
        )
        self.assertEqual(
            [step["method_name"] for step in reverse_config["pre_encode"]],
            [
                "load_dependency_table",
                "dataframe_column_to_list",
                "generate_reverse_lookup",
            ],
        )
        self.assertNotIn("capacity", str(sort_config))
        self.assertNotIn("card_intid.bin", str(sort_config))

    def test_physical_and_virtual_resources_share_encode_orchestration(self):
        source = (ROOT / "repositories" / "game" / "repository.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("_generate_" + "virtual_resource", source)
        tree = ast.parse(source)
        encode_archive = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "encode_archive"
        )
        calls = [
            node.func.attr
            for node in ast.walk(encode_archive)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertEqual(calls.count("_encode_resource"), 1)
