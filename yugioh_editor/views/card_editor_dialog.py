from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QRegularExpression, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QIntValidator, QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from yugioh_editor.common.card_images import (
    TOKEN_CARD_IMAGE_NAME,
    generate_unique_card_image_name,
)
from yugioh_editor.common.card_passwords import (
    CARD_PASSWORD_HEX_WIDTH,
    normalize_card_password,
)
from yugioh_editor.common.card_properties import (
    ATTRIBUTE_LABELS,
    CARD_LEVEL_MAX,
    CARD_LEVEL_MIN,
    CARD_STAT_MAX,
    CARD_STAT_MIN,
    MONSTER_CATEGORY_LABELS,
    MONSTER_TYPE_LABELS,
    SPELL_TRAP_SUBTYPE_LABELS,
    code_for_property_label,
    display_property_label,
)
from yugioh_editor.common.constants import (
    DEFAULT_LANGUAGE,
    LANGUAGE_PREFIXES,
    PACK_NAMES,
    ui_path,
)
from yugioh_editor.models.card_editing import (
    CardEditDraft,
    CardSuggestionResult,
)
from yugioh_editor.models.entities import ProjectManifest
from yugioh_editor.services.card_service import CardService
from yugioh_editor.views.ui_loader import load_ui
from yugioh_editor.workers.task_runner import TaskError, TaskRunner


def fit_pixmap_without_upscaling(
    pixmap: QPixmap,
    available_size: QSize,
) -> QPixmap:
    """Fit a pixmap inside a preview area without enlarging its source pixels."""
    if pixmap.isNull() or available_size.width() <= 0 or available_size.height() <= 0:
        return QPixmap()
    if (
        pixmap.width() <= available_size.width()
        and pixmap.height() <= available_size.height()
    ):
        return pixmap
    return pixmap.scaled(
        available_size,
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )


class CardEditorDialog(QDialog):
    saved = Signal(object)

    def __init__(
        self,
        manifest: ProjectManifest,
        card_service: CardService,
        draft: CardEditDraft,
        parent=None,
        *,
        card_lookup: Callable[[int], CardEditDraft | None] | None = None,
        card_bounds: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._manifest = manifest
        self._service = card_service
        self._draft = draft.clone()
        self._card_lookup = card_lookup
        self._card_bounds = card_bounds
        self._current_language = DEFAULT_LANGUAGE
        self._loading = True
        self._discarding = False
        self._thread_pool = QThreadPool.globalInstance()
        self._suggest_runner: TaskRunner | None = None
        self._image_runner: TaskRunner | None = None
        self._save_runner: TaskRunner | None = None
        self._suggest_request = 0
        self._image_request = 0
        self._image_cache: dict[str, tuple[bytes, bytes]] = {}
        self._original_image_pixmaps: dict[bool, QPixmap] = {}

        self.setWindowTitle("Add Card" if draft.is_new else "Card Detail")
        root = load_ui(ui_path("card_editor_dialog.ui"), self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(root)

        self._card_index = self.findChild(QLineEdit, "txtCardIndex")
        self._card_id = self.findChild(QLineEdit, "txtCardId")
        self._language = self.findChild(QComboBox, "cmbLanguage")
        self._name = self.findChild(QLineEdit, "txtCardName")
        self._description = self.findChild(QPlainTextEdit, "txtDescription")
        self._password = self.findChild(QLineEdit, "txtPassword")
        self._level = self.findChild(QLineEdit, "txtLevel")
        self._attack = self.findChild(QLineEdit, "txtAttack")
        self._defense = self.findChild(QLineEdit, "txtDefense")
        self._attribute = self.findChild(QComboBox, "cmbAttribute")
        self._card_type = self.findChild(QComboBox, "cmbCardType")
        self._card_category = self.findChild(QComboBox, "cmbCardCategory")
        self._pack = self.findChild(QComboBox, "cmbPack")
        self._image_name = self.findChild(QLineEdit, "txtImageName")
        self._image_layout = self.findChild(QHBoxLayout, "imageLayout")
        self._large_image_frame = self.findChild(QFrame, "frameLargeImage")
        self._small_image_frame = self.findChild(QFrame, "frameSmallImage")
        self._large_image = self.findChild(QLabel, "lblLargeImage")
        self._small_image = self.findChild(QLabel, "lblSmallImage")
        self._image_layout.setStretch(0, 1)
        self._image_layout.setStretch(1, 1)
        self._small_image_frame.layout().setAlignment(
            self._small_image,
            Qt.AlignCenter,
        )
        self._status = self.findChild(QLabel, "lblStatus")
        self._suggest_button = self.findChild(QPushButton, "btnSuggest")
        self._previous_button = self.findChild(QPushButton, "btnPrevious")
        self._next_button = self.findChild(QPushButton, "btnNext")
        self._numeric_fields = {
            "level": self._level,
            "attack": self._attack,
            "defense": self._defense,
        }
        self._enum_fields = {
            "attribute": self._attribute,
            "card_type": self._card_type,
            "card_category": self._card_category,
            "pack": self._pack,
        }

        self._card_index.setText(str(self._draft.card_index))
        self._card_id.setText(str(self._draft.card_id))
        self._language.addItems(LANGUAGE_PREFIXES)
        self._populate_combo(self._attribute, ATTRIBUTE_LABELS.values())
        self._populate_combo(self._card_type, MONSTER_TYPE_LABELS.values())
        self._populate_combo(self._pack, PACK_NAMES.values())
        self._set_combo_value(self._attribute, self._draft.attribute)
        self._set_combo_value(self._card_type, self._draft.card_type)
        self._populate_card_categories(self._draft.card_category)
        self._set_combo_value(self._card_category, self._draft.card_category)
        self._set_combo_value(self._pack, self._draft.pack)
        self._password.setMaxLength(CARD_PASSWORD_HEX_WIDTH)
        self._password.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(rf"[0-9A-Fa-f]{{{CARD_PASSWORD_HEX_WIDTH}}}"),
                self,
            )
        )
        self._password.setText(self._display_password(self._draft.password))
        self._level.setText(self._display_optional(self._draft.level))
        self._attack.setText(self._display_optional(self._draft.attack))
        self._defense.setText(self._display_optional(self._draft.defense))
        self._level.setValidator(QIntValidator(CARD_LEVEL_MIN, CARD_LEVEL_MAX, self))
        self._attack.setValidator(QIntValidator(CARD_STAT_MIN, CARD_STAT_MAX, self))
        self._defense.setValidator(QIntValidator(CARD_STAT_MIN, CARD_STAT_MAX, self))
        self._image_name.setText(self._draft.image_name)
        self._load_localized_text(DEFAULT_LANGUAGE)
        self._refresh_image_previews()

        self._language.currentTextChanged.connect(self._change_language)
        self._card_type.currentIndexChanged.connect(self._card_type_changed)
        self._name.textChanged.connect(
            lambda: self._mark_touched(f"name:{self._current_language}")
        )
        self._description.textChanged.connect(
            lambda: self._mark_touched(f"description:{self._current_language}")
        )
        self._password.textChanged.connect(lambda: self._mark_touched("password"))
        for field_name, widget in self._numeric_fields.items():
            widget.textChanged.connect(
                lambda _text, name=field_name: self._mark_touched(name)
            )
        for field_name, widget in self._enum_fields.items():
            widget.currentIndexChanged.connect(
                lambda _index, name=field_name: self._mark_touched(name)
            )
        for preview_widget in (
            self._large_image_frame,
            self._small_image_frame,
            self._large_image,
            self._small_image,
        ):
            preview_widget.installEventFilter(self)
        self._suggest_button.clicked.connect(self._suggest)
        self._previous_button.clicked.connect(lambda: self._navigate(-1))
        self._next_button.clicked.connect(lambda: self._navigate(1))
        self._save_button = self.findChild(QPushButton, "btnSave")
        self._save_button.clicked.connect(self._save_and_close)
        self.findChild(QPushButton, "btnClose").clicked.connect(self.close)
        self._loading = False
        self._update_navigation_buttons()

    @property
    def draft(self) -> CardEditDraft:
        self._flush_controls()
        return self._draft.clone()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Resize and watched in {
            self._large_image_frame,
            self._small_image_frame,
        }:
            self._scale_image_previews()
        if event.type() == QEvent.MouseButtonDblClick:
            if watched in {self._large_image_frame, self._large_image}:
                self._choose_image(mini=False)
                return True
            if watched in {self._small_image_frame, self._small_image}:
                self._choose_image(mini=True)
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._scale_image_previews()

    def closeEvent(self, event) -> None:
        if self._discarding or not self._draft.dirty:
            event.ignore()
            self.reject()
            return
        answer = QMessageBox.warning(
            self,
            "Unsaved Card Changes",
            "This card has unsaved changes.",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Save:
            event.ignore()
            self._start_commit(self.accept)
        elif answer == QMessageBox.Discard:
            self._discarding = True
            event.ignore()
            self.reject()
        else:
            event.ignore()

    def _change_language(self, language: str) -> None:
        if self._loading or language == self._current_language:
            return
        self._flush_localized_text()
        self._current_language = language
        self._load_localized_text(language)

    def _flush_localized_text(self) -> None:
        self._draft.localized_text.names[self._current_language] = self._name.text()
        self._draft.localized_text.descriptions[self._current_language] = (
            self._description.toPlainText()
        )

    def _load_localized_text(self, language: str) -> None:
        previous = self._loading
        self._loading = True
        self._name.setText(self._draft.localized_text.names[language])
        self._description.setPlainText(
            self._draft.localized_text.descriptions[language]
        )
        self._loading = previous

    def _flush_controls(self) -> None:
        self._flush_localized_text()
        password = self._password.text().strip().upper()
        self._draft.password = password
        if self._password.text() != password:
            self._password.setText(password)
        for field_name, widget in self._numeric_fields.items():
            setattr(self._draft, field_name, self._optional_number(widget.text()))
        for field_name, widget in self._enum_fields.items():
            setattr(self._draft, field_name, str(widget.currentData()))

    def _mark_touched(self, field_name: str) -> None:
        if not self._loading:
            self._draft.mark_touched(field_name)

    def _save_and_close(self) -> None:
        self._start_commit(self.accept)

    def _start_commit(self, after_success: Callable[[], None]) -> bool:
        if self._save_runner is not None:
            return False
        self._flush_controls()
        errors = self._service.validate_card_draft(self._draft)
        if errors:
            QMessageBox.warning(
                self,
                "Invalid Card Data",
                "Please correct the following fields:\n\n- " + "\n- ".join(errors),
            )
            self._focus_first_error(errors[0])
            return False
        draft = self._draft.clone()
        runner = TaskRunner(
            lambda: (
                self._service.create_card(self._manifest, draft)
                if draft.is_new
                else self._service.update_card(self._manifest, draft)
            )
        )
        self._save_runner = runner
        self._save_button.setEnabled(False)
        self._status.setText("Saving card...")
        runner.signals.succeeded.connect(
            lambda saved: self._commit_succeeded(saved, after_success)
        )
        runner.signals.failed.connect(self._commit_failed)
        runner.signals.finished.connect(lambda: self._commit_finished(runner))
        self._thread_pool.start(runner)
        return True

    def _commit_succeeded(self, saved, after_success: Callable[[], None]) -> None:
        self._draft = saved.to_draft()
        self._draft.dirty = False
        self.saved.emit(self._draft.clone())
        self._status.setText("Card saved.")
        after_success()

    def _commit_failed(self, error: TaskError) -> None:
        self._status.setText("Card save failed.")
        QMessageBox.critical(self, "Save Card Error", str(error))

    def _commit_finished(self, runner: TaskRunner) -> None:
        if self._save_runner is runner:
            self._save_runner = None
            self._save_button.setEnabled(True)

    def _commit(self) -> bool:
        self._flush_controls()
        errors = self._service.validate_card_draft(self._draft)
        if errors:
            QMessageBox.warning(
                self,
                "Invalid Card Data",
                "Please correct the following fields:\n\n- " + "\n- ".join(errors),
            )
            self._focus_first_error(errors[0])
            return False
        try:
            saved = (
                self._service.create_card(self._manifest, self._draft)
                if self._draft.is_new
                else self._service.update_card(self._manifest, self._draft)
            )
        except Exception as error:
            logging.exception(
                "Saving card index %s (ID %s) failed.",
                self._draft.card_index,
                self._draft.card_id,
            )
            QMessageBox.critical(self, "Save Card Error", str(error))
            return False
        self._draft = saved.to_draft()
        self._draft.dirty = False
        self.saved.emit(self._draft.clone())
        return True

    def _suggest(self) -> None:
        self._flush_controls()
        query = CardService.select_suggestion_query(
            self._draft,
            self._current_language,
        )
        if query is None:
            QMessageBox.information(
                self,
                "Suggest Card Data",
                "At least one card name is required before suggesting data.",
            )
            self._name.setFocus()
            return
        self._suggest_button.setEnabled(False)
        self._status.setText("Looking up card reference data...")
        self._suggest_request += 1
        request = (self._suggest_request, self._draft.card_index)
        runner = TaskRunner(
            lambda: (
                request,
                self._service.suggest_card_draft(
                    self._manifest,
                    self._draft.clone(),
                    preferred_language=self._current_language,
                ),
            )
        )
        self._suggest_runner = runner
        runner.signals.succeeded.connect(self._suggest_succeeded)
        runner.signals.failed.connect(self._suggest_failed)
        runner.signals.finished.connect(self._suggest_finished)
        self._thread_pool.start(runner)

    def _suggest_succeeded(
        self,
        payload: tuple[tuple[int, int], CardSuggestionResult],
    ) -> None:
        request, result = payload
        if request != (self._suggest_request, self._draft.card_index):
            return
        if not result.reference_found:
            self._status.setText("Card reference not found.")
            return
        self._draft = result.draft.clone()
        self._reload_from_draft()
        source_labels = {
            "official_direct": "Official direct",
            "official_after_alias": "Official after alias",
            "ygocdb_fallback": "YGOCDB fallback",
        }
        status = source_labels.get(result.reference_source, result.reference_source)
        status += ". Suggested fields: " + (
            ", ".join(result.applied_fields) if result.applied_fields else "none"
        )
        if result.image_error:
            status += f". Card data applied; image unavailable: {result.image_error}"
        self._status.setText(status)

    def _suggest_failed(self, error: TaskError) -> None:
        message = str(error)
        self._status.setText(
            "Ambiguous card reference."
            if "multiple exact" in message.casefold() or "ambigu" in message.casefold()
            else "Card reference provider error."
        )
        QMessageBox.warning(self, "Suggest Card Data", str(error))

    def _suggest_finished(self) -> None:
        self._suggest_button.setEnabled(True)
        self._suggest_runner = None

    def _reload_from_draft(self) -> None:
        self._loading = True
        self._password.setText(self._display_password(self._draft.password))
        self._level.setText(self._display_optional(self._draft.level))
        self._attack.setText(self._display_optional(self._draft.attack))
        self._defense.setText(self._display_optional(self._draft.defense))
        self._set_combo_value(self._attribute, self._draft.attribute)
        self._set_combo_value(self._card_type, self._draft.card_type)
        self._populate_card_categories(self._draft.card_category)
        self._set_combo_value(self._card_category, self._draft.card_category)
        self._set_combo_value(self._pack, self._draft.pack)
        self._image_name.setText(self._draft.image_name)
        self._load_localized_text(self._current_language)
        self._loading = False
        self._refresh_image_previews()
        self._update_navigation_buttons()

    def _update_navigation_buttons(self) -> None:
        if self._draft.is_new:
            self._previous_button.hide()
            self._next_button.hide()
            return
        self._previous_button.show()
        self._next_button.show()
        try:
            minimum, maximum = (
                self._card_bounds()
                if self._card_bounds is not None
                else self._service.card_index_bounds(self._manifest)
            )
        except Exception:
            minimum = maximum = self._draft.card_index
        self._previous_button.setEnabled(self._draft.card_index > minimum)
        self._next_button.setEnabled(self._draft.card_index < maximum)

    def _navigate(self, offset: int) -> None:
        if self._draft.is_new or offset not in {-1, 1}:
            return
        target_index = self._draft.card_index + offset
        self._flush_controls()
        if self._draft.dirty:
            answer = QMessageBox.warning(
                self,
                "Unsaved Card Changes",
                "Save changes before navigating to another card?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.Save:
                self._start_commit(lambda: self._navigate_to(target_index))
                return
        self._navigate_to(target_index)

    def _navigate_to(self, target_index: int) -> None:
        try:
            detail = (
                self._card_lookup(target_index)
                if self._card_lookup is not None
                else self._service.get_card_detail(self._manifest, target_index)
            )
            if detail is None:
                raise IndexError(f"Card index {target_index} was not found.")
        except Exception as error:
            logging.exception("Loading card index %s failed.", target_index)
            QMessageBox.warning(self, "Navigate Cards", str(error))
            return
        self._draft = (
            detail.clone() if isinstance(detail, CardEditDraft) else detail.to_draft()
        )
        self._draft.dirty = False
        self._card_index.setText(str(self._draft.card_index))
        self._card_id.setText(str(self._draft.card_id))
        self.setWindowTitle("Card Detail")
        self._original_image_pixmaps.clear()
        self._suggest_request += 1
        self._image_request += 1
        self._reload_from_draft()

    def _choose_image(self, *, mini: bool) -> None:
        first = self._pick_image("small" if mini else "large")
        if first is None:
            return
        try:
            self._service.validate_image_source(
                self._manifest,
                first,
                mini=mini,
            )
        except Exception as error:
            QMessageBox.warning(self, "Invalid Card Image", str(error))
            return
        if self._draft.image_name.casefold() == TOKEN_CARD_IMAGE_NAME.casefold():
            generated_name = generate_unique_card_image_name(
                self._service.existing_card_image_names(self._manifest)
            )
            other_label = "large" if mini else "small"
            QMessageBox.information(
                self,
                "Complete Image Pair",
                f"Select the matching {other_label} image for {generated_name}.",
            )
            second = self._pick_image(other_label)
            if second is None:
                return
            try:
                self._service.validate_image_source(
                    self._manifest,
                    second,
                    mini=not mini,
                )
            except Exception as error:
                QMessageBox.warning(self, "Invalid Card Image", str(error))
                return
            self._draft.image_name = generated_name
            if mini:
                self._draft.small_image_source = first
                self._draft.large_image_source = second
            else:
                self._draft.large_image_source = first
                self._draft.small_image_source = second
        elif mini:
            self._draft.small_image_source = first
        else:
            self._draft.large_image_source = first
        self._draft.dirty = True
        self._image_name.setText(self._draft.image_name)
        self._refresh_image_previews()

    def _pick_image(self, variant: str) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select {variant} card image",
            "",
            "Images (*.bmp *.png *.jpg *.jpeg *.gif)",
        )
        return Path(path) if path else None

    def _refresh_image_previews(self) -> None:
        self._original_image_pixmaps.clear()
        self._set_preview_message(self._large_image, "Loading large image...")
        self._set_preview_message(self._small_image, "Loading mini image...")
        large_source = self._draft.large_image_source
        small_source = self._draft.small_image_source
        if large_source is not None:
            self._set_image_preview(self._large_image, large_source, mini=False)
        if small_source is not None:
            self._set_image_preview(self._small_image, small_source, mini=True)
        self._scale_image_previews()
        key = self._draft.image_name.casefold()
        cached = self._image_cache.get(key)
        if cached is not None:
            self._apply_image_pair(key, cached)
            return
        if large_source is not None and small_source is not None:
            return
        if not key:
            self._mark_missing_previews_unavailable()
            return
        self._image_request += 1
        request = (self._image_request, self._draft.card_index, key)
        runner = TaskRunner(
            lambda: (request, self._service.load_card_images(self._manifest, key))
        )
        self._image_runner = runner
        runner.signals.succeeded.connect(self._image_pair_loaded)
        runner.signals.failed.connect(
            lambda error, active_request=request: self._image_pair_failed(
                active_request, error
            )
        )
        runner.signals.finished.connect(lambda: self._image_runner_finished(runner))
        self._thread_pool.start(runner)

    def _set_image_preview(
        self,
        label: QLabel,
        source: Path | bytes | None,
        *,
        mini: bool,
    ) -> None:
        pixmap = QPixmap()
        if isinstance(source, bytes):
            pixmap.loadFromData(source)
        elif source is not None:
            pixmap.load(str(source))
        else:
            return
        if pixmap.isNull():
            self._original_image_pixmaps.pop(mini, None)
            self._set_preview_message(label, "Image unavailable")
            return
        self._original_image_pixmaps[mini] = pixmap

    def _image_pair_loaded(
        self,
        payload: tuple[tuple[int, int, str], tuple[bytes, bytes]],
    ) -> None:
        request, pair = payload
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not all(isinstance(value, bytes) for value in pair)
        ):
            self._image_pair_failed(request, None)
            return
        _sequence, _card_index, key = request
        self._image_cache[key] = pair
        while len(self._image_cache) > 8:
            self._image_cache.pop(next(iter(self._image_cache)))
        if request != (
            self._image_request,
            self._draft.card_index,
            self._draft.image_name.casefold(),
        ):
            return
        self._apply_image_pair(key, pair)

    def _image_pair_failed(
        self,
        request: tuple[int, int, str],
        _error: TaskError | None,
    ) -> None:
        if request != (
            self._image_request,
            self._draft.card_index,
            self._draft.image_name.casefold(),
        ):
            return
        self._mark_missing_previews_unavailable()

    def _mark_missing_previews_unavailable(self) -> None:
        for mini, label, source in (
            (False, self._large_image, self._draft.large_image_source),
            (True, self._small_image, self._draft.small_image_source),
        ):
            if source is None and mini not in self._original_image_pixmaps:
                self._set_preview_message(label, "Image unavailable")

    def _apply_image_pair(self, key: str, pair: tuple[bytes, bytes]) -> None:
        if key != self._draft.image_name.casefold():
            return
        if self._draft.large_image_source is None:
            self._set_image_preview(self._large_image, pair[0], mini=False)
        if self._draft.small_image_source is None:
            self._set_image_preview(self._small_image, pair[1], mini=True)
        self._scale_image_previews()

    def _image_runner_finished(self, runner: TaskRunner) -> None:
        if self._image_runner is runner:
            self._image_runner = None

    def _scale_image_previews(self) -> None:
        large_pixmap = self._original_image_pixmaps.get(False)
        if large_pixmap is not None and not large_pixmap.isNull():
            self._large_image.setPixmap(
                large_pixmap.scaled(
                    self._preview_available_size(self._large_image_frame),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

        mini_pixmap = self._original_image_pixmaps.get(True)
        if mini_pixmap is None or mini_pixmap.isNull():
            return
        self._small_image.setPixmap(
            fit_pixmap_without_upscaling(
                mini_pixmap,
                self._preview_available_size(self._small_image_frame),
            )
        )
        self._small_image.updateGeometry()

    @staticmethod
    def _preview_available_size(frame: QFrame) -> QSize:
        frame_size = frame.contentsRect().size()
        margins = frame.layout().contentsMargins()
        return QSize(
            max(0, frame_size.width() - margins.left() - margins.right()),
            max(0, frame_size.height() - margins.top() - margins.bottom()),
        )

    @staticmethod
    def _set_preview_message(label: QLabel, message: str) -> None:
        label.clear()
        label.setText(message)
        label.updateGeometry()

    @staticmethod
    def _populate_combo(combo: QComboBox, values) -> None:
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            combo.addItem(display_property_label(value), value)

    def _populate_card_categories(self, selected: str | None = None) -> None:
        try:
            class_code = (
                int(self._draft.monster_type_code)
                if self._draft.monster_type_code is not None
                else code_for_property_label(
                    self._card_type.currentData(),
                    MONSTER_TYPE_LABELS,
                    field="card_type",
                )
            )
        except (TypeError, ValueError):
            class_code = 0
        if 1 <= class_code <= 20:
            values = MONSTER_CATEGORY_LABELS.values()
        elif class_code in {21, 22}:
            values = SPELL_TRAP_SUBTYPE_LABELS.values()
        else:
            values = ("",)
        previous = self._loading
        self._loading = True
        self._card_category.clear()
        self._populate_combo(self._card_category, values)
        self._set_combo_value(self._card_category, selected or "")
        self._loading = previous

    def _card_type_changed(self, _index: int) -> None:
        if self._loading:
            return
        self._draft.card_type = str(self._card_type.currentData())
        self._draft.monster_type_code = None
        current_category = str(self._card_category.currentData() or "")
        self._populate_card_categories(current_category)
        self._draft.card_category = str(self._card_category.currentData() or "")
        self._draft.card_category_code = None

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index < 0:
            combo.addItem(f"Unknown/Raw: {value}", value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    @staticmethod
    def _display_optional(value: object) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _display_password(value: object) -> str:
        if value is None:
            return ""
        try:
            return normalize_card_password(value)
        except ValueError:
            return str(value).strip().upper()

    @staticmethod
    def _optional_number(value: str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return stripped

    def _focus_first_error(self, error: str) -> None:
        fields = {
            "password": self._password,
            **self._numeric_fields,
            **self._enum_fields,
            "name:": self._name,
            "description:": self._description,
        }
        for prefix, widget in fields.items():
            if error.startswith(prefix):
                widget.setFocus()
                break
