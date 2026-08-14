import ast
import inspect
import multiprocessing
import queue
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from yugioh_editor.common.constants import CODEC_OPERATIONS
from yugioh_editor.common.errors import RulePipelineError
from yugioh_editor.common.subfile_rules_config import (
    CARD_DESCRIPTION_TEXT_LAYOUT,
    DIALOG_TEXT_LAYOUT,
    SUBFILE_RULE_CONFIGS,
)
from yugioh_editor.models.entities import (
    ContainerEntry,
    ProjectFileRecord,
    ProjectResource,
)
from yugioh_editor.repositories.game import subfile_rule_factory as factory_module
from yugioh_editor.repositories.game.connection import GameFolderConnection
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.game.subfile_rule import (
    RuleProcessingContext,
    SubfileRule,
)
from yugioh_editor.repositories.game.subfile_rule_factory import (
    ALLOWED_RULE_METHODS,
    PIPELINE_FIELDS,
    VALID_CODEC_NAMES,
    SubfileRuleFactory,
)


def _large_reverse_lookup_process(result_queue) -> None:
    try:
        repository = GameRepository.from_root(".")
        rule = repository.find_rule("card_intid.bin")
        context = RuleProcessingContext(
            repository=repository,
            rule=rule,
            relative_path="large_reverse.bin",
            language=None,
            decode_params={},
            encode_params=dict(rule.encode_params),
            metadata={},
        )
        result = repository.generate_reverse_lookup(
            list(range(10_000)),
            context=context,
        )
        result_queue.put(("ok", len(result), result[0], result[9_999]))
    except Exception as error:
        result_queue.put(("error", f"{type(error).__name__}: {error}"))


class SubfileRuleConfigTests(unittest.TestCase):
    def test_config_contains_only_plain_dictionaries(self):
        self.assertIsInstance(SUBFILE_RULE_CONFIGS, tuple)
        self.assertTrue(SUBFILE_RULE_CONFIGS)
        self.assertTrue(
            all(isinstance(config, dict) for config in SUBFILE_RULE_CONFIGS)
        )
        self.assertTrue(
            all(not isinstance(config, SubfileRule) for config in SUBFILE_RULE_CONFIGS)
        )

    def test_config_does_not_import_runtime_layers(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "yugioh_editor"
            / "common"
            / "subfile_rules_config.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        forbidden = ("repositories", "connection", "codec", "SubfileRule")
        self.assertFalse(
            [name for name in imported if any(value in name for value in forbidden)]
        )

    def test_factory_builds_immutable_runtime_rules(self):
        rules = SubfileRuleFactory().build_rules(SUBFILE_RULE_CONFIGS)
        self.assertTrue(all(isinstance(rule, SubfileRule) for rule in rules))
        with self.assertRaises(TypeError):
            rules[0].decode_params["changed"] = True
        with self.assertRaises(TypeError):
            rules[-1].pre_decode[0].params["changed"] = True

    def test_indexed_text_profiles_are_centralized_and_copied_per_rule_location(self):
        expected_profiles = {
            "card_desc[lang].bin": CARD_DESCRIPTION_TEXT_LAYOUT,
            "dlg_text[lang].bin": DIALOG_TEXT_LAYOUT,
        }
        for text_pattern, expected in expected_profiles.items():
            with self.subTest(text_pattern=text_pattern):
                text = next(
                    config
                    for config in SUBFILE_RULE_CONFIGS
                    if config["pattern"] == text_pattern
                )
                index_pattern = text_pattern.replace("desc", "indx").replace(
                    "text", "indx"
                )
                index = next(
                    config
                    for config in SUBFILE_RULE_CONFIGS
                    if config["pattern"] == index_pattern
                )
                generator = next(
                    step
                    for step in index["pre_encode"]
                    if step["method_name"] == "generate_string_offsets"
                )
                layouts = (
                    text["decode_params"],
                    text["encode_params"],
                    generator["params"],
                )
                for layout in layouts:
                    self.assertEqual(
                        {
                            key: layout[key]
                            for key in (
                                "terminator",
                                "alignment",
                                "minimum_padding",
                            )
                        },
                        expected,
                    )
                    self.assertEqual(layout["encoding"], "language")
                    self.assertIsNot(layout, expected)
                self.assertEqual(len({id(layout) for layout in layouts}), 3)

    def test_factory_rejects_indexed_text_layout_mismatch_and_missing_fields(self):
        mismatched = deepcopy(SUBFILE_RULE_CONFIGS)
        card_index = next(
            config
            for config in mismatched
            if config["pattern"] == "card_indx[lang].bin"
        )
        generator = next(
            step
            for step in card_index["pre_encode"]
            if step["method_name"] == "generate_string_offsets"
        )
        generator["params"]["minimum_padding"] = 1
        with self.assertRaisesRegex(
            ValueError,
            r"Indexed-text layout mismatch:.*card_indx\[lang\]\.bin.*"
            r"minimum_padding.*1.*card_desc\[lang\]\.bin.*minimum_padding.*2",
        ):
            SubfileRuleFactory().build_rules(mismatched)

        missing_layout = deepcopy(SUBFILE_RULE_CONFIGS)
        card_description = next(
            config
            for config in missing_layout
            if config["pattern"] == "card_desc[lang].bin"
        )
        del card_description["encode_params"]["terminator"]
        with self.assertRaisesRegex(
            ValueError,
            r"Indexed-text layout mismatch:.*card_desc\[lang\]\.bin.*"
            r"encode_params.*terminator",
        ):
            SubfileRuleFactory().build_rules(missing_layout)

    def test_factory_deep_freezes_nested_params_and_contexts_are_independent(self):
        config = {
            "pattern": "nested.bin",
            "codec_name": "binary",
            "decode_params": {
                "nested": {
                    "items": [{"value": 1}],
                }
            },
            "post_decode": (
                {
                    "method_name": "append_bytes",
                    "params": {"nested": {"value": 2}},
                },
            ),
        }
        rule = SubfileRuleFactory().build_rule(config, index=0)
        with self.assertRaises(TypeError):
            rule.decode_params["nested"]["items"][0]["value"] = 3
        with self.assertRaises(TypeError):
            rule.post_decode[0].params["nested"]["value"] = 3

        repository = GameRepository.from_root(".")
        context_a = repository._create_rule_context(
            rule,
            relative_path="nested.bin",
            language=None,
        )
        context_b = repository._create_rule_context(
            rule,
            relative_path="nested.bin",
            language=None,
        )
        self.assertIsNot(context_a.decode_params, context_b.decode_params)
        context_a.decode_params["nested"]["items"][0]["value"] = 9
        self.assertEqual(context_b.decode_params["nested"]["items"][0]["value"], 1)
        self.assertEqual(rule.decode_params["nested"]["items"][0]["value"], 1)
        self.assertEqual(config["decode_params"]["nested"]["items"][0]["value"], 1)

    def test_pipeline_defaults_are_empty(self):
        rule = SubfileRuleFactory().build_rule(
            {
                "pattern": "plain.bin",
                "codec_name": "binary",
            },
            index=0,
        )
        for field in PIPELINE_FIELDS:
            self.assertEqual(getattr(rule, field), ())

    def test_factory_rejects_invalid_configs(self):
        removed_field_key = "gene" + "rator"
        valid = {
            "pattern": "special.bin",
            "codec_name": "integer_list",
            "decode_params": {
                "byte_width": 2,
                "signed": True,
                "byte_order": "little",
            },
        }
        invalid = (
            [],
            {},
            {**valid, "pattern": ""},
            {**valid, "codec_name": "card_id"},
            {**valid, "codec_name": "missing"},
            {**valid, "decode_params": []},
            {**valid, "encode_params": []},
            {**valid, "virtual": "yes"},
            {**valid, "operation": "integer_list"},
            {
                **valid,
                "pattern": "plain.bin",
                "decode_params": {"table": "card_name[lang].bin"},
            },
            {
                **valid,
                "virtual": True,
                "encode_params": {},
            },
            {
                **valid,
                "encode_params": {removed_field_key: "offset"},
            },
            {
                **valid,
                "virtual": True,
                "pre_encode": (
                    {
                        "method_name": "dataframe_column_to_list",
                        "params": {"column": "value"},
                    },
                ),
            },
            {
                **valid,
                "pattern": "self.bin",
                "virtual": True,
                "pre_encode": (
                    {
                        "method_name": "load_dependency_table",
                        "params": {"table": "SELF.BIN"},
                    },
                ),
            },
            {
                **valid,
                "decode_params": {"byte_width": 0},
            },
            {
                **valid,
                "decode_params": {"signed": 1},
            },
            {
                **valid,
                "decode_params": {"byte_order": "middle"},
            },
            {
                **valid,
                "post_decode": {},
            },
            {
                **valid,
                "post_decode": ("not-a-mapping",),
            },
            {
                **valid,
                "post_decode": ({"method_name": "", "params": {}},),
            },
            {
                **valid,
                "post_decode": (
                    {"method_name": "sequence_to_dataframe", "params": []},
                ),
            },
            {
                **valid,
                "post_decode": (
                    {
                        "method_name": "sequence_to_dataframe",
                        "params": {},
                        "extra": True,
                    },
                ),
            },
            {
                **valid,
                "post_decode": ({"method_name": "missing_method", "params": {}},),
            },
            {
                **valid,
                "post_decode": ({"method_name": "_normalize", "params": {}},),
            },
        )
        factory = SubfileRuleFactory()
        for index, config in enumerate(invalid):
            with self.subTest(config=config):
                with self.assertRaises((TypeError, ValueError)):
                    factory.build_rule(config, index=index)

    def test_repository_rejects_allowed_method_that_is_not_static(self):
        config = {
            "pattern": "plain.bin",
            "codec_name": "binary",
            "post_decode": (
                {
                    "method_name": "find_rule",
                    "params": {},
                },
            ),
        }
        with patch.object(
            factory_module,
            "ALLOWED_RULE_METHODS",
            ALLOWED_RULE_METHODS | {"find_rule"},
        ):
            rules = SubfileRuleFactory().build_rules((config,))
        factory = Mock()
        factory.build_rules.return_value = rules
        with self.assertRaisesRegex(TypeError, "must be an existing static"):
            GameRepository(Mock(), factory)

    def test_factory_does_not_import_game_repository(self):
        path = Path(factory_module.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        self.assertFalse(
            [
                name
                for name in imports
                if name.endswith(".repository") or "GameRepository" in name
            ]
        )

    def test_factory_validates_table_metadata_and_conflicts(self):
        base = {
            "pattern": "values.bin",
            "codec_name": "integer_list",
            "table_name": "values",
        }
        invalid = (
            {**base, "table_name": ""},
            {**base, "table_parameters": "language"},
            {**base, "table_parameters": ("language", "language")},
            {
                **base,
                "pattern": "values[lang].bin",
                "table_parameters": (),
            },
            {**base, "virtual": True},
        )
        for config in invalid:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    SubfileRuleFactory().build_rule(config, index=0)
        with self.assertRaisesRegex(ValueError, "Conflicting physical"):
            SubfileRuleFactory().build_rules((base, dict(base)))

    def test_empty_virtual_pipeline_error_names_rule(self):
        with self.assertRaisesRegex(
            ValueError,
            "virtual rule 'empty.bin'.*pre_encode.*empty",
        ):
            SubfileRuleFactory().build_rule(
                {
                    "pattern": "empty.bin",
                    "codec_name": "integer_list",
                    "virtual": True,
                },
                index=0,
            )

    def test_config_has_no_callable_values(self):
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield from walk(key)
                    yield from walk(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    yield from walk(item)
            else:
                yield value

        self.assertFalse(
            [value for value in walk(SUBFILE_RULE_CONFIGS) if callable(value)]
        )

    def test_allowed_pipeline_methods_are_repository_static_methods(self):
        self.assertNotIn("add_indexed_text_metadata", ALLOWED_RULE_METHODS)
        self.assertNotIn("prepare_indexed_text_records", ALLOWED_RULE_METHODS)
        for method_name in ALLOWED_RULE_METHODS:
            with self.subTest(method_name=method_name):
                descriptor = inspect.getattr_static(GameRepository, method_name)
                self.assertIsInstance(descriptor, staticmethod)

    def test_executable_card_capacity_rule_has_exact_profile_and_whitelist(self):
        config = next(
            item for item in SUBFILE_RULE_CONFIGS if item["pattern"] == "*_pc.exe"
        )
        self.assertEqual(config["codec_name"], "binary")
        self.assertFalse(config["virtual"])
        self.assertEqual(config["decode_params"], {})
        self.assertEqual(config["encode_params"], {})
        self.assertEqual(len(config["pre_encode"]), 1)
        step = config["pre_encode"][0]
        self.assertEqual(step["method_name"], "patch_executable_card_capacity")
        self.assertIn("patch_executable_card_capacity", ALLOWED_RULE_METHODS)
        self.assertNotIn("executable", CODEC_OPERATIONS)

        profile = step["params"]["profile"]
        self.assertEqual(
            {
                key: profile[key]
                for key in (
                    "legacy_card_record_count",
                    "minimum_patched_record_count",
                    "maximum_card_record_count",
                    "state_base_address",
                    "state_limit_address",
                    "state_record_size",
                    "snapshot_stack_overhead",
                )
            },
            {
                "legacy_card_record_count": 1115,
                "minimum_patched_record_count": 1116,
                "maximum_card_record_count": 2166,
                "state_base_address": 0x00A53CCC,
                "state_limit_address": 0x00A54DB8,
                "state_record_size": 2,
                "snapshot_stack_overhead": 0x10,
            },
        )
        self.assertEqual(
            profile["source_sha256"],
            "c5749eb934a1cf68d9236e44ff81e98b8aaee486b4f8ebd417440505d44ac1ea",
        )
        self.assertEqual(
            profile["known_output_sha256"],
            {
                1116: (
                    "cd04132ea2915e186fa7c4d67f7db73fe9fdb784fc0c95c7ab5b96733d3da699"
                )
            },
        )
        integer_sites = profile["integer_patch_sites"]
        conditional_sites = profile["conditional_patch_sites"]
        self.assertEqual(len(integer_sites), 21)
        self.assertEqual(len(conditional_sites), 1)
        self.assertEqual(
            Counter(site["value_name"] for site in integer_sites),
            {
                "maximum_internal_id": 5,
                "exclusive_upper_bound": 6,
                "state_end_address": 6,
                "snapshot_stack_size": 2,
                "snapshot_dword_count": 1,
                "state_byte_count": 1,
            },
        )
        self.assertEqual(
            [
                (
                    site["offset"],
                    site["expected"],
                    site["value_offset"],
                    site["value_width"],
                    site["value_name"],
                )
                for site in integer_sites
            ],
            [
                (
                    0x2315,
                    b"\x66\x81\xfe\x5b\x04",
                    3,
                    2,
                    "maximum_internal_id",
                ),
                (
                    0x3A9B2,
                    b"\x81\xfe\x5b\x04\x00\x00",
                    2,
                    4,
                    "exclusive_upper_bound",
                ),
                (
                    0x3AA9D,
                    b"\x81\xfb\x5b\x04\x00\x00",
                    2,
                    4,
                    "exclusive_upper_bound",
                ),
                (
                    0x45703,
                    b"\x81\xfe\x5b\x04\x00\x00",
                    2,
                    4,
                    "exclusive_upper_bound",
                ),
                (
                    0x6E5B3,
                    b"\x3d\x5b\x04\x00\x00",
                    1,
                    4,
                    "exclusive_upper_bound",
                ),
                (
                    0x76327,
                    b"\x3d\x5b\x04\x00\x00",
                    1,
                    4,
                    "exclusive_upper_bound",
                ),
                (
                    0x7DBFD,
                    b"\x81\xff\x5b\x04\x00\x00",
                    2,
                    4,
                    "exclusive_upper_bound",
                ),
                (
                    0x6E5C7,
                    b"\x3d\x5a\x04\x00\x00",
                    1,
                    4,
                    "maximum_internal_id",
                ),
                (
                    0x6E5CE,
                    b"\xb8\x5a\x04\x00\x00",
                    1,
                    4,
                    "maximum_internal_id",
                ),
                (
                    0x76339,
                    b"\x3d\x5a\x04\x00\x00",
                    1,
                    4,
                    "maximum_internal_id",
                ),
                (
                    0x76340,
                    b"\xb8\x5a\x04\x00\x00",
                    1,
                    4,
                    "maximum_internal_id",
                ),
                (
                    0x63F18,
                    b"\x3d\x82\x45\xa5\x00",
                    1,
                    4,
                    "state_end_address",
                ),
                (
                    0x63F8B,
                    b"\x81\xfe\x82\x45\xa5\x00",
                    2,
                    4,
                    "state_end_address",
                ),
                (
                    0x7DA75,
                    b"\x81\xfe\x82\x45\xa5\x00",
                    2,
                    4,
                    "state_end_address",
                ),
                (
                    0x7DC7D,
                    b"\x81\xff\x82\x45\xa5\x00",
                    2,
                    4,
                    "state_end_address",
                ),
                (
                    0x1BED0D,
                    b"\x3d\x82\x45\xa5\x00",
                    1,
                    4,
                    "state_end_address",
                ),
                (
                    0x1BEE16,
                    b"\x81\xfe\x82\x45\xa5\x00",
                    2,
                    4,
                    "state_end_address",
                ),
                (
                    0x7DCE0,
                    b"\x81\xec\xc8\x08\x00\x00",
                    2,
                    4,
                    "snapshot_stack_size",
                ),
                (
                    0x7DDAB,
                    b"\x81\xc4\xc8\x08\x00\x00",
                    2,
                    4,
                    "snapshot_stack_size",
                ),
                (
                    0x7DCEA,
                    b"\xb9\x2d\x02\x00\x00",
                    1,
                    4,
                    "snapshot_dword_count",
                ),
                (
                    0x7DD89,
                    b"\x3d\xb6\x08\x00\x00",
                    1,
                    4,
                    "state_byte_count",
                ),
            ],
        )
        self.assertEqual(conditional_sites[0]["offset"], 0x7DCFE)
        self.assertEqual(conditional_sites[0]["expected"], b"\x66\xa5")
        self.assertEqual(conditional_sites[0]["odd_record_bytes"], b"\x66\xa5")
        self.assertEqual(conditional_sites[0]["even_record_bytes"], b"\x90\x90")
        self.assertTrue(all(site["description"].strip() for site in integer_sites))
        self.assertTrue(all(site["description"].strip() for site in conditional_sites))

        repository = GameRepository.from_root(".")
        for name in (
            "joey_pc.exe",
            "mai_pc.exe",
            "eng_pc.exe",
            "version-2_pc.exe",
            "YUGI_PC.EXE",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    repository.find_rule(name).source_pattern,
                    "*_pc.exe",
                )
        self.assertIsNone(repository.subfile_rule("launcher.exe"))

    def test_executable_profile_is_frozen_and_each_pipeline_call_gets_a_copy(self):
        repository = GameRepository.from_root(".")
        rule = repository.find_rule("mai_pc.exe")
        frozen_profile = rule.pre_encode[0].params["profile"]
        with self.assertRaises(TypeError):
            frozen_profile["maximum_card_record_count"] = 9999
        with self.assertRaises(TypeError):
            frozen_profile["integer_patch_sites"][0]["offset"] = -1

        observed_profiles = []
        observed_offsets_before_mutation = []
        observed_context_states = []

        def probe(value, *, context, profile):
            observed_profiles.append(profile)
            self.assertIsInstance(profile["integer_patch_sites"], list)
            observed_offsets_before_mutation.append(
                profile["integer_patch_sites"][0]["offset"]
            )
            observed_context_states.append(context.metadata.get("mutated"))
            profile["integer_patch_sites"][0]["offset"] = -1
            context.metadata["mutated"] = True
            return value

        contexts = [
            repository._create_rule_context(
                rule,
                relative_path="mai_pc.exe",
                language=None,
                metadata={"card_record_count": 1116},
            )
            for _ in range(2)
        ]
        with patch.object(
            GameRepository,
            "patch_executable_card_capacity",
            new=staticmethod(probe),
        ):
            for context in contexts:
                repository._run_rule_pipeline(
                    b"source",
                    rule.pre_encode,
                    context=context,
                    phase="pre_encode",
                )

        self.assertIsNot(observed_profiles[0], observed_profiles[1])
        self.assertIsNot(
            observed_profiles[0]["integer_patch_sites"],
            observed_profiles[1]["integer_patch_sites"],
        )
        self.assertEqual(observed_offsets_before_mutation, [0x2315, 0x2315])
        self.assertEqual(observed_context_states, [None, None])
        self.assertNotEqual(
            frozen_profile["integer_patch_sites"][0]["offset"],
            -1,
        )
        self.assertIsNot(contexts[0].metadata, contexts[1].metadata)
        self.assertTrue(contexts[0].metadata["mutated"])
        self.assertTrue(contexts[1].metadata["mutated"])


class SubfileRuleMatchingTests(unittest.TestCase):
    def setUp(self):
        self.repository = GameRepository.from_root(".")

    def test_dat_bin_and_specific_rule_precedence(self):
        expected = {
            "Data.dat": ("container", False),
            "Voice.dat": ("container", False),
            "Region.dat": ("binary", False),
            "unknown.bin": ("binary", False),
            "card_id.bin": ("integer_list", False),
            "card_pass.bin": ("fixed_hex_list", False),
            "card_indxeng.bin": ("integer_list", True),
            "card_desceng.bin": ("offset_string_table", False),
            "card_sorteng.bin": ("integer_list", True),
            "card_nameeng.bin": ("fixed_string_list", False),
        }
        for file_name, result in expected.items():
            with self.subTest(file_name=file_name):
                rule = self.repository.find_rule(file_name)
                self.assertEqual((rule.codec_name, rule.virtual), result)

    def test_specific_list_card_rule_wins_over_generic_cp932_text_rules(self):
        for relative_path in (
            "data/card/list_card.txt",
            "data/mini/list_card.txt",
        ):
            with self.subTest(relative_path=relative_path):
                rule = self.repository.find_rule(relative_path)
                self.assertEqual(rule.codec_name, "regex_record_table")
                self.assertEqual(rule.decode_params["encoding"], "utf-8-sig")
                self.assertEqual(rule.encode_params["encoding"], "utf-8")

        for file_name in ("notes.txt", "notes.text"):
            with self.subTest(file_name=file_name):
                rule = self.repository.find_rule(file_name)
                self.assertEqual(rule.codec_name, "text")
                self.assertEqual(rule.decode_params, {"encoding": "cp932"})
                self.assertEqual(rule.encode_params, {"encoding": "cp932"})

    def test_custom_later_rule_wins(self):
        configs = (
            {
                "pattern": "*.bin",
                "codec_name": "binary",
            },
            {
                "pattern": "special.bin",
                "codec_name": "integer_list",
                "decode_params": {
                    "byte_width": 2,
                    "signed": True,
                    "byte_order": "little",
                },
            },
        )
        rules = SubfileRuleFactory().build_rules(configs)
        factory = Mock()
        factory.build_rules.return_value = rules
        repository = GameRepository(GameFolderConnection("."), factory)
        self.assertEqual(
            repository.find_rule("special.bin").codec_name,
            "integer_list",
        )

    def test_language_placeholder_is_canonical_and_case_insensitive(self):
        for file_name in (
            "card_nameeng.bin",
            "card_namespa.bin",
            "CARD_NAMEITA.BIN",
        ):
            with self.subTest(file_name=file_name):
                self.assertEqual(
                    self.repository.find_rule(file_name).codec_name,
                    "fixed_string_list",
                )
        self.assertEqual(
            self.repository.find_rule("card_namespan.bin").codec_name,
            "binary",
        )

    def test_virtual_metadata_is_in_the_same_rule(self):
        index = self.repository.find_rule("card_indxeng.bin")
        description = self.repository.find_rule("card_desceng.bin")
        sort = self.repository.find_rule("card_sorteng.bin")
        self.assertTrue(index.virtual)
        self.assertEqual(
            [step.method_name for step in index.pre_encode],
            [
                "load_dependency_table",
                "dataframe_to_indexed_text_records",
                "generate_string_offsets",
                "pad_integer_sequence",
            ],
        )
        self.assertEqual(
            index.pre_encode[0].params["table"],
            "card_desc[lang].bin",
        )
        self.assertFalse(index.decode_params["signed"])
        self.assertEqual(index.decode_params["byte_width"], 4)
        self.assertFalse(description.virtual)
        self.assertEqual(
            description.pre_decode[0].params["table"],
            "card_indx[lang].bin",
        )
        self.assertEqual(
            [step.method_name for step in description.post_decode],
            ["records_to_dataframe"],
        )
        self.assertEqual(
            [step.method_name for step in description.pre_encode],
            ["dataframe_to_indexed_text_records"],
        )
        self.assertFalse(sort.decode_params["signed"])
        self.assertEqual(sort.decode_params["byte_width"], 2)
        reverse = self.repository.find_rule("card_intid.bin")
        self.assertTrue(reverse.virtual)
        self.assertEqual(reverse.pre_encode[0].params["table"], "card_id.bin")
        self.assertEqual(
            [step.method_name for step in reverse.pre_encode],
            [
                "load_dependency_table",
                "dataframe_column_to_list",
                "generate_reverse_lookup",
            ],
        )
        self.assertEqual(
            [step.method_name for step in sort.pre_encode],
            [
                "load_card_sort_records",
                "generate_sort_indices",
            ],
        )
        self.assertNotIn("card_intid.bin", str(sort.pre_encode))
        self.assertFalse(reverse.encode_params["signed"])
        self.assertEqual(reverse.encode_params["byte_width"], 2)
        self.assertEqual(reverse.encode_params["byte_order"], "little")

    def test_codec_names_are_generic_operations(self):
        self.assertEqual(VALID_CODEC_NAMES, CODEC_OPERATIONS)
        self.assertEqual(
            {
                rule.codec_name
                for rule in SubfileRuleFactory().build_rules(SUBFILE_RULE_CONFIGS)
            }.difference(VALID_CODEC_NAMES),
            set(),
        )

    def test_card_id_signed_round_trip(self):
        data = bytes.fromhex("FF FF 00 00 01 00 FF 7F 00 80")
        table = GameRepository.decode_binary_resource("card_id.bin", data)
        self.assertEqual(
            table["value"].tolist(),
            [-1, 0, 1, 32767, -32768],
        )
        self.assertEqual(
            GameRepository.encode_binary_resource("card_id.bin", table),
            data,
        )

    def test_card_passcode_rule_preserves_raw_order_hex_strings(self):
        data = bytes.fromhex("00 00 00 01 FF FF FF FF 12 34 56 78")
        table = GameRepository.decode_binary_resource("card_pass.bin", data)
        self.assertEqual(
            table["value"].tolist(),
            ["00000001", "FFFFFFFF", "12345678"],
        )
        self.assertEqual(
            GameRepository.encode_binary_resource("card_pass.bin", table),
            data,
        )
        with self.assertRaisesRegex(RulePipelineError, "uppercase hexadecimal"):
            GameRepository.encode_binary_resource(
                "card_pass.bin",
                pd.DataFrame({"value": ["abcdef12"]}),
            )

    def test_connection_reports_unknown_generic_operation(self):
        connection = GameFolderConnection(".")
        with self.assertRaisesRegex(
            ValueError,
            "Unknown codec operation 'missing'.*Available operations",
        ):
            connection.decode_resource("missing", b"")


class RulePipelineTests(unittest.TestCase):
    def _repository_for(self, config, connection=None):
        rules = SubfileRuleFactory().build_rules((config,))
        factory = Mock()
        factory.build_rules.return_value = rules
        repository = GameRepository(connection or Mock(), factory)
        return repository, rules[0]

    def test_decode_pipeline_runs_in_order_around_codec(self):
        connection = Mock()
        connection.decode_resource.side_effect = lambda codec_name, value, **params: (
            value + b"C"
        )
        repository, rule = self._repository_for(
            {
                "pattern": "ordered.bin",
                "codec_name": "binary",
                "pre_decode": (
                    {
                        "method_name": "slice_bytes",
                        "params": {"start": 1},
                    },
                ),
                "post_decode": (
                    {
                        "method_name": "append_bytes",
                        "params": {"suffix": b"A"},
                    },
                    {
                        "method_name": "append_bytes",
                        "params": {"suffix": b"B"},
                    },
                ),
            },
            connection,
        )
        result = repository._decode_rule_value(
            rule,
            b"Xraw",
            None,
            {},
            relative_path="ordered.bin",
        )
        self.assertEqual(result, b"rawCAB")
        self.assertEqual(connection.decode_resource.call_args.args, ("binary", b"raw"))

    def test_encode_pipeline_runs_in_order_around_codec(self):
        connection = Mock()
        connection.encode_resource.side_effect = lambda codec_name, value, **params: (
            b"C" + value
        )
        repository, rule = self._repository_for(
            {
                "pattern": "ordered.bin",
                "codec_name": "binary",
                "pre_encode": (
                    {
                        "method_name": "slice_bytes",
                        "params": {"start": 1},
                    },
                ),
                "post_encode": (
                    {
                        "method_name": "append_bytes",
                        "params": {"suffix": b"A"},
                    },
                    {
                        "method_name": "append_bytes",
                        "params": {"suffix": b"B"},
                    },
                ),
            },
            connection,
        )
        result = repository._encode_rule_value(
            rule,
            b"Xraw",
            None,
            relative_path="ordered.bin",
        )
        self.assertEqual(result, b"CrawAB")
        self.assertEqual(connection.encode_resource.call_args.args, ("binary", b"raw"))

    def test_dataframe_is_converted_before_integer_codec(self):
        connection = Mock()
        connection.encode_resource.return_value = b"encoded"
        repository, rule = self._repository_for(
            {
                "pattern": "values.bin",
                "codec_name": "integer_list",
                "encode_params": {"byte_width": 2},
                "pre_encode": (
                    {
                        "method_name": "dataframe_column_to_list",
                        "params": {
                            "column": "value",
                            "fill_value": 0,
                            "cast": "int",
                        },
                    },
                ),
            },
            connection,
        )
        repository._encode_rule_value(
            rule,
            pd.DataFrame({"value": [1, 2]}),
            None,
            relative_path="values.bin",
        )
        self.assertEqual(
            connection.encode_resource.call_args.args,
            ("integer_list", [1, 2]),
        )

    def test_reverse_lookup_power_of_two_boundaries_and_missing_ids(self):
        repository, rule = self._repository_for(
            {
                "pattern": "card_intid.bin",
                "codec_name": "integer_list",
                "encode_params": {
                    "byte_width": 2,
                    "signed": False,
                    "byte_order": "little",
                },
            }
        )
        context = RuleProcessingContext(
            repository=repository,
            rule=rule,
            relative_path="bin#/card_intid.bin",
            language=None,
            decode_params={},
            encode_params=dict(rule.encode_params),
            metadata={},
        )

        for maximum_id, expected_length in (
            (0, 1),
            (1, 2),
            (2, 4),
            (3, 4),
            (4, 8),
            (1023, 1024),
            (1024, 2048),
            (2047, 2048),
            (2048, 4096),
            (2389, 4096),
        ):
            with self.subTest(maximum_id=maximum_id):
                result = repository.generate_reverse_lookup(
                    [maximum_id],
                    context=context,
                )
                self.assertEqual(len(result), expected_length)

        with self.assertRaisesRegex(
            ValueError,
            "Cannot generate card_intid: card_id contains no non-negative IDs",
        ):
            repository.generate_reverse_lookup([-1, -1], context=context)

        result = repository.generate_reverse_lookup([-1, 3], context=context)
        self.assertEqual(len(result), 4)
        self.assertEqual(result, [0, 0, 0, 1])

    def test_reverse_lookup_mapping_and_duplicates(self):
        repository = GameRepository.from_root(".")
        rule = repository.find_rule("card_intid.bin")
        context = RuleProcessingContext(
            repository=repository,
            rule=rule,
            relative_path="bin#/card_intid.bin",
            language=None,
            decode_params=dict(rule.decode_params),
            encode_params=dict(rule.encode_params),
            metadata={},
        )
        reverse = repository.generate_reverse_lookup([3, 1, 6], context=context)
        self.assertEqual(reverse, [0, 1, 0, 0, 0, 0, 2, 0])
        duplicate = repository.generate_reverse_lookup([0, 5, 0], context=context)
        self.assertEqual(duplicate[0], 2)
        self.assertEqual(duplicate[5], 1)

    def test_sort_indices_preserve_dummy_and_real_row_permutation(self):
        repository = GameRepository.from_root(".")
        rule = repository.find_rule("card_sorteng.bin")
        context = RuleProcessingContext(
            repository=repository,
            rule=rule,
            relative_path="bin#/card_sorteng.bin",
            language="eng",
            decode_params=dict(rule.decode_params),
            encode_params=dict(rule.encode_params),
            metadata={},
        )
        fixtures = (
            (["", "Gamma", "Alpha"], [0, 1, 0]),
            (["", "Alpha", "Beta"], [0, 0, 1]),
            (["", "Beta", "", "alpha", "Alpha"], [0, 3, 0, 1, 2]),
            (["", "Same", "Same"], [0, 0, 1]),
            (["", "ss", "é", "f"], [0, 2, 0, 1]),
        )
        for names, expected in fixtures:
            with self.subTest(names=names):
                records = [
                    {
                        "card_index": index,
                        "name": name,
                        "card_id": (
                            -1
                            if index == 0
                            else len(names)
                            if index == len(names) - 1
                            else index
                        ),
                    }
                    for index, name in enumerate(names)
                ]
                ranks = repository.generate_sort_indices(records, context=context)
                self.assertEqual(ranks[0], 0)
                self.assertEqual(
                    sorted(ranks[1 : len(names)]),
                    list(range(len(names) - 1)),
                )
                self.assertEqual(ranks[: len(names)], expected)
                self.assertFalse(any(ranks[len(names) :]))

    def test_english_sort_exact_blank_hyphen_and_duplicate_order(self):
        repository = GameRepository.from_root(".")
        rule = repository.find_rule("card_sorteng.bin")
        context = RuleProcessingContext(
            repository=repository,
            rule=rule,
            relative_path="bin#/card_sorteng.bin",
            language="eng",
            decode_params=dict(rule.decode_params),
            encode_params=dict(rule.encode_params),
            metadata={},
        )
        names = [
            "",
            "",
            "",
            "",
            "",
            "Blue-Eyes",
            "blueeyes",
            "Same",
            "Same",
        ]
        card_ids = [-1, 1250, 1251, 1257, 1258, 9000, 9001, 5, 5]
        records = [
            {"card_index": index, "name": name, "card_id": card_ids[index]}
            for index, name in enumerate(names)
        ]
        ranks = repository.generate_sort_indices(records, context=context)
        self.assertEqual(ranks[: len(records)], [0, 0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(len(ranks), 16)
        self.assertFalse(any(ranks[len(records) :]))

    def test_virtual_dependency_cycle_reports_full_generation_chain(self):
        configs = (
            {
                "pattern": "seed.bin",
                "codec_name": "integer_list",
                "decode_params": {"byte_width": 2},
                "encode_params": {"byte_width": 2},
            },
            *(
                {
                    "pattern": name,
                    "codec_name": "integer_list",
                    "decode_params": {"byte_width": 2},
                    "encode_params": {"byte_width": 2},
                    "virtual": True,
                    "pre_encode": (
                        {
                            "method_name": "load_dependency_table",
                            "params": {"table": "seed.bin"},
                        },
                        {
                            "method_name": "dataframe_column_to_list",
                            "params": {"column": "value", "cast": "int"},
                        },
                        {
                            "method_name": (
                                "pad_integer_sequence_to_dependency_length"
                            ),
                            "params": {"dependency": dependency},
                        },
                    ),
                }
                for name, dependency in (("a.bin", "b.bin"), ("b.bin", "a.bin"))
            ),
        )
        repository = GameRepository.from_root(".")
        repository._subfile_rules = SubfileRuleFactory().build_rules(configs)
        resources = [
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    "seed.bin",
                    "data/seed.bin",
                    "table",
                    "table",
                    order=2,
                ),
                pd.DataFrame({"value": [1]}),
            )
        ]
        for order, name in enumerate(("a.bin", "b.bin")):
            resources.append(
                ProjectResource(
                    ProjectFileRecord(
                        "Data.dat",
                        name,
                        None,
                        "virtual",
                        "virtual",
                        generated_on_pack=True,
                        virtual=True,
                        order=order,
                    )
                )
            )

        with self.assertRaisesRegex(
            Exception,
            "Circular virtual-resource dependency: a.bin -> b.bin -> a.bin",
        ):
            repository.encode_archive("Data.dat", resources)

    def test_reverse_lookup_handles_ten_thousand_values_with_finite_progress(self):
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=_large_reverse_lookup_process,
            args=(result_queue,),
        )
        process.start()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
            self.fail("Reverse lookup did not complete within the test timeout.")
        self.assertEqual(process.exitcode, 0)
        try:
            result = result_queue.get(timeout=1)
        except queue.Empty:
            self.fail("Reverse lookup process exited without returning a result.")
        finally:
            result_queue.close()
            result_queue.join_thread()
        self.assertEqual(result, ("ok", 16_384, 0, 9_999))

    def test_pipeline_error_contains_full_context_and_cause(self):
        repository, rule = self._repository_for(
            {
                "pattern": "broken.bin",
                "codec_name": "binary",
                "pre_encode": (
                    {
                        "method_name": "dataframe_column_to_list",
                        "params": {"column": "value"},
                    },
                ),
            },
            Mock(),
        )
        with self.assertRaises(RulePipelineError) as raised:
            repository._encode_rule_value(
                rule,
                b"not-a-table",
                None,
                relative_path="folder/broken.bin",
            )
        message = str(raised.exception)
        for expected in (
            "folder/broken.bin",
            "pattern='broken.bin'",
            "phase='pre_encode'",
            "step=0",
            "method='dataframe_column_to_list'",
            "requires a DataFrame",
        ):
            self.assertIn(expected, message)
        self.assertIsNotNone(raised.exception.__cause__)

    def test_resolver_does_not_allow_arbitrary_repository_methods(self):
        repository = GameRepository.from_root(".")
        with self.assertRaisesRegex(ValueError, "Unknown rule processing method"):
            repository._resolve_rule_method("write_binary")

    def test_runtime_context_uses_independent_parameter_dicts(self):
        repository = GameRepository.from_root(".")
        rule = repository.find_rule("card_id.bin")
        context = RuleProcessingContext(
            repository=repository,
            rule=rule,
            relative_path="card_id.bin",
            language=None,
            decode_params=dict(rule.decode_params),
            encode_params=dict(rule.encode_params),
            metadata={},
        )
        context.decode_params["temporary"] = True
        self.assertNotIn("temporary", rule.decode_params)

    def test_dependency_loaders_resolve_language_and_preserve_resource(self):
        repository = GameRepository.from_root(".")
        rule = repository.find_rule("card_indxeng.bin")
        table = pd.DataFrame({"value": ["Alpha"]})
        resource = ProjectResource(
            ProjectFileRecord(
                source_file="Data.dat",
                relative_path="BIN#/CARD_DESCENG.BIN",
                workspace_path="data/BIN#/CARD_DESCENG.BIN",
                file_kind="table",
                storage_format="table",
                language="eng",
            ),
            table,
        )
        context = RuleProcessingContext(
            repository=repository,
            rule=rule,
            relative_path="bin#/card_indxeng.bin",
            language="eng",
            decode_params={},
            encode_params={},
            metadata={"resources": {"BIN#/CARD_DESCENG.BIN": resource}},
        )
        loaded = GameRepository.load_dependency_table(
            None,
            context=context,
            table="card_desc[lang].bin",
        )
        self.assertIs(loaded, table)
        self.assertEqual(resource.record.relative_path, "BIN#/CARD_DESCENG.BIN")

    def test_missing_and_self_dependencies_are_clear(self):
        repository = GameRepository.from_root(".")
        rule = repository.find_rule("card_indxeng.bin")
        context = RuleProcessingContext(
            repository=repository,
            rule=rule,
            relative_path="bin#/card_indxeng.bin",
            language="eng",
            decode_params={},
            encode_params={},
            metadata={"resources": {}},
        )
        with self.assertRaisesRegex(
            KeyError,
            "card_indxeng.bin.*card_desc\\[lang\\].bin.*card_desceng.bin",
        ):
            GameRepository.load_dependency_table(
                None,
                context=context,
                table="card_desc[lang].bin",
            )
        with self.assertRaisesRegex(
            ValueError,
            "card_indxeng.bin.*itself.*card_indxeng.bin",
        ):
            GameRepository.load_dependency_table(
                None,
                context=context,
                table="card_indx[lang].bin",
            )

    def test_circular_decode_dependencies_report_the_full_chain(self):
        def rule(pattern, dependency):
            return {
                "pattern": pattern,
                "codec_name": "binary",
                "pre_decode": (
                    {
                        "method_name": "inject_offset_dependency",
                        "params": {"table": dependency},
                    },
                ),
            }

        cases = (
            (
                (rule("A.bin", "A.bin"),),
                ("A.bin",),
                "A.bin -> A.bin",
            ),
            (
                (rule("A.bin", "B.bin"), rule("B.bin", "A.bin")),
                ("A.bin", "B.bin"),
                "A.bin -> B.bin -> A.bin",
            ),
            (
                (
                    rule("A.bin", "B.bin"),
                    rule("B.bin", "C.bin"),
                    rule("C.bin", "A.bin"),
                ),
                ("A.bin", "B.bin", "C.bin"),
                "A.bin -> B.bin -> C.bin -> A.bin",
            ),
        )
        for configs, paths, expected in cases:
            with self.subTest(expected=expected):
                rules = SubfileRuleFactory().build_rules(configs)
                factory = Mock()
                factory.build_rules.return_value = rules
                repository = GameRepository(Mock(), factory)
                entries = {
                    path.casefold(): ContainerEntry(path, data=b"x") for path in paths
                }
                with self.assertRaisesRegex(
                    RulePipelineError,
                    f"Circular sub-file dependency: {expected}",
                ):
                    repository._decode_rule_value(
                        repository.find_rule("A.bin"),
                        b"x",
                        None,
                        entries,
                        relative_path="A.bin",
                    )
