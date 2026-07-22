from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import (
    FIRST_COMPLETED,
    CancelledError,
    Future,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import pandas as pd

from yugioh_editor.common.card_errors import (
    CardError,
    CardImageError,
    CardImportError,
    CardPersistenceError,
    CardValidationError,
)
from yugioh_editor.common.card_images import (
    TOKEN_CARD_IMAGE_NAME,
    build_card_image_pair,
)
from yugioh_editor.common.card_passwords import (
    MISSING_CARD_PASSWORD,
    is_missing_card_password,
    normalize_card_password,
)
from yugioh_editor.common.card_properties import (
    ATTRIBUTE_LABELS,
    CARD_LEVEL_MAX,
    CARD_LEVEL_MIN,
    CARD_STAT_MAX,
    CARD_STAT_MIN,
    CARD_STAT_STEP,
    MONSTER_CATEGORY_LABELS,
    MONSTER_TYPE_LABELS,
    SPELL_TRAP_SUBTYPE_LABELS,
    code_for_property_label,
    normalize_property_label,
)
from yugioh_editor.common.constants import (
    DEFAULT_LANGUAGE,
    JAPANESE_LANGUAGE,
    LANGUAGE_PREFIXES,
    PACK_NAMES,
    language_encoding,
    normalize_language_code,
)
from yugioh_editor.common.worker_limits import (
    estimate_available_memory_bytes,
    select_bulk_suggest_worker_count,
)
from yugioh_editor.models.card_editing import (
    CARD_CSV_COLUMNS,
    BulkSuggestionResult,
    CardDetailData,
    CardEditDraft,
    CardImportApplyResult,
    CardImportResult,
    CardLocalizedText,
    CardReferenceData,
    CardSuggestionResult,
    card_description_column,
    card_name_column,
)
from yugioh_editor.models.entities import NamedCardImagePair, ProjectManifest
from yugioh_editor.repositories.project.repository import ProjectRepository
from yugioh_editor.services.card_reference_data_service import (
    CardReferenceDataService,
)

_ATTRIBUTE_VALUES = frozenset(ATTRIBUTE_LABELS.values())
_CARD_TYPE_VALUES = frozenset(MONSTER_TYPE_LABELS.values())
_CARD_CATEGORY_VALUES = frozenset(
    (*MONSTER_CATEGORY_LABELS.values(), *SPELL_TRAP_SUBTYPE_LABELS.values())
)
_PACK_VALUES = frozenset(PACK_NAMES.values())
_CARD_NUMERIC_EDIT_FIELDS = ("level", "attack", "defense")
_CARD_ENUM_EDIT_FIELDS = ("attribute", "card_type", "card_category", "pack")


@dataclass(frozen=True, slots=True)
class _ResolvedCardSuggestion:
    draft: CardEditDraft
    applied_fields: tuple[str, ...]
    reference_found: bool
    image_pair: tuple[bytes, bytes] | None = None
    image_error: str | None = None
    reference_source: str = "not_found"
    reference_confidence: str | None = None
    no_query: bool = False


@dataclass(frozen=True, slots=True)
class _StagedImageSaveStats:
    new_pairs: int = 0
    replacement_pairs: int = 0


class CardService:
    """Coordinate card editing through the repository's logical table API."""

    def __init__(
        self,
        repository: ProjectRepository | None = None,
        card_reference_data_service: CardReferenceDataService | None = None,
    ) -> None:
        self._repository = repository
        self._card_reference_data_service = (
            CardReferenceDataService()
            if card_reference_data_service is None
            else card_reference_data_service
        )

    def load_card_details(
        self,
        manifest: ProjectManifest | None = None,
    ) -> list[CardDetailData]:
        frame = self._project(manifest).get_table(
            "cards",
            language=DEFAULT_LANGUAGE,
        )
        return [self._row_to_detail(row._asdict()) for row in frame.itertuples()]

    def get_card_detail(
        self,
        manifest: ProjectManifest | None,
        card_index: int,
    ) -> CardDetailData:
        matches = [
            card
            for card in self.load_card_details(manifest)
            if card.card_index == int(card_index)
        ]
        if not matches:
            raise IndexError(f"Card index {card_index} was not found.")
        return matches[0]

    def card_index_bounds(
        self,
        manifest: ProjectManifest | None,
    ) -> tuple[int, int]:
        cards = self.load_card_details(manifest)
        if not cards:
            raise IndexError("The project contains no cards.")
        indexes = [card.card_index for card in cards]
        return min(indexes), max(indexes)

    def create_card_draft(
        self,
        manifest: ProjectManifest | None = None,
    ) -> CardEditDraft:
        cards = self.load_card_details(manifest)
        next_index = max((card.card_index for card in cards), default=-1) + 1
        existing_ids = {card.card_id for card in cards}
        next_id = 0
        while next_id in existing_ids:
            next_id += 1
        return CardEditDraft(
            card_index=next_index,
            card_id=next_id,
            localized_text=CardLocalizedText(),
            attribute="",
            card_type="non_game_card",
            card_category="",
            pack="disabled",
            image_name=TOKEN_CARD_IMAGE_NAME,
            is_new=True,
        )

    def create_card(
        self,
        manifest: ProjectManifest | None,
        draft: CardEditDraft,
    ) -> CardDetailData:
        if not draft.is_new:
            raise CardValidationError(["create_card requires a CREATE-mode draft."])
        self.save_card_changes(manifest, [draft])
        return draft.to_detail()

    def update_card(
        self,
        manifest: ProjectManifest | None,
        draft: CardEditDraft,
    ) -> CardDetailData:
        if draft.is_new:
            raise CardValidationError(
                ["update_card cannot update a CREATE-mode draft."]
            )
        self.save_card_changes(manifest, [draft])
        return draft.to_detail()

    def save_card_changes(
        self,
        manifest: ProjectManifest | None,
        changes: Sequence[CardEditDraft],
    ) -> None:
        overall_started = perf_counter()
        drafts = [draft.clone() for draft in changes]
        if not drafts:
            return
        validation_errors: list[str] = []
        for draft in drafts:
            try:
                draft.password = normalize_card_password(draft.password)
            except ValueError:
                pass
            validation_errors.extend(self.validate_card_draft(draft))
        if validation_errors:
            raise CardValidationError(validation_errors)

        repository = self._project(manifest)
        current = repository.get_table("cards", language=DEFAULT_LANGUAGE)
        updated = current.reset_index(drop=True).copy()
        existing_indexes = set(updated["card_index"].astype(int).tolist())
        existing_ids = set(updated["card_id"].astype(int).tolist())
        row_by_index = {
            int(card_index): row_index
            for row_index, card_index in enumerate(updated["card_index"])
        }
        new_rows: list[dict[str, object]] = []
        changed_indexes: set[int] = set()
        changed_ids: set[int] = set()
        for draft in drafts:
            if draft.card_index in changed_indexes:
                validation_errors.append(
                    f"Duplicate changed card index {draft.card_index}."
                )
            if draft.card_id in changed_ids:
                validation_errors.append(f"Duplicate changed card ID {draft.card_id}.")
            changed_indexes.add(draft.card_index)
            changed_ids.add(draft.card_id)
            if draft.is_new:
                expected_index = max(existing_indexes, default=-1) + 1
                if draft.card_index != expected_index:
                    validation_errors.append(
                        f"Card index conflict for new card: expected {expected_index}, "
                        f"got {draft.card_index}."
                    )
                if draft.card_id in existing_ids:
                    validation_errors.append(
                        f"Card ID conflict for new card: {draft.card_id} "
                        "already exists."
                    )
                existing_indexes.add(draft.card_index)
                existing_ids.add(draft.card_id)
                new_rows.append(self._draft_to_row(draft))
                continue
            row_index = row_by_index.get(draft.card_index)
            if row_index is None:
                validation_errors.append(
                    f"Card index {draft.card_index} no longer exists."
                )
                continue
            persisted_id = int(updated.loc[row_index, "card_id"])
            if persisted_id != draft.card_id:
                validation_errors.append(
                    f"Card ID is immutable for index {draft.card_index}: "
                    f"expected {persisted_id}, got {draft.card_id}."
                )
                continue
            row = self._draft_to_row(draft)
            for column, value in row.items():
                updated.loc[row_index, column] = value
        if new_rows:
            updated = pd.concat(
                [updated, pd.DataFrame.from_records(new_rows)],
                ignore_index=True,
            )
        if validation_errors:
            raise CardValidationError(validation_errors)

        staging_started = perf_counter()
        staging = repository.begin_update()
        staging_duration = perf_counter() - staging_started
        try:
            image_started = perf_counter()
            image_stats = self._apply_staged_images(staging, drafts)
            image_duration = perf_counter() - image_started
            table_started = perf_counter()
            staging.save_table(
                "cards",
                updated,
                language=DEFAULT_LANGUAGE,
            )
            table_duration = perf_counter() - table_started
            commit_started = perf_counter()
            repository.commit_update(staging)
            commit_duration = perf_counter() - commit_started
        except CardError:
            staging.discard()
            raise
        except Exception as error:
            staging.discard()
            indexes = ", ".join(str(draft.card_index) for draft in drafts)
            raise CardPersistenceError(
                f"save_card_changes failed for card indexes {indexes}; "
                f"the project was not committed: {error}"
            ) from error

        logging.info(
            "Card Save completed: changed_cards=%d new_image_pairs=%d "
            "replacement_image_pairs=%d staging_clone=%.3fs images=%.3fs "
            "tables_and_manifest=%.3fs commit=%.3fs overall=%.3fs",
            len(drafts),
            image_stats.new_pairs,
            image_stats.replacement_pairs,
            staging_duration,
            image_duration,
            table_duration,
            commit_duration,
            perf_counter() - overall_started,
        )

        for original, saved in zip(changes, drafts, strict=True):
            original.password = saved.password
            original.dirty = False
            original.is_new = False
            original.large_image_source = None
            original.small_image_source = None

    def validate_card_draft(self, draft: CardEditDraft) -> list[str]:
        errors: list[str] = []
        if not 0 <= draft.card_index <= 9999:
            errors.append(f"card_index {draft.card_index} must be between 0 and 9999.")
        minimum_id = 0 if draft.is_new else -1
        if not minimum_id <= draft.card_id <= 9999:
            errors.append(
                f"card_id {draft.card_id} must be between {minimum_id} and 9999."
            )
        self._validate_password(draft.password, errors)
        self._validate_optional_integer(
            draft.level,
            "level",
            CARD_LEVEL_MIN,
            CARD_LEVEL_MAX,
            errors,
        )
        self._validate_stat(draft.attack, "attack", errors)
        self._validate_stat(draft.defense, "defense", errors)
        self._validate_enum(draft.attribute, "attribute", _ATTRIBUTE_VALUES, errors)
        self._validate_enum(draft.card_type, "card_type", _CARD_TYPE_VALUES, errors)
        self._validate_card_category(draft, errors)
        self._validate_enum(draft.pack, "pack", _PACK_VALUES, errors)
        if draft.is_new and not str(draft.image_name).strip():
            errors.append("image_name must not be empty for a new card.")
        if draft.image_name.casefold() == TOKEN_CARD_IMAGE_NAME.casefold() and (
            draft.large_image_source is not None or draft.small_image_source is not None
        ):
            errors.append(
                "token_sl.bmp replacement must generate a new image name and "
                "stage both large and small images."
            )
        for language in LANGUAGE_PREFIXES:
            encoding = language_encoding(language)
            name = draft.localized_text.names[language]
            description = draft.localized_text.descriptions[language]
            try:
                encoded_name = name.encode(encoding, errors="strict")
                if len(encoded_name) > 63:
                    errors.append(
                        f"name:{language} uses {len(encoded_name)} bytes; "
                        "maximum is 63 "
                        f"using {encoding}."
                    )
            except UnicodeEncodeError as error:
                errors.append(
                    f"name:{language} cannot be encoded using {encoding} at "
                    f"character {error.start}."
                )
            try:
                description.encode(encoding, errors="strict")
            except UnicodeEncodeError as error:
                errors.append(
                    f"description:{language} cannot be encoded using {encoding} at "
                    f"character {error.start}."
                )
        return errors

    def load_card_image(
        self,
        manifest: ProjectManifest | None,
        image_name: str,
        *,
        mini: bool = False,
    ) -> bytes:
        return self._project(manifest).read_card_image(image_name, mini=mini)

    def load_card_images(
        self,
        manifest: ProjectManifest | None,
        image_name: str,
    ) -> tuple[bytes, bytes]:
        return self._project(manifest).read_card_images(image_name)

    def existing_card_image_names(
        self,
        manifest: ProjectManifest | None,
    ) -> set[str]:
        return self._project(manifest).existing_card_image_names()

    def validate_image_source(
        self,
        manifest: ProjectManifest | None,
        source: str | Path | bytes,
        *,
        mini: bool = False,
    ) -> None:
        repository = self._project(manifest)
        try:
            repository.prepare_image_bytes(source)
        except (OSError, ValueError) as error:
            raise CardImageError(
                f"validate_image_source could not decode {source!s}: {error}"
            ) from error

    def export_cards_csv(
        self,
        manifest: ProjectManifest | None,
        path: str | Path,
        cards: Sequence[CardDetailData | CardEditDraft],
    ) -> Path:
        rows = [self._card_to_csv_row(card) for card in cards]
        frame = pd.DataFrame(rows, columns=CARD_CSV_COLUMNS)
        return self._project(manifest).write_external_table(
            path,
            frame,
            CARD_CSV_COLUMNS,
        )

    def parse_card_import_csv(
        self,
        manifest: ProjectManifest | None,
        path: str | Path,
    ) -> CardImportResult:
        try:
            frame = self._project(manifest).read_external_table(path)
        except Exception as error:
            raise CardImportError(
                f"Unable to read card import CSV {path}: {error}"
            ) from error
        if tuple(frame.columns) != CARD_CSV_COLUMNS:
            raise CardImportError(
                "Card import CSV header must be exactly: " + ",".join(CARD_CSV_COLUMNS)
            )
        rows: list[dict[str, str]] = []
        for row_number, row in frame.iterrows():
            values = {column: str(row[column]) for column in CARD_CSV_COLUMNS}
            try:
                int(values["card_id"])
            except ValueError as error:
                raise CardImportError(
                    f"CSV row {row_number + 2}: invalid card_id {values['card_id']!r}."
                ) from error
            rows.append(values)
        return CardImportResult(tuple(rows))

    def apply_import_to_drafts(
        self,
        import_result: CardImportResult,
        current_cards: Sequence[CardDetailData | CardEditDraft],
    ) -> CardImportApplyResult:
        cards = [
            card.clone() if isinstance(card, CardEditDraft) else card.to_draft()
            for card in current_cards
        ]
        position_by_id = {card.card_id: position for position, card in enumerate(cards)}
        seen_matched_ids: set[int] = set()
        matched = 0
        skipped = 0
        ignored_image_names = 0
        errors: list[str] = []
        for position, row in enumerate(import_result.rows, start=2):
            card_id = int(row["card_id"])
            card_position = position_by_id.get(card_id)
            if card_position is None:
                skipped += 1
                continue
            if card_id in seen_matched_ids:
                errors.append(
                    f"CSV row {position}: duplicate matched card_id {card_id}."
                )
                continue
            seen_matched_ids.add(card_id)
            draft = cards[card_position]
            matched += 1
            try:
                imported = self._apply_csv_row(draft, row)
            except (TypeError, ValueError) as error:
                errors.append(f"CSV row {position}, card_id {card_id}: {error}")
                continue
            if row["image_name"] != draft.image_name:
                ignored_image_names += 1
            cards[card_position] = imported
        if errors:
            raise CardImportError(
                "Card import validation failed:\n- " + "\n- ".join(errors)
            )
        return CardImportApplyResult(
            cards=tuple(cards),
            total_rows=len(import_result.rows),
            matched=matched,
            skipped_unknown_ids=skipped,
            ignored_image_name_changes=ignored_image_names,
            updated=matched,
        )

    def suggest_card_reference(
        self,
        card_name: str,
        language: str,
    ) -> CardReferenceData | None:
        return self._card_reference_data_service.suggest_card_reference(
            card_name,
            language,
        )

    @staticmethod
    def select_suggestion_query(
        card: CardEditDraft,
        preferred_language: str | None = None,
    ) -> tuple[str, str] | None:
        preferred = (DEFAULT_LANGUAGE, JAPANESE_LANGUAGE)
        if preferred_language is not None:
            normalized_preferred = normalize_language_code(preferred_language)
            preferred = (
                normalized_preferred,
                *(code for code in preferred if code != normalized_preferred),
            )
        languages = (
            *preferred,
            *(code for code in LANGUAGE_PREFIXES if code not in preferred),
        )
        for language in languages:
            name = card.localized_text.names.get(language)
            if isinstance(name, str) and name.strip():
                return name, language
        return None

    def apply_reference_to_draft(
        self,
        draft: CardEditDraft,
        reference: CardReferenceData,
        *,
        include_scalars: bool = True,
    ) -> tuple[str, ...]:
        applied: list[str] = []
        for language, value in reference.localized_names.items():
            field_name = f"name:{language}"
            if (
                language in draft.localized_text.names
                and not draft.localized_text.names[language].strip()
                and field_name not in draft.touched_fields
                and str(value).strip()
            ):
                draft.localized_text.names[language] = str(value)
                applied.append(field_name)
        for language, value in reference.localized_descriptions.items():
            field_name = f"description:{language}"
            if (
                language in draft.localized_text.descriptions
                and not draft.localized_text.descriptions[language].strip()
                and field_name not in draft.touched_fields
                and str(value).strip()
            ):
                draft.localized_text.descriptions[language] = str(value)
                applied.append(field_name)
        if include_scalars:
            if (
                is_missing_card_password(draft.password)
                and "password" not in draft.touched_fields
                and reference.password is not None
            ):
                try:
                    password = normalize_card_password(reference.password)
                except ValueError:
                    password = None
                if password is not None and password != MISSING_CARD_PASSWORD:
                    draft.password = password
                    applied.append("password")
            for field_name in ("card_type", "card_category"):
                value = getattr(reference, field_name)
                if (
                    self.is_missing_suggest_field(draft, field_name)
                    and field_name not in draft.touched_fields
                    and value is not None
                ):
                    setattr(draft, field_name, value)
                    setattr(
                        draft,
                        {
                            "attribute": "attribute_code",
                            "card_type": "monster_type_code",
                            "card_category": "card_category_code",
                        }[field_name],
                        None,
                    )
                    applied.append(field_name)
            required_fields = frozenset(self._suggest_required_scalar_fields(draft))
            for field_name in _CARD_NUMERIC_EDIT_FIELDS:
                value = getattr(reference, field_name)
                if (
                    field_name in required_fields
                    and self.is_missing_suggest_field(draft, field_name)
                    and field_name not in draft.touched_fields
                    and value is not None
                ):
                    setattr(draft, field_name, value)
                    applied.append(field_name)
            if "attribute" in required_fields:
                value = reference.attribute
                if (
                    self.is_missing_suggest_field(draft, "attribute")
                    and "attribute" not in draft.touched_fields
                    and value is not None
                ):
                    draft.attribute = value
                    draft.attribute_code = None
                    applied.append("attribute")
        if applied:
            draft.dirty = True
        return tuple(applied)

    def suggest_card_draft(
        self,
        manifest: ProjectManifest | None,
        draft: CardEditDraft,
        *,
        include_image: bool = True,
        preferred_language: str | None = None,
    ) -> CardSuggestionResult:
        return self._suggest_card_draft(
            manifest,
            draft,
            include_image=include_image,
            preferred_language=preferred_language,
            reserved_image_names=None,
            image_inventory_error=None,
        )

    def _suggest_card_draft(
        self,
        manifest: ProjectManifest | None,
        draft: CardEditDraft,
        *,
        include_image: bool,
        preferred_language: str | None,
        reserved_image_names: set[str] | None,
        image_inventory_error: str | None,
    ) -> CardSuggestionResult:
        """Run the shared one-card Suggest pipeline without mutating ``draft``."""

        resolved = self._resolve_card_suggestion(
            draft,
            include_image=include_image,
            preferred_language=preferred_language,
            image_download_error=image_inventory_error,
            require_query=True,
        )
        if resolved.image_pair is not None and image_inventory_error is not None:
            resolved = _ResolvedCardSuggestion(
                draft=resolved.draft,
                applied_fields=resolved.applied_fields,
                reference_found=resolved.reference_found,
                image_pair=None,
                image_error=image_inventory_error,
                reference_source=resolved.reference_source,
                reference_confidence=resolved.reference_confidence,
            )
        return self._finalize_resolved_suggestion(
            manifest,
            resolved,
            reserved_image_names=reserved_image_names,
        )

    def _resolve_card_suggestion(
        self,
        draft: CardEditDraft,
        *,
        include_image: bool,
        preferred_language: str | None,
        image_download_error: str | None,
        require_query: bool,
    ) -> _ResolvedCardSuggestion:
        """Resolve one clone; workers never allocate names or touch the project."""

        staged = draft.clone()
        reference_service = self._card_reference_data_service
        query = self.select_suggestion_query(staged, preferred_language)
        image_password = self.select_image_lookup_password(staged.password)
        can_resolve_without_query = (
            include_image
            and self._needs_suggested_image(staged)
            and image_password is not None
        )
        if query is None:
            if require_query:
                raise CardValidationError(
                    ["At least one card name is required before suggesting data."]
                )
            if not can_resolve_without_query:
                return _ResolvedCardSuggestion(staged, (), False, no_query=True)

        reference = None if query is None else self.suggest_card_reference(*query)
        applied: list[str] = []
        english_name = None
        if reference is not None and query is not None:
            english_name = self.select_image_lookup_name(reference, staged, query)
            applied.extend(self.apply_reference_to_draft(staged, reference))
            applied.extend(self._apply_suggestion_defaults(staged))
            image_password = self.select_image_lookup_password(staged.password)

        image_pair = None
        image_error = None
        if (
            include_image
            and self._needs_suggested_image(staged)
            and (image_password is not None or english_name is not None)
        ):
            image_errors: list[str] = []
            if image_download_error is not None:
                image_errors.append(image_download_error)
            else:
                if image_password is not None:
                    try:
                        original = reference_service.crawl_card_image_by_password(
                            image_password
                        )
                        image_pair = build_card_image_pair(original)
                    except Exception as error:
                        logging.warning(
                            "Direct card image lookup or conversion failed for "
                            "password %s: %s",
                            image_password,
                            error,
                        )
                        image_errors.append(f"password image: {error}")
                if image_pair is None and english_name is not None:
                    try:
                        original = reference_service.crawl_card_image(english_name)
                        image_pair = build_card_image_pair(original)
                    except Exception as error:
                        logging.warning(
                            "Card image name fallback lookup or conversion failed for "
                            "%r: %s",
                            english_name,
                            error,
                        )
                        image_errors.append(f"name image: {error}")
            if image_pair is None and image_errors:
                image_error = "; ".join(image_errors)

        return _ResolvedCardSuggestion(
            draft=staged,
            applied_fields=tuple(applied),
            reference_found=reference is not None,
            image_pair=image_pair,
            image_error=image_error,
            reference_source=("not_found" if reference is None else reference.source),
            reference_confidence=(None if reference is None else reference.confidence),
        )

    def _finalize_resolved_suggestion(
        self,
        manifest: ProjectManifest | None,
        resolved: _ResolvedCardSuggestion,
        *,
        reserved_image_names: set[str] | None,
    ) -> CardSuggestionResult:
        staged = resolved.draft
        applied = list(resolved.applied_fields)
        image_staged = False
        image_error = resolved.image_error
        if resolved.image_pair is not None:
            try:
                if reserved_image_names is None:
                    reserved_image_names = {
                        name.casefold()
                        for name in self.existing_card_image_names(manifest)
                    }
                candidate = self._card_reference_data_service.generate_card_image_name(
                    reserved_image_names
                )
                candidate_key = candidate.casefold()
                if candidate_key in reserved_image_names:
                    raise CardImageError(
                        f"Suggested image name {candidate!r} is already in use."
                    )
                large, small = resolved.image_pair
                staged.large_image_source = large
                staged.small_image_source = small
                staged.image_name = candidate
                staged.dirty = True
                reserved_image_names.add(candidate_key)
                applied.extend(("image_name", "large_image", "small_image"))
                image_staged = True
            except Exception as error:
                logging.warning("Card image name allocation failed: %s", error)
                allocation_error = f"image name: {error}"
                image_error = (
                    allocation_error
                    if image_error is None
                    else f"{image_error}; {allocation_error}"
                )
        return CardSuggestionResult(
            staged,
            tuple(applied),
            resolved.reference_found,
            image_staged,
            image_error,
            resolved.reference_source,
            resolved.reference_confidence,
        )

    @staticmethod
    def select_image_lookup_name(
        reference: CardReferenceData,
        draft: CardEditDraft,
        original_query: tuple[str, str],
    ) -> str | None:
        matched_english_name = (
            reference.matched_name
            if reference.matched_language == DEFAULT_LANGUAGE
            else None
        )
        original_name, original_language = original_query
        candidates = (
            matched_english_name,
            reference.localized_names.get(DEFAULT_LANGUAGE),
            draft.localized_text.names.get(DEFAULT_LANGUAGE),
            original_name if original_language == DEFAULT_LANGUAGE else None,
        )
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            normalized = " ".join(candidate.split())
            if normalized:
                return normalized
        return None

    @staticmethod
    def select_image_lookup_password(value: object) -> str | None:
        try:
            normalized = normalize_card_password(value)
        except ValueError:
            return None
        return None if normalized == MISSING_CARD_PASSWORD else normalized

    @staticmethod
    def _apply_suggestion_defaults(draft: CardEditDraft) -> tuple[str, ...]:
        applied: list[str] = []
        if (
            draft.pack is None or not str(draft.pack).strip()
        ) and "pack" not in draft.touched_fields:
            draft.pack = "disabled"
            applied.append("pack")
        if applied:
            draft.dirty = True
        return tuple(applied)

    @staticmethod
    def is_missing_suggest_field(
        card: CardEditDraft,
        field_name: str,
    ) -> bool:
        if field_name == "password":
            return is_missing_card_password(card.password)
        if field_name in _CARD_NUMERIC_EDIT_FIELDS:
            value = getattr(card, field_name)
            return value is None or (isinstance(value, str) and not value.strip())
        code_fields = {
            "attribute": "attribute_code",
            "card_type": "monster_type_code",
            "card_category": "card_category_code",
        }
        if field_name not in code_fields:
            raise ValueError(f"Unsupported Suggest field {field_name!r}.")
        raw_code = getattr(card, code_fields[field_name])
        if raw_code is not None:
            try:
                code = int(raw_code)
                if field_name == "attribute":
                    encoded_value = ATTRIBUTE_LABELS.get(code, "")
                elif field_name == "card_type":
                    encoded_value = MONSTER_TYPE_LABELS.get(code, "")
                else:
                    encoded_value = (
                        SPELL_TRAP_SUBTYPE_LABELS
                        if CardService._suggest_card_kind(card) in {"spell", "trap"}
                        else MONSTER_CATEGORY_LABELS
                    ).get(code, "")
                return not bool(encoded_value)
            except (TypeError, ValueError):
                pass
        value = getattr(card, field_name)
        if value is None or not str(value).strip():
            return True
        return field_name == "card_type" and card.is_new and value == "non_game_card"

    def bulk_suggest_missing_text(
        self,
        cards: Sequence[CardEditDraft],
        *,
        manifest: ProjectManifest | None = None,
        is_cancelled: Callable[[], bool] = lambda: False,
        report_progress: Callable[[int, int], None] = lambda _done, _total: None,
    ) -> BulkSuggestionResult:
        started_at = perf_counter()
        staged = [card.clone() for card in cards]
        candidate_positions = [
            position
            for position, card in enumerate(staged)
            if self.is_suggest_candidate(card)
        ]
        total_candidates = len(candidate_positions)
        skipped_complete = len(staged) - total_candidates
        report_progress(0, total_candidates)
        available_memory = estimate_available_memory_bytes()
        worker_count = select_bulk_suggest_worker_count(
            total_candidates,
            available_memory,
        )
        needs_image_inventory = any(
            self._is_image_suggest_candidate(staged[position])
            for position in candidate_positions
        )
        reserved_image_names: set[str] = set()
        image_inventory_error = None
        if needs_image_inventory:
            try:
                reserved_image_names = {
                    name.casefold() for name in self.existing_card_image_names(manifest)
                }
            except Exception as error:
                logging.warning("Bulk card image inventory failed: %s", error)
                image_inventory_error = f"image inventory: {error}"
        resolved = 0
        partially_filled = 0
        not_found = 0
        skipped_no_name = 0
        unchanged = 0
        failed = 0
        image_staged = 0
        image_failed = 0
        processed = 0
        cancelled = False

        outcomes: dict[int, _ResolvedCardSuggestion | BaseException | None] = {}
        next_submit = 0
        next_commit = 0
        maximum_window = max(1, worker_count * 2)
        futures: dict[Future[_ResolvedCardSuggestion], int] = {}

        def submit_available(executor: ThreadPoolExecutor) -> None:
            nonlocal next_submit
            while (
                not cancelled
                and next_submit < total_candidates
                and next_submit - next_commit < maximum_window
            ):
                position = candidate_positions[next_submit]
                future = executor.submit(
                    self._resolve_card_suggestion,
                    staged[position],
                    include_image=True,
                    preferred_language=None,
                    image_download_error=image_inventory_error,
                    require_query=False,
                )
                futures[future] = next_submit
                next_submit += 1

        if worker_count and is_cancelled():
            cancelled = True
        if worker_count and not cancelled:
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="card-suggest",
            ) as executor:
                submit_available(executor)
                while futures or next_commit < next_submit:
                    completed, _pending = wait(
                        tuple(futures),
                        timeout=0.05,
                        return_when=FIRST_COMPLETED,
                    )
                    if not completed and is_cancelled():
                        cancelled = True
                        for future in futures:
                            future.cancel()
                        break
                    for future in completed:
                        ordinal = futures.pop(future)
                        try:
                            outcomes[ordinal] = future.result()
                        except CancelledError:
                            outcomes[ordinal] = None
                        except BaseException as error:
                            outcomes[ordinal] = error

                    while next_commit in outcomes:
                        outcome = outcomes.pop(next_commit)
                        position = candidate_positions[next_commit]
                        card = staged[position]
                        next_commit += 1
                        if outcome is None:
                            continue
                        if isinstance(outcome, BaseException):
                            logging.error(
                                "Bulk card suggestion failed for card index %s, ID %s.",
                                card.card_index,
                                card.card_id,
                                exc_info=(
                                    type(outcome),
                                    outcome,
                                    outcome.__traceback__,
                                ),
                            )
                            failed += 1
                        elif outcome.no_query:
                            skipped_no_name += 1
                            unchanged += 1
                        else:
                            suggestion = self._finalize_resolved_suggestion(
                                manifest,
                                outcome,
                                reserved_image_names=reserved_image_names,
                            )
                            if suggestion.image_staged:
                                image_staged += 1
                            if suggestion.image_error is not None:
                                image_failed += 1
                            if suggestion.applied_fields:
                                staged[position] = suggestion.draft
                                resolved += 1
                                if self.is_suggest_candidate(suggestion.draft):
                                    partially_filled += 1
                            elif not suggestion.reference_found:
                                not_found += 1
                                unchanged += 1
                            else:
                                unchanged += 1
                        processed += 1
                        report_progress(processed, total_candidates)
                        if is_cancelled():
                            cancelled = True
                            for future in futures:
                                future.cancel()
                            break

                    if cancelled:
                        break
                    if not cancelled:
                        submit_available(executor)

        duration = perf_counter() - started_at
        logging.info(
            "Bulk Suggest completed: source_cards=%d candidates=%d "
            "skipped_complete=%d workers=%d available_memory_bytes=%s "
            "duration_seconds=%.3f resolved=%d partially_filled=%d "
            "not_found=%d no_query=%d unchanged=%d failed=%d "
            "image_staged=%d image_failed=%d cancelled=%s",
            len(staged),
            total_candidates,
            skipped_complete,
            worker_count,
            available_memory,
            duration,
            resolved,
            partially_filled,
            not_found,
            skipped_no_name,
            unchanged,
            failed,
            image_staged,
            image_failed,
            cancelled,
        )
        return BulkSuggestionResult(
            cards=tuple(staged),
            total_candidates=total_candidates,
            resolved=resolved,
            partially_filled=partially_filled,
            not_found=not_found,
            skipped_no_query_name=skipped_no_name,
            unchanged=unchanged,
            failed=failed,
            cancelled=cancelled,
            image_staged=image_staged,
            image_failed=image_failed,
            total_source_cards=len(staged),
            skipped_complete=skipped_complete,
            selected_workers=worker_count,
            available_memory_bytes=available_memory,
        )

    def _apply_staged_images(
        self,
        repository: ProjectRepository,
        drafts: Sequence[CardEditDraft],
    ) -> _StagedImageSaveStats:
        image_drafts = [
            draft
            for draft in drafts
            if draft.large_image_source is not None
            or draft.small_image_source is not None
        ]
        if not image_drafts:
            return _StagedImageSaveStats()

        additions: list[NamedCardImagePair] = []
        replacements: list[CardEditDraft] = []
        for draft in image_drafts:
            large = draft.large_image_source
            small = draft.small_image_source
            if not repository.card_image_pair_exists(draft.image_name):
                if large is None or small is None:
                    raise CardImageError(
                        f"New image {draft.image_name} requires a complete pair."
                    )
                additions.append(
                    NamedCardImagePair(
                        image_name=draft.image_name,
                        large_source=large,
                        mini_source=small,
                    )
                )
                continue
            replacements.append(draft)

        if additions:
            repository.add_named_card_images_batch(
                additions,
                save_manifest=False,
            )
        for draft in replacements:
            large = draft.large_image_source
            small = draft.small_image_source
            if large is not None:
                repository.replace_card_image(draft.image_name, large, mini=False)
            if small is not None:
                repository.replace_card_image(draft.image_name, small, mini=True)
        return _StagedImageSaveStats(
            new_pairs=len(additions),
            replacement_pairs=len(replacements),
        )

    @staticmethod
    def _row_to_detail(row: Mapping[str, object]) -> CardDetailData:
        localized = CardLocalizedText(
            names={
                language: str(row.get(card_name_column(language), ""))
                for language in LANGUAGE_PREFIXES
            },
            descriptions={
                language: str(row.get(card_description_column(language), ""))
                for language in LANGUAGE_PREFIXES
            },
        )
        return CardDetailData(
            card_index=int(row["card_index"]),
            card_id=int(row["card_id"]),
            localized_text=localized,
            password=normalize_card_password(row["passcode"]),
            level=int(row["level"]),
            attack=int(row["attack"]),
            defense=int(row["defense"]),
            attribute=normalize_property_label(row["attribute"]),
            card_type=normalize_property_label(row["monster_type"]),
            card_category=normalize_property_label(row["card_category"]),
            pack=str(row["pack"]),
            image_name=str(row.get("image_name", "")),
            note=str(row.get("note", "")),
            monster_type_code=int(row["monster_type_code"]),
            card_category_code=int(row["card_category_code"]),
            attribute_code=int(row["attribute_code"]),
        )

    @staticmethod
    def _draft_to_row(draft: CardEditDraft) -> dict[str, object]:
        row: dict[str, object] = {
            "card_index": draft.card_index,
            "card_id": draft.card_id,
            "passcode": normalize_card_password(draft.password),
            "pack": draft.pack,
            "attack": CardService._optional_int_value(draft.attack),
            "defense": CardService._optional_int_value(draft.defense),
            "monster_type": draft.card_type,
            "card_category": draft.card_category,
            "attribute": draft.attribute,
            "monster_type_code": draft.monster_type_code,
            "card_category_code": draft.card_category_code,
            "attribute_code": draft.attribute_code,
            "level": CardService._optional_int_value(draft.level),
            "image_name": draft.image_name,
            "note": draft.note,
            "name": draft.localized_text.names[DEFAULT_LANGUAGE],
            "description": draft.localized_text.descriptions[DEFAULT_LANGUAGE],
        }
        for language in LANGUAGE_PREFIXES:
            row[card_name_column(language)] = draft.localized_text.names[language]
            row[card_description_column(language)] = draft.localized_text.descriptions[
                language
            ]
        return row

    @staticmethod
    def _card_to_csv_row(
        card: CardDetailData | CardEditDraft,
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "card_index": card.card_index,
            "card_id": card.card_id,
            "password": "" if card.password is None else card.password,
            "level": "" if card.level is None else card.level,
            "attack": "" if card.attack is None else card.attack,
            "defense": "" if card.defense is None else card.defense,
            "attribute": card.attribute,
            "card_type": card.card_type,
            "card_category": card.card_category,
            "pack": card.pack,
            "image_name": card.image_name,
        }
        for language in LANGUAGE_PREFIXES:
            row[card_name_column(language)] = card.localized_text.names[language]
            row[card_description_column(language)] = card.localized_text.descriptions[
                language
            ]
        return row

    def _apply_csv_row(
        self,
        current: CardEditDraft,
        row: dict[str, str] | object,
    ) -> CardEditDraft:
        values = dict(row)
        draft = current.clone()
        draft.password = normalize_card_password(values["password"])
        draft.touched_fields.add("password")
        for field_name in _CARD_NUMERIC_EDIT_FIELDS:
            raw = values[field_name].strip()
            setattr(draft, field_name, None if not raw else int(raw))
            draft.touched_fields.add(field_name)
        for field_name in _CARD_ENUM_EDIT_FIELDS:
            raw = values[field_name].strip()
            if field_name == "pack" and not raw:
                raise ValueError(f"{field_name} must not be blank.")
            setattr(draft, field_name, raw)
            draft.mark_touched(field_name)
        for language in LANGUAGE_PREFIXES:
            draft.localized_text.names[language] = values[card_name_column(language)]
            draft.localized_text.descriptions[language] = values[
                card_description_column(language)
            ]
            draft.touched_fields.update((f"name:{language}", f"description:{language}"))
        draft.dirty = True
        errors = self.validate_card_draft(draft)
        if errors:
            raise ValueError("; ".join(errors))
        return draft

    @staticmethod
    def _validate_optional_integer(
        value: object,
        field_name: str,
        minimum: int,
        maximum: int,
        errors: list[str],
    ) -> None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return
        if isinstance(value, bool):
            errors.append(f"{field_name} must be an integer, got {value!r}.")
            return
        try:
            integer = int(value)
        except (TypeError, ValueError):
            errors.append(f"{field_name} must be an integer, got {value!r}.")
            return
        if str(value).strip().lstrip("+").isdigit() is False:
            errors.append(f"{field_name} must be an integer, got {value!r}.")
            return
        if not minimum <= integer <= maximum:
            errors.append(
                f"{field_name} {integer} must be between {minimum} and {maximum}."
            )

    @staticmethod
    def _validate_password(value: object, errors: list[str]) -> None:
        try:
            normalize_card_password(value)
        except ValueError as error:
            errors.append(f"password: {error}")

    @staticmethod
    def _validate_stat(
        value: object,
        field_name: str,
        errors: list[str],
    ) -> None:
        before = len(errors)
        CardService._validate_optional_integer(
            value,
            field_name,
            CARD_STAT_MIN,
            CARD_STAT_MAX,
            errors,
        )
        if len(errors) != before or value is None or str(value).strip() == "":
            return
        if int(value) % CARD_STAT_STEP:
            errors.append(
                f"{field_name} {int(value)} must be representable in steps of "
                f"{CARD_STAT_STEP} from {CARD_STAT_MIN} to {CARD_STAT_MAX}."
            )

    @staticmethod
    def _validate_enum(
        value: object,
        field_name: str,
        allowed: frozenset[str],
        errors: list[str],
    ) -> None:
        normalized = normalize_property_label(value)
        if normalized not in allowed:
            errors.append(
                f"Unsupported {field_name} {value!r}; allowed values: "
                + ", ".join(sorted(allowed))
            )

    @staticmethod
    def _validate_card_category(
        draft: CardEditDraft,
        errors: list[str],
    ) -> None:
        try:
            class_code = (
                int(draft.monster_type_code)
                if draft.monster_type_code is not None
                else code_for_property_label(
                    draft.card_type,
                    MONSTER_TYPE_LABELS,
                    field="card_type",
                )
            )
        except (TypeError, ValueError):
            return
        if 1 <= class_code <= 20:
            allowed = frozenset(MONSTER_CATEGORY_LABELS.values())
        elif class_code in {21, 22}:
            allowed = frozenset(SPELL_TRAP_SUBTYPE_LABELS.values())
        else:
            allowed = frozenset({""})
        CardService._validate_enum(
            draft.card_category,
            "card_category",
            allowed,
            errors,
        )

    @staticmethod
    def _optional_int_value(value: object) -> int:
        if value is None or (isinstance(value, str) and not value.strip()):
            return 0
        return int(value)

    @staticmethod
    def suggest_required_fields(card: CardEditDraft) -> tuple[str, ...]:
        localized_fields = tuple(
            field_name
            for language in LANGUAGE_PREFIXES
            for field_name in (f"name:{language}", f"description:{language}")
        )
        return (
            *localized_fields,
            *CardService._suggest_required_scalar_fields(card),
            "image",
        )

    @staticmethod
    def missing_suggest_fields(card: CardEditDraft) -> tuple[str, ...]:
        missing: list[str] = []
        for field_name in CardService.suggest_required_fields(card):
            if field_name == "image":
                if CardService._is_image_suggest_candidate(card):
                    missing.append(field_name)
                continue
            if field_name in card.touched_fields:
                continue
            field_kind, separator, language = field_name.partition(":")
            if separator:
                values = (
                    card.localized_text.names
                    if field_kind == "name"
                    else card.localized_text.descriptions
                )
                if not values[language].strip():
                    missing.append(field_name)
                continue
            if CardService.is_missing_suggest_field(card, field_name):
                missing.append(field_name)
        return tuple(missing)

    @staticmethod
    def is_suggest_candidate(card: CardEditDraft) -> bool:
        return bool(CardService.missing_suggest_fields(card))

    @staticmethod
    def _has_missing_suggest_fields(card: CardEditDraft) -> bool:
        return CardService.is_suggest_candidate(card)

    @staticmethod
    def _suggest_required_scalar_fields(card: CardEditDraft) -> tuple[str, ...]:
        common = ("password", "card_type", "card_category")
        if CardService._suggest_card_kind(card) in {"spell", "trap"}:
            return common
        return (
            "password",
            "level",
            "attack",
            "defense",
            "attribute",
            "card_type",
            "card_category",
        )

    @staticmethod
    def _suggest_card_kind(card: CardEditDraft) -> str:
        if card.monster_type_code is not None:
            try:
                code = int(card.monster_type_code)
            except (TypeError, ValueError):
                return "unknown"
            if 1 <= code <= 20:
                return "monster"
            if code == 21:
                return "trap"
            if code == 22:
                return "spell"
            return "unknown"

        normalized_type = normalize_property_label(card.card_type)
        if normalized_type == "trap_card":
            return "trap"
        if normalized_type == "spell_card":
            return "spell"
        monster_labels = frozenset(
            label for code, label in MONSTER_TYPE_LABELS.items() if 1 <= code <= 20
        )
        return "monster" if normalized_type in monster_labels else "unknown"

    @staticmethod
    def _is_image_suggest_candidate(card: CardEditDraft) -> bool:
        return CardService._needs_suggested_image(card) and (
            CardService.select_image_lookup_password(card.password) is not None
            or CardService.select_suggestion_query(card) is not None
        )

    @staticmethod
    def _needs_suggested_image(card: CardEditDraft) -> bool:
        return card.image_name.casefold() == TOKEN_CARD_IMAGE_NAME.casefold()

    def _project(
        self,
        manifest: ProjectManifest | None,
    ) -> ProjectRepository:
        if manifest is not None:
            if self._repository is not None:
                if self._repository.root.resolve() == manifest.root.resolve():
                    return self._repository
                return self._repository.with_manifest(manifest)
            return ProjectRepository(manifest)
        if self._repository is None:
            raise ValueError("A project manifest or repository is required.")
        return self._repository
