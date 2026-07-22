from __future__ import annotations

import importlib.util
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None

if PYSIDE_AVAILABLE:
    from PySide6.QtCore import QEvent, QPoint, Qt, QThread
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QHeaderView,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QTableView,
        QWidget,
    )

    from main import (
        WINDOWS_APP_USER_MODEL_ID,
        ApplicationController,
        configure_application_icon,
        configure_windows_app_id,
    )
    from yugioh_editor.common.constants import ui_path
    from yugioh_editor.common.errors import PackResourceError
    from yugioh_editor.models.card_editing import CardDetailData, CardLocalizedText
    from yugioh_editor.models.entities import (
        ExecutableManifest,
        ProjectFileRecord,
        ProjectManifest,
    )
    from yugioh_editor.resources import get_resource_path
    from yugioh_editor.services.card_service import CardService
    from yugioh_editor.services.project_service import ProjectService
    from yugioh_editor.views.card_list_view import CardListView
    from yugioh_editor.views.editors import BinaryEditor, TableEditor
    from yugioh_editor.views.project_view import ProjectView
    from yugioh_editor.views.start_view import StartView
    from yugioh_editor.views.ui_loader import load_ui
    from yugioh_editor.workers.task_runner import TaskError, TaskRunner


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is required for UI loading tests.")
class UiLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.application.processEvents()
            if predicate():
                self.application.processEvents()
                return True
            time.sleep(0.01)
        self.application.processEvents()
        return bool(predicate())

    @staticmethod
    def card_detail(
        index: int,
        *,
        name: str = "",
        card_type: str = "dragon",
        card_category: str = "normal",
        attribute: str = "dark",
    ) -> CardDetailData:
        return CardDetailData(
            card_index=index,
            card_id=index,
            localized_text=CardLocalizedText(names={"eng": name}),
            password="00000000",
            level=4,
            attack=1600,
            defense=1200,
            attribute=attribute,
            card_type=card_type,
            card_category=card_category,
            pack="disabled",
            image_name="token_sl.bmp",
        )

    def test_all_designer_files_load(self):
        for file_name in (
            "start_window.ui",
            "project_window.ui",
            "card_list_window.ui",
            "card_editor_dialog.ui",
        ):
            with self.subTest(ui_file=file_name):
                widget = load_ui(ui_path(file_name))
                self.assertIsNotNone(widget, file_name)
                widget.deleteLater()

    def test_application_icon_resource_loads(self):
        resource_path = get_resource_path("app.icon")
        self.assertTrue(resource_path.is_file())
        self.assertFalse(QIcon(str(resource_path)).isNull())
        with self.assertRaises(FileNotFoundError):
            get_resource_path("missing.icon")

    def test_startup_configures_application_icon_and_windows_app_id(self):
        self.application.setWindowIcon(QIcon())
        configured_icon = configure_application_icon(self.application)
        self.assertFalse(self.application.windowIcon().isNull())
        self.assertEqual(
            configured_icon.cacheKey(),
            self.application.windowIcon().cacheKey(),
        )

        with tempfile.TemporaryDirectory() as directory:
            project_service = Mock(spec=ProjectService)
            project_service.list_visible_resources.return_value = []
            card_service = Mock(spec=CardService)
            card_service.load_card_details.return_value = []
            manifest = ProjectManifest(
                "Application icon",
                directory,
                version_prefix="mai",
            )
            windows = (
                StartView(project_service),
                ProjectView(manifest, project_service, card_service),
                CardListView(manifest, card_service),
            )
            for window in windows:
                with self.subTest(window=type(window).__name__):
                    self.assertFalse(window.windowIcon().isNull())
                    self.assertEqual(
                        window.windowIcon().cacheKey(),
                        configured_icon.cacheKey(),
                    )
                window.deleteLater()

        with (
            patch("main.sys.platform", "linux"),
            patch(
                "main.ctypes.windll",
                create=True,
            ) as windll,
        ):
            configure_windows_app_id()
            windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_not_called()

        with (
            patch("main.sys.platform", "win32"),
            patch(
                "main.ctypes.windll",
                create=True,
            ) as windll,
        ):
            windll.shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0
            configure_windows_app_id()
            windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with(
                WINDOWS_APP_USER_MODEL_ID
            )

    def test_controller_opens_project_window_maximized(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = ApplicationController()
            manifest = ProjectManifest(
                "Maximized project",
                directory,
                version_prefix="mai",
            )
            with (
                patch.object(controller.start_view, "hide") as hide,
                patch.object(
                    ProjectView,
                    "showMaximized",
                    autospec=True,
                ) as show_maximized,
                patch.object(ProjectView, "show", autospec=True) as show,
            ):
                controller.open_project(manifest)

            hide.assert_called_once_with()
            show_maximized.assert_called_once_with()
            show.assert_not_called()
            controller.project_view.deleteLater()
            controller.start_view.deleteLater()

    def test_start_view_preserves_designer_values(self):
        view = StartView(ProjectService())
        view.show()
        self.application.processEvents()
        self.assertEqual((view.width(), view.height()), (760, 200))
        self.assertLess(view.height(), 250)
        self.assertEqual(view.minimumSize(), view.maximumSize())
        self.assertEqual(view.centralWidget().minimumSize(), view.size())
        self.assertEqual(view.centralWidget().maximumSize(), view.size())
        self.assertEqual(view.centralWidget().layout().count(), 4)
        content_rect = view.centralWidget().contentsRect()
        for child in view.centralWidget().findChildren(QWidget):
            if not child.isVisibleTo(view.centralWidget()):
                continue
            with self.subTest(widget=child.objectName()):
                top_left = child.mapTo(
                    view.centralWidget(),
                    child.rect().topLeft(),
                )
                bottom_right = child.mapTo(
                    view.centralWidget(),
                    child.rect().bottomRight(),
                )
                self.assertTrue(content_rect.contains(top_left))
                self.assertTrue(content_rect.contains(bottom_right))
        self.assertEqual(
            view.findChild(QLineEdit, "versionPrefixLineEdit").text(),
            "mai",
        )
        self.assertEqual(
            view.findChild(QLineEdit, "txtWorkspace").text(),
            r"D:\Projects\YGOMOD\Mod_Editor_Projects",
        )
        self.assertEqual(
            view.findChild(QLineEdit, "txtGameFolder").text(),
            r"D:\Game\Yugioh\Yu-Gi-Oh! Power of Chaos JOEY THE PASSION",
        )
        view.close()
        view.deleteLater()

    def test_card_list_is_maximized_and_both_scrollbars_have_range(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=CardService)
            service.load_card_details.return_value = [
                self.card_detail(index, name=f"Card {index} " + ("x" * 80))
                for index in range(200)
            ]
            view = CardListView(
                ProjectManifest(
                    "Card list",
                    directory,
                    version_prefix="mai",
                ),
                service,
            )
            view.showMaximized()
            self.assertTrue(self.wait_until(lambda: view._model.rowCount() == 200))
            self.application.processEvents()

            table = view.findChild(QTableView, "tableCards")
            self.assertTrue(view.isMaximized())
            self.assertEqual(
                table.verticalScrollBarPolicy(),
                Qt.ScrollBarPolicy.ScrollBarAsNeeded,
            )
            self.assertEqual(
                table.horizontalScrollBarPolicy(),
                Qt.ScrollBarPolicy.ScrollBarAsNeeded,
            )
            self.assertGreater(table.verticalScrollBar().maximum(), 0)
            self.assertGreater(table.horizontalScrollBar().maximum(), 0)
            self.assertFalse(table.horizontalHeader().stretchLastSection())
            self.assertEqual(
                table.horizontalHeader().sectionResizeMode(0),
                QHeaderView.ResizeMode.Interactive,
            )
            view.close()
            view.deleteLater()

    def test_card_list_shows_category_and_full_type_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=CardService)
            service.load_card_details.return_value = [
                self.card_detail(
                    0,
                    card_type="non_game_card",
                    card_category="effect",
                    attribute="divine",
                )
            ]
            view = CardListView(
                ProjectManifest(
                    "Card list",
                    directory,
                    version_prefix="mai",
                ),
                service,
            )
            self.assertTrue(self.wait_until(lambda: view._model.rowCount() == 1))
            headers = [
                str(view._model.headerData(column, Qt.Horizontal))
                for column in range(view._model.columnCount())
            ]
            self.assertIn("Category", headers)
            self.assertIn("Card Type", headers)
            self.assertNotIn("card_category_code", headers)
            self.assertNotIn("monster_type_code", headers)
            self.assertNotIn("requires_two_tributes", headers)
            category_column = headers.index("Category")
            type_column = headers.index("Card Type")
            self.assertEqual(
                view._model.data(view._model.index(0, category_column)),
                "Effect",
            )
            self.assertEqual(
                view._model.data(view._model.index(0, type_column)),
                "Non-Game Card",
            )
            view.close()
            view.deleteLater()

    def test_project_view_reuses_live_card_list_and_recreates_after_close(self):
        with tempfile.TemporaryDirectory() as directory:
            project_service = Mock(spec=ProjectService)
            project_service.list_visible_resources.return_value = []
            card_service = Mock(spec=CardService)
            card_service.load_card_details.return_value = [
                CardDetailData(
                    card_index=1,
                    card_id=11,
                    localized_text=CardLocalizedText(names={"eng": "Card"}),
                    password="12345678",
                    level=4,
                    attack=1000,
                    defense=1000,
                    attribute="dark",
                    card_type="dragon",
                    card_category="normal",
                    pack="disabled",
                    image_name="",
                )
            ]
            card_service.load_card_image.side_effect = KeyError("no fixture image")
            view = ProjectView(
                ProjectManifest(
                    "Cards",
                    directory,
                    version_prefix="mai",
                ),
                project_service,
                card_service,
            )

            view._open_card_list()
            first = view._card_list_view
            self.assertIsNotNone(first)
            with (
                patch.object(first, "showMaximized") as show,
                patch.object(first, "raise_") as raise_window,
                patch.object(first, "activateWindow") as activate,
            ):
                view._open_card_list()
            self.assertIs(view._card_list_view, first)
            show.assert_called_once_with()
            raise_window.assert_called_once_with()
            activate.assert_called_once_with()

            instances = [first]
            for _cycle in range(3):
                view._card_list_view.close()
                self.application.processEvents()
                self.application.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertIsNone(view._card_list_view)
                view._open_card_list()
                self.application.processEvents()
                self.assertTrue(view._card_list_view.isVisible())
                instances.append(view._card_list_view)
            self.assertEqual(len({id(dialog) for dialog in instances}), 4)

            reopened = view._card_list_view
            self.assertTrue(self.wait_until(lambda: reopened._model.rowCount() == 1))
            self.assertEqual(reopened._model.rowCount(), 1)
            reopened._table.selectRow(0)
            reopened._open_index(reopened._model.index(0, 0))
            first_detail = reopened._editor_dialog
            self.assertIsNotNone(first_detail)
            self.application.processEvents()
            self.assertIs(reopened._editor_dialog, first_detail)
            self.assertTrue(first_detail.isVisible())
            reopened._editor_dialog.close()
            self.application.processEvents()
            self.application.sendPostedEvents(None, QEvent.DeferredDelete)
            reopened._update_card()
            self.application.processEvents()
            self.assertTrue(reopened._editor_dialog.isVisible())
            reopened._editor_dialog.close()
            self.application.processEvents()
            self.application.sendPostedEvents(None, QEvent.DeferredDelete)

            view._card_list_view.close()
            self.application.processEvents()
            view.deleteLater()

    def test_start_view_passes_current_widget_prefix_to_service(self):
        service = Mock(spec=ProjectService)
        service.validate_version_prefix.return_value = "not-the-widget-value"
        view = StartView(service)
        view._txt_project_name.setText("Demo")
        view._txt_workspace.setText("workspace")
        view._txt_game_folder.setText("game")
        view._version_prefix.setText("  eng  ")

        with patch.object(view, "_run_task") as run_task:
            view._create_project()

        service.validate_version_prefix.assert_called_once_with("eng")
        action = run_task.call_args.args[0]
        action()
        service.create_project.assert_called_once_with(
            "Demo",
            "workspace",
            "game",
            "eng",
        )
        view.deleteLater()

    def test_start_view_rejects_empty_prefix_before_starting_task(self):
        for value in ("", "   "):
            with self.subTest(prefix=value):
                service = Mock(spec=ProjectService)
                view = StartView(service)
                view._version_prefix.setText(value)
                with (
                    patch.object(view, "_run_task") as run_task,
                    patch.object(QMessageBox, "warning") as warning,
                ):
                    view._create_project()
                warning.assert_called_once_with(
                    view,
                    "Invalid Version Prefix",
                    "Version prefix is required.",
                )
                run_task.assert_not_called()
                service.validate_version_prefix.assert_not_called()
                service.create_project.assert_not_called()
                view.deleteLater()

    def test_large_binary_editor_is_read_only_and_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            record = ProjectFileRecord(
                source_file="game_pc.exe",
                relative_path="mai/mai_pc.exe",
                workspace_path="mai/mai_pc.exe",
                file_kind="exe",
                storage_format="binary",
            )
            manifest = ProjectManifest(
                "Binary",
                directory,
                version_prefix="mai",
                files=[record],
            )
            path = manifest.root / "mai" / "mai_pc.exe"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"A" * (BinaryEditor.PREVIEW_LIMIT + 1))
            editor = BinaryEditor(ProjectService(), manifest, record)
            self.assertTrue(editor.editor.isReadOnly())
            self.assertNotIn(
                "41 " * (BinaryEditor.PREVIEW_LIMIT + 1),
                editor.editor.toPlainText(),
            )
            editor.deleteLater()

    def test_indexed_text_table_editor_has_one_column_and_preserves_row_order(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = ProjectManifest(
                "Indexed text UI",
                directory,
                version_prefix="mai",
            )
            for relative_path in (
                "bin#/card_desceng.bin",
                "bin#/dlg_texteng.bin",
            ):
                with self.subTest(relative_path=relative_path):
                    service = Mock(spec=ProjectService)
                    service.read_project_table.return_value = pd.DataFrame(
                        {
                            "text": ["", "First", "", "Last"],
                            "is_reserved": [False, False, True, False],
                        }
                    )
                    service.project_table_editor_columns.return_value = ("text",)
                    record = ProjectFileRecord(
                        source_file="Data.dat",
                        relative_path=relative_path,
                        workspace_path=f"data/{relative_path}",
                        file_kind="table",
                        storage_format="table",
                        language="eng",
                    )
                    editor = TableEditor(service, manifest, record)
                    self.assertEqual(
                        list(editor.frame.columns),
                        ["text", "is_reserved"],
                    )
                    self.assertEqual(editor.model.columnCount(), 1)
                    self.assertEqual(editor.model.rowCount(), 4)
                    self.assertEqual(
                        editor.model.headerData(0, Qt.Horizontal),
                        "text",
                    )
                    self.assertEqual(editor.model.headerData(0, Qt.Vertical), 1)
                    self.assertFalse(editor.table.isSortingEnabled())
                    self.assertEqual(
                        editor.table.dragDropMode(),
                        QAbstractItemView.DragDropMode.NoDragDrop,
                    )
                    editor.save()
                    saved = service.write_project_table.call_args.args[2]
                    self.assertEqual(saved["text"].tolist(), ["", "First", "", "Last"])
                    self.assertEqual(
                        saved["is_reserved"].tolist(),
                        [False, False, True, False],
                    )
                    editor.deleteLater()

    def test_project_view_enables_run_only_for_manifest_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            empty_manifest = ProjectManifest(
                "Empty",
                directory,
                version_prefix="mai",
            )
            empty_view = ProjectView(
                empty_manifest,
                ProjectService(),
                CardService(),
            )
            self.assertFalse(
                empty_view.findChild(QPushButton, "btnBuildAndRun").isEnabled()
            )
            self.assertEqual(
                empty_view.findChild(QPushButton, "btnBuildAndRun").toolTip(),
                "This project does not contain an executable.",
            )
            empty_view.deleteLater()

            manifest = ProjectManifest(
                "Executable",
                directory,
                version_prefix="mai",
                executable=ExecutableManifest(
                    source_name="joey_pc.exe",
                    relative_path="mai/mai_pc.exe",
                ),
            )
            view = ProjectView(manifest, ProjectService(), CardService())
            self.assertTrue(view.findChild(QPushButton, "btnBuildAndRun").isEnabled())
            self.assertEqual(view.findChild(QPushButton, "btnBuild").text(), "Build")
            self.assertEqual(
                view.findChild(QPushButton, "btnBuildAndRun").text(),
                "Run",
            )
            self.assertEqual(
                view.findChild(QPushButton, "btnBuildAndRun").toolTip(), ""
            )
            self.assertIsNone(view.findChild(QPushButton, "btnPack"))
            self.assertIsNone(view.findChild(QPushButton, "btnRun"))
            view.deleteLater()

    def test_project_view_run_success_has_no_dialog_and_restores_state(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            mocked_process = object()
            worker_threads = []

            def run_packed_game(_manifest):
                worker_threads.append(QThread.currentThread())
                return mocked_process

            service.run_packed_game.side_effect = run_packed_game
            manifest = ProjectManifest(
                "Run success",
                directory,
                version_prefix="mai",
                executable=ExecutableManifest(
                    source_name="joey_pc.exe",
                    relative_path="mai/mai_pc.exe",
                ),
            )
            view = ProjectView(manifest, service, CardService())
            view.show()
            run_button = view.findChild(QPushButton, "btnBuildAndRun")

            with (
                patch.object(QMessageBox, "information") as information,
                patch.object(QMessageBox, "critical") as critical,
            ):
                run_button.click()
                self.assertTrue(view._pgb_progress.isVisible())
                self.assertTrue(self.wait_until(lambda: not view._active_runners))

            service.run_packed_game.assert_called_once_with(manifest)
            service.pack_project.assert_not_called()
            self.assertNotEqual(worker_threads[0], self.application.thread())
            information.assert_not_called()
            critical.assert_not_called()
            self.assertFalse(view._pgb_progress.isVisible())
            self.assertTrue(view.isVisible())
            view.close()
            view.deleteLater()

    def test_project_view_run_failure_shows_error_and_restores_state(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            service.run_packed_game.side_effect = FileNotFoundError(
                "Packed executable was not found."
            )
            manifest = ProjectManifest(
                "Run failure",
                directory,
                version_prefix="mai",
                executable=ExecutableManifest(
                    source_name="joey_pc.exe",
                    relative_path="mai/mai_pc.exe",
                ),
            )
            view = ProjectView(manifest, service, CardService())
            view.show()
            run_button = view.findChild(QPushButton, "btnBuildAndRun")

            with (
                self.assertLogs(level="ERROR"),
                patch.object(QMessageBox, "information") as information,
                patch.object(QMessageBox, "critical") as critical,
            ):
                run_button.click()
                self.assertTrue(view._pgb_progress.isVisible())
                self.assertTrue(self.wait_until(lambda: not view._active_runners))

            service.run_packed_game.assert_called_once_with(manifest)
            service.pack_project.assert_not_called()
            critical.assert_called_once_with(
                view,
                "Operation Failed",
                "Packed executable was not found.",
            )
            information.assert_not_called()
            self.assertFalse(view._pgb_progress.isVisible())
            self.assertTrue(view.isVisible())
            view.close()
            view.deleteLater()

    def test_project_view_pack_success_runs_off_ui_thread_and_restores_state(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            output = Path(directory) / "bin"
            worker_threads = []

            def pack(_manifest):
                worker_threads.append(QThread.currentThread())
                return output

            service.pack_project.side_effect = pack
            manifest = ProjectManifest(
                "Pack success",
                directory,
                version_prefix="mai",
            )
            view = ProjectView(manifest, service, CardService())
            view.show()
            pack_button = view.findChild(QPushButton, "btnBuild")
            callback_threads = []

            def show_success(*_args):
                callback_threads.append(QThread.currentThread())

            with (
                patch.object(QMessageBox, "information", side_effect=show_success),
                patch.object(QMessageBox, "critical") as critical,
            ):
                pack_button.click()
                self.assertFalse(pack_button.isEnabled())
                self.assertTrue(view._pgb_progress.isVisible())
                self.assertTrue(self.wait_until(lambda: not view._pack_in_progress))

            service.pack_project.assert_called_once_with(manifest)
            self.assertNotEqual(worker_threads[0], self.application.thread())
            self.assertEqual(callback_threads[0], self.application.thread())
            self.assertTrue(pack_button.isEnabled())
            self.assertFalse(view._pgb_progress.isVisible())
            self.assertEqual(view._active_runners, {})
            self.assertTrue(view.isVisible())
            critical.assert_not_called()
            view.close()
            view.deleteLater()

    def test_project_view_starts_only_one_pack_task_for_rapid_clicks(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            manifest = ProjectManifest(
                "Pack once",
                directory,
                version_prefix="mai",
            )
            view = ProjectView(manifest, service, CardService())
            pack_button = view.findChild(QPushButton, "btnBuild")

            with patch.object(view, "_run_task") as run_task:
                pack_button.click()
                pack_button.click()

            self.assertEqual(run_task.call_count, 1)
            self.assertFalse(pack_button.isEnabled())
            view.deleteLater()

    def test_project_view_pack_error_has_resource_context_and_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            failure = PackResourceError(
                source_file="Data.dat",
                resource="bin#/card_intid.bin",
                pattern="card_intid.bin",
                codec="integer_list",
                virtual=True,
                phase="pre_encode",
                step=0,
                method="load_dependency_table",
                cause=KeyError("card_id.bin"),
            )
            service.pack_project.side_effect = failure
            manifest = ProjectManifest(
                "Pack failure",
                directory,
                version_prefix="mai",
            )
            view = ProjectView(manifest, service, CardService())
            view.show()
            pack_button = view.findChild(QPushButton, "btnBuild")

            with (
                self.assertLogs(level="ERROR") as logs,
                patch.object(QMessageBox, "critical") as critical,
            ):
                pack_button.click()
                self.assertTrue(self.wait_until(lambda: not view._pack_in_progress))

            critical.assert_called_once_with(
                view,
                "Packing Failed",
                "Packing failed while processing 'card_intid.bin'. "
                "Check the application log for details.",
            )
            log_output = "\n".join(logs.output)
            self.assertIn("Background task failed", log_output)
            self.assertIn("card_id.bin", log_output)
            self.assertNotIn("QThread: Destroyed", log_output)
            self.assertTrue(pack_button.isEnabled())
            self.assertFalse(view._pgb_progress.isVisible())
            self.assertEqual(view._active_runners, {})
            self.assertTrue(view.isVisible())

            service.pack_project.side_effect = None
            service.pack_project.return_value = Path(directory) / "bin"
            with patch.object(QMessageBox, "information"):
                pack_button.click()
                self.assertTrue(self.wait_until(lambda: not view._pack_in_progress))
            self.assertEqual(service.pack_project.call_count, 2)
            view.close()
            view.deleteLater()

    def test_task_runner_emits_structured_error_with_traceback(self):
        received = []

        def fail():
            raise ValueError("controlled worker error")

        runner = TaskRunner(fail)
        runner.signals.failed.connect(received.append)
        runner.run()

        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], TaskError)
        self.assertEqual(received[0].exception_type, "ValueError")
        self.assertIn("controlled worker error", received[0].message)
        self.assertIn("Traceback", received[0].details)

    def test_project_view_prevents_close_while_pack_is_running(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            started = threading.Event()
            release = threading.Event()

            def pack(_manifest):
                started.set()
                self.assertTrue(release.wait(5))
                return Path(directory) / "bin"

            service.pack_project.side_effect = pack
            manifest = ProjectManifest(
                "Pack close",
                directory,
                version_prefix="mai",
            )
            view = ProjectView(manifest, service, CardService())
            view.show()
            pack_button = view.findChild(QPushButton, "btnBuild")

            with patch.object(QMessageBox, "information") as information:
                pack_button.click()
                self.assertTrue(self.wait_until(started.is_set))
                view.close()
                self.application.processEvents()
                self.assertTrue(view.isVisible())
                self.assertTrue(view._pack_in_progress)
                release.set()
                self.assertTrue(self.wait_until(lambda: not view._pack_in_progress))

            self.assertTrue(
                any(
                    call.args[1] == "Packing in Progress"
                    for call in information.call_args_list
                )
            )
            view.close()
            view.deleteLater()

    def test_project_tree_deduplicates_roots_paths_and_refreshes(self):
        with tempfile.TemporaryDirectory() as directory:
            records = [
                ProjectFileRecord(
                    source_file="Data.dat",
                    relative_path=r"data\bin#\card_id.bin",
                    workspace_path="data/bin#/card_id.bin",
                    file_kind="table",
                    storage_format="table",
                ),
                ProjectFileRecord(
                    source_file="data.dat",
                    relative_path="Data/BIN#/card_prop.bin",
                    workspace_path="data/bin#/card_prop.bin",
                    file_kind="table",
                    storage_format="table",
                ),
                ProjectFileRecord(
                    source_file="DATA.DAT",
                    relative_path="DATA/bin#/CARD_ID.BIN",
                    workspace_path="data/bin#/card_id.bin",
                    file_kind="table",
                    storage_format="table",
                ),
                ProjectFileRecord(
                    source_file="VOICE.DAT",
                    relative_path="voice/a.wav",
                    workspace_path="voice/a.wav",
                    file_kind="audio",
                    storage_format="binary",
                ),
                ProjectFileRecord(
                    source_file="Voice.dat",
                    relative_path="Voice/sub/b.wav",
                    workspace_path="voice/sub/b.wav",
                    file_kind="audio",
                    storage_format="binary",
                ),
                ProjectFileRecord(
                    source_file="Data.dat",
                    relative_path="bin#/card_sorteng.bin",
                    workspace_path=None,
                    file_kind="virtual",
                    storage_format="virtual",
                    generated_on_pack=True,
                    virtual=True,
                ),
            ]
            manifest = ProjectManifest(
                "Tree",
                directory,
                version_prefix="mai",
                files=records,
            )
            view = ProjectView(manifest, ProjectService(), CardService())
            view.resize(1280, 720)
            view.show()
            self.application.processEvents()
            tree = view._tree
            self.assertEqual(tree.minimumWidth(), 200)
            self.assertEqual(tree.maximumWidth(), 460)
            tree_width, data_width = view._splitter.sizes()
            self.assertGreater(data_width, tree_width)

            def snapshot():
                return [
                    (
                        tree.topLevelItem(index).text(0),
                        tree.topLevelItem(index).childCount(),
                    )
                    for index in range(tree.topLevelItemCount())
                ]

            first = snapshot()
            view._populate_tree()
            second = snapshot()
            view._populate_tree()
            third = snapshot()
            self.assertEqual(first, second)
            self.assertEqual(second, third)
            roots = [name.casefold() for name, _ in first]
            self.assertEqual(roots.count("data"), 1)
            self.assertEqual(roots.count("voice"), 1)
            data_root = next(
                tree.topLevelItem(index)
                for index in range(tree.topLevelItemCount())
                if tree.topLevelItem(index).text(0).casefold() == "data"
            )
            self.assertEqual(data_root.childCount(), 1)
            self.assertEqual(data_root.child(0).text(0).casefold(), "bin#")
            self.assertEqual(data_root.child(0).childCount(), 2)
            view.deleteLater()

    def test_project_editor_content_tracks_tree_height_without_changing_width(self):
        with tempfile.TemporaryDirectory() as directory:
            project_service = Mock(spec=ProjectService)
            project_service.list_visible_resources.return_value = []
            project_service.read_project_table.return_value = pd.DataFrame(
                {"value": ["fixture"]}
            )
            project_service.project_table_editor_columns.return_value = ()
            project_service.read_project_binary_preview.return_value = (
                b"\x00\x01",
                2,
            )
            manifest = ProjectManifest(
                "Layout",
                directory,
                version_prefix="mai",
            )
            table_record = ProjectFileRecord(
                source_file="Data.dat",
                relative_path="data/example.bin",
                workspace_path="data/example.csv",
                file_kind="table",
                storage_format="table",
            )
            binary_record = ProjectFileRecord(
                source_file="Data.dat",
                relative_path="data/raw.bin",
                workspace_path="data/raw.bin",
                file_kind="binary",
                storage_format="binary",
            )
            view = ProjectView(manifest, project_service, Mock(spec=CardService))
            view.resize(1280, 720)
            view.show()
            self.application.processEvents()

            initial_splitter_widths = view._splitter.sizes()
            initial_tree_width = view._tree.width()
            initial_host_width = view._editor_host.width()

            def vertical_extent(widget: QWidget) -> tuple[int, int]:
                top = widget.mapTo(view, QPoint(0, 0)).y()
                bottom = widget.mapTo(
                    view,
                    QPoint(0, widget.height() - 1),
                ).y()
                return top, bottom

            def assert_matches_tree(content: QWidget) -> None:
                self.assertEqual(content.height(), view._tree.height())
                self.assertEqual(vertical_extent(content), vertical_extent(view._tree))
                self.assertEqual(view._tree.width(), initial_tree_width)
                self.assertEqual(view._editor_host.width(), initial_host_width)
                self.assertEqual(view._splitter.sizes(), initial_splitter_widths)

            self.assertIsNone(view._current_editor)
            self.assertEqual(view._editor_layout.count(), 0)
            assert_matches_tree(view._editor_host)

            table_editor = TableEditor(project_service, manifest, table_record)
            view._set_editor(table_editor)
            self.application.processEvents()
            table_margins = table_editor.layout().contentsMargins()
            initial_content_width = table_editor.table.width()
            initial_content_height = table_editor.table.height()
            self.assertEqual(table_margins.top(), 0)
            self.assertEqual(table_margins.bottom(), 0)
            self.assertGreater(table_margins.left(), 0)
            self.assertEqual(table_margins.left(), table_margins.right())
            self.assertEqual(table_editor.height(), view._tree.height())
            assert_matches_tree(table_editor.table)
            self.assertEqual(
                table_editor.table.width(),
                table_editor.width() - table_margins.left() - table_margins.right(),
            )

            view.resize(1280, 900)
            self.application.processEvents()
            self.assertGreater(table_editor.table.height(), initial_content_height)
            self.assertEqual(table_editor.height(), view._tree.height())
            assert_matches_tree(table_editor.table)
            self.assertEqual(table_editor.table.width(), initial_content_width)

            binary_editor = BinaryEditor(project_service, manifest, binary_record)
            view._set_editor(binary_editor)
            self.application.processEvents()
            binary_margins = binary_editor.layout().contentsMargins()
            self.assertIs(view._current_editor, binary_editor)
            self.assertEqual(view._editor_layout.count(), 1)
            self.assertEqual(binary_margins.top(), 0)
            self.assertEqual(binary_margins.bottom(), 0)
            self.assertEqual(binary_margins.left(), table_margins.left())
            self.assertEqual(binary_margins.right(), table_margins.right())
            self.assertEqual(binary_editor.height(), view._tree.height())
            assert_matches_tree(binary_editor.editor)
            self.assertEqual(binary_editor.editor.width(), initial_content_width)

            view.resize(1280, 700)
            self.application.processEvents()
            self.assertLess(binary_editor.editor.height(), initial_content_height)
            self.assertEqual(binary_editor.height(), view._tree.height())
            assert_matches_tree(binary_editor.editor)
            self.assertEqual(binary_editor.editor.width(), initial_content_width)
            view.close()
            view.deleteLater()
