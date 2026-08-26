from __future__ import annotations

import tempfile
import threading
import unicodedata
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from time import sleep
from unittest.mock import Mock, call, patch

import pandas as pd
from PIL import Image

from tests.pipeline_support import decode_description_resource
from tests.test_repository_tables import ProjectTableFixture
from yugioh_editor.common import worker_limits
from yugioh_editor.common.card_errors import (
    CardCapacityError,
    CardImportError,
    CardPersistenceError,
    CardValidationError,
)
from yugioh_editor.common.card_images import (
    build_card_image_pair,
    generate_unique_card_image_name,
)
from yugioh_editor.common.card_name_normalization import CardNameNormalizer
from yugioh_editor.common.constants import LANGUAGE_PREFIXES
from yugioh_editor.common.worker_limits import (
    MEBIBYTE,
    estimate_available_memory_bytes,
    select_bulk_suggest_worker_count,
)
from yugioh_editor.models.card_editing import (
    CARD_CSV_COLUMNS,
    CardEditDraft,
    CardLocalizedText,
    CardReferenceData,
)
from yugioh_editor.models.entities import (
    CardImageVariant,
    ProjectFileRecord,
    ProjectResource,
)
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.project.repository import (
    ProjectRepository,
    normalize_project_path,
)
from yugioh_editor.services.card_service import (
    CardService,
    normalize_card_text_for_encoding,
)

BLACK_CIRCLE_DESCRIPTION = (
    'Target 1 "Red Dragon Archfiend" you control; if that monster you '
    "control battles an opponent's monster this turn, apply these effects "
    "until the end of the Damage Step. ●It gains 1000 ATK. "
    "●Your opponent cannot activate cards or effects. "
    "●If it attacks a Defense Position monster, inflict piercing battle "
    "damage to your opponent. "
    "●Any battle damage your opponent takes from that battle is doubled."
)
OFFICIAL_UNENCODABLE_JAPANESE_NAME = "熒焅聖 アレクゥス"


class CardEditingModelTests(unittest.TestCase):
    def test_localized_text_always_has_all_languages(self):
        localized = CardLocalizedText(names={"eng": "Name"})
        self.assertEqual(tuple(localized.names), LANGUAGE_PREFIXES)
        self.assertEqual(tuple(localized.descriptions), LANGUAGE_PREFIXES)
        self.assertEqual(localized.names["eng"], "Name")
        self.assertTrue(
            all(
                localized.names[language] == ""
                for language in LANGUAGE_PREFIXES
                if language != "eng"
            )
        )

    def test_unique_image_name_is_deterministic_and_case_insensitive(self):
        self.assertEqual(generate_unique_card_image_name(set()), "usr000.bmp")
        self.assertEqual(
            generate_unique_card_image_name({"USR000.BMP", "usr001.bmp"}),
            "usr002.bmp",
        )
        occupied = {f"usr{index:03d}.bmp" for index in range(1000)}
        self.assertEqual(generate_unique_card_image_name(occupied), "uss000.bmp")

    def test_property_typo_is_not_silently_corrected(self):
        draft = CardEditDraft(card_index=1, card_id=1, attribute="warter")
        service = CardService(Mock(), Mock())
        errors = service.validate_card_draft(draft)
        self.assertTrue(any("attribute" in error for error in errors))
        self.assertEqual(draft.attribute, "warter")

    def test_bulk_worker_count_is_ram_aware_and_bounded(self):
        self.assertEqual(
            select_bulk_suggest_worker_count(1, 8 * 1024 * MEBIBYTE),
            1,
        )
        self.assertEqual(
            select_bulk_suggest_worker_count(100, 512 * MEBIBYTE),
            1,
        )
        self.assertEqual(
            select_bulk_suggest_worker_count(100, 640 * MEBIBYTE),
            2,
        )
        self.assertEqual(
            select_bulk_suggest_worker_count(100, 8 * 1024 * MEBIBYTE),
            8,
        )
        self.assertEqual(select_bulk_suggest_worker_count(100, None), 4)
        self.assertEqual(select_bulk_suggest_worker_count(0, None), 0)

    def test_available_memory_estimate_falls_back_when_psutil_is_unavailable(self):
        with (
            patch.dict("sys.modules", {"psutil": None}),
            patch.object(worker_limits.os, "name", "nt"),
            patch.object(
                worker_limits,
                "_windows_available_memory_bytes",
                return_value=123 * MEBIBYTE,
            ),
        ):
            self.assertEqual(estimate_available_memory_bytes(), 123 * MEBIBYTE)

    def test_available_memory_estimate_falls_back_when_psutil_probe_fails(self):
        psutil = Mock()
        psutil.virtual_memory.side_effect = RuntimeError("probe failed")
        with (
            patch.dict("sys.modules", {"psutil": psutil}),
            patch.object(worker_limits.os, "name", "nt"),
            patch.object(
                worker_limits,
                "_windows_available_memory_bytes",
                return_value=456 * MEBIBYTE,
            ),
        ):
            self.assertEqual(estimate_available_memory_bytes(), 456 * MEBIBYTE)


class CardEditingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manifest, self.repository = ProjectTableFixture.build(
            self.root / "project"
        )
        self.reference_service = Mock()
        self.service = CardService(self.repository, self.reference_service)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _image_payload(color: str = "red") -> bytes:
        payload = BytesIO()
        Image.new("RGB", (320, 460), color).save(payload, format="PNG")
        return payload.getvalue()

    def _canonicalize_fast_path_catalogs(self) -> None:
        card_ids = self.repository.get_table("card_ids")["value"].astype(int)
        english_names = self.repository.get_table("card_names", language="eng")[
            "value"
        ].astype(str)
        large = self.repository.get_table(
            "card_catalog", image_variant=CardImageVariant.LARGE
        ).reset_index(drop=True)
        for variant in CardImageVariant:
            catalog = self.repository.get_table(
                "card_catalog", image_variant=variant
            ).reset_index(drop=True)
            catalog["index"] = range(len(card_ids))
            catalog["card_id"] = [0 if card_id < 0 else card_id for card_id in card_ids]
            for row_index, card_id in enumerate(card_ids):
                if card_id >= 0:
                    catalog.at[row_index, "name"] = english_names.iloc[row_index]
                catalog.at[row_index, "image_name"] = large.iloc[row_index][
                    "image_name"
                ]
                catalog.at[row_index, "note"] = large.iloc[row_index]["note"]
            self.repository.save_table(
                "card_catalog",
                catalog,
                image_variant=variant,
            )

    @staticmethod
    def _complete_suggest_card(
        index: int,
        *,
        kind: str = "monster",
        image_name: str = "existing.bmp",
    ) -> CardEditDraft:
        class_data = {
            "monster": (1, "dragon", 0, "normal"),
            "trap": (21, "trap_card", 0, "normal"),
            "spell": (22, "spell_card", 0, "normal"),
        }
        class_code, card_type, category_code, category = class_data[kind]
        is_monster = kind == "monster"
        return CardEditDraft(
            card_index=index,
            card_id=index,
            localized_text=CardLocalizedText(
                names={
                    language: f"Complete {index} {language}"
                    for language in LANGUAGE_PREFIXES
                },
                descriptions={
                    language: f"Description {index} {language}"
                    for language in LANGUAGE_PREFIXES
                },
            ),
            password="12345678",
            level=0 if is_monster else None,
            attack=0 if is_monster else None,
            defense=0 if is_monster else None,
            attribute="dark" if is_monster else "",
            card_type=card_type,
            card_category=category,
            image_name=image_name,
            monster_type_code=class_code,
            card_category_code=category_code,
            attribute_code=2 if is_monster else 0,
        )

    def test_card_text_preflight_normalizes_projection_without_mutating_draft(self):
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.localized_text.descriptions["eng"] = BLACK_CIRCLE_DESCRIPTION
        draft.dirty = True
        draft.touched_fields.add("description:eng")

        normalized = normalize_card_text_for_encoding(
            BLACK_CIRCLE_DESCRIPTION,
            "cp1252",
        )
        errors = self.service.validate_card_draft(draft)

        self.assertEqual(BLACK_CIRCLE_DESCRIPTION.count("●"), 4)
        self.assertEqual(BLACK_CIRCLE_DESCRIPTION.index("●"), 168)
        self.assertEqual(normalized.count("•"), 4)
        self.assertNotIn("●", normalized)
        normalized.encode("cp1252", errors="strict")
        self.assertEqual(errors, [])
        self.assertEqual(
            draft.localized_text.descriptions["eng"],
            BLACK_CIRCLE_DESCRIPTION,
        )
        self.assertTrue(draft.dirty)
        self.assertEqual(draft.touched_fields, {"description:eng"})

    def test_trusted_detail_fast_path_rewrites_only_affected_property_table(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.attack = 1700
        edited.dirty = True
        edited.touched_fields.add("attack")
        before = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        staging = self.repository.begin_update()

        with (
            patch.object(self.repository, "begin_update", return_value=staging),
            patch.object(staging, "get_table", side_effect=AssertionError("composite")),
            patch.object(
                staging,
                "save_table",
                side_effect=AssertionError("composite"),
            ),
            patch.object(
                staging._connection,
                "rewrite_csv_rows",
                wraps=staging._connection.rewrite_csv_rows,
            ) as rewrite_rows,
        ):
            saved = self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        self.assertEqual(saved.attack, 1700)
        self.assertFalse(edited.dirty)
        self.assertEqual(rewrite_rows.call_count, 1)
        self.assertTrue(
            rewrite_rows.call_args.args[0].replace("\\", "/").endswith("card_prop.bin")
        )
        after = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        changed = {name for name in before if before[name] != after[name]}
        self.assertEqual(changed, {"data/bin#/card_prop.bin"})
        self.assertEqual(
            self.service.get_card_detail(self.manifest, 1).attack,
            1700,
        )

    def test_trusted_detail_fast_path_groups_each_affected_resource_once(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.password = "00112233"
        edited.pack = "disabled"
        edited.attack = 1800
        edited.defense = 1300
        edited.localized_text.names["eng"] = "Fast Dragon"
        edited.localized_text.descriptions["eng"] = 'Fast, quoted "effect"'
        edited.dirty = True
        staging = self.repository.begin_update()

        with (
            patch.object(self.repository, "begin_update", return_value=staging),
            patch.object(
                staging._connection,
                "rewrite_csv_rows",
                wraps=staging._connection.rewrite_csv_rows,
            ) as rewrite_rows,
            patch.object(
                staging._connection,
                "write_manifest",
                wraps=staging._connection.write_manifest,
            ) as manifest_write,
        ):
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        paths = [
            str(call_item.args[0]).replace("\\", "/")
            for call_item in rewrite_rows.call_args_list
        ]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(len(paths), 7)
        self.assertTrue(any(path.endswith("card_pass.bin") for path in paths))
        self.assertTrue(any(path.endswith("card_pack.bin") for path in paths))
        self.assertTrue(any(path.endswith("card_prop.bin") for path in paths))
        self.assertTrue(any(path.endswith("card_nameeng.bin") for path in paths))
        self.assertTrue(any(path.endswith("card_desceng.bin") for path in paths))
        self.assertTrue(any(path.endswith("card/list_card.txt") for path in paths))
        self.assertTrue(any(path.endswith("mini/list_card.txt") for path in paths))
        self.assertEqual(manifest_write.call_count, 1)
        reloaded = self.service.get_card_detail(self.manifest, 1)
        self.assertEqual(reloaded.password, "00112233")
        self.assertEqual(reloaded.pack, "disabled")
        self.assertEqual(reloaded.attack, 1800)
        self.assertEqual(reloaded.defense, 1300)
        self.assertEqual(reloaded.localized_text.names["eng"], "Fast Dragon")
        self.assertEqual(
            reloaded.localized_text.descriptions["eng"],
            'Fast, quoted "effect"',
        )

    def test_trusted_detail_true_noop_skips_staging_and_cleans_draft(self):
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.dirty = True
        edited.touched_fields.add("attack")

        with patch.object(self.repository, "begin_update") as begin_update:
            saved = self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        begin_update.assert_not_called()
        self.assertEqual(saved, original.to_detail())
        self.assertFalse(edited.dirty)

    def test_trusted_detail_catalog_identity_conflict_fails_atomically(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.localized_text.names["eng"] = "Rejected Rename"
        edited.image_name = "usr777.bmp"
        edited.large_image_source, edited.small_image_source = build_card_image_pair(
            self._image_payload("orange")
        )
        edited.dirty = True
        mini = self.repository.get_table(
            "card_catalog", image_variant=CardImageVariant.MINI
        )
        mini.at[1, "card_id"] = 1
        self.repository.save_table(
            "card_catalog", mini, image_variant=CardImageVariant.MINI
        )
        before = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }

        with self.assertRaises(CardPersistenceError) as raised:
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        self.assertIn("mini card catalog", str(raised.exception))
        after = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertTrue(edited.dirty)
        self.assertEqual(
            self.service.get_card_detail(self.manifest, 1).localized_text.names["eng"],
            "Dragon",
        )
        self.assertFalse(
            any(
                path.name.casefold() == "usr777.bmp"
                for path in self.repository.root.rglob("*")
            )
        )

    def test_trusted_detail_rejects_generated_image_reference_without_pair(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.image_name = "usr404.bmp"
        edited.dirty = True
        before = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }

        with self.assertRaisesRegex(
            CardPersistenceError,
            "complete physical card/mini pair",
        ):
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        after = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertTrue(edited.dirty)
        self.assertEqual(
            self.service.get_card_detail(self.manifest, 1).image_name,
            original.image_name,
        )

    def test_trusted_detail_stale_clean_baseline_fails_without_fallback(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        concurrent = self.repository.get_table("card_packs")
        concurrent.at[1, "value"] = "yugi"
        self.repository.save_table("card_packs", concurrent)
        edited = original.clone()
        edited.pack = "disabled"
        edited.dirty = True

        with (
            patch.object(
                self.repository,
                "get_table",
                wraps=self.repository.get_table,
            ) as get_table,
            self.assertRaises(CardPersistenceError) as raised,
        ):
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        self.assertIn("Stale CSV row 1", str(raised.exception))
        self.assertFalse(
            any(call_item.args[0] == "cards" for call_item in get_table.call_args_list)
        )
        self.assertTrue(edited.dirty)
        self.assertEqual(
            self.repository.get_table("card_packs").iloc[1]["value"],
            "yugi",
        )

    def test_trusted_detail_normalization_only_diff_is_persisted(self):
        self._canonicalize_fast_path_catalogs()
        raw_name = "Dragon \N{BLACK CIRCLE}"
        expected_name = "Dragon \N{BULLET}"
        names = self.repository.get_table("card_names", language="eng")
        names.at[1, "value"] = raw_name
        self.repository.save_table("card_names", names, language="eng")
        for variant in CardImageVariant:
            catalog = self.repository.get_table("card_catalog", image_variant=variant)
            catalog.at[1, "name"] = raw_name
            self.repository.save_table("card_catalog", catalog, image_variant=variant)
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.dirty = True

        saved = self.service.update_card(
            self.manifest,
            edited,
            original=original,
        )

        self.assertEqual(saved.localized_text.names["eng"], expected_name)
        self.assertEqual(edited.localized_text.names["eng"], expected_name)
        self.assertEqual(
            self.repository.get_table("card_names", language="eng").iloc[1]["value"],
            expected_name,
        )

    def test_trusted_detail_description_empty_preserves_active_state(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.localized_text.descriptions["eng"] = ""
        edited.dirty = True

        self.service.update_card(
            self.manifest,
            edited,
            original=original,
        )

        descriptions = self.repository.get_table("card_descriptions", language="eng")
        self.assertEqual(descriptions.iloc[1]["text"], "")
        self.assertFalse(descriptions.iloc[1]["is_reserved"])

    def test_trusted_detail_new_image_consumes_each_pending_catalog_once(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.image_name = "usr321.bmp"
        edited.large_image_source, edited.small_image_source = build_card_image_pair(
            self._image_payload("navy")
        )
        edited.dirty = True
        staging = self.repository.begin_update()

        with (
            patch.object(self.repository, "begin_update", return_value=staging),
            patch.object(
                staging,
                "_save_card_catalog",
                wraps=staging._save_card_catalog,
            ) as catalog_save,
            patch.object(
                staging._connection,
                "write_manifest",
                wraps=staging._connection.write_manifest,
            ) as manifest_write,
        ):
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        self.assertEqual(catalog_save.call_count, 2)
        self.assertEqual(
            {
                call_item.kwargs["image_variant"]
                for call_item in catalog_save.call_args_list
            },
            set(CardImageVariant),
        )
        self.assertEqual(staging._pending_card_catalogs, {})
        self.assertEqual(manifest_write.call_count, 1)
        self.assertEqual(
            self.service.get_card_detail(self.manifest, 1).image_name,
            "usr321.bmp",
        )
        self.assertTrue(
            all(
                self.repository.card_image_pair_exists("usr321.bmp") for _unused in (0,)
            )
        )

    def test_trusted_detail_same_name_replacement_writes_only_supplied_variant(self):
        seeded = self.service.get_card_detail(self.manifest, 1).to_draft()
        seeded.image_name = "usr099.bmp"
        seeded.large_image_source, seeded.small_image_source = build_card_image_pair(
            self._image_payload("red")
        )
        seeded.dirty = True
        self.service.save_card_changes(self.manifest, [seeded])
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        catalog_paths = {}
        for variant in CardImageVariant:
            folder = "mini" if variant is CardImageVariant.MINI else "card"
            record = next(
                record
                for record in self.manifest.files
                if record.relative_path.replace("\\", "/") == f"{folder}/list_card.txt"
            )
            catalog_paths[self.repository.resource_path(record)] = variant
        catalog_bytes = {path: path.read_bytes() for path in catalog_paths}
        edited = original.clone()
        replacement_large = self._image_payload("blue")
        edited.large_image_source = replacement_large
        edited.dirty = True
        staging = self.repository.begin_update()

        with (
            patch.object(self.repository, "begin_update", return_value=staging),
            patch.object(
                staging,
                "replace_card_images",
                wraps=staging.replace_card_images,
            ) as replace_images,
            patch.object(
                staging,
                "_save_card_catalog",
                wraps=staging._save_card_catalog,
            ) as catalog_save,
        ):
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        replace_images.assert_called_once_with(
            "usr099.bmp",
            large_source=replacement_large,
            mini_source=None,
        )
        catalog_save.assert_not_called()
        for path, before in catalog_bytes.items():
            self.assertEqual(path.read_bytes(), before)
        large, mini = self.service.load_card_images(self.manifest, "usr099.bmp")
        self.assertNotEqual(large, mini)

    def test_trusted_detail_preflight_checks_unaffected_localized_headers(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.attack = 1900
        edited.dirty = True
        names_record = next(
            record
            for record in self.manifest.files
            if record.relative_path.endswith("card_nameeng.bin")
        )
        self.repository._connection.write_table(
            names_record.workspace_path,
            pd.DataFrame({"wrong": ["", "Dragon"]}),
        )

        with self.assertRaises(CardPersistenceError) as raised:
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        self.assertIn("header mismatch", str(raised.exception))
        self.assertEqual(
            self.repository.get_table("card_properties").iloc[1]["attack"],
            1600,
        )

    def test_trusted_detail_preflight_rejects_reserved_dummy_description(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.attack = 1900
        edited.dirty = True
        descriptions = self.repository.get_table("card_descriptions", language="eng")
        descriptions.at[0, "text"] = ""
        descriptions.at[0, "is_reserved"] = True
        record = next(
            record
            for record in self.manifest.files
            if record.relative_path.endswith("card_desceng.bin")
        )
        self.repository._connection.write_table(
            record.workspace_path,
            descriptions,
            ("text", "is_reserved"),
        )

        with self.assertRaises(CardPersistenceError) as raised:
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        self.assertIn(
            "active indexed text row cannot be reserved",
            str(raised.exception),
        )

    def test_trusted_detail_preflight_rejects_reordered_catalog(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.localized_text.names["eng"] = "Rejected"
        edited.dirty = True
        mini = self.repository.get_table(
            "card_catalog", image_variant=CardImageVariant.MINI
        ).iloc[::-1]
        self.repository.save_table(
            "card_catalog",
            mini,
            image_variant=CardImageVariant.MINI,
        )

        with self.assertRaises(CardPersistenceError) as raised:
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        self.assertIn("reordered", str(raised.exception))

    def test_update_card_without_trusted_original_uses_batch_path(self):
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.attack = 2000
        draft.dirty = True

        with patch.object(
            ProjectRepository,
            "plan_existing_card_update",
            side_effect=AssertionError("unexpected fast path"),
        ):
            self.service.update_card(self.manifest, draft)

        self.assertEqual(self.service.get_card_detail(self.manifest, 1).attack, 2000)

    def test_trusted_detail_localized_field_mapping_targets_one_language(self):
        order = max(record.order for record in self.manifest.files) + 1
        resources = []
        for stem, table in (
            ("card_name", pd.DataFrame({"value": ["", "Ancien"]})),
            (
                "card_desc",
                pd.DataFrame(
                    {
                        "text": ["Dos", "Ancienne description"],
                        "is_reserved": [False, False],
                    }
                ),
            ),
        ):
            relative_path = f"bin#/{stem}fra.bin"
            resources.append(
                ProjectResource(
                    ProjectFileRecord(
                        "Data.dat",
                        relative_path,
                        f"data/{relative_path}",
                        "table",
                        "table",
                        language="fra",
                        order=order,
                    ),
                    table,
                )
            )
            order += 1
        self.manifest.files.extend(self.repository.import_resources(resources))
        self.repository.save(self.manifest)
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.localized_text.names["fra"] = "Nouveau"
        edited.localized_text.descriptions["fra"] = "Nouvelle description"
        edited.dirty = True
        staging = self.repository.begin_update()

        with (
            patch.object(self.repository, "begin_update", return_value=staging),
            patch.object(
                staging._connection,
                "rewrite_csv_rows",
                wraps=staging._connection.rewrite_csv_rows,
            ) as rewrite_rows,
        ):
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        paths = {
            str(call_item.args[0]).replace("\\", "/")
            for call_item in rewrite_rows.call_args_list
        }
        self.assertEqual(
            paths,
            {
                "data/bin#/card_namefra.bin",
                "data/bin#/card_descfra.bin",
            },
        )

    def test_trusted_detail_commit_failure_rolls_back_rows_and_draft(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        edited = original.clone()
        edited.attack = 2100
        edited.dirty = True
        before = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }

        with (
            patch.object(
                self.repository,
                "commit_update",
                side_effect=OSError("controlled fast commit failure"),
            ),
            self.assertRaises(CardPersistenceError),
        ):
            self.service.update_card(
                self.manifest,
                edited,
                original=original,
            )

        after = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertTrue(edited.dirty)
        self.assertEqual(edited.attack, 2100)
        self.assertEqual(self.service.get_card_detail(self.manifest, 1).attack, 1600)

    def test_official_japanese_name_audits_every_cp932_character(self):
        with self.assertRaises(UnicodeEncodeError) as raised:
            OFFICIAL_UNENCODABLE_JAPANESE_NAME.encode("cp932", errors="strict")

        self.assertEqual(raised.exception.start, 0)
        self.assertEqual(raised.exception.end, 1)
        unsupported: list[tuple[int, str, str, str]] = []
        encoded: dict[int, bytes] = {}
        for index, character in enumerate(OFFICIAL_UNENCODABLE_JAPANESE_NAME):
            try:
                encoded[index] = character.encode("cp932", errors="strict")
            except UnicodeEncodeError:
                unsupported.append(
                    (
                        index,
                        character,
                        f"U+{ord(character):04X}",
                        unicodedata.name(character),
                    )
                )

        self.assertEqual(
            unsupported,
            [
                (0, "熒", "U+7192", "CJK UNIFIED IDEOGRAPH-7192"),
                (1, "焅", "U+7105", "CJK UNIFIED IDEOGRAPH-7105"),
            ],
        )
        self.assertEqual(
            encoded,
            {
                2: b"\x90\xb9",
                3: b"\x20",
                4: b"\x83\x41",
                5: b"\x83\x8c",
                6: b"\x83\x4e",
                7: b"\x83\x44",
                8: b"\x83\x58",
            },
        )
        self.assertEqual(
            normalize_card_text_for_encoding(
                OFFICIAL_UNENCODABLE_JAPANESE_NAME,
                "cp932",
            ),
            OFFICIAL_UNENCODABLE_JAPANESE_NAME,
        )

    def test_official_japanese_name_remains_exact_through_suggest_merge(self):
        draft = self.service.create_card_draft(self.manifest)
        draft.localized_text.names["eng"] = "Arequus the Shining Mars Saint"
        reference = CardReferenceData(
            matched_name="Arequus the Shining Mars Saint",
            matched_language="eng",
            localized_names={
                "eng": "Arequus the Shining Mars Saint",
                "jpn": OFFICIAL_UNENCODABLE_JAPANESE_NAME,
            },
            localized_descriptions={},
            canonical_id="21966",
            source="official_card_database",
        )
        self.reference_service.suggest_card_reference.return_value = reference

        suggestion = self.service.suggest_card_draft(
            self.manifest,
            draft,
            include_image=False,
        )

        self.assertIn("name:jpn", suggestion.applied_fields)
        self.assertEqual(
            suggestion.draft.localized_text.names["jpn"],
            OFFICIAL_UNENCODABLE_JAPANESE_NAME,
        )
        self.assertEqual(
            reference.localized_names["jpn"],
            OFFICIAL_UNENCODABLE_JAPANESE_NAME,
        )

    def test_official_japanese_name_reports_identity_and_fails_before_staging(self):
        before = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.localized_text.names["jpn"] = OFFICIAL_UNENCODABLE_JAPANESE_NAME
        draft.dirty = True
        draft.touched_fields.add("name:jpn")
        expected = (
            "Card index 1, ID 2: name:jpn cannot be encoded using cp932 at "
            "character 0: '熒' (U+7192 CJK UNIFIED IDEOGRAPH-7192)."
        )

        self.assertEqual(self.service.validate_card_draft(draft), [expected])
        self.assertEqual(
            draft.localized_text.names["jpn"],
            OFFICIAL_UNENCODABLE_JAPANESE_NAME,
        )
        self.assertTrue(draft.dirty)
        self.assertEqual(draft.touched_fields, {"name:jpn"})

        with (
            patch.object(self.repository, "begin_update") as begin_update,
            self.assertRaises(CardValidationError) as raised,
        ):
            self.service.save_card_changes(self.manifest, [draft])

        begin_update.assert_not_called()
        self.assertEqual(raised.exception.errors, (expected,))
        after = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertEqual(
            draft.localized_text.names["jpn"],
            OFFICIAL_UNENCODABLE_JAPANESE_NAME,
        )
        self.assertTrue(draft.dirty)
        self.assertEqual(draft.touched_fields, {"name:jpn"})

    def test_card_text_normalizer_preserves_directly_encodable_characters(self):
        cp1252_text = "Café • déjà vu"
        cp932_text = "日本語●"
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.localized_text.names["jpn"] = cp932_text
        draft.localized_text.descriptions["jpn"] = "効果●"

        self.assertEqual(
            normalize_card_text_for_encoding(cp1252_text, "cp1252"),
            cp1252_text,
        )
        self.assertEqual(
            normalize_card_text_for_encoding(cp932_text, "cp932"),
            cp932_text,
        )
        self.assertEqual("●".encode("cp932", errors="strict"), b"\x81\x9c")
        errors = self.service.validate_card_draft(draft)
        self.assertFalse(any("name:jpn" in error for error in errors))
        self.assertFalse(any("description:jpn" in error for error in errors))
        self.assertEqual(draft.localized_text.names["jpn"], cp932_text)
        self.assertEqual(draft.localized_text.descriptions["jpn"], "効果●")

    def test_card_text_normalizer_canonicalizes_cp1252_aliases(self):
        for encoding in ("cp1252", "CP1252", "windows-1252", "1252"):
            with self.subTest(encoding=encoding):
                self.assertEqual(
                    normalize_card_text_for_encoding("Before ● After", encoding),
                    "Before • After",
                )

        self.assertEqual(
            normalize_card_text_for_encoding("●", "cp932"),
            "●",
        )
        with self.assertRaises(LookupError):
            normalize_card_text_for_encoding("●", "unknown-card-codec")

    def test_unmapped_card_text_fails_before_staging_without_corruption(self):
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        unsupported = "Unsupported 😀 marker"
        draft.localized_text.descriptions["eng"] = unsupported
        draft.dirty = True
        expected = (
            "Card index 1, ID 2: description:eng cannot be encoded using cp1252 "
            "at character 12: '😀' (U+1F600 GRINNING FACE)."
        )

        with (
            patch.object(self.repository, "begin_update") as begin_update,
            self.assertRaises(CardValidationError) as raised,
        ):
            self.service.save_card_changes(self.manifest, [draft])

        begin_update.assert_not_called()
        self.assertEqual(raised.exception.errors, (expected,))
        self.assertEqual(draft.localized_text.descriptions["eng"], unsupported)
        self.assertIn("😀", draft.localized_text.descriptions["eng"])
        self.assertNotIn("?", draft.localized_text.descriptions["eng"])
        self.assertTrue(draft.dirty)

    def test_card_save_normalizes_syncs_reloads_and_rebuilds_description_pair(self):
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.localized_text.names["eng"] = "Dragon ●"
        draft.localized_text.descriptions["eng"] = BLACK_CIRCLE_DESCRIPTION
        draft.dirty = True
        draft.touched_fields.update({"name:eng", "description:eng"})
        expected_name = "Dragon •"
        expected = normalize_card_text_for_encoding(
            BLACK_CIRCLE_DESCRIPTION,
            "cp1252",
        )

        self.service.save_card_changes(self.manifest, [draft])

        self.assertEqual(draft.localized_text.names["eng"], expected_name)
        self.assertEqual(draft.localized_text.descriptions["eng"], expected)
        self.assertFalse(draft.dirty)
        self.assertEqual(
            draft.touched_fields,
            {"name:eng", "description:eng"},
        )
        reloaded = CardService(
            ProjectRepository(self.repository.root),
            Mock(),
        ).get_card_detail(self.manifest, 1)
        self.assertEqual(reloaded.localized_text.names["eng"], expected_name)
        self.assertEqual(reloaded.localized_text.descriptions["eng"], expected)

        archive = GameRepository.from_root(self.root).encode_archive(
            "Data.dat",
            self.repository.export_resources(
                self.repository.list_resources(
                    self.manifest,
                    include_virtual=True,
                )
            ),
        )
        payloads = {
            entry.relative_path.replace("\\", "/"): entry.data
            for entry in archive.entries
        }
        name_data = payloads["bin#/card_nameeng.bin"]
        description_data = payloads["bin#/card_desceng.bin"]
        index_data = payloads["bin#/card_indxeng.bin"]
        decoded = decode_description_resource(description_data, index_data, "eng")
        decoded_names = GameRepository.decode_binary_resource(
            "bin#/card_nameeng.bin",
            name_data,
            "eng",
        )

        self.assertEqual(len(index_data), 8192)
        self.assertEqual(description_data.count(b"\x95"), 4)
        self.assertEqual(decoded_names.iloc[1]["value"], expected_name)
        self.assertEqual(decoded.iloc[1]["text"], expected)
        self.assertFalse(decoded.iloc[1]["is_reserved"])

    def test_suggest_text_remains_unicode_until_centralized_save(self):
        draft = self.service.create_card_draft(self.manifest)
        draft.localized_text.names["eng"] = "Suggested Card"
        reference = CardReferenceData(
            matched_name="Suggested Card",
            matched_language="eng",
            localized_names={"eng": "Suggested Card"},
            localized_descriptions={"eng": BLACK_CIRCLE_DESCRIPTION},
        )
        self.reference_service.suggest_card_reference.return_value = reference

        suggestion = self.service.suggest_card_draft(
            self.manifest,
            draft,
            include_image=False,
        )
        suggested = suggestion.draft
        self.assertEqual(
            suggested.localized_text.descriptions["eng"],
            BLACK_CIRCLE_DESCRIPTION,
        )
        self.assertEqual(
            reference.localized_descriptions["eng"],
            BLACK_CIRCLE_DESCRIPTION,
        )

        self.service.save_card_changes(self.manifest, [suggested])

        expected = normalize_card_text_for_encoding(
            BLACK_CIRCLE_DESCRIPTION,
            "cp1252",
        )
        self.assertEqual(suggested.localized_text.descriptions["eng"], expected)
        self.assertEqual(
            self.service.get_card_detail(self.manifest, 2).localized_text.descriptions[
                "eng"
            ],
            expected,
        )
        self.assertEqual(
            reference.localized_descriptions["eng"],
            BLACK_CIRCLE_DESCRIPTION,
        )

    def test_late_commit_failure_preserves_unnormalized_draft_and_project(self):
        before = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        draft = self.service.create_card_draft(self.manifest)
        draft.localized_text.descriptions["eng"] = BLACK_CIRCLE_DESCRIPTION
        draft.password = "00abcdef"
        draft.image_name = "usr096.bmp"
        draft.large_image_source, draft.small_image_source = build_card_image_pair(
            self._image_payload("purple")
        )
        draft.dirty = True
        draft.touched_fields.update({"description:eng", "password", "image_name"})
        original_large = draft.large_image_source
        original_small = draft.small_image_source

        with (
            patch.object(
                self.repository,
                "commit_update",
                side_effect=OSError("controlled commit failure"),
            ),
            self.assertRaises(CardPersistenceError),
        ):
            self.service.save_card_changes(self.manifest, [draft])

        after = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertEqual(
            list(
                self.repository.root.parent.glob(
                    f".{self.repository.root.name}.cards.*.tmp"
                )
            ),
            [],
        )
        self.assertEqual(
            draft.localized_text.descriptions["eng"],
            BLACK_CIRCLE_DESCRIPTION,
        )
        self.assertEqual(draft.password, "00abcdef")
        self.assertTrue(draft.dirty)
        self.assertTrue(draft.is_new)
        self.assertEqual(
            draft.touched_fields,
            {"description:eng", "password", "image_name"},
        )
        self.assertIs(draft.large_image_source, original_large)
        self.assertIs(draft.small_image_source, original_small)

    def test_final_logging_failure_does_not_fail_or_desynchronize_committed_save(self):
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.localized_text.descriptions["eng"] = "Effect ●"
        draft.dirty = True
        draft.touched_fields.add("description:eng")
        staging = self.repository.begin_update()
        completion_logs = 0

        def fail_completion_log(message, *_args, **_kwargs):
            nonlocal completion_logs
            if str(message).startswith("Card Save completed:"):
                completion_logs += 1
                raise RuntimeError("controlled completion log failure")

        with (
            patch.object(
                self.repository,
                "begin_update",
                return_value=staging,
            ) as begin_update,
            patch.object(
                self.repository,
                "commit_update",
                wraps=self.repository.commit_update,
            ) as commit_update,
            patch.object(staging, "discard", wraps=staging.discard) as discard,
            patch(
                "yugioh_editor.services.card_service.logging.info",
                side_effect=fail_completion_log,
            ),
        ):
            self.service.save_card_changes(self.manifest, [draft])

        expected = "Effect •"
        begin_update.assert_called_once_with()
        commit_update.assert_called_once_with(staging)
        discard.assert_not_called()
        self.assertEqual(completion_logs, 1)
        self.assertEqual(draft.localized_text.descriptions["eng"], expected)
        self.assertFalse(draft.dirty)
        self.assertFalse(draft.is_new)
        self.assertEqual(draft.touched_fields, {"description:eng"})
        self.assertEqual(
            self.service.get_card_detail(self.manifest, 1).localized_text.descriptions[
                "eng"
            ],
            expected,
        )

    def test_multi_card_validation_failure_is_positional_and_all_or_nothing(self):
        first = self.service.get_card_detail(self.manifest, 0).to_draft()
        second = self.service.get_card_detail(self.manifest, 1).to_draft()
        first.localized_text.descriptions["eng"] = "First ●"
        second.localized_text.descriptions["eng"] = "Second 😀"
        first.dirty = True
        second.dirty = True

        with (
            patch.object(self.repository, "begin_update") as begin_update,
            self.assertRaises(CardValidationError),
        ):
            self.service.save_card_changes(self.manifest, [first, second])

        begin_update.assert_not_called()
        self.assertEqual(first.localized_text.descriptions["eng"], "First ●")
        self.assertEqual(second.localized_text.descriptions["eng"], "Second 😀")
        self.assertTrue(first.dirty)
        self.assertTrue(second.dirty)
        persisted = self.service.load_card_details(self.manifest)
        self.assertEqual(persisted[0].localized_text.descriptions["eng"], "Back")
        self.assertEqual(
            persisted[1].localized_text.descriptions["eng"],
            "Description",
        )

    def test_create_card_defaults_and_atomic_resource_append(self):
        draft = self.service.create_card_draft()
        self.assertEqual(draft.card_index, 2)
        self.assertEqual(draft.card_id, 0)
        self.assertEqual(draft.attribute, "")
        self.assertEqual(draft.card_type, "non_game_card")
        self.assertEqual(draft.card_category, "")
        self.assertEqual(draft.pack, "disabled")
        self.assertEqual(draft.image_name, "token_sl.bmp")
        saved = self.service.create_card(None, draft)
        self.assertEqual(saved.card_index, 2)
        self.assertFalse(draft.is_new)

        for table_name in (
            "card_ids",
            "card_passcodes",
            "card_packs",
            "card_properties",
            "card_names",
            "card_descriptions",
        ):
            parameters = (
                {"language": "eng"}
                if table_name
                in {
                    "card_names",
                    "card_descriptions",
                }
                else {}
            )
            self.assertEqual(
                len(self.repository.get_table(table_name, **parameters)),
                3,
            )
        for variant in CardImageVariant:
            catalog = self.repository.get_table(
                "card_catalog",
                image_variant=variant,
            )
            self.assertEqual(len(catalog), 3)
            self.assertEqual(catalog.iloc[-1]["image_name"], "token_sl.bmp")

    def test_create_card_draft_allows_slot_4094_and_allocates_id_4093(self):
        dummy = self.service.get_card_detail(self.manifest, 0)
        card = self.service.get_card_detail(self.manifest, 1)
        cards = [dummy]
        cards.extend(
            replace(card, card_index=index, card_id=index - 1)
            for index in range(1, 4094)
        )

        with patch.object(self.service, "load_card_details", return_value=cards):
            draft = self.service.create_card_draft(self.manifest)

        self.assertEqual(draft.card_index, 4094)
        self.assertEqual(draft.card_id, 4093)

    def test_create_card_draft_rejects_full_or_missing_dummy_topology(self):
        dummy = self.service.get_card_detail(self.manifest, 0)
        card = self.service.get_card_detail(self.manifest, 1)
        full_cards = [dummy]
        full_cards.extend(
            replace(card, card_index=index, card_id=index - 1)
            for index in range(1, 4095)
        )

        with (
            patch.object(
                self.service,
                "load_card_details",
                return_value=full_cards,
            ),
            self.assertRaisesRegex(CardCapacityError, "4094 active cards"),
        ):
            self.service.create_card_draft(self.manifest)

        with (
            patch.object(self.service, "load_card_details", return_value=[]),
            self.assertRaisesRegex(CardCapacityError, "dummy slot 0"),
        ):
            self.service.create_card_draft(self.manifest)

    def test_new_card_rejects_protected_alias_before_staging(self):
        draft = self.service.create_card_draft(self.manifest)
        draft.card_id = 2000

        errors = self.service.validate_card_draft(draft)
        self.assertTrue(any("protected legacy alias" in error for error in errors))
        with (
            patch.object(self.repository, "begin_update") as begin_update,
            self.assertRaisesRegex(CardValidationError, "protected legacy alias"),
        ):
            self.service.save_card_changes(self.manifest, [draft])
        begin_update.assert_not_called()

    def test_existing_protected_alias_remains_editable(self):
        self.repository.save_table(
            "card_ids",
            pd.DataFrame({"value": [-1, 2000]}),
        )
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.localized_text.names["eng"] = "Existing Alias"
        draft.dirty = True

        self.service.save_card_changes(self.manifest, [draft])

        reloaded = self.service.get_card_detail(self.manifest, 1)
        self.assertEqual(reloaded.card_id, 2000)
        self.assertEqual(reloaded.localized_text.names["eng"], "Existing Alias")

    def test_stale_new_draft_revalidates_capacity_before_staging(self):
        current = pd.DataFrame(
            {
                "card_index": range(4095),
                "card_id": [-1, *range(4094)],
            }
        )
        stale = CardEditDraft(card_index=4094, card_id=4094, is_new=True)

        with (
            patch.object(self.repository, "get_table", return_value=current),
            patch.object(self.repository, "begin_update") as begin_update,
            self.assertRaisesRegex(CardValidationError, "4095 total records"),
        ):
            self.service.save_card_changes(self.manifest, [stale])

        begin_update.assert_not_called()

    def test_corrupt_persisted_id_topology_fails_before_staging(self):
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        invalid_cases = (
            ([-1, 2, 2], "duplicate active Card ID 2"),
            ([-1, 2, 5000], "outside the supported Joey range"),
        )

        for card_ids, message in invalid_cases:
            with (
                self.subTest(message=message),
                patch.object(
                    self.repository,
                    "get_table",
                    return_value=pd.DataFrame(
                        {
                            "card_index": range(len(card_ids)),
                            "card_id": card_ids,
                        }
                    ),
                ),
                patch.object(self.repository, "begin_update") as begin_update,
                self.assertRaisesRegex(CardCapacityError, message),
            ):
                self.service.save_card_changes(self.manifest, [draft])

            begin_update.assert_not_called()

    def test_generated_image_pair_is_bmp_and_catalogs_are_synchronized(self):
        large = self.root / "large.png"
        small = self.root / "small.jpg"
        Image.new("RGB", (80, 120), "red").save(large)
        Image.new("RGB", (30, 40), "blue").save(small)
        draft = self.service.create_card_draft()
        draft.image_name = "usr000.bmp"
        draft.large_image_source = large
        draft.small_image_source = small
        self.service.create_card(None, draft)

        records = [
            record
            for record in self.manifest.files
            if record.relative_path.casefold().endswith("usr000.bmp")
        ]
        self.assertEqual(len(records), 2)
        self.assertTrue(
            all(
                bytes(self.repository.get_resource(record)).startswith(b"BM")
                for record in records
            )
        )
        image_names = [
            self.repository.get_table("card_catalog", image_variant=variant).iloc[-1][
                "image_name"
            ]
            for variant in CardImageVariant
        ]
        self.assertEqual(image_names, ["usr000.bmp", "usr000.bmp"])

    def test_text_only_save_skips_image_inventory_and_writes_manifest_once(self):
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.localized_text.names["eng"] = "Edited Dragon"
        draft.dirty = True
        staging = self.repository.begin_update()

        with (
            patch.object(self.repository, "begin_update", return_value=staging),
            patch.object(staging, "card_image_pair_exists") as pair_exists,
            patch.object(staging, "existing_card_image_names") as inventory,
            patch.object(staging, "add_named_card_images_batch") as image_batch,
            patch.object(staging, "replace_card_image") as replace_image,
            patch.object(
                staging._connection,
                "write_manifest",
                wraps=staging._connection.write_manifest,
            ) as manifest_write,
        ):
            self.service.save_card_changes(self.manifest, [draft])

        pair_exists.assert_not_called()
        inventory.assert_not_called()
        image_batch.assert_not_called()
        replace_image.assert_not_called()
        self.assertEqual(manifest_write.call_count, 1)
        self.assertEqual(
            self.service.get_card_detail(self.manifest, 1).localized_text.names["eng"],
            "Edited Dragon",
        )

    def test_multi_image_save_uses_one_batch_inventory_and_manifest_write(self):
        large, mini = build_card_image_pair(self._image_payload("orange"))
        drafts = [
            self.service.get_card_detail(self.manifest, index).to_draft()
            for index in (0, 1)
        ]
        for offset, draft in enumerate(drafts, start=10):
            draft.image_name = f"usr{offset:03d}.bmp"
            draft.large_image_source = large
            draft.small_image_source = mini
            draft.dirty = True
        staging = self.repository.begin_update()

        with (
            patch.object(self.repository, "begin_update", return_value=staging),
            patch.object(
                staging,
                "add_named_card_images_batch",
                wraps=staging.add_named_card_images_batch,
            ) as image_batch,
            patch.object(
                staging,
                "existing_card_image_names",
                wraps=staging.existing_card_image_names,
            ) as inventory,
            patch.object(
                staging,
                "_get_card_catalog",
                wraps=staging._get_card_catalog,
            ) as catalog_load,
            patch.object(
                staging._connection,
                "write_manifest",
                wraps=staging._connection.write_manifest,
            ) as manifest_write,
        ):
            self.service.save_card_changes(self.manifest, drafts)

        image_batch.assert_called_once()
        self.assertFalse(image_batch.call_args.kwargs["save_manifest"])
        self.assertEqual(
            [item.image_name for item in image_batch.call_args.args[0]],
            ["usr010.bmp", "usr011.bmp"],
        )
        self.assertEqual(inventory.call_count, 1)
        self.assertEqual(catalog_load.call_count, 2)
        self.assertEqual(manifest_write.call_count, 1)
        added = [
            record
            for record in self.manifest.files
            if Path(record.relative_path).name.casefold()
            in {"usr010.bmp", "usr011.bmp"}
        ]
        self.assertEqual(len(added), 4)
        self.assertTrue(all(not record.compressed for record in added))
        self.assertTrue(
            all(
                record.workspace_path is not None
                and (self.repository.root / record.workspace_path).is_file()
                for record in added
            )
        )

    def test_existing_image_replacement_preserves_order_and_compression(self):
        original_large, original_mini = build_card_image_pair(
            self._image_payload("blue")
        )
        seeded = self.service.get_card_detail(self.manifest, 1).to_draft()
        seeded.image_name = "usr019.bmp"
        seeded.large_image_source = original_large
        seeded.small_image_source = original_mini
        seeded.dirty = True
        self.service.save_card_changes(self.manifest, [seeded])
        seeded_records = [
            record
            for record in self.manifest.files
            if Path(record.relative_path).name.casefold() == "usr019.bmp"
        ]
        for record in seeded_records:
            record.compressed = True
        self.repository.save(self.manifest)
        before_count = len(self.manifest.files)
        before_metadata = {
            record.relative_path.casefold(): (record.order, record.compressed)
            for record in seeded_records
        }

        replacement = self.service.get_card_detail(self.manifest, 1).to_draft()
        replacement_large, _unused = build_card_image_pair(
            self._image_payload("purple")
        )
        replacement.large_image_source = replacement_large
        replacement.dirty = True
        self.service.save_card_changes(self.manifest, [replacement])

        after_records = [
            record
            for record in self.manifest.files
            if Path(record.relative_path).name.casefold() == "usr019.bmp"
        ]
        self.assertEqual(len(self.manifest.files), before_count)
        self.assertEqual(
            {
                record.relative_path.casefold(): (record.order, record.compressed)
                for record in after_records
            },
            before_metadata,
        )
        large, mini = self.service.load_card_images(self.manifest, "usr019.bmp")
        self.assertEqual(large, replacement_large)
        self.assertEqual(mini, original_mini)

    def test_same_casefold_image_target_coalesces_latest_complete_pair(self):
        first = self.service.get_card_detail(self.manifest, 0).to_draft()
        second = self.service.get_card_detail(self.manifest, 1).to_draft()
        first_large, first_mini = build_card_image_pair(self._image_payload("red"))
        last_large, last_mini = build_card_image_pair(self._image_payload("green"))
        first.image_name = "usr030.bmp"
        first.large_image_source = first_large
        first.small_image_source = first_mini
        first.dirty = True
        second.image_name = "USR030.BMP"
        second.large_image_source = last_large
        second.small_image_source = last_mini
        second.dirty = True

        self.service.save_card_changes(self.manifest, [first, second])

        self.assertEqual(first.image_name, "USR030.BMP")
        self.assertEqual(second.image_name, "USR030.BMP")
        large, mini = self.service.load_card_images(self.manifest, "usr030.bmp")
        self.assertEqual(large, last_large)
        self.assertEqual(mini, last_mini)
        records = [
            record
            for record in self.manifest.files
            if Path(record.relative_path).name.casefold() == "usr030.bmp"
        ]
        self.assertEqual(len(records), 2)
        self.assertEqual(
            len({record.relative_path.casefold() for record in records}),
            2,
        )
        reloaded = {
            card.card_index: card
            for card in self.service.load_card_details(self.manifest)
        }
        self.assertEqual(reloaded[0].image_name, "USR030.BMP")
        self.assertEqual(reloaded[1].image_name, "USR030.BMP")

    def test_same_image_target_merges_latest_nonempty_variants(self):
        first = self.service.get_card_detail(self.manifest, 0).to_draft()
        second = self.service.get_card_detail(self.manifest, 1).to_draft()
        first_large, _unused_first_mini = build_card_image_pair(
            self._image_payload("navy")
        )
        _unused_last_large, last_mini = build_card_image_pair(
            self._image_payload("orange")
        )
        first.image_name = "usr031.bmp"
        first.large_image_source = first_large
        first.dirty = True
        second.image_name = "USR031.BMP"
        second.small_image_source = last_mini
        second.dirty = True

        self.service.save_card_changes(self.manifest, [first, second])

        large, mini = self.service.load_card_images(self.manifest, "usr031.bmp")
        self.assertEqual(large, first_large)
        self.assertEqual(mini, last_mini)
        self.assertEqual(first.image_name, "USR031.BMP")
        self.assertEqual(second.image_name, "USR031.BMP")

    def test_detail_image_commit_then_list_batch_reuses_existing_pair(self):
        self._canonicalize_fast_path_catalogs()
        original = self.service.get_card_detail(self.manifest, 1).to_draft()
        detail = original.clone()
        detail_large, detail_mini = build_card_image_pair(self._image_payload("blue"))
        detail.image_name = "usr032.bmp"
        detail.large_image_source = detail_large
        detail.small_image_source = detail_mini
        detail.dirty = True

        self.service.update_card(
            self.manifest,
            detail,
            original=original,
        )

        list_draft = self.service.get_card_detail(self.manifest, 0).to_draft()
        list_large, list_mini = build_card_image_pair(self._image_payload("purple"))
        list_draft.image_name = "USR032.BMP"
        list_draft.large_image_source = list_large
        list_draft.small_image_source = list_mini
        list_draft.dirty = True
        self.service.save_card_changes(self.manifest, [list_draft])

        large, mini = self.service.load_card_images(self.manifest, "usr032.bmp")
        self.assertEqual(large, list_large)
        self.assertEqual(mini, list_mini)
        records = [
            record
            for record in self.manifest.files
            if Path(record.relative_path).name.casefold() == "usr032.bmp"
        ]
        self.assertEqual(len(records), 2)
        self.assertEqual(
            len({record.relative_path.casefold() for record in records}),
            2,
        )

    def test_existing_image_overwrite_failure_rolls_back_staging(self):
        seeded = self.service.get_card_detail(self.manifest, 1).to_draft()
        original_large, original_mini = build_card_image_pair(
            self._image_payload("teal")
        )
        seeded.image_name = "usr033.bmp"
        seeded.large_image_source = original_large
        seeded.small_image_source = original_mini
        seeded.dirty = True
        self.service.save_card_changes(self.manifest, [seeded])
        before = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }

        replacement = self.service.get_card_detail(self.manifest, 1).to_draft()
        replacement.large_image_source, replacement.small_image_source = (
            build_card_image_pair(self._image_payload("yellow"))
        )
        replacement.dirty = True
        staging = self.repository.begin_update()
        with (
            patch.object(self.repository, "begin_update", return_value=staging),
            patch.object(
                staging,
                "save_table",
                side_effect=OSError("controlled table failure"),
            ),
            self.assertRaises(CardPersistenceError),
        ):
            self.service.save_card_changes(self.manifest, [replacement])

        after = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertTrue(replacement.dirty)
        self.assertIsNotNone(replacement.large_image_source)
        self.assertIsNotNone(replacement.small_image_source)
        large, mini = self.service.load_card_images(self.manifest, "usr033.bmp")
        self.assertEqual(large, original_large)
        self.assertEqual(mini, original_mini)

    def test_mixed_image_save_preserves_replacement_metadata_and_reloads(self):
        original_large, original_mini = build_card_image_pair(
            self._image_payload("blue")
        )
        seeded = self.service.get_card_detail(self.manifest, 1).to_draft()
        seeded.image_name = "usr020.bmp"
        seeded.large_image_source = original_large
        seeded.small_image_source = original_mini
        seeded.dirty = True
        self.service.save_card_changes(self.manifest, [seeded])
        seeded_records = [
            record
            for record in self.manifest.files
            if Path(record.relative_path).name.casefold() == "usr020.bmp"
        ]
        self.assertEqual(len(seeded_records), 2)
        for record in seeded_records:
            record.compressed = True
        self.repository.save(self.manifest)
        original_metadata = {
            record.relative_path.casefold(): (record.order, record.compressed)
            for record in seeded_records
        }
        original_data_paths = [
            record.relative_path
            for record in sorted(
                (
                    record
                    for record in self.manifest.files
                    if record.source_file.casefold() == "data.dat"
                ),
                key=lambda record: record.order,
            )
        ]

        replacement = self.service.get_card_detail(self.manifest, 1).to_draft()
        replacement_large, _unused = build_card_image_pair(
            self._image_payload("purple")
        )
        replacement.large_image_source = replacement_large
        replacement.dirty = True
        text_only = self.service.get_card_detail(self.manifest, 0).to_draft()
        text_only.localized_text.names["eng"] = "Edited Back"
        text_only.dirty = True
        new_card = self.service.create_card_draft(self.manifest)
        new_large, new_mini = build_card_image_pair(self._image_payload("green"))
        new_card.image_name = "usr021.bmp"
        new_card.large_image_source = new_large
        new_card.small_image_source = new_mini
        staging = self.repository.begin_update()

        with (
            patch.object(self.repository, "begin_update", return_value=staging),
            patch.object(
                staging,
                "add_named_card_images_batch",
                wraps=staging.add_named_card_images_batch,
            ) as image_batch,
            patch.object(
                staging,
                "replace_card_images",
                wraps=staging.replace_card_images,
            ) as replace_images,
            patch.object(
                staging._connection,
                "write_manifest",
                wraps=staging._connection.write_manifest,
            ) as manifest_write,
        ):
            self.service.save_card_changes(
                self.manifest,
                [replacement, text_only, new_card],
            )

        image_batch.assert_called_once()
        self.assertEqual(
            [item.image_name for item in image_batch.call_args.args[0]],
            ["usr021.bmp"],
        )
        replace_images.assert_called_once_with(
            "usr020.bmp",
            large_source=replacement_large,
            mini_source=None,
        )
        self.assertEqual(manifest_write.call_count, 1)
        replacement_records = [
            record
            for record in self.manifest.files
            if Path(record.relative_path).name.casefold() == "usr020.bmp"
        ]
        self.assertEqual(len(replacement_records), 2)
        self.assertEqual(
            {
                record.relative_path.casefold(): record.compressed
                for record in replacement_records
            },
            {
                path: compressed
                for path, (_order, compressed) in original_metadata.items()
            },
        )
        new_records = [
            record
            for record in self.manifest.files
            if Path(record.relative_path).name.casefold() == "usr021.bmp"
        ]
        self.assertEqual(len(new_records), 2)
        self.assertTrue(all(not record.compressed for record in new_records))
        data_orders = sorted(
            record.order
            for record in self.manifest.files
            if record.source_file.casefold() == "data.dat"
        )
        self.assertEqual(data_orders, list(range(len(data_orders))))
        data_paths_after = [
            record.relative_path
            for record in sorted(
                (
                    record
                    for record in self.manifest.files
                    if record.source_file.casefold() == "data.dat"
                ),
                key=lambda record: record.order,
            )
        ]
        old_paths_after = [
            path
            for path in data_paths_after
            if Path(path).name.casefold() != "usr021.bmp"
        ]
        self.assertCountEqual(old_paths_after, original_data_paths)
        self.assertEqual(
            data_paths_after,
            sorted(
                data_paths_after,
                key=lambda path: normalize_project_path(path).as_posix().casefold(),
            ),
        )

        reloaded_repository = ProjectRepository(self.repository.root)
        reloaded_manifest = reloaded_repository.load()
        reloaded_service = CardService(reloaded_repository, Mock())
        self.assertEqual(
            reloaded_service.get_card_detail(reloaded_manifest, 0).localized_text.names[
                "eng"
            ],
            "Edited Back",
        )
        self.assertEqual(
            reloaded_service.get_card_detail(reloaded_manifest, 1).image_name,
            "usr020.bmp",
        )
        self.assertEqual(
            reloaded_service.get_card_detail(reloaded_manifest, 2).image_name,
            "usr021.bmp",
        )
        reloaded_large, reloaded_mini = reloaded_service.load_card_images(
            reloaded_manifest,
            "usr020.bmp",
        )
        self.assertEqual(reloaded_large, replacement_large)
        self.assertEqual(reloaded_mini, original_mini)

    def test_transaction_failure_preserves_all_original_resources(self):
        before = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        draft = self.service.get_card_detail(None, 1).to_draft()
        draft.attack = 2400
        draft.image_name = "usr099.bmp"
        draft.large_image_source = self.root / "rollback-large.png"
        draft.small_image_source = self.root / "rollback-small.png"
        Image.new("RGB", (80, 120), "red").save(draft.large_image_source)
        Image.new("RGB", (30, 40), "blue").save(draft.small_image_source)
        draft.dirty = True
        with (
            patch.object(
                ProjectRepository,
                "_save_cards",
                side_effect=OSError("controlled write failure"),
            ),
            self.assertRaises(CardPersistenceError),
        ):
            self.service.save_card_changes(None, [draft])
        after = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertEqual(
            list(
                self.repository.root.parent.glob(
                    f".{self.repository.root.name}.cards.*.tmp"
                )
            ),
            [],
        )
        self.assertFalse(
            any(
                path.name.casefold() == "usr099.bmp"
                for path in self.repository.root.rglob("*")
            )
        )

    def test_catalog_save_failure_discards_staging_and_restores_original_order(self):
        before = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        original_orders = [
            (record.source_file, record.relative_path, record.order)
            for record in self.manifest.files
        ]
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.image_name = "usr097.bmp"
        draft.large_image_source, draft.small_image_source = build_card_image_pair(
            self._image_payload("orange")
        )
        draft.dirty = True
        original_save_catalog = ProjectRepository._save_card_catalog
        catalog_calls: list[CardImageVariant] = []

        def fail_second_catalog(
            repository,
            table,
            *,
            image_variant=CardImageVariant.LARGE,
        ):
            catalog_calls.append(image_variant)
            if len(catalog_calls) == 2:
                raise OSError("controlled catalog save failure")
            return original_save_catalog(
                repository,
                table,
                image_variant=image_variant,
            )

        with (
            patch.object(
                ProjectRepository,
                "_save_card_catalog",
                new=fail_second_catalog,
            ),
            self.assertRaises(CardPersistenceError),
        ):
            self.service.save_card_changes(self.manifest, [draft])

        after = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(catalog_calls, list(CardImageVariant))
        self.assertEqual(after, before)
        self.assertEqual(
            [
                (record.source_file, record.relative_path, record.order)
                for record in self.manifest.files
            ],
            original_orders,
        )
        self.assertEqual(
            list(
                self.repository.root.parent.glob(
                    f".{self.repository.root.name}.cards.*.tmp"
                )
            ),
            [],
        )
        self.assertFalse(
            any(
                path.name.casefold() == "usr097.bmp"
                for path in self.repository.root.rglob("*")
            )
        )
        self.assertTrue(draft.dirty)
        self.assertIsNotNone(draft.large_image_source)
        self.assertIsNotNone(draft.small_image_source)

    def test_commit_failure_discards_complete_staging_and_preserves_project(self):
        before = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.localized_text.names["eng"] = "Never committed"
        draft.image_name = "usr098.bmp"
        draft.large_image_source, draft.small_image_source = build_card_image_pair(
            self._image_payload("yellow")
        )
        draft.dirty = True

        with (
            patch.object(
                self.repository,
                "commit_update",
                side_effect=OSError("controlled commit failure"),
            ),
            self.assertRaises(CardPersistenceError),
        ):
            self.service.save_card_changes(self.manifest, [draft])

        after = {
            path.relative_to(self.repository.root).as_posix(): path.read_bytes()
            for path in self.repository.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertEqual(
            list(
                self.repository.root.parent.glob(
                    f".{self.repository.root.name}.cards.*.tmp"
                )
            ),
            [],
        )
        self.assertEqual(draft.image_name, "usr098.bmp")
        self.assertIsNotNone(draft.large_image_source)

    def test_export_import_uses_id_and_keeps_immutable_fields(self):
        cards = [detail.to_draft() for detail in self.service.load_card_details()]
        cards[1].localized_text.names["eng"] = "Staged"
        export_path = self.root / "cards.csv"
        self.service.export_cards_csv(None, export_path, cards)
        payload = export_path.read_bytes()
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(
            payload.decode("utf-8-sig").splitlines()[0],
            ",".join(CARD_CSV_COLUMNS),
        )

        frame = self.repository.read_external_table(export_path)
        frame.loc[1, "card_index"] = "999"
        frame.loc[1, "image_name"] = "bypass.bmp"
        frame.loc[1, "name_eng"] = "Imported"
        frame.loc[1, "desc_eng"] = ""
        unknown = frame.iloc[1].copy()
        unknown["card_id"] = "9999"
        frame.loc[len(frame)] = unknown
        self.repository.write_external_table(export_path, frame, CARD_CSV_COLUMNS)

        parsed = self.service.parse_card_import_csv(None, export_path)
        result = self.service.apply_import_to_drafts(
            parsed,
            self.service.load_card_details(),
        )
        imported = next(card for card in result.cards if card.card_id == 2)
        self.assertEqual(imported.card_index, 1)
        self.assertEqual(imported.image_name, "")
        self.assertEqual(imported.localized_text.names["eng"], "Imported")
        self.assertEqual(imported.localized_text.descriptions["eng"], "")
        self.assertEqual(result.skipped_unknown_ids, 1)
        self.assertEqual(result.ignored_image_name_changes, 1)
        self.assertEqual(
            self.repository.get_table("card_names", language="eng").iloc[1]["value"],
            "Dragon",
        )

    def test_duplicate_csv_id_aborts_before_apply(self):
        path = self.root / "duplicate.csv"
        cards = self.service.load_card_details()
        self.service.export_cards_csv(None, path, cards)
        frame = self.repository.read_external_table(path)
        frame.loc[1, "card_id"] = frame.loc[0, "card_id"]
        self.repository.write_external_table(path, frame, CARD_CSV_COLUMNS)
        parsed = self.service.parse_card_import_csv(None, path)
        with self.assertRaises(CardImportError):
            self.service.apply_import_to_drafts(
                parsed,
                self.service.load_card_details(),
            )

    def test_reference_fill_respects_existing_and_touched_values(self):
        draft = self.service.get_card_detail(None, 1).to_draft()
        draft.localized_text.names["jpn"] = ""
        draft.localized_text.descriptions["eng"] = ""
        draft.touched_fields.add("description:eng")
        reference = CardReferenceData(
            matched_name="Dragon",
            matched_language="eng",
            localized_names={"eng": "Overwrite", "jpn": "ãƒ‰ãƒ©ã‚´ãƒ³"},
            localized_descriptions={"eng": "Suggested"},
            attack=3000,
            source="test",
            confidence="exact",
        )
        applied = self.service.apply_reference_to_draft(draft, reference)
        self.assertEqual(draft.localized_text.names["eng"], "Dragon")
        self.assertEqual(draft.localized_text.names["jpn"], "ãƒ‰ãƒ©ã‚´ãƒ³")
        self.assertEqual(draft.localized_text.descriptions["eng"], "")
        self.assertEqual(draft.attack, 1600)
        self.assertEqual(applied, ("name:jpn",))

    def test_suggestion_query_uses_shared_language_priority(self):
        draft = self.service.get_card_detail(None, 1).to_draft()
        draft.localized_text.names = {language: "" for language in LANGUAGE_PREFIXES}
        draft.localized_text.names["fra"] = "Nom francais"
        draft.localized_text.names["jpn"] = "Japanese"
        draft.localized_text.names["eng"] = "  English query  "
        self.assertEqual(
            self.service.select_suggestion_query(draft),
            ("  English query  ", "eng"),
        )
        draft.localized_text.names["eng"] = ""
        self.assertEqual(
            self.service.select_suggestion_query(draft),
            ("Japanese", "jpn"),
        )
        draft.localized_text.names["jpn"] = ""
        self.assertEqual(
            self.service.select_suggestion_query(draft),
            ("Nom francais", "fra"),
        )

    def test_detail_suggestion_stages_defaults_properties_and_image_in_memory(self):
        draft = self.service.create_card_draft()
        draft.localized_text.names["eng"] = "Suggested Card"
        draft.pack = ""
        reference = CardReferenceData(
            "Suggested Card",
            "eng",
            {"eng": "Suggested Card", "fra": "Carte suggeree"},
            {"eng": "Official text"},
            level=7,
            attack=2400,
            defense=1800,
            attribute="light",
            card_type="spellcaster",
            card_category="effect",
        )
        self.reference_service.suggest_card_reference.return_value = reference
        self.reference_service.generate_card_image_name.return_value = "usr000.bmp"
        original = BytesIO()
        Image.new("RGBA", (320, 460), "red").save(original, format="PNG")
        self.reference_service.crawl_card_image.return_value = original.getvalue()

        result = self.service.suggest_card_draft(None, draft)

        self.assertEqual(result.draft.password, "FFFFFFFF")
        self.assertEqual(result.draft.pack, "disabled")
        self.assertEqual(result.draft.level, 7)
        self.assertEqual(result.draft.attack, 2400)
        self.assertEqual(result.draft.attribute, "light")
        self.assertEqual(result.draft.card_type, "spellcaster")
        self.assertEqual(result.draft.card_category, "effect")
        self.assertEqual(result.draft.image_name, "usr000.bmp")
        self.assertTrue(result.image_staged)
        for payload, size in (
            (result.draft.large_image_source, (200, 290)),
            (result.draft.small_image_source, (50, 72)),
        ):
            self.assertIsInstance(payload, bytes)
            with Image.open(BytesIO(payload)) as image:
                self.assertEqual(image.format, "BMP")
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.size, size)
        self.assertFalse(
            any(path.name == "usr000.bmp" for path in self.root.rglob("*"))
        )
        self.service.create_card(None, result.draft)
        records = {
            record.workspace_path
            for record in self.manifest.files
            if record.relative_path.endswith("usr000.bmp")
        }
        self.assertEqual(records, {"data/card/usr000.bmp", "data/mini/usr000.bmp"})

    def test_detail_suggestion_skips_casefolded_pending_list_reservation(self):
        draft = self.service.create_card_draft()
        draft.localized_text.names["eng"] = "Reserved Image Card"
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            matched_name="Reserved Image Card",
            matched_language="eng",
            localized_names={"eng": "Reserved Image Card"},
            localized_descriptions={"eng": "Reference text"},
        )
        self.reference_service.generate_card_image_name.side_effect = (
            generate_unique_card_image_name
        )
        source = BytesIO()
        Image.new("RGB", (320, 460), "blue").save(source, format="PNG")
        self.reference_service.crawl_card_image.return_value = source.getvalue()

        result = self.service.suggest_card_draft(
            self.manifest,
            draft,
            additional_reserved_image_names=("USR000.BMP",),
        )

        self.assertTrue(result.image_staged)
        self.assertEqual(result.draft.image_name, "usr001.bmp")
        reserved = self.reference_service.generate_card_image_name.call_args.args[0]
        self.assertIn("usr000.bmp", reserved)

    def test_japanese_suggestion_uses_canonical_english_name_for_image(self):
        draft = self.service.create_card_draft()
        draft.localized_text.names["jpn"] = "仮面魔獣デス・ガーディウス"
        reference = CardReferenceData(
            matched_name="仮面魔獣デス・ガーディウス",
            matched_language="jpn",
            localized_names={
                "eng": "Masked Beast Des Gardius",
                "jpn": "仮面魔獣デス・ガーディウス",
            },
            localized_descriptions={"eng": "Official text"},
        )
        self.reference_service.suggest_card_reference.return_value = reference
        self.reference_service.generate_card_image_name.return_value = "usr000.bmp"
        original = BytesIO()
        Image.new("RGB", (320, 460), "red").save(original, format="PNG")
        self.reference_service.crawl_card_image.return_value = original.getvalue()

        result = self.service.suggest_card_draft(
            None,
            draft,
            preferred_language="jpn",
        )

        self.assertTrue(result.image_staged)
        self.reference_service.suggest_card_reference.assert_called_once_with(
            "仮面魔獣デス・ガーディウス",
            "jpn",
        )
        self.reference_service.crawl_card_image.assert_called_once_with(
            "Masked Beast Des Gardius"
        )

    def test_reference_password_fill_is_canonical_and_respects_existing_or_touched(
        self,
    ):
        reference = CardReferenceData(
            matched_name="Card",
            matched_language="eng",
            localized_names={},
            localized_descriptions={},
            password="00abcdef",
        )

        missing = self.service.create_card_draft()
        applied = self.service.apply_reference_to_draft(missing, reference)
        self.assertEqual(missing.password, "00ABCDEF")
        self.assertIn("password", applied)

        existing = self.service.create_card_draft()
        existing.password = "12345678"
        applied = self.service.apply_reference_to_draft(existing, reference)
        self.assertEqual(existing.password, "12345678")
        self.assertNotIn("password", applied)

        touched = self.service.create_card_draft()
        touched.touched_fields.add("password")
        applied = self.service.apply_reference_to_draft(touched, reference)
        self.assertEqual(touched.password, "FFFFFFFF")
        self.assertNotIn("password", applied)

    def test_invalid_reference_password_is_ignored(self):
        for provider_password in ("not-a-password", "FFFFFFFF"):
            with self.subTest(provider_password=provider_password):
                draft = self.service.create_card_draft()
                reference = CardReferenceData(
                    matched_name="Card",
                    matched_language="eng",
                    localized_names={},
                    localized_descriptions={},
                    password=provider_password,
                )

                applied = self.service.apply_reference_to_draft(draft, reference)

                self.assertEqual(draft.password, "FFFFFFFF")
                self.assertNotIn("password", applied)

    def test_new_reference_password_is_used_immediately_for_direct_image_lookup(self):
        draft = self.service.create_card_draft()
        draft.localized_text.names["eng"] = "Search Name"
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            matched_name="Canonical Name",
            matched_language="eng",
            localized_names={"eng": "Canonical Name"},
            localized_descriptions={},
            password="00abcdef",
        )
        self.reference_service.generate_card_image_name.return_value = "usr000.bmp"
        original = BytesIO()
        Image.new("RGB", (320, 460), "red").save(original, format="PNG")
        self.reference_service.crawl_card_image_by_password.return_value = (
            original.getvalue()
        )

        result = self.service.suggest_card_draft(None, draft)

        self.assertEqual(result.draft.password, "00ABCDEF")
        self.assertTrue(result.image_staged)
        self.reference_service.crawl_card_image_by_password.assert_called_once_with(
            "00ABCDEF"
        )
        self.reference_service.crawl_card_image.assert_not_called()

    def test_suggested_password_survives_save_and_repository_reload(self):
        draft = self.service.create_card_draft()
        draft.localized_text.names["eng"] = "Persisted Password Card"
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            matched_name="Persisted Password Card",
            matched_language="eng",
            localized_names={"eng": "Persisted Password Card"},
            localized_descriptions={},
            password="00abcdef",
        )

        suggested = self.service.suggest_card_draft(
            None,
            draft,
            include_image=False,
        ).draft
        saved = self.service.create_card(None, suggested)

        reloaded_repository = ProjectRepository(self.repository.root)
        reloaded_manifest = reloaded_repository.load()
        reloaded_service = CardService(reloaded_repository, Mock())
        reloaded = reloaded_service.get_card_detail(
            reloaded_manifest,
            saved.card_index,
        )
        self.assertEqual(reloaded.password, "00ABCDEF")
        self.assertEqual(
            reloaded_repository.get_table("card_passcodes").iloc[-1]["value"],
            "00ABCDEF",
        )

    def test_existing_valid_password_has_priority_over_reference_for_direct_image(self):
        draft = self.service.create_card_draft()
        draft.password = "12345678"
        draft.localized_text.names["eng"] = "Search Name"
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            matched_name="Canonical Name",
            matched_language="eng",
            localized_names={"eng": "Canonical Name"},
            localized_descriptions={},
            password="ABCDEF00",
        )
        self.reference_service.generate_card_image_name.return_value = "usr000.bmp"
        original = BytesIO()
        Image.new("RGB", (320, 460), "red").save(original, format="PNG")
        self.reference_service.crawl_card_image_by_password.return_value = (
            original.getvalue()
        )

        result = self.service.suggest_card_draft(None, draft)

        self.assertEqual(result.draft.password, "12345678")
        self.assertTrue(result.image_staged)
        self.reference_service.crawl_card_image_by_password.assert_called_once_with(
            "12345678"
        )
        self.reference_service.crawl_card_image.assert_not_called()

    def test_direct_image_failure_falls_back_to_canonical_english_name(self):
        draft = self.service.create_card_draft()
        draft.localized_text.names["jpn"] = "Japanese Query"
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            matched_name="Japanese Query",
            matched_language="jpn",
            localized_names={"eng": "Canonical English"},
            localized_descriptions={},
            password="00ABCDEF",
        )
        self.reference_service.generate_card_image_name.return_value = "usr000.bmp"
        self.reference_service.crawl_card_image_by_password.side_effect = OSError(
            "direct unavailable"
        )
        original = BytesIO()
        Image.new("RGB", (320, 460), "red").save(original, format="PNG")
        self.reference_service.crawl_card_image.return_value = original.getvalue()

        result = self.service.suggest_card_draft(
            None,
            draft,
            preferred_language="jpn",
        )

        self.assertTrue(result.image_staged)
        self.reference_service.crawl_card_image_by_password.assert_called_once_with(
            "00ABCDEF"
        )
        self.reference_service.crawl_card_image.assert_called_once_with(
            "Canonical English"
        )

    def test_direct_image_conversion_failure_falls_back_once_to_canonical_name(self):
        draft = self.service.create_card_draft()
        draft.localized_text.names["eng"] = "Lookup Name"
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            matched_name="Canonical English",
            matched_language="eng",
            localized_names={"eng": "Canonical English"},
            localized_descriptions={},
            password="00ABCDEF",
        )
        self.reference_service.generate_card_image_name.return_value = "usr000.bmp"
        direct_image = BytesIO()
        Image.new("RGB", (320, 460), "red").save(direct_image, format="PNG")
        name_image = BytesIO()
        Image.new("RGB", (320, 460), "blue").save(name_image, format="PNG")
        direct_payload = direct_image.getvalue()
        name_payload = name_image.getvalue()
        self.reference_service.crawl_card_image_by_password.return_value = (
            direct_payload
        )
        self.reference_service.crawl_card_image.return_value = name_payload

        with patch(
            "yugioh_editor.services.card_service.build_card_image_pair",
            side_effect=[ValueError("direct conversion failed"), (b"large", b"small")],
        ) as converter:
            result = self.service.suggest_card_draft(None, draft)

        self.assertTrue(result.image_staged)
        self.assertIsNone(result.image_error)
        self.assertEqual(result.draft.large_image_source, b"large")
        self.assertEqual(result.draft.small_image_source, b"small")
        self.reference_service.crawl_card_image_by_password.assert_called_once_with(
            "00ABCDEF"
        )
        self.reference_service.crawl_card_image.assert_called_once_with(
            "Canonical English"
        )
        self.assertEqual(
            converter.call_args_list,
            [call(direct_payload), call(name_payload)],
        )

    def test_include_image_false_never_calls_any_image_operation(self):
        draft = self.service.create_card_draft()
        draft.localized_text.names["eng"] = "Search Name"
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            matched_name="Canonical Name",
            matched_language="eng",
            localized_names={"eng": "Canonical Name"},
            localized_descriptions={},
            password="00ABCDEF",
        )

        result = self.service.suggest_card_draft(None, draft, include_image=False)

        self.assertEqual(result.draft.password, "00ABCDEF")
        self.assertFalse(result.image_staged)
        self.reference_service.generate_card_image_name.assert_not_called()
        self.reference_service.crawl_card_image_by_password.assert_not_called()
        self.reference_service.crawl_card_image.assert_not_called()

    def test_detail_suggestion_image_failure_keeps_data_and_token(self):
        draft = self.service.create_card_draft()
        draft.localized_text.names["eng"] = "Suggested Card"
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            "Suggested Card",
            "eng",
            {"fra": "Carte suggeree"},
            {"eng": "Official text"},
            attack=2500,
        )
        self.reference_service.generate_card_image_name.return_value = "usr000.bmp"
        self.reference_service.crawl_card_image.side_effect = OSError("offline")

        result = self.service.suggest_card_draft(None, draft)

        self.assertTrue(result.reference_found)
        self.assertFalse(result.image_staged)
        self.assertIn("offline", result.image_error)
        self.assertEqual(result.draft.attack, 2500)
        self.assertEqual(
            result.draft.localized_text.descriptions["eng"], "Official text"
        )
        self.assertEqual(result.draft.image_name, "token_sl.bmp")
        self.assertIsNone(result.draft.large_image_source)
        self.assertIsNone(result.draft.small_image_source)

    def test_edit_suggestion_preserves_zero_and_valid_existing_properties(self):
        draft = self.service.get_card_detail(None, 1).to_draft()
        draft.level = 0
        draft.attack = 0
        draft.defense = 0
        draft.attribute = "dark"
        draft.card_type = "warrior"
        draft.card_category = "ritual"
        reference = CardReferenceData(
            "Dragon",
            "eng",
            {},
            {},
            level=8,
            attack=3000,
            defense=2500,
            attribute="light",
            card_type="dragon",
            card_category="effect",
        )
        self.service.apply_reference_to_draft(draft, reference)
        self.assertEqual((draft.level, draft.attack, draft.defense), (0, 0, 0))
        self.assertEqual(draft.attribute, "dark")
        self.assertEqual(draft.card_type, "warrior")
        self.assertEqual(draft.card_category, "ritual")

    def test_create_suggestion_does_not_replace_touched_placeholders(self):
        draft = self.service.create_card_draft()
        draft.touched_fields.update({"attribute", "card_type", "card_category"})
        reference = CardReferenceData(
            "Card",
            "eng",
            {},
            {},
            attribute="light",
            card_type="dragon",
            card_category="effect",
        )
        self.service.apply_reference_to_draft(draft, reference)
        self.assertEqual(draft.attribute, "")
        self.assertEqual(draft.card_type, "non_game_card")
        self.assertEqual(draft.card_category, "")

    def test_semantically_complete_monster_spell_and_trap_are_prefiltered(self):
        cards = (
            self._complete_suggest_card(30, kind="monster"),
            self._complete_suggest_card(31, kind="spell"),
            self._complete_suggest_card(32, kind="trap"),
        )
        progress = []

        result = self.service.bulk_suggest_missing_text(
            cards,
            report_progress=lambda done, total: progress.append((done, total)),
        )

        self.assertEqual(result.total_source_cards, 3)
        self.assertEqual(result.total_candidates, 0)
        self.assertEqual(result.skipped_complete, 3)
        self.assertEqual(result.selected_workers, 0)
        self.assertEqual(progress, [(0, 0)])
        self.reference_service.suggest_card_reference.assert_not_called()
        self.reference_service.crawl_card_image_by_password.assert_not_called()
        self.reference_service.crawl_card_image.assert_not_called()

    def test_semantic_candidate_fields_follow_raw_card_kind(self):
        spell = self._complete_suggest_card(33, kind="spell")
        spell.card_category = ""
        spell.card_category_code = 7
        spell_with_stale_label = self._complete_suggest_card(40, kind="spell")
        spell_with_stale_label.card_category = "normal"
        spell_with_stale_label.card_category_code = 7
        trap = self._complete_suggest_card(34, kind="trap")
        trap.localized_text.descriptions["fra"] = ""
        monster = self._complete_suggest_card(35, kind="monster")
        monster.attack = None
        raw_spell_with_monster_label = self._complete_suggest_card(36, kind="spell")
        raw_spell_with_monster_label.card_type = "dragon"
        raw_spell_with_monster_label.level = None
        raw_spell_with_monster_label.attack = None
        raw_spell_with_monster_label.defense = None
        raw_spell_with_monster_label.attribute = ""

        self.assertEqual(
            self.service.missing_suggest_fields(spell),
            ("card_category",),
        )
        self.assertEqual(
            self.service.missing_suggest_fields(spell_with_stale_label),
            ("card_category",),
        )
        self.assertEqual(
            self.service.missing_suggest_fields(trap),
            ("description:fra",),
        )
        self.assertEqual(
            self.service.missing_suggest_fields(monster),
            ("attack",),
        )
        self.assertFalse(
            self.service.is_suggest_candidate(raw_spell_with_monster_label)
        )

    def test_semantic_candidate_requires_a_writable_missing_field(self):
        touched_only = self._complete_suggest_card(37)
        touched_only.attack = None
        touched_only.touched_fields.add("attack")
        missing_text = self._complete_suggest_card(38)
        missing_text.localized_text.names["jpn"] = ""
        image_only = self._complete_suggest_card(
            39,
            image_name="ToKeN_sL.BmP",
        )

        self.assertFalse(self.service.is_suggest_candidate(touched_only))
        self.assertEqual(
            self.service.missing_suggest_fields(missing_text),
            ("name:jpn",),
        )
        self.assertEqual(
            self.service.missing_suggest_fields(image_only),
            ("image",),
        )

        result = self.service.bulk_suggest_missing_text((touched_only,))
        self.assertEqual(result.total_candidates, 0)
        self.reference_service.suggest_card_reference.assert_not_called()

    def test_bulk_suggest_uses_each_query_language_and_preserves_existing_values(self):
        cards = [detail.to_draft() for detail in self.service.load_card_details()]
        cards[0].localized_text.names["jpn"] = "ãƒãƒƒã‚¯"
        cards[1].localized_text.names["eng"] = "Dragon"
        self.reference_service.suggest_card_reference.side_effect = [
            CardReferenceData(
                "ãƒãƒƒã‚¯",
                "jpn",
                {"eng": "Back"},
                {},
                attack=999,
                source="test",
                confidence="exact",
            ),
            CardReferenceData(
                "Dragon",
                "eng",
                {"jpn": "ãƒ‰ãƒ©ã‚´ãƒ³"},
                {},
                attack=999,
                source="test",
                confidence="exact",
            ),
        ]
        progress = []
        result = self.service.bulk_suggest_missing_text(
            cards,
            report_progress=lambda done, total: progress.append((done, total)),
        )
        self.assertEqual(
            self.reference_service.suggest_card_reference.call_args_list[0].args,
            ("ãƒãƒƒã‚¯", "jpn"),
        )
        self.assertEqual(
            self.reference_service.suggest_card_reference.call_args_list[1].args,
            ("Dragon", "eng"),
        )
        self.assertEqual(result.cards[1].attack, 1600)
        self.assertTrue(progress)

    def test_bulk_suggest_fills_missing_data_and_stages_distinct_images(self):
        first = CardEditDraft(
            card_index=10,
            card_id=10,
            localized_text=CardLocalizedText(names={"eng": "First Query"}),
            password="FFFFFFFF",
            level=None,
            attack=None,
            defense=None,
            attribute="",
            card_type="",
            card_category="",
        )
        second = CardEditDraft(
            card_index=11,
            card_id=11,
            localized_text=CardLocalizedText(
                names={
                    language: f"Existing name {language}"
                    for language in LANGUAGE_PREFIXES
                },
                descriptions={
                    language: (
                        "" if language == "fra" else f"Existing description {language}"
                    )
                    for language in LANGUAGE_PREFIXES
                },
            ),
            password="12345678",
            level=0,
            attack=0,
            defense=0,
            attribute="dark",
            card_type="dragon",
            card_category="normal",
        )
        first_reference = CardReferenceData(
            matched_name="First Query",
            matched_language="eng",
            localized_names={
                language: f"Suggested name {language}" for language in LANGUAGE_PREFIXES
            },
            localized_descriptions={
                language: f"Suggested description {language}"
                for language in LANGUAGE_PREFIXES
            },
            password="00abcdef",
            level=0,
            attack=0,
            defense=0,
            attribute="light",
            card_type="spellcaster",
            card_category="effect",
        )
        second_reference = CardReferenceData(
            matched_name="Existing name eng",
            matched_language="eng",
            localized_names={
                language: f"Replacement name {language}"
                for language in LANGUAGE_PREFIXES
            },
            localized_descriptions={
                language: f"Replacement description {language}"
                for language in LANGUAGE_PREFIXES
            },
            password="ABCDEF00",
            level=8,
            attack=3000,
            defense=2500,
            attribute="light",
            card_type="warrior",
            card_category="effect",
        )
        self.reference_service.suggest_card_reference.side_effect = (
            first_reference,
            second_reference,
        )
        self.reference_service.generate_card_image_name.side_effect = (
            generate_unique_card_image_name
        )
        self.reference_service.crawl_card_image_by_password.side_effect = (
            self._image_payload("red"),
            self._image_payload("blue"),
        )
        original_cards = (first.clone(), second.clone())

        with patch.object(
            self.service,
            "existing_card_image_names",
            return_value={"USR000.BMP"},
        ) as existing_names:
            result = self.service.bulk_suggest_missing_text(
                (first, second),
                manifest=self.manifest,
            )

        self.assertEqual(result.total_candidates, 2)
        self.assertEqual(result.resolved, 2)
        self.assertEqual(result.partially_filled, 0)
        self.assertEqual(result.not_found, 0)
        self.assertEqual(result.skipped_no_query_name, 0)
        self.assertEqual(result.unchanged, 0)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.image_staged, 2)
        self.assertEqual(result.image_failed, 0)
        filled = result.cards[0]
        self.assertEqual(filled.localized_text.names["eng"], "First Query")
        for language in LANGUAGE_PREFIXES:
            expected_name = (
                "First Query" if language == "eng" else f"Suggested name {language}"
            )
            self.assertEqual(filled.localized_text.names[language], expected_name)
            self.assertEqual(
                filled.localized_text.descriptions[language],
                f"Suggested description {language}",
            )
        self.assertEqual(filled.password, "00ABCDEF")
        self.assertEqual((filled.level, filled.attack, filled.defense), (0, 0, 0))
        self.assertEqual(filled.attribute, "light")
        self.assertEqual(filled.card_type, "spellcaster")
        self.assertEqual(filled.card_category, "effect")
        self.assertEqual(filled.image_name, "usr001.bmp")

        preserved = result.cards[1]
        self.assertEqual(preserved.localized_text.names, second.localized_text.names)
        self.assertEqual(
            preserved.localized_text.descriptions["fra"],
            "Replacement description fra",
        )
        for language in LANGUAGE_PREFIXES:
            if language != "fra":
                self.assertEqual(
                    preserved.localized_text.descriptions[language],
                    second.localized_text.descriptions[language],
                )
        self.assertEqual(preserved.password, "12345678")
        self.assertEqual(
            (preserved.level, preserved.attack, preserved.defense),
            (0, 0, 0),
        )
        self.assertEqual(preserved.attribute, "dark")
        self.assertEqual(preserved.card_type, "dragon")
        self.assertEqual(preserved.card_category, "normal")
        self.assertEqual(preserved.image_name, "usr002.bmp")
        for staged in result.cards:
            self.assertIsInstance(staged.large_image_source, bytes)
            self.assertIsInstance(staged.small_image_source, bytes)
        self.assertEqual((first, second), original_cards)
        existing_names.assert_called_once_with(self.manifest)
        self.assertEqual(
            self.reference_service.generate_card_image_name.call_count,
            2,
        )
        self.assertEqual(
            self.reference_service.crawl_card_image_by_password.call_args_list,
            [call("00ABCDEF"), call("12345678")],
        )
        self.reference_service.crawl_card_image.assert_not_called()

    def test_bulk_suggest_image_only_candidate_falls_back_to_canonical_name(self):
        draft = CardEditDraft(
            card_index=12,
            card_id=12,
            localized_text=CardLocalizedText(
                names={
                    language: f"Complete name {language}"
                    for language in LANGUAGE_PREFIXES
                },
                descriptions={
                    language: f"Complete description {language}"
                    for language in LANGUAGE_PREFIXES
                },
            ),
            password="00ABCDEF",
            level=0,
            attack=0,
            defense=0,
            attribute="dark",
            card_type="dragon",
            card_category="normal",
        )
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            matched_name="Canonical English",
            matched_language="eng",
            localized_names={"eng": "Canonical English"},
            localized_descriptions={},
        )
        self.reference_service.generate_card_image_name.return_value = "usr003.bmp"
        self.reference_service.crawl_card_image_by_password.side_effect = OSError(
            "direct image unavailable"
        )
        self.reference_service.crawl_card_image.return_value = self._image_payload(
            "green"
        )

        result = self.service.bulk_suggest_missing_text(
            (draft,),
            manifest=self.manifest,
        )

        self.assertEqual(result.total_candidates, 1)
        self.assertEqual(result.resolved, 1)
        self.assertEqual(result.partially_filled, 0)
        self.assertEqual(result.image_staged, 1)
        self.assertEqual(result.image_failed, 0)
        self.assertEqual(result.cards[0].image_name, "usr003.bmp")
        self.reference_service.crawl_card_image_by_password.assert_called_once_with(
            "00ABCDEF"
        )
        self.reference_service.crawl_card_image.assert_called_once_with(
            "Canonical English"
        )

    def test_bulk_suggest_image_failure_keeps_info_and_other_card_image(self):
        def candidate(index: int, name: str) -> CardEditDraft:
            draft = CardEditDraft(
                card_index=index,
                card_id=index,
                localized_text=CardLocalizedText(
                    names={
                        language: f"{name} {language}" for language in LANGUAGE_PREFIXES
                    },
                    descriptions={
                        language: f"Description {name} {language}"
                        for language in LANGUAGE_PREFIXES
                    },
                ),
                password="12345678",
                level=0,
                attack=0,
                defense=0,
                attribute="dark",
                card_type="dragon",
                card_category="normal",
            )
            draft.localized_text.names["fra"] = ""
            return draft

        first = candidate(13, "First")
        second = candidate(14, "Second")
        originals = (first.clone(), second.clone())
        self.reference_service.suggest_card_reference.side_effect = (
            CardReferenceData(
                "First eng",
                "eng",
                {"fra": "Premiere"},
                {},
            ),
            CardReferenceData(
                "Second eng",
                "eng",
                {"fra": "Deuxieme"},
                {},
            ),
        )
        self.reference_service.generate_card_image_name.side_effect = (
            "usr004.bmp",
            "usr005.bmp",
        )
        self.reference_service.crawl_card_image_by_password.side_effect = (
            OSError("direct image unavailable"),
            self._image_payload("blue"),
        )
        self.reference_service.crawl_card_image.side_effect = OSError(
            "fallback image unavailable"
        )

        result = self.service.bulk_suggest_missing_text(
            (first, second),
            manifest=self.manifest,
        )

        self.assertEqual(result.resolved, 2)
        self.assertEqual(result.partially_filled, 1)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.image_staged, 1)
        self.assertEqual(result.image_failed, 1)
        self.assertEqual(result.cards[0].localized_text.names["fra"], "Premiere")
        self.assertEqual(result.cards[0].image_name, "token_sl.bmp")
        self.assertIsNone(result.cards[0].large_image_source)
        self.assertEqual(result.cards[1].localized_text.names["fra"], "Deuxieme")
        self.assertEqual(result.cards[1].image_name, "usr004.bmp")
        self.assertIsInstance(result.cards[1].large_image_source, bytes)
        self.assertEqual((first, second), originals)
        self.assertEqual(
            self.reference_service.crawl_card_image_by_password.call_args_list,
            [call("12345678"), call("12345678")],
        )
        self.reference_service.crawl_card_image.assert_called_once_with("First eng")

    def test_bulk_suggest_inventory_failure_keeps_info_as_partial_update(self):
        draft = CardEditDraft(
            card_index=15,
            card_id=15,
            localized_text=CardLocalizedText(
                names={
                    language: f"Inventory name {language}"
                    for language in LANGUAGE_PREFIXES
                },
                descriptions={
                    language: f"Inventory description {language}"
                    for language in LANGUAGE_PREFIXES
                },
            ),
            password="00ABCDEF",
            level=0,
            attack=0,
            defense=0,
            attribute="dark",
            card_type="dragon",
            card_category="normal",
        )
        draft.localized_text.descriptions["fra"] = ""
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            "Inventory name eng",
            "eng",
            {},
            {"fra": "Description restauree"},
        )

        with patch.object(
            self.service,
            "existing_card_image_names",
            side_effect=OSError("catalog unavailable"),
        ) as existing_names:
            result = self.service.bulk_suggest_missing_text(
                (draft,),
                manifest=self.manifest,
            )

        self.assertEqual(result.resolved, 1)
        self.assertEqual(result.partially_filled, 1)
        self.assertEqual(result.image_staged, 0)
        self.assertEqual(result.image_failed, 1)
        self.assertEqual(result.failed, 0)
        self.assertEqual(
            result.cards[0].localized_text.descriptions["fra"],
            "Description restauree",
        )
        self.assertEqual(result.cards[0].image_name, "token_sl.bmp")
        existing_names.assert_called_once_with(self.manifest)
        self.reference_service.generate_card_image_name.assert_not_called()
        self.reference_service.crawl_card_image_by_password.assert_not_called()
        self.reference_service.crawl_card_image.assert_not_called()

    def test_bulk_suggest_token_without_query_does_not_read_image_inventory(self):
        draft = CardEditDraft(card_index=16, card_id=16)

        with patch.object(
            self.service,
            "existing_card_image_names",
        ) as existing_names:
            result = self.service.bulk_suggest_missing_text(
                (draft,),
                manifest=self.manifest,
            )

        self.assertEqual(result.total_candidates, 1)
        self.assertEqual(result.skipped_no_query_name, 1)
        self.assertEqual(result.unchanged, 1)
        existing_names.assert_not_called()

    def test_bulk_suggest_is_atomic_per_card_and_reports_unchanged_and_failed(self):
        def candidate(index: int, name: str) -> CardEditDraft:
            draft = CardEditDraft(
                card_index=index,
                card_id=index,
                localized_text=CardLocalizedText(
                    names={
                        language: f"{name} {language}" for language in LANGUAGE_PREFIXES
                    },
                    descriptions={
                        language: f"Description {name} {language}"
                        for language in LANGUAGE_PREFIXES
                    },
                ),
                password="12345678",
                level=0,
                attack=0,
                defense=0,
                attribute="dark",
                card_type="dragon",
                card_category="normal",
                image_name="existing.bmp",
            )
            draft.localized_text.names["fra"] = ""
            return draft

        success = candidate(20, "Success")
        failure = candidate(21, "Failure")
        unchanged = candidate(22, "Touched")
        unchanged.touched_fields.add("name:fra")
        no_query = candidate(23, "No Query")
        no_query.localized_text.names = {language: "" for language in LANGUAGE_PREFIXES}
        not_found = candidate(24, "Not Found")
        cards = (success, failure, unchanged, no_query, not_found)
        originals = tuple(card.clone() for card in cards)
        success_reference = CardReferenceData(
            "Success eng",
            "eng",
            {"fra": "Succès"},
            {},
        )
        failure_reference = CardReferenceData(
            "Failure eng",
            "eng",
            {"fra": "Échec"},
            {},
        )
        self.reference_service.suggest_card_reference.side_effect = (
            success_reference,
            failure_reference,
            None,
        )
        real_apply = self.service.apply_reference_to_draft

        def controlled_apply(draft, reference, *, include_scalars=True):
            if draft.card_index == failure.card_index:
                draft.localized_text.names["fra"] = "partial mutation"
                draft.attack = 9999
                raise RuntimeError("controlled per-card failure")
            return real_apply(
                draft,
                reference,
                include_scalars=include_scalars,
            )

        progress = []
        with (
            self.assertLogs(level="ERROR") as logs,
            patch.object(
                self.service,
                "apply_reference_to_draft",
                side_effect=controlled_apply,
            ),
        ):
            result = self.service.bulk_suggest_missing_text(
                cards,
                report_progress=lambda done, total: progress.append((done, total)),
            )

        self.assertEqual(result.total_candidates, 4)
        self.assertEqual(result.resolved, 1)
        self.assertEqual(result.partially_filled, 0)
        self.assertEqual(result.not_found, 1)
        self.assertEqual(result.skipped_no_query_name, 1)
        self.assertEqual(result.unchanged, 2)
        self.assertEqual(result.failed, 1)
        self.assertFalse(result.cancelled)
        self.assertEqual(result.cards[0].localized_text.names["fra"], "Succès")
        self.assertEqual(result.cards[1], originals[1])
        self.assertEqual(result.cards[2], originals[2])
        self.assertEqual(result.cards[3], originals[3])
        self.assertEqual(result.cards[4], originals[4])
        self.assertEqual(cards, originals)
        self.assertEqual(progress[-1], (4, 4))
        self.assertTrue(
            any("card index 21" in message for message in logs.output),
            logs.output,
        )
        self.reference_service.generate_card_image_name.assert_not_called()
        self.reference_service.crawl_card_image_by_password.assert_not_called()
        self.reference_service.crawl_card_image.assert_not_called()

    def test_bulk_suggest_cancel_keeps_completed_staged_results(self):
        cards = [detail.to_draft() for detail in self.service.load_card_details()]
        cards[0].localized_text.names["eng"] = "First"
        cards[1].localized_text.names["eng"] = "Second"
        cards[0].image_name = "token_sl.bmp"
        cards[1].image_name = "token_sl.bmp"
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            "Back",
            "eng",
            {"fra": "Dos"},
            {},
            password="00ABCDEF",
            source="test",
            confidence="exact",
        )
        self.reference_service.generate_card_image_name.return_value = "usr006.bmp"
        self.reference_service.crawl_card_image_by_password.return_value = (
            self._image_payload("purple")
        )
        cancellation_checks = iter((False, True))

        result = self.service.bulk_suggest_missing_text(
            cards,
            manifest=self.manifest,
            is_cancelled=lambda: next(cancellation_checks),
        )

        self.assertTrue(result.cancelled)
        self.assertGreaterEqual(
            self.reference_service.suggest_card_reference.call_count,
            1,
        )
        self.assertLessEqual(
            self.reference_service.suggest_card_reference.call_count,
            result.selected_workers * 2,
        )
        self.assertEqual(result.cards[0].localized_text.names["fra"], "Dos")
        self.assertEqual(result.cards[1].localized_text.names["fra"], "")
        self.assertEqual(result.image_staged, 1)
        self.assertEqual(result.image_failed, 0)
        self.assertEqual(result.cards[0].image_name, "usr006.bmp")
        self.assertIsInstance(result.cards[0].large_image_source, bytes)
        self.assertEqual(result.cards[1].image_name, "token_sl.bmp")
        self.assertIsNone(result.cards[1].large_image_source)

    def test_bulk_suggest_parallel_resolve_commits_source_order_and_unique_names(self):
        first = self._complete_suggest_card(
            40,
            image_name="token_sl.bmp",
        )
        first.password = "11111111"
        second = self._complete_suggest_card(
            41,
            image_name="token_sl.bmp",
        )
        second.password = "22222222"
        overlap_barrier = threading.Barrier(2)
        active = 0
        maximum_active = 0
        active_lock = threading.Lock()

        def resolve_reference(name: str, language: str) -> CardReferenceData:
            nonlocal active, maximum_active
            with active_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            overlap_barrier.wait(timeout=2)
            with active_lock:
                active -= 1
            return CardReferenceData(name, language, {}, {})

        second_downloaded = threading.Event()
        completion_order = []

        def download(password: str) -> bytes:
            if password == "11111111":
                self.assertTrue(second_downloaded.wait(timeout=2))
            else:
                second_downloaded.set()
            completion_order.append(password)
            return self._image_payload("red" if password == "11111111" else "blue")

        self.reference_service.suggest_card_reference.side_effect = resolve_reference
        self.reference_service.crawl_card_image_by_password.side_effect = download
        self.reference_service.generate_card_image_name.side_effect = (
            generate_unique_card_image_name
        )

        with (
            patch(
                "yugioh_editor.services.card_service.select_bulk_suggest_worker_count",
                return_value=2,
            ),
            patch.object(
                self.service,
                "existing_card_image_names",
                return_value={"USR000.BMP"},
            ),
        ):
            result = self.service.bulk_suggest_missing_text(
                (first, second),
                manifest=self.manifest,
            )

        self.assertGreaterEqual(maximum_active, 2)
        self.assertEqual(completion_order, ["22222222", "11111111"])
        self.assertEqual([card.card_index for card in result.cards], [40, 41])
        self.assertEqual(
            [card.image_name for card in result.cards],
            ["usr001.bmp", "usr002.bmp"],
        )
        self.assertEqual(result.image_staged, 2)
        self.assertFalse(
            any(
                thread.name.startswith("card-suggest")
                for thread in threading.enumerate()
            )
        )

    def test_bulk_suggest_cancel_limits_submissions_and_shuts_down_workers(self):
        cards = []
        for index in range(50, 70):
            card = self._complete_suggest_card(index)
            card.localized_text.descriptions["fra"] = ""
            cards.append(card)

        def resolve_reference(name: str, language: str) -> CardReferenceData:
            sleep(0.02)
            return CardReferenceData(
                name,
                language,
                {},
                {"fra": f"Resolved {name}"},
            )

        self.reference_service.suggest_card_reference.side_effect = resolve_reference
        progress = []

        with patch(
            "yugioh_editor.services.card_service.select_bulk_suggest_worker_count",
            return_value=2,
        ):
            result = self.service.bulk_suggest_missing_text(
                cards,
                is_cancelled=lambda: bool(progress and progress[-1][0] >= 1),
                report_progress=lambda done, total: progress.append((done, total)),
            )

        self.assertTrue(result.cancelled)
        self.assertEqual(progress[0], (0, 20))
        self.assertEqual(progress[-1], (1, 20))
        self.assertEqual(result.resolved, 1)
        self.assertGreaterEqual(
            self.reference_service.suggest_card_reference.call_count,
            1,
        )
        self.assertLessEqual(
            self.reference_service.suggest_card_reference.call_count,
            4,
        )
        self.assertEqual(
            result.cards[0].localized_text.descriptions["fra"],
            "Resolved Complete 50 eng",
        )
        self.assertEqual(result.cards[1], cards[1])
        self.assertFalse(
            any(
                thread.name.startswith("card-suggest")
                for thread in threading.enumerate()
            )
        )

    def test_bulk_suggest_prefilters_complete_cards_before_parallel_work(self):
        cards = [
            self._complete_suggest_card(
                index,
                kind="spell" if index % 2 else "trap",
            )
            for index in range(100, 195)
        ]
        for index in range(195, 200):
            card = self._complete_suggest_card(index)
            card.localized_text.descriptions["spa"] = ""
            cards.append(card)

        self.reference_service.suggest_card_reference.side_effect = (
            lambda name, language: CardReferenceData(
                name,
                language,
                {},
                {"spa": f"Resolved {name}"},
            )
        )
        progress = []

        with patch(
            "yugioh_editor.services.card_service.select_bulk_suggest_worker_count",
            return_value=4,
        ):
            result = self.service.bulk_suggest_missing_text(
                cards,
                report_progress=lambda done, total: progress.append((done, total)),
            )

        self.assertEqual(result.total_source_cards, 100)
        self.assertEqual(result.total_candidates, 5)
        self.assertEqual(result.skipped_complete, 95)
        self.assertEqual(result.resolved, 5)
        self.assertEqual(
            self.reference_service.suggest_card_reference.call_count,
            5,
        )
        self.assertEqual(progress[0], (0, 5))
        self.assertEqual(progress[-1], (5, 5))

    def test_bulk_suggest_staged_image_pair_survives_save_and_reload(self):
        draft = self.service.get_card_detail(self.manifest, 1).to_draft()
        draft.image_name = "token_sl.bmp"
        draft.password = "12345678"
        self.reference_service.suggest_card_reference.return_value = CardReferenceData(
            "Dragon",
            "eng",
            {"eng": "Dragon"},
            {},
        )
        self.reference_service.generate_card_image_name.return_value = "usr099.bmp"
        self.reference_service.crawl_card_image_by_password.return_value = (
            self._image_payload("orange")
        )

        result = self.service.bulk_suggest_missing_text(
            (draft,),
            manifest=self.manifest,
        )
        staged = result.cards[0]
        self.assertTrue(result.image_staged)
        self.assertIsInstance(staged.large_image_source, bytes)
        self.assertIsInstance(staged.small_image_source, bytes)

        self.service.save_card_changes(self.manifest, [staged])

        reloaded_repository = ProjectRepository(self.repository.root)
        reloaded_manifest = reloaded_repository.load()
        reloaded_service = CardService(reloaded_repository, Mock())
        reloaded = reloaded_service.get_card_detail(reloaded_manifest, 1)
        self.assertEqual(reloaded.image_name, "usr099.bmp")
        large, mini = reloaded_service.load_card_images(
            reloaded_manifest,
            "usr099.bmp",
        )
        for payload, expected_size in ((large, (200, 290)), (mini, (50, 72))):
            self.assertTrue(payload.startswith(b"BM"))
            with Image.open(BytesIO(payload)) as image:
                self.assertEqual(image.format, "BMP")
                self.assertEqual(image.size, expected_size)

    def test_six_language_create_reload_and_pack_integration(self):
        order = max(record.order for record in self.manifest.files) + 1
        resources: list[ProjectResource] = []
        for language in LANGUAGE_PREFIXES:
            if language == "eng":
                continue
            for stem, table in (
                (
                    "card_name",
                    pd.DataFrame({"value": ["", f"Old {language}"]}),
                ),
                (
                    "card_desc",
                    pd.DataFrame(
                        {
                            "text": ["Back", f"Old description {language}"],
                            "is_reserved": [False, False],
                        }
                    ),
                ),
            ):
                relative = f"bin#/{stem}{language}.bin"
                resources.append(
                    ProjectResource(
                        ProjectFileRecord(
                            "Data.dat",
                            relative,
                            f"data/{relative}",
                            "table",
                            "table",
                            language=language,
                            order=order,
                        ),
                        table,
                    )
                )
                order += 1
            for stem in ("card_indx", "card_sort"):
                resources.append(
                    ProjectResource(
                        ProjectFileRecord(
                            "Data.dat",
                            f"bin#/{stem}{language}.bin",
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
                order += 1
        self.manifest.files.extend(self.repository.import_resources(resources))
        self.repository.save(self.manifest)

        service = CardService(self.repository, self.reference_service)
        edited = service.get_card_detail(None, 1).to_draft()
        for language in LANGUAGE_PREFIXES:
            edited.localized_text.names[language] = f"Updated {language}"
            edited.localized_text.descriptions[language] = f"Description {language}"
        edited.attack = 2400
        service.update_card(None, edited)

        created = service.create_card_draft()
        for language in LANGUAGE_PREFIXES:
            created.localized_text.names[language] = f"New {language}"
            created.localized_text.descriptions[language] = f"New desc {language}"
        service.create_card(None, created)

        reloaded = CardService(ProjectRepository(self.manifest), self.reference_service)
        cards = reloaded.load_card_details()
        self.assertEqual(len(cards), 3)
        for language in LANGUAGE_PREFIXES:
            self.assertEqual(
                cards[1].localized_text.names[language],
                f"Updated {language}",
            )
            self.assertEqual(
                cards[2].localized_text.descriptions[language],
                f"New desc {language}",
            )
            self.assertEqual(
                len(
                    self.repository.get_table(
                        "card_names",
                        language=language,
                    )
                ),
                3,
            )
        for variant in CardImageVariant:
            self.assertEqual(
                self.repository.get_table(
                    "card_catalog",
                    image_variant=variant,
                )["image_name"].tolist(),
                ["", "", "token_sl.bmp"],
            )

        reading_service = Mock()
        reading_service.get_japanese_reading.return_value = "reading"
        game = GameRepository.from_root(
            self.root,
            CardNameNormalizer(reading_service),
        )
        archive = game.encode_archive(
            "Data.dat",
            self.repository.export_resources(
                self.repository.list_resources(
                    self.manifest,
                    include_virtual=True,
                )
            ),
        )
        packed_paths = {
            entry.relative_path.replace("\\", "/") for entry in archive.entries
        }
        for language in LANGUAGE_PREFIXES:
            self.assertIn(f"bin#/card_name{language}.bin", packed_paths)
            self.assertIn(f"bin#/card_desc{language}.bin", packed_paths)
            self.assertIn(f"bin#/card_indx{language}.bin", packed_paths)
            self.assertIn(f"bin#/card_sort{language}.bin", packed_paths)


if __name__ == "__main__":
    unittest.main()
