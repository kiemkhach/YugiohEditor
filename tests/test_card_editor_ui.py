from __future__ import annotations

import importlib.util
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import ANY, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None

if PYSIDE_AVAILABLE:
    from PySide6.QtCore import QRect, QSize, Qt, QThread
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QComboBox,
        QFileDialog,
        QFrame,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QWidget,
    )

    from yugioh_editor.common.constants import LANGUAGE_PREFIXES
    from yugioh_editor.models.card_editing import (
        BulkSuggestionResult,
        CardDetailData,
        CardLocalizedText,
        CardSuggestionResult,
    )
    from yugioh_editor.models.entities import ProjectManifest
    from yugioh_editor.services.card_service import CardService
    from yugioh_editor.views.card_editor_dialog import (
        CardEditorDialog,
        fit_pixmap_without_upscaling,
    )
    from yugioh_editor.views.card_list_model import (
        CardListModel,
        UnusedCardFilterProxyModel,
    )
    from yugioh_editor.views.card_list_view import CardListView


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is required for card editor tests.")
class CardEditorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.manifest = ProjectManifest(
            "Cards",
            self.temporary.name,
            version_prefix="mai",
        )
        self.service = Mock(spec=CardService)
        self.service.load_card_image.side_effect = KeyError("fixture has no image")
        self.service.load_card_images.return_value = (b"", b"")
        self.service.existing_card_image_names.return_value = {"token_sl.bmp"}
        self.service.validate_card_draft.return_value = []

    def wait_for_cards(self, view: CardListView, count: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and view._model.rowCount() != count:
            self.application.processEvents()
            time.sleep(0.01)
        self.application.processEvents()
        self.assertEqual(view._model.rowCount(), count)

    def wait_until(self, predicate, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.application.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def image_source(
        self,
        file_name: str,
        size: tuple[int, int],
        color: Qt.GlobalColor,
    ) -> Path:
        source = Path(self.temporary.name) / file_name
        pixmap = QPixmap(*size)
        pixmap.fill(color)
        self.assertTrue(pixmap.save(str(source), "BMP"))
        return source

    @staticmethod
    def rendered_color_bounds(frame: QFrame, color: Qt.GlobalColor) -> QRect:
        image = frame.grab().toImage()
        expected = QColor(color).rgb()
        points = [
            (x, y)
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).rgb() == expected
        ]
        if not points:
            return QRect()
        left = min(point[0] for point in points)
        top = min(point[1] for point in points)
        right = max(point[0] for point in points)
        bottom = max(point[1] for point in points)
        return QRect(left, top, right - left + 1, bottom - top + 1)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def detail(index: int = 1) -> CardDetailData:
        return CardDetailData(
            card_index=index,
            card_id=10 + index,
            localized_text=CardLocalizedText(
                names={"eng": "English", "jpn": "Japanese"},
                descriptions={"eng": "English description"},
            ),
            password="12345678",
            level=4,
            attack=1600,
            defense=1200,
            attribute="dark",
            card_type="dragon",
            card_category="normal",
            pack="disabled",
            image_name="token_sl.bmp",
        )

    def test_identity_fields_language_order_and_default_are_fixed(self):
        dialog = CardEditorDialog(
            self.manifest,
            self.service,
            self.detail().to_draft(),
        )
        self.assertTrue(dialog.findChild(QLineEdit, "txtCardIndex").isReadOnly())
        self.assertTrue(dialog.findChild(QLineEdit, "txtCardId").isReadOnly())
        self.assertTrue(dialog.findChild(QLineEdit, "txtImageName").isReadOnly())
        language = dialog.findChild(QComboBox, "cmbLanguage")
        self.assertEqual(
            tuple(language.itemText(index) for index in range(language.count())),
            LANGUAGE_PREFIXES,
        )
        self.assertEqual(language.currentText(), "eng")
        dialog.deleteLater()

    def test_password_is_eight_hex_characters_and_flushes_to_uppercase(self):
        detail = replace(self.detail(), password="FFFFFFFF")
        dialog = CardEditorDialog(
            self.manifest,
            self.service,
            detail.to_draft(),
        )
        self.assertEqual(dialog._password.text(), "FFFFFFFF")
        self.assertEqual(dialog._password.maxLength(), 8)
        self.assertTrue(dialog._password.hasAcceptableInput())

        dialog._password.setText("00ab12cd")
        self.assertTrue(dialog._password.hasAcceptableInput())
        draft = dialog.draft
        self.assertEqual(draft.password, "00AB12CD")
        self.assertEqual(dialog._password.text(), "00AB12CD")
        self.assertTrue(draft.password.startswith("00"))
        dialog.deleteLater()

    def test_incomplete_password_is_not_padded_and_blocks_save(self):
        self.service.validate_card_draft.return_value = [
            "password must contain exactly 8 hexadecimal characters."
        ]
        dialog = CardEditorDialog(
            self.manifest,
            self.service,
            self.detail().to_draft(),
        )
        dialog.show()
        self.application.processEvents()
        dialog._password.setText("abc")
        self.assertFalse(dialog._password.hasAcceptableInput())

        with patch.object(QMessageBox, "warning"):
            self.assertFalse(dialog._commit())
        validated = self.service.validate_card_draft.call_args.args[0]
        self.assertEqual(validated.password, "ABC")
        self.assertEqual(dialog._password.text(), "ABC")
        self.assertTrue(dialog._password.hasFocus())
        self.service.update_card.assert_not_called()
        dialog._discarding = True
        dialog.close()
        dialog.deleteLater()

    def test_language_switch_preserves_unsaved_name_and_description(self):
        dialog = CardEditorDialog(
            self.manifest,
            self.service,
            self.detail().to_draft(),
        )
        dialog._name.setText("Edited English")
        dialog._description.setPlainText("Edited English description")
        dialog._language.setCurrentText("jpn")
        dialog._name.setText("Edited Japanese")
        dialog._description.setPlainText("Edited Japanese description")
        dialog._language.setCurrentText("fra")
        dialog._name.setText("French")
        dialog._language.setCurrentText("eng")
        self.assertEqual(dialog._name.text(), "Edited English")
        self.assertEqual(
            dialog._description.toPlainText(),
            "Edited English description",
        )
        draft = dialog.draft
        self.assertEqual(draft.localized_text.names["jpn"], "Edited Japanese")
        self.assertEqual(
            draft.localized_text.descriptions["jpn"],
            "Edited Japanese description",
        )
        self.assertEqual(draft.localized_text.names["fra"], "French")
        dialog.deleteLater()

    def test_save_flushes_all_languages_and_failure_keeps_dialog(self):
        detail = self.detail()
        self.service.update_card.return_value = detail
        dialog = CardEditorDialog(
            self.manifest,
            self.service,
            detail.to_draft(),
        )
        dialog._name.setText("Saved English")
        dialog._language.setCurrentText("jpn")
        dialog._description.setPlainText("Saved Japanese description")
        self.assertTrue(dialog._commit())
        saved_draft = self.service.update_card.call_args.args[1]
        self.assertEqual(
            saved_draft.localized_text.names["eng"],
            "Saved English",
        )
        self.assertEqual(
            saved_draft.localized_text.descriptions["jpn"],
            "Saved Japanese description",
        )

        self.service.update_card.side_effect = OSError("controlled")
        dialog._name.setText("Retry value")
        with patch.object(QMessageBox, "critical"):
            self.assertFalse(dialog._commit())
        self.assertEqual(dialog._name.text(), "Retry value")
        dialog.deleteLater()

    def test_invalid_data_never_calls_service_save(self):
        self.service.validate_card_draft.return_value = [
            "attack 'bad' must be an integer."
        ]
        dialog = CardEditorDialog(
            self.manifest,
            self.service,
            self.detail().to_draft(),
        )
        dialog._attack.setValidator(None)
        dialog._attack.setText("bad")
        with patch.object(QMessageBox, "warning"):
            self.assertFalse(dialog._commit())
        self.service.update_card.assert_not_called()
        dialog.deleteLater()

    def test_token_pair_cancel_rolls_back_first_selection(self):
        source = Path(self.temporary.name) / "image.png"
        source.write_bytes(b"fixture")
        dialog = CardEditorDialog(
            self.manifest,
            self.service,
            self.detail().to_draft(),
        )
        with (
            patch.object(dialog, "_pick_image", side_effect=[source, None]),
            patch.object(QMessageBox, "information"),
        ):
            dialog._choose_image(mini=False)
        self.assertEqual(dialog.draft.image_name, "token_sl.bmp")
        self.assertIsNone(dialog.draft.large_image_source)
        self.assertIsNone(dialog.draft.small_image_source)
        dialog.deleteLater()

    def test_detail_suggest_is_dispatched_to_background_runner(self):
        draft = self.detail().to_draft()
        self.service.suggest_card_draft.return_value = CardSuggestionResult(
            draft, (), False
        )
        dialog = CardEditorDialog(
            self.manifest,
            self.service,
            draft,
        )
        with patch.object(dialog._thread_pool, "start") as start:
            dialog._suggest()

        start.assert_called_once()
        self.service.suggest_card_draft.assert_not_called()
        runner = start.call_args.args[0]
        self.assertFalse(dialog._suggest_button.isEnabled())
        runner.run()
        self.service.suggest_card_draft.assert_called_once_with(
            self.manifest,
            ANY,
            preferred_language="eng",
        )
        dialog.deleteLater()

    def test_detail_suggest_uses_available_name_without_switching_language(self):
        draft = self.detail().to_draft()
        draft.localized_text.names["eng"] = ""
        draft.localized_text.names["jpn"] = "Japanese query"
        self.service.suggest_card_draft.return_value = CardSuggestionResult(
            draft, (), False
        )
        dialog = CardEditorDialog(self.manifest, self.service, draft)
        dialog._language.setCurrentText("fra")
        with patch.object(dialog._thread_pool, "start") as start:
            dialog._suggest()
        runner = start.call_args.args[0]
        runner.run()
        self.service.suggest_card_draft.assert_called_once_with(
            self.manifest,
            ANY,
            preferred_language="fra",
        )
        self.assertEqual(dialog._language.currentText(), "fra")
        dialog.deleteLater()

    def test_card_list_language_changes_only_displayed_text(self):
        self.service.load_card_details.return_value = [self.detail()]
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, len(self.service.load_card_details.return_value))
        columns = {
            name: position
            for position, (name, _label) in enumerate(view._model.COLUMNS)
        }
        name_index = view._model.index(0, columns["name"])
        description_index = view._model.index(0, columns["description"])
        self.assertEqual(view._model.data(name_index), "English")
        view._display_language.setCurrentText("jpn")
        self.assertEqual(view._model.data(name_index), "Japanese")
        self.assertEqual(view._model.data(description_index), "")
        self.assertEqual(view._model.card_at(0).localized_text.names["eng"], "English")
        self.service.load_card_details.assert_called_once_with(self.manifest)
        view.deleteLater()

    def test_model_language_signal_is_scoped_and_same_language_is_noop(self):
        model = CardListModel([self.detail()])
        emissions = []
        model.dataChanged.connect(lambda *args: emissions.append(args))
        model.set_display_language("ENG")
        self.assertEqual(emissions, [])
        model.set_display_language("fra")
        self.assertEqual(len(emissions), 1)
        left, right = emissions[0][:2]
        columns = {
            name: position for position, (name, _label) in enumerate(model.COLUMNS)
        }
        self.assertEqual(
            (left.column(), right.column()), (columns["name"], columns["description"])
        )

    def test_preview_frames_are_equal_and_mini_renders_native_and_centered(self):
        large_source = self.image_source("large.bmp", (200, 300), Qt.blue)
        mini_source = self.image_source("mini.bmp", (50, 72), Qt.red)
        draft = self.detail().to_draft()
        draft.large_image_source = large_source
        draft.small_image_source = mini_source
        dialog = CardEditorDialog(self.manifest, self.service, draft)
        dialog.show()
        self.application.processEvents()

        self.assertEqual(
            dialog._large_image_frame.size(), dialog._small_image_frame.size()
        )
        self.assertGreaterEqual(dialog._small_image_frame.width(), 260)
        self.assertGreaterEqual(dialog._small_image_frame.height(), 260)
        self.assertFalse(dialog._small_image.hasScaledContents())
        original = dialog._original_image_pixmaps[True]
        self.assertEqual((original.width(), original.height()), (50, 72))
        preview = dialog._small_image.pixmap()
        self.assertEqual(preview.size(), QSize(50, 72))
        self.assertEqual(preview.width() * 72, preview.height() * 50)
        bounds = self.rendered_color_bounds(dialog._small_image_frame, Qt.red)
        self.assertEqual(bounds.size(), QSize(50, 72))
        self.assertLessEqual(
            abs(bounds.center().x() - dialog._small_image_frame.rect().center().x()),
            1,
        )
        self.assertLessEqual(
            abs(bounds.center().y() - dialog._small_image_frame.rect().center().y()),
            1,
        )

        dialog.resize(1200, 760)
        self.application.processEvents()
        self.assertIs(dialog._original_image_pixmaps[True], original)
        preview = dialog._small_image.pixmap()
        self.assertEqual(preview.size(), QSize(50, 72))
        self.assertEqual(
            dialog._large_image_frame.size(), dialog._small_image_frame.size()
        )
        bounds = self.rendered_color_bounds(dialog._small_image_frame, Qt.red)
        self.assertEqual(bounds.size(), QSize(50, 72))
        self.assertLessEqual(
            abs(bounds.center().x() - dialog._small_image_frame.rect().center().x()),
            1,
        )
        self.assertLessEqual(
            abs(bounds.center().y() - dialog._small_image_frame.rect().center().y()),
            1,
        )
        dialog.close()
        dialog.deleteLater()

    def test_mini_preview_scales_down_only_when_available_area_is_smaller(self):
        original = QPixmap(500, 100)
        original.fill(Qt.red)
        self.assertEqual(
            fit_pixmap_without_upscaling(original, QSize(600, 200)).size(),
            QSize(500, 100),
        )

        large_source = self.image_source("large.bmp", (100, 150), Qt.blue)
        mini_source = self.image_source("wide-mini.bmp", (500, 100), Qt.red)
        draft = self.detail().to_draft()
        draft.large_image_source = large_source
        draft.small_image_source = mini_source
        dialog = CardEditorDialog(self.manifest, self.service, draft)
        dialog.resize(1400, 620)
        dialog.show()
        self.application.processEvents()

        self.assertEqual(dialog._small_image.pixmap().size(), QSize(500, 100))
        large_preview = dialog._large_image.pixmap()
        self.assertLessEqual(
            large_preview.width(), dialog._large_image.contentsRect().width()
        )
        self.assertLessEqual(
            large_preview.height(), dialog._large_image.contentsRect().height()
        )
        self.assertAlmostEqual(
            large_preview.width() / large_preview.height(), 2 / 3, places=2
        )

        dialog.resize(dialog.minimumSize())
        self.application.processEvents()
        preview = dialog._small_image.pixmap()
        available = dialog._small_image_frame.layout().contentsRect().size()
        self.assertLess(preview.width(), 500)
        self.assertLess(preview.height(), 100)
        self.assertLessEqual(preview.width(), available.width())
        self.assertLessEqual(preview.height(), available.height())
        self.assertAlmostEqual(
            preview.width() / preview.height(),
            5.0,
            delta=0.05,
        )

        dialog.resize(1400, 620)
        self.application.processEvents()
        self.assertEqual(dialog._small_image.pixmap().size(), QSize(500, 100))
        dialog.close()
        dialog.deleteLater()

    def test_preview_states_and_image_to_empty_transition_keep_outer_geometry(self):
        large_source = self.image_source("large.bmp", (200, 300), Qt.blue)
        mini_source = self.image_source("mini.bmp", (50, 72), Qt.red)
        current = self.detail(1).to_draft()
        current.large_image_source = large_source
        current.small_image_source = mini_source
        empty = self.detail(2).to_draft()
        empty.image_name = "missing.bmp"
        dialog = CardEditorDialog(
            self.manifest,
            self.service,
            current,
            card_lookup=lambda _index: empty,
        )
        dialog.show()
        self.application.processEvents()
        original_dialog_size = dialog.size()
        original_large_geometry = dialog._large_image_frame.geometry()
        original_small_geometry = dialog._small_image_frame.geometry()

        with patch.object(dialog._thread_pool, "start") as start:
            dialog._navigate_to(2)
        start.assert_called_once()
        self.application.processEvents()
        self.assertEqual(dialog._large_image.text(), "Loading large image...")
        self.assertEqual(dialog._small_image.text(), "Loading mini image...")
        self.assertEqual(dialog.size(), original_dialog_size)
        self.assertEqual(dialog._large_image_frame.geometry(), original_large_geometry)
        self.assertEqual(dialog._small_image_frame.geometry(), original_small_geometry)

        request = (
            dialog._image_request,
            dialog.draft.card_index,
            dialog.draft.image_name.casefold(),
        )
        dialog._image_pair_failed(request, None)
        self.application.processEvents()
        self.assertEqual(dialog._large_image.text(), "Image unavailable")
        self.assertEqual(dialog._small_image.text(), "Image unavailable")
        self.assertEqual(dialog.size(), original_dialog_size)
        self.assertEqual(dialog._large_image_frame.geometry(), original_large_geometry)
        self.assertEqual(dialog._small_image_frame.geometry(), original_small_geometry)
        self.assertEqual(
            dialog._large_image_frame.size(), dialog._small_image_frame.size()
        )
        dialog.close()
        dialog.deleteLater()

    def test_stale_image_result_does_not_replace_the_current_card_preview(self):
        old_large = self.image_source("old-large.bmp", (200, 300), Qt.blue)
        old_mini = self.image_source("old-mini.bmp", (50, 72), Qt.red)
        new_large = self.image_source("new-large.bmp", (200, 300), Qt.yellow)
        new_mini = self.image_source("new-mini.bmp", (50, 72), Qt.green)
        current = self.detail(1).to_draft()
        current.large_image_source = old_large
        current.small_image_source = old_mini
        replacement = self.detail(2).to_draft()
        replacement.image_name = "new.bmp"
        replacement.large_image_source = new_large
        replacement.small_image_source = new_mini
        dialog = CardEditorDialog(
            self.manifest,
            self.service,
            current,
            card_lookup=lambda _index: replacement,
        )

        dialog._draft.large_image_source = None
        dialog._draft.small_image_source = None
        dialog._draft.image_name = "old.bmp"
        self.service.load_card_images.return_value = (
            old_large.read_bytes(),
            old_mini.read_bytes(),
        )
        with patch.object(dialog._thread_pool, "start") as start:
            dialog._refresh_image_previews()
        stale_runner = start.call_args.args[0]
        dialog._navigate_to(2)
        dialog.show()
        self.application.processEvents()
        stale_runner.run()
        self.application.processEvents()

        self.assertEqual(dialog.draft.card_index, 2)
        self.assertEqual(dialog._small_image.pixmap().size(), QSize(50, 72))
        self.assertEqual(
            dialog._small_image.pixmap().toImage().pixelColor(25, 36).rgb(),
            QColor(Qt.green).rgb(),
        )
        bounds = self.rendered_color_bounds(dialog._small_image_frame, Qt.green)
        self.assertEqual(bounds.size(), QSize(50, 72))
        dialog.close()
        dialog.deleteLater()

    def test_suggest_with_no_localized_name_does_not_start_network_task(self):
        draft = self.detail().to_draft()
        draft.localized_text.names = {language: "" for language in LANGUAGE_PREFIXES}
        dialog = CardEditorDialog(self.manifest, self.service, draft)
        with (
            patch.object(QMessageBox, "information") as information,
            patch.object(dialog._thread_pool, "start") as start,
        ):
            dialog._suggest()
        start.assert_not_called()
        self.service.suggest_card_draft.assert_not_called()
        self.assertIn("At least one card name", information.call_args.args[2])
        dialog.deleteLater()

    def test_previous_next_uses_card_index_and_preserves_language(self):
        self.service.card_index_bounds.return_value = (0, 2)
        self.service.get_card_detail.return_value = self.detail(2)
        dialog = CardEditorDialog(
            self.manifest, self.service, self.detail(1).to_draft()
        )
        dialog._language.setCurrentText("jpn")
        dialog._next_button.click()
        self.service.get_card_detail.assert_called_once_with(self.manifest, 2)
        self.assertEqual(dialog.draft.card_index, 2)
        self.assertEqual(dialog._language.currentText(), "jpn")
        self.assertFalse(dialog.draft.dirty)
        self.assertTrue(dialog._previous_button.isEnabled())
        self.assertFalse(dialog._next_button.isEnabled())
        dialog.deleteLater()

    def test_dirty_navigation_cancel_stays_and_discard_moves(self):
        self.service.card_index_bounds.return_value = (0, 2)
        self.service.get_card_detail.return_value = self.detail(2)
        dialog = CardEditorDialog(
            self.manifest, self.service, self.detail(1).to_draft()
        )
        dialog._name.setText("Changed")
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.Cancel):
            dialog._next_button.click()
        self.assertEqual(dialog.draft.card_index, 1)
        self.service.get_card_detail.assert_not_called()
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.Discard):
            dialog._next_button.click()
        self.assertEqual(dialog.draft.card_index, 2)
        dialog.deleteLater()

    def test_dirty_navigation_save_moves_only_after_success(self):
        self.service.card_index_bounds.return_value = (0, 2)
        self.service.update_card.return_value = self.detail(1)
        self.service.get_card_detail.return_value = self.detail(2)
        dialog = CardEditorDialog(
            self.manifest, self.service, self.detail(1).to_draft()
        )
        dialog._name.setText("Changed")
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.Save):
            dialog._next_button.click()
        self.assertTrue(
            self.wait_until(lambda: self.service.update_card.call_count == 1)
        )
        self.assertTrue(self.wait_until(lambda: dialog.draft.card_index == 2))
        self.service.update_card.assert_called_once()
        self.service.get_card_detail.assert_called_once_with(self.manifest, 2)
        self.assertEqual(dialog.draft.card_index, 2)
        dialog.deleteLater()

    def test_create_hides_navigation_and_readonly_fields_share_style(self):
        draft = self.detail().to_draft(is_new=True)
        dialog = CardEditorDialog(self.manifest, self.service, draft)
        self.assertTrue(dialog._previous_button.isHidden())
        self.assertTrue(dialog._next_button.isHidden())
        root = dialog.findChild(QWidget, "CardEditorDialog")
        self.assertIn('QLineEdit[readOnly="true"]', root.styleSheet())
        dialog.deleteLater()

    def test_unused_filter_maps_rows_and_export_uses_visible_order(self):
        used = replace(self.detail(2), pack="yugi")
        self.service.load_card_details.return_value = [used, self.detail(1)]
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, 2)
        self.assertEqual(view._proxy_model.rowCount(), 2)
        view._table.selectRow(0)
        view._unused_filter_button.click()
        self.assertEqual(view._proxy_model.rowCount(), 1)
        self.assertEqual(view._unused_filter_button.text(), "un-filter empty")
        self.assertEqual(view._table.selectionModel().selectedRows(), [])
        with patch.object(view, "_open_card_detail") as open_editor:
            view._open_index(view._proxy_model.index(0, 0))
        self.assertEqual(open_editor.call_args.args[0].card_index, 1)
        destination = str(Path(self.temporary.name) / "filtered.csv")
        with (
            patch.object(
                QFileDialog, "getSaveFileName", return_value=(destination, "")
            ),
            patch.object(QMessageBox, "information"),
        ):
            view._export_cards()
        exported = self.service.export_cards_csv.call_args.args[2]
        self.assertEqual([card.card_index for card in exported], [1])
        self.assertEqual(len(view._model.cards()), 2)
        view.deleteLater()

    def test_enable_all_model_stages_atomically_and_preserves_protected_cards(self):
        def named_detail(
            index: int,
            name: str,
            *,
            card_type: str = "dragon",
            pack: str = "disabled",
            image_name: str = "token_sl.bmp",
            monster_type_code: int | None = None,
        ) -> CardDetailData:
            return replace(
                self.detail(index),
                localized_text=CardLocalizedText(
                    names={"eng": name, "fra": f"FR {name}"},
                    descriptions={"eng": f"Description {name}"},
                ),
                card_type=card_type,
                pack=pack,
                image_name=image_name,
                monster_type_code=monster_type_code,
            )

        cards = [
            # Actual data proves token_sl.bmp is also a generic placeholder, so
            # it must not by itself classify this ordinary card as a Token.
            named_detail(1, "Ancient Tool"),
            named_detail(2, "A Different Divine Card", card_type="divine"),
            named_detail(
                3,
                "Non-game Code Only",
                card_type="dragon",
                monster_type_code=0x17,
            ),
            named_detail(4, "Non-game Label Only", card_type="non_game_card"),
            named_detail(5, "Obelisk the Tormentor", card_type="divine"),
            named_detail(6, "Slifer the Sky Dragon", card_type="divine"),
            named_detail(7, "The Winged Dragon of Ra", card_type="divine"),
            named_detail(8, "Insect Monster Token", image_name="token_in.bmp"),
            named_detail(9, "Token Collector"),
            named_detail(10, "Already Joey", pack="joey"),
            named_detail(11, "Already Other Pack", pack="yugi"),
            named_detail(12, "The Winged Dragon of Ra - Sphere Mode"),
        ]
        protected = cards[2].to_draft()
        protected.dirty = True
        protected.touched_fields.add("name:eng")
        cards[2] = protected
        model = CardListModel(cards)
        changed = Mock()
        model.dataChanged.connect(changed)

        result = model.enable_all_eligible_cards()

        self.assertEqual(result.updated, 4)
        self.assertEqual(result.protected_non_game, 2)
        self.assertEqual(result.protected_god, 3)
        self.assertEqual(result.protected_token, 1)
        self.assertEqual(result.protected, 6)
        self.assertEqual(result.already_enabled, 2)
        self.assertEqual(result.skipped, 8)
        self.assertEqual(result.updated + result.skipped, len(cards))
        self.assertEqual(changed.call_count, 1)
        top_left, bottom_right, roles = changed.call_args.args
        pack_column = next(
            column
            for column, (field_name, _label) in enumerate(model.COLUMNS)
            if field_name == "pack"
        )
        self.assertEqual((top_left.row(), top_left.column()), (0, pack_column))
        self.assertEqual(
            (bottom_right.row(), bottom_right.column()),
            (len(cards) - 1, pack_column),
        )
        self.assertEqual(roles, [Qt.DisplayRole, Qt.UserRole])
        by_index = {card.card_index: card for card in model.cards()}
        for card_index in (1, 2, 9, 12):
            self.assertEqual(by_index[card_index].pack, "joey")
            self.assertTrue(by_index[card_index].dirty)
            self.assertIn("pack", by_index[card_index].touched_fields)
        for card_index in (3, 4, 5, 6, 7, 8):
            self.assertEqual(by_index[card_index].pack, "disabled")
        self.assertEqual(by_index[10].pack, "joey")
        self.assertEqual(by_index[11].pack, "yugi")
        self.assertTrue(by_index[3].dirty)
        self.assertEqual(by_index[3].touched_fields, {"name:eng"})

        before_second_click = model.cards()
        second = model.enable_all_eligible_cards()
        self.assertEqual(second.updated, 0)
        self.assertEqual(second.protected, 6)
        self.assertEqual(second.already_enabled, 6)
        self.assertEqual(second.skipped, len(cards))
        self.assertEqual(model.cards(), before_second_click)
        self.assertEqual(changed.call_count, 1)

    def test_enable_all_model_exception_is_atomic_after_staged_change(self):
        cards = [self.detail(1), self.detail(2), self.detail(3)]
        model = CardListModel(cards)
        before = model.cards()
        changed = Mock()
        model.dataChanged.connect(changed)

        with (
            patch(
                "yugioh_editor.views.card_list_model._enable_all_disposition",
                side_effect=["updated", RuntimeError("controlled")],
            ),
            self.assertRaisesRegex(RuntimeError, "controlled"),
        ):
            model.enable_all_eligible_cards()

        self.assertEqual(model.cards(), before)
        self.assertFalse(model.has_dirty_cards())
        self.assertEqual(
            [model.row_for_card_index(index) for index in (1, 2, 3)],
            [0, 1, 2],
        )
        changed.assert_not_called()

    def test_enable_all_model_with_no_eligible_cards_is_stable(self):
        cards = [
            replace(self.detail(1), pack="joey"),
            replace(self.detail(2), pack="yugi"),
            replace(
                self.detail(3),
                localized_text=CardLocalizedText(names={"eng": "Kuriboh Token"}),
            ),
            replace(
                self.detail(4),
                localized_text=CardLocalizedText(
                    names={"eng": "Slifer the Sky Dragon"}
                ),
            ),
            replace(self.detail(5), card_type="non_game_card"),
        ]
        model = CardListModel(cards)
        before = model.cards()
        changed = Mock()
        model.dataChanged.connect(changed)

        result = model.enable_all_eligible_cards()

        self.assertEqual(result.updated, 0)
        self.assertEqual(result.protected, 3)
        self.assertEqual(result.already_enabled, 2)
        self.assertEqual(result.skipped, len(cards))
        self.assertEqual(model.cards(), before)
        self.assertFalse(model.has_dirty_cards())
        changed.assert_not_called()

    def test_enable_all_button_uses_source_rows_with_live_filter_and_keeps_state(self):
        class SearchProxy(UnusedCardFilterProxyModel):
            def __init__(self, parent=None) -> None:
                super().__init__(parent)
                self.search_text = ""

            def set_search_text(self, text: str) -> None:
                self.search_text = text.casefold()
                self.beginFilterChange()
                self.endFilterChange()

            def filterAcceptsRow(self, source_row, source_parent) -> bool:
                if not super().filterAcceptsRow(source_row, source_parent):
                    return False
                source = self.sourceModel()
                name_column = next(
                    column
                    for column, (field_name, _label) in enumerate(source.COLUMNS)
                    if field_name == "name"
                )
                name = source.data(
                    source.index(source_row, name_column),
                    Qt.UserRole,
                )
                return self.search_text in str(name).casefold()

        def named_detail(
            index: int,
            name: str,
            *,
            card_type: str = "dragon",
            french_name: str | None = None,
        ) -> CardDetailData:
            return replace(
                self.detail(index),
                localized_text=CardLocalizedText(
                    names={"eng": name, "fra": french_name or f"FR {name}"},
                    descriptions={"eng": f"Description {name}"},
                ),
                card_type=card_type,
                image_name=f"card{index:03d}.bmp",
            )

        cards = [
            named_detail(1, "First Eligible", french_name="Keep Alpha"),
            named_detail(2, "Second Eligible", french_name="Hidden Beta"),
            named_detail(
                3,
                "Obelisk the Tormentor",
                card_type="divine",
                french_name="Keep Protected",
            ),
        ]
        persisted = {card.card_index: card for card in cards}

        def load_cards(_manifest):
            return [persisted[index] for index in sorted(persisted)]

        def save_cards(_manifest, changes):
            for draft in changes:
                draft.dirty = False
                draft.is_new = False
                persisted[draft.card_index] = draft.to_detail()

        self.service.load_card_details.side_effect = load_cards
        self.service.save_card_changes.side_effect = save_cards
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, len(cards))
        self.assertEqual(view._unused_filter_button.text(), "filter empty")
        self.assertEqual(view._enable_all_button.text(), "enable all")
        view._display_language.setCurrentText("fra")
        view._unused_filter_button.click()
        self.assertTrue(view._unused_filter_button.isChecked())

        search_proxy = SearchProxy(view)
        search_proxy.setSourceModel(view._model)
        search_proxy.set_unused_only(True)
        search_proxy.set_search_text("keep")
        view._proxy_model = search_proxy
        view._table.setModel(search_proxy)
        view._table.selectionModel().selectionChanged.connect(view._selection_changed)
        name_column = next(
            column
            for column, (field_name, _label) in enumerate(view._model.COLUMNS)
            if field_name == "name"
        )
        search_proxy.sort(name_column, Qt.DescendingOrder)
        self.assertEqual(search_proxy.rowCount(), 2)
        hidden_source_row = view._model.row_for_card_index(2)
        self.assertIsNotNone(hidden_source_row)
        self.assertFalse(
            search_proxy.mapFromSource(
                view._model.index(hidden_source_row, 0)
            ).isValid()
        )
        self.assertTrue(view._select_card(3))
        source_changed = Mock()
        dirty_changed = Mock()
        view._model.dataChanged.connect(source_changed)
        view.dirty_changed.connect(dirty_changed)

        with patch.object(QMessageBox, "information") as information:
            view._enable_all_button.click()

        self.assertEqual(source_changed.call_count, 1)
        dirty_changed.assert_called_once_with(True)
        by_index = {card.card_index: card for card in view._model.cards()}
        self.assertEqual(by_index[1].pack, "joey")
        self.assertEqual(by_index[2].pack, "joey")
        self.assertEqual(by_index[3].pack, "disabled")
        self.assertEqual(search_proxy.rowCount(), 1)
        self.assertEqual(view._selected_card_index(), 3)
        self.assertEqual(view._display_language.currentText(), "fra")
        self.assertTrue(view._unused_filter_button.isChecked())
        self.assertEqual(view._unused_filter_button.text(), "un-filter empty")
        self.assertEqual(search_proxy.search_text, "keep")
        self.assertEqual(search_proxy.sortColumn(), name_column)
        self.assertEqual(search_proxy.sortOrder(), Qt.DescendingOrder)
        self.assertTrue(view.is_dirty)
        self.assertTrue(view._save_button.isEnabled())
        self.assertEqual(view.windowTitle(), "Card List *")
        self.assertEqual(information.call_count, 1)
        self.assertEqual(
            information.call_args.args[2],
            "Updated: 2\nProtected: 1\nSkipped: 1",
        )

        first_state = view._model.cards()
        with patch.object(QMessageBox, "information") as second_information:
            view._enable_all_button.click()
        self.assertEqual(view._model.cards(), first_state)
        self.assertEqual(source_changed.call_count, 1)
        self.assertEqual(dirty_changed.call_count, 1)
        self.assertEqual(second_information.call_count, 1)
        self.assertEqual(
            second_information.call_args.args[2],
            "Updated: 0\nProtected: 1\nSkipped: 3",
        )

        with patch.object(QMessageBox, "information") as saved_information:
            view._save_button.click()
            self.assertTrue(
                self.wait_until(
                    lambda: (
                        self.service.save_card_changes.call_count == 1
                        and not view._active_runners
                    )
                )
            )
        saved_changes = self.service.save_card_changes.call_args.args[1]
        self.assertEqual(
            [(card.card_index, card.pack) for card in saved_changes],
            [(1, "joey"), (2, "joey")],
        )
        self.assertFalse(view.is_dirty)
        self.assertFalse(view._model.has_dirty_cards())
        self.assertFalse(view._save_button.isEnabled())
        self.assertEqual(view.windowTitle(), "Card List")
        self.assertEqual(view._selected_card_index(), 3)
        self.assertEqual(
            saved_information.call_args.args[2],
            "All staged card changes were committed successfully.",
        )

        view._reload(selected_card_index=3)
        self.assertTrue(
            self.wait_until(
                lambda: (
                    self.service.load_card_details.call_count == 2
                    and not view._active_runners
                )
            )
        )
        reloaded = {card.card_index: card for card in view._model.cards()}
        self.assertEqual(reloaded[1].pack, "joey")
        self.assertEqual(reloaded[2].pack, "joey")
        self.assertEqual(reloaded[3].pack, "disabled")
        self.assertEqual(view._selected_card_index(), 3)
        self.assertEqual(view._display_language.currentText(), "fra")
        self.assertTrue(view._unused_filter_button.isChecked())
        self.assertEqual(search_proxy.search_text, "keep")
        self.assertEqual(search_proxy.sortColumn(), name_column)
        self.assertEqual(search_proxy.sortOrder(), Qt.DescendingOrder)
        self.assertFalse(view.is_dirty)
        view.deleteLater()

    def test_bulk_suggest_click_uses_all_source_rows_and_preserves_view_state(self):
        def missing_detail(
            index: int,
            english_name: str,
            french_name: str,
            *,
            pack: str = "disabled",
        ) -> CardDetailData:
            return replace(
                self.detail(index),
                localized_text=CardLocalizedText(
                    names={"eng": english_name, "fra": french_name},
                    descriptions={},
                ),
                password="FFFFFFFF",
                level=None,
                attack=None,
                defense=None,
                attribute="",
                card_type="",
                card_category="",
                pack=pack,
                monster_type_code=None,
                card_category_code=None,
                attribute_code=None,
            )

        source_cards = [
            missing_detail(1, "First Query", "Zulu Query"),
            missing_detail(2, "Second Query", "Alpha Query"),
            replace(
                self.detail(3),
                localized_text=CardLocalizedText(
                    names={
                        language: f"Hidden {language} name"
                        for language in LANGUAGE_PREFIXES
                    },
                    descriptions={
                        language: f"Hidden {language} description"
                        for language in LANGUAGE_PREFIXES
                    },
                ),
                pack="yugi",
            ),
        ]
        staged_cards = [card.to_draft() for card in source_cards]
        filled = staged_cards[0]
        filled.localized_text.names["jpn"] = "Filled Japanese name"
        filled.localized_text.descriptions["fra"] = "Description remplie"
        filled.password = "0012ABCD"
        filled.level = 6
        filled.attack = 2400
        filled.defense = 1800
        filled.attribute = "light"
        filled.card_type = "spellcaster"
        filled.card_category = "effect"
        filled.dirty = True
        filled.touched_fields.update(
            {
                "name:jpn",
                "description:fra",
                "password",
                "level",
                "attack",
                "defense",
                "attribute",
                "card_type",
                "card_category",
            }
        )
        staged_cards[2].image_name = "card001.bmp"
        staged_cards[2].large_image_source = b"large image"
        staged_cards[2].small_image_source = b"mini image"
        staged_cards[2].dirty = True
        result = BulkSuggestionResult(
            cards=tuple(staged_cards),
            total_candidates=3,
            resolved=2,
            partially_filled=1,
            not_found=1,
            skipped_no_query_name=0,
            unchanged=1,
            failed=0,
            cancelled=False,
            image_staged=1,
            image_failed=1,
            total_source_cards=3,
            skipped_complete=0,
            selected_workers=3,
        )

        self.service.load_card_details.return_value = source_cards

        def suggest_cards(_cards, **kwargs):
            kwargs["report_progress"](0, result.total_candidates)
            kwargs["report_progress"](
                result.total_candidates,
                result.total_candidates,
            )
            return result

        self.service.bulk_suggest_missing_text.side_effect = suggest_cards
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, len(source_cards))
        view._display_language.setCurrentText("fra")
        view._unused_filter_button.click()
        name_column = next(
            column
            for column, (field_name, _label) in enumerate(view._model.COLUMNS)
            if field_name == "name"
        )
        view._proxy_model.sort(name_column, Qt.DescendingOrder)
        self.assertEqual(view._proxy_model.rowCount(), 2)
        self.assertTrue(view._select_card(2))

        with (
            patch.object(view._thread_pool, "start") as start,
            patch.object(QMessageBox, "information") as information,
        ):
            view._suggest_button.click()
            view._suggest_button.click()
            start.assert_called_once()
            self.assertFalse(view._add_button.isEnabled())
            self.assertFalse(view._update_button.isEnabled())
            self.assertFalse(view._import_button.isEnabled())
            self.assertFalse(view._enable_all_button.isEnabled())
            self.assertFalse(view._save_button.isEnabled())
            self.assertFalse(view._suggest_button.isEnabled())
            self.assertTrue(view._unused_filter_button.isEnabled())
            self.assertTrue(view._display_language.isEnabled())
            self.assertEqual(view._pgb_progress.minimum(), 0)
            self.assertEqual(view._pgb_progress.maximum(), 0)
            self.service.bulk_suggest_missing_text.assert_not_called()
            start.call_args.args[0].run()
            self.application.processEvents()

        self.service.bulk_suggest_missing_text.assert_called_once()
        suggest_call = self.service.bulk_suggest_missing_text.call_args
        called_cards = suggest_call.args[0]
        self.assertEqual(
            [card.card_index for card in called_cards],
            [1, 2, 3],
        )
        self.assertIs(suggest_call.kwargs["manifest"], self.manifest)
        self.assertIn("is_cancelled", suggest_call.kwargs)
        self.assertIn("report_progress", suggest_call.kwargs)
        self.assertEqual(information.call_count, 1)
        self.assertEqual(
            information.call_args.args[2],
            "Source cards: 3\n"
            "Candidates: 3\n"
            "Skipped complete: 0\n"
            "Resolved: 2\n"
            "Unchanged: 1\n"
            "Partially filled: 1\n"
            "Not found: 1\n"
            "No query name: 0\n"
            "Failed: 0\n"
            "Images staged: 1\n"
            "Images failed: 1\n"
            "Cancelled: no",
        )

        updated = view._model.card_by_index(1)
        self.assertEqual(updated.localized_text.names["jpn"], "Filled Japanese name")
        self.assertEqual(
            updated.localized_text.descriptions["fra"],
            "Description remplie",
        )
        self.assertEqual(updated.password, "0012ABCD")
        self.assertEqual(
            (
                updated.level,
                updated.attack,
                updated.defense,
                updated.attribute,
                updated.card_type,
                updated.card_category,
            ),
            (6, 2400, 1800, "light", "spellcaster", "effect"),
        )
        image_only_update = view._model.card_by_index(3)
        self.assertEqual(image_only_update.image_name, "card001.bmp")
        self.assertEqual(image_only_update.large_image_source, b"large image")
        self.assertEqual(image_only_update.small_image_source, b"mini image")
        self.assertTrue(image_only_update.dirty)
        self.assertEqual(view._selected_card_index(), 2)
        self.assertEqual(view._display_language.currentText(), "fra")
        self.assertTrue(view._unused_filter_button.isChecked())
        self.assertEqual(view._unused_filter_button.text(), "un-filter empty")
        self.assertEqual(view._proxy_model.rowCount(), 2)
        self.assertEqual(view._proxy_model.sortColumn(), name_column)
        self.assertEqual(view._proxy_model.sortOrder(), Qt.DescendingOrder)
        self.assertTrue(view.is_dirty)
        self.assertTrue(view._save_button.isEnabled())
        self.assertEqual(view.windowTitle(), "Card List *")
        self.assertIsNone(view._suggest_runner)
        self.assertTrue(view._add_button.isEnabled())
        self.assertTrue(view._update_button.isEnabled())
        self.assertTrue(view._import_button.isEnabled())
        self.assertTrue(view._enable_all_button.isEnabled())
        self.assertTrue(view._suggest_button.isEnabled())
        self.assertTrue(view._cancel_suggest_button.isHidden())
        self.assertEqual(view._pgb_progress.maximum(), 3)
        self.assertEqual(view._pgb_progress.value(), 3)
        self.service.suggest_card_draft.assert_not_called()
        self.service.suggest_card_reference.assert_not_called()
        self.service.load_card_image.assert_not_called()
        self.service.load_card_images.assert_not_called()
        self.service.existing_card_image_names.assert_not_called()
        self.service.validate_image_source.assert_not_called()
        self.service.select_image_lookup_name.assert_not_called()
        self.service.select_image_lookup_password.assert_not_called()
        view.deleteLater()

    def test_bulk_suggest_runs_off_ui_thread_and_callbacks_return_to_ui_thread(self):
        source = self.detail()
        draft = source.to_draft()
        result = BulkSuggestionResult(
            cards=(draft,),
            total_candidates=1,
            resolved=0,
            partially_filled=0,
            not_found=0,
            skipped_no_query_name=0,
            unchanged=1,
            failed=0,
            cancelled=False,
            total_source_cards=1,
            skipped_complete=0,
            selected_workers=1,
        )
        worker_threads = []

        def suggest_cards(_cards, **kwargs):
            worker_threads.append(QThread.currentThread())
            kwargs["report_progress"](0, 1)
            kwargs["report_progress"](1, 1)
            return result

        self.service.load_card_details.return_value = [source]
        self.service.bulk_suggest_missing_text.side_effect = suggest_cards
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, 1)
        view.show()
        callback_threads = []

        def show_result(*_args):
            callback_threads.append(QThread.currentThread())

        with patch.object(QMessageBox, "information", side_effect=show_result):
            view._suggest_button.click()
            self.assertTrue(
                self.wait_until(
                    lambda: (
                        self.service.bulk_suggest_missing_text.call_count == 1
                        and view._suggest_runner is None
                    )
                )
            )

        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], self.application.thread())
        self.assertEqual(callback_threads, [self.application.thread()])
        self.assertEqual(view._pgb_progress.maximum(), 1)
        self.assertEqual(view._pgb_progress.value(), 1)
        self.assertTrue(view._pgb_progress.isHidden())
        view.close()
        view.deleteLater()

    def test_card_list_save_double_click_dispatches_one_transaction(self):
        self.service.load_card_details.return_value = [self.detail()]
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, 1)
        draft = view._model.card_at(0)
        draft.pack = "joey"
        draft.dirty = True
        draft.touched_fields.add("pack")
        view._model.update_card(draft)
        view._table.selectRow(0)
        view._set_dirty(True)

        with (
            patch.object(view._thread_pool, "start") as start,
            patch.object(QMessageBox, "information"),
        ):
            view._save_button.click()
            view._save_button.click()
            start.assert_called_once()
            self.service.save_card_changes.assert_not_called()
            self.assertIsNotNone(view._save_runner)
            self.assertFalse(view._save_button.isEnabled())
            self.assertFalse(view._add_button.isEnabled())
            self.assertFalse(view._update_button.isEnabled())
            self.assertFalse(view._import_button.isEnabled())
            self.assertFalse(view._enable_all_button.isEnabled())
            self.assertFalse(view._suggest_button.isEnabled())

            start.call_args.args[0].run()
            self.application.processEvents()

        self.service.save_card_changes.assert_called_once()
        self.assertIsNone(view._save_runner)
        self.assertFalse(view.is_dirty)
        self.assertFalse(view._save_button.isEnabled())
        self.assertTrue(view._add_button.isEnabled())
        self.assertTrue(view._update_button.isEnabled())
        self.assertTrue(view._import_button.isEnabled())
        self.assertTrue(view._enable_all_button.isEnabled())
        self.assertTrue(view._suggest_button.isEnabled())
        view.deleteLater()

    def test_closing_card_list_requests_active_bulk_suggest_cancellation(self):
        self.service.load_card_details.return_value = [self.detail()]
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, 1)
        runner = Mock()
        view._suggest_runner = runner
        event = Mock()

        with patch.object(view, "reject") as reject:
            view.closeEvent(event)
            runner.cancel.assert_called_once_with()
            event.ignore.assert_called_once_with()
            reject.assert_not_called()
            self.assertFalse(view._closing)
            self.assertTrue(view._reject_after_suggest)

            view._suggest_finished()
            reject.assert_called_once_with()

        self.assertTrue(view._closing)
        self.assertIsNone(view._suggest_runner)
        view.deleteLater()

    def test_card_list_is_read_only_and_old_image_actions_are_absent(self):
        self.service.load_card_details.return_value = [self.detail()]
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, 1)
        index = view._model.index(0, 0)
        self.assertFalse(view._model.flags(index) & Qt.ItemIsEditable)
        self.assertEqual(
            view._table.editTriggers(),
            QAbstractItemView.NoEditTriggers,
        )
        self.assertIsNone(view.findChild(QPushButton, "btnReplaceImage"))
        self.assertIsNone(view.findChild(QPushButton, "btnReplaceMini"))
        self.assertFalse(view._update_button.isEnabled())
        view._table.selectRow(0)
        self.application.processEvents()
        self.assertTrue(view._update_button.isEnabled())
        with patch.object(view, "_open_card_detail") as open_editor:
            view._open_index(index)
        self.assertEqual(open_editor.call_args.args[0].card_index, 1)
        view.deleteLater()

    def test_card_detail_reopens_for_double_click_and_update_across_cycles(self):
        self.service.load_card_details.return_value = [self.detail(1), self.detail(2)]
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, 2)
        view.show()

        opened_indexes = []
        for cycle, row in enumerate((0, 0, 1, 1), start=1):
            view._table.selectRow(row)
            self.application.processEvents()
            if cycle in (2, 4):
                view._update_card()
            else:
                view._open_index(view._model.index(row, 0))
            self.application.processEvents()
            dialog = view._editor_dialog
            self.assertIsNotNone(dialog)
            self.assertTrue(dialog.isVisible())
            opened_indexes.append(dialog.draft.card_index)
            dialog.close()
            self.application.processEvents()
            self.assertIsNone(view._editor_dialog)

        self.assertEqual(opened_indexes, [1, 1, 2, 2])

        self.service.update_card.return_value = self.detail(1)
        view._open_index(view._model.index(0, 0))
        self.application.processEvents()
        view._editor_dialog._save_and_close()
        self.assertTrue(self.wait_until(lambda: view._editor_dialog is None))
        self.service.update_card.assert_called_once()
        self.assertIsNone(view._editor_dialog)
        view.close()
        view.deleteLater()

    def test_open_live_card_detail_focuses_existing_without_duplicate(self):
        self.service.load_card_details.return_value = [self.detail(1), self.detail(2)]
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, 2)
        view._open_card_detail(view._model.card_at(0))
        dialog = view._editor_dialog
        with (
            patch.object(dialog, "show") as show,
            patch.object(dialog, "raise_") as raise_window,
            patch.object(dialog, "activateWindow") as activate,
        ):
            view._open_card_detail(view._model.card_at(1))
        self.assertIs(view._editor_dialog, dialog)
        show.assert_called_once_with()
        raise_window.assert_called_once_with()
        activate.assert_called_once_with()
        dialog.close()
        self.application.processEvents()
        view.deleteLater()

    def test_double_click_signal_has_one_open_callback(self):
        self.service.load_card_details.return_value = [self.detail()]
        view = CardListView(self.manifest, self.service)
        self.wait_for_cards(view, 1)
        with patch.object(view, "_open_card_detail") as open_detail:
            view._table.doubleClicked.emit(view._model.index(0, 0))
            self.application.processEvents()
        open_detail.assert_called_once()
        view.deleteLater()


if __name__ == "__main__":
    unittest.main()
