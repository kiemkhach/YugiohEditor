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
    from PySide6.QtCore import QEvent, QPoint, QSettings, Qt, QThread
    from PySide6.QtGui import QAction, QIcon, QKeySequence
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QFileDialog,
        QFormLayout,
        QHeaderView,
        QLineEdit,
        QMenu,
        QMessageBox,
        QPushButton,
        QSizePolicy,
        QTableView,
        QToolBar,
        QTreeWidgetItem,
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
    from yugioh_editor.views.editors import (
        AudioEditor,
        BinaryEditor,
        ImageEditor,
        TableEditor,
    )
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

    def isolated_start_settings(self) -> QSettings:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = QSettings(
            str(Path(directory.name) / "settings.ini"),
            QSettings.Format.IniFormat,
        )
        settings.setFallbacksEnabled(False)
        return settings

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
            project_service.find_registered_game_folder.return_value = None
            project_service.list_visible_resources.return_value = []
            card_service = Mock(spec=CardService)
            card_service.load_card_details.return_value = []
            manifest = ProjectManifest(
                "Application icon",
                directory,
                version_prefix="mai",
            )
            windows = (
                StartView(project_service, self.isolated_start_settings()),
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
            with (
                patch.object(
                    StartView,
                    "_create_default_settings",
                    return_value=self.isolated_start_settings(),
                ),
                patch.object(
                    ProjectService,
                    "find_registered_game_folder",
                    return_value=None,
                ),
            ):
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
        service = Mock(spec=ProjectService)
        service.find_registered_game_folder.return_value = None
        view = StartView(service, self.isolated_start_settings())
        view.show()
        self.application.processEvents()
        self.assertEqual((view.width(), view.height()), (760, 230))
        self.assertLess(view.height(), 280)
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
            "",
        )
        self.assertEqual(
            view.findChild(QLineEdit, "txtGameFolder").text(),
            "",
        )
        self.assertEqual(view.findChild(QLineEdit, "txtIcon").text(), "")
        self.assertEqual(view.findChild(QFormLayout, "formLayout").rowCount(), 5)
        view.close()
        view.deleteLater()

    def test_start_view_default_settings_use_stable_user_ini_location(self):
        with patch("yugioh_editor.views.start_view.QSettings") as settings_type:
            settings_type.Format.IniFormat = QSettings.Format.IniFormat
            settings_type.Scope.UserScope = QSettings.Scope.UserScope
            settings = StartView._create_default_settings()

        settings_type.assert_called_once_with(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            StartView.SETTINGS_ORGANIZATION,
            StartView.SETTINGS_APPLICATION,
        )
        settings.setFallbacksEnabled.assert_called_once_with(False)

    def test_start_view_missing_workspace_preference_is_normal(self):
        service = Mock(spec=ProjectService)
        service.find_registered_game_folder.return_value = None
        with self.assertLogs(level="DEBUG") as logs:
            view = StartView(service, self.isolated_start_settings())
        self.assertIn(
            "No saved Workspace folder was found",
            "\n".join(logs.output),
        )
        self.assertEqual(view._txt_workspace.text(), "")
        view.deleteLater()

    def test_start_view_missing_registered_game_folder_is_silent(self):
        service = Mock(spec=ProjectService)
        service.find_registered_game_folder.return_value = None
        with (
            patch.object(QMessageBox, "critical") as critical,
            patch.object(QMessageBox, "warning") as warning,
        ):
            view = StartView(service, self.isolated_start_settings())

        service.find_registered_game_folder.assert_called_once_with()
        self.assertEqual(view._txt_game_folder.text(), "")
        critical.assert_not_called()
        warning.assert_not_called()
        view.deleteLater()

    def test_start_view_detects_game_folder_once_without_overwriting_ui_value(self):
        registered_folder = r"C:\Games\Joey"
        service = Mock(spec=ProjectService)
        service.find_registered_game_folder.return_value = registered_folder
        view = StartView(service, self.isolated_start_settings())

        self.assertEqual(view._txt_game_folder.text(), registered_folder)
        view._detect_game_folder()
        service.find_registered_game_folder.assert_called_once_with()
        view.deleteLater()

        def load_with_current_game_folder(ui_file, parent):
            root = load_ui(ui_file, parent)
            root.findChild(QLineEdit, "txtGameFolder").setText("current folder")
            return root

        service = Mock(spec=ProjectService)
        with patch(
            "yugioh_editor.views.start_view.load_ui",
            side_effect=load_with_current_game_folder,
        ):
            view = StartView(service, self.isolated_start_settings())

        self.assertEqual(view._txt_game_folder.text(), "current folder")
        service.find_registered_game_folder.assert_not_called()
        view.deleteLater()

    def test_start_view_logs_registry_detection_error_without_popup(self):
        service = Mock(spec=ProjectService)
        service.find_registered_game_folder.side_effect = OSError("registry denied")
        with (
            self.assertLogs(level="ERROR") as logs,
            patch.object(QMessageBox, "critical") as critical,
            patch.object(QMessageBox, "warning") as warning,
        ):
            view = StartView(service, self.isolated_start_settings())

        self.assertIn(
            "Unable to detect the registered Game folder",
            "\n".join(logs.output),
        )
        critical.assert_not_called()
        warning.assert_not_called()
        self.assertEqual(view._txt_game_folder.text(), "")
        view.deleteLater()

    def test_start_view_restores_workspace_and_persists_browse_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.ini"
            first_settings = QSettings(
                str(settings_path),
                QSettings.Format.IniFormat,
            )
            service = Mock(spec=ProjectService)
            service.find_registered_game_folder.return_value = None
            view = StartView(service, first_settings)

            with patch.object(
                QFileDialog,
                "getExistingDirectory",
                return_value=directory,
            ) as select_directory:
                view.findChild(QPushButton, "btnBrowseWorkspace").click()

            select_directory.assert_called_once_with(view, "Select folder")
            self.assertEqual(view._txt_workspace.text(), directory)
            self.assertEqual(
                first_settings.value(StartView.WORKSPACE_SETTING_KEY),
                directory,
            )
            self.assertEqual(
                first_settings.allKeys(),
                [StartView.WORKSPACE_SETTING_KEY],
            )
            view._txt_game_folder.setText("game folder is never persisted")
            view.deleteLater()

            restored_settings = QSettings(
                str(settings_path),
                QSettings.Format.IniFormat,
            )
            service = Mock(spec=ProjectService)
            service.find_registered_game_folder.return_value = None
            restored_view = StartView(service, restored_settings)
            self.assertEqual(restored_view._txt_workspace.text(), directory)
            self.assertEqual(
                restored_settings.allKeys(),
                [StartView.WORKSPACE_SETTING_KEY],
            )
            restored_view.deleteLater()

    def test_start_view_persists_manually_entered_existing_workspace_on_create(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.isolated_start_settings()
            service = Mock(spec=ProjectService)
            service.find_registered_game_folder.return_value = None
            view = StartView(service, settings)
            view._txt_project_name.setText("Demo")
            view._txt_workspace.setText(directory)
            view._txt_game_folder.setText("game")
            view._txt_icon.setText("   ")

            with patch.object(view, "_run_task") as run_task:
                view._create_project()

            self.assertEqual(
                settings.value(StartView.WORKSPACE_SETTING_KEY),
                directory,
            )
            action = run_task.call_args.args[0]
            action()
            service.create_project.assert_called_once_with(
                "Demo",
                directory,
                "game",
                "mai",
                icon_source=None,
            )
            view.deleteLater()

    def test_start_view_ignores_corrupt_or_unreadable_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.ini"
            settings_path.write_bytes(b"\xff\xfe\x00invalid")
            corrupt_settings = QSettings(
                str(settings_path),
                QSettings.Format.IniFormat,
            )
            service = Mock(spec=ProjectService)
            service.find_registered_game_folder.return_value = None
            with self.assertLogs(level="WARNING") as logs:
                view = StartView(service, corrupt_settings)
            self.assertIn("settings read failed", "\n".join(logs.output))
            self.assertEqual(view._txt_workspace.text(), "")
            view.deleteLater()

        unreadable_settings = Mock()
        unreadable_settings.status.return_value = QSettings.Status.NoError
        unreadable_settings.contains.side_effect = OSError("settings denied")
        service = Mock(spec=ProjectService)
        service.find_registered_game_folder.return_value = None
        with self.assertLogs(level="ERROR") as logs:
            view = StartView(service, unreadable_settings)
        self.assertIn(
            "Unable to restore the saved Workspace folder",
            "\n".join(logs.output),
        )
        self.assertEqual(view._txt_workspace.text(), "")
        view.deleteLater()

    def test_start_view_load_project_uses_existing_workspace_as_initial_folder(self):
        with tempfile.TemporaryDirectory() as workspace:
            service = Mock(spec=ProjectService)
            service.find_registered_game_folder.return_value = None
            view = StartView(service, self.isolated_start_settings())
            view._txt_workspace.setText(workspace)

            with (
                patch.object(
                    QFileDialog,
                    "getExistingDirectory",
                    return_value=str(Path(workspace) / "project"),
                ) as select_directory,
                patch.object(view, "_run_task") as run_task,
            ):
                view._load_project()

            select_directory.assert_called_once_with(
                view,
                "Select project folder",
                workspace,
            )
            action = run_task.call_args.args[0]
            action()
            service.load_project.assert_called_once_with(
                str(Path(workspace) / "project")
            )
            view.deleteLater()

    def test_start_view_load_project_uses_two_argument_dialog_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid_workspace = str(Path(directory) / "missing")
            for workspace in ("", invalid_workspace):
                with self.subTest(workspace=workspace):
                    service = Mock(spec=ProjectService)
                    service.find_registered_game_folder.return_value = None
                    view = StartView(service, self.isolated_start_settings())
                    view._txt_workspace.setText(workspace)
                    with patch.object(
                        QFileDialog,
                        "getExistingDirectory",
                        return_value="",
                    ) as select_directory:
                        view._load_project()
                    select_directory.assert_called_once_with(
                        view,
                        "Select project folder",
                    )
                    service.load_project.assert_not_called()
                    view.deleteLater()

    def test_start_view_browses_for_ico_file_and_passes_it_to_create(self):
        service = Mock(spec=ProjectService)
        service.find_registered_game_folder.return_value = None
        view = StartView(service, self.isolated_start_settings())
        icon_path = r"C:\icons\project.ico"

        with patch.object(
            QFileDialog,
            "getOpenFileName",
            return_value=(icon_path, "Icon files (*.ico)"),
        ) as select_icon:
            view.findChild(QPushButton, "btnBrowseIcon").click()

        select_icon.assert_called_once_with(
            view,
            "Select icon file",
            "",
            "Icon files (*.ico)",
        )
        self.assertEqual(view._txt_icon.text(), icon_path)
        view._txt_project_name.setText("Icon Project")
        view._txt_workspace.setText("workspace")
        view._txt_game_folder.setText("game")
        with patch.object(view, "_run_task") as run_task:
            view._create_project()
        run_task.call_args.args[0]()
        service.create_project.assert_called_once_with(
            "Icon Project",
            "workspace",
            "game",
            "mai",
            icon_source=icon_path,
        )
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
        service.find_registered_game_folder.return_value = None
        service.validate_version_prefix.return_value = "not-the-widget-value"
        view = StartView(service, self.isolated_start_settings())
        view._txt_project_name.setText("Demo")
        view._txt_workspace.setText("workspace")
        view._txt_game_folder.setText("game")
        view._txt_icon.setText(r"C:\icons\demo.ico")
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
            icon_source=r"C:\icons\demo.ico",
        )
        view.deleteLater()

    def test_start_view_rejects_empty_prefix_before_starting_task(self):
        for value in ("", "   "):
            with self.subTest(prefix=value):
                service = Mock(spec=ProjectService)
                service.find_registered_game_folder.return_value = None
                view = StartView(service, self.isolated_start_settings())
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

    def test_project_view_menu_actions_are_canonical_without_toolbar(self):
        with tempfile.TemporaryDirectory() as directory:
            project_service = Mock(spec=ProjectService)
            project_service.list_visible_resources.return_value = []
            empty_manifest = ProjectManifest(
                "Empty",
                directory,
                version_prefix="mai",
            )
            empty_view = ProjectView(
                empty_manifest,
                project_service,
                Mock(spec=CardService),
            )
            self.assertFalse(empty_view._run_action.isEnabled())
            self.assertEqual(
                empty_view._run_action.toolTip(),
                "This project does not contain an executable.",
            )
            self.assertFalse(empty_view._save_current_file_action.isEnabled())
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
            view = ProjectView(manifest, project_service, Mock(spec=CardService))
            self.assertTrue(view._run_action.isEnabled())

            self.assertEqual(
                [action.text().replace("&", "") for action in view.menuBar().actions()],
                ["File", "Tools", "Build"],
            )
            self.assertEqual(len(view.menuBar().actions()), 3)
            self.assertEqual(
                [
                    None if action.isSeparator() else action.text()
                    for action in view._file_menu.actions()
                ],
                ["Save Current File", "Export Files…", None, "Close Project"],
            )
            self.assertEqual(
                [action.text() for action in view._tools_menu.actions()],
                ["Card List"],
            )
            self.assertEqual(
                [action.text() for action in view._build_menu.actions()],
                ["Build", "Run"],
            )
            self.assertEqual(view.findChildren(QToolBar), [])
            self.assertIs(view._file_menu.actions()[0], view._save_current_file_action)
            self.assertIs(view._tools_menu.actions()[0], view._card_list_action)
            self.assertIs(view.findChild(QMenu, "menuFile"), view._file_menu)
            self.assertIs(view.findChild(QMenu, "menuTools"), view._tools_menu)
            self.assertIs(view.findChild(QMenu, "menuBuild"), view._build_menu)
            expected_names = {
                "actionSaveCurrentFile": view._save_current_file_action,
                "actionExportFiles": view._export_files_action,
                "actionCloseProject": view._close_project_action,
                "actionCardList": view._card_list_action,
                "actionBuild": view._build_action,
                "actionRun": view._run_action,
            }
            for object_name, action in expected_names.items():
                self.assertIs(view.findChild(QAction, object_name), action)
                self.assertIs(action.parent(), view)
            self.assertTrue(
                all(
                    action.statusTip()
                    for action in (
                        view._save_current_file_action,
                        view._export_files_action,
                        view._close_project_action,
                        view._card_list_action,
                        view._build_action,
                        view._run_action,
                    )
                )
            )
            self.assertEqual(
                view._run_action.toolTip(),
                view._run_action.statusTip(),
            )
            for object_name in (
                "btnCardList",
                "btnSaveFile",
                "btnExportFiles",
                "btnBuild",
                "btnBuildAndRun",
                "btnCloseProject",
            ):
                self.assertIsNone(view.findChild(QPushButton, object_name))

            future_tool = QAction("Future Tool", view)
            view._tools_menu.addAction(future_tool)
            self.assertEqual(
                view._tools_menu.actions(),
                [view._card_list_action, future_tool],
            )
            view.deleteLater()

    def test_project_content_layout_expands_splitter_and_keeps_progress_compact(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            view = ProjectView(
                ProjectManifest("Layout", directory, version_prefix="mai"),
                service,
                Mock(spec=CardService),
            )
            content_layout = view.centralWidget().layout()
            self.assertEqual(content_layout.stretch(0), 1)
            self.assertEqual(content_layout.stretch(1), 0)
            self.assertEqual(
                view._splitter.sizePolicy().verticalPolicy(),
                QSizePolicy.Policy.Expanding,
            )
            self.assertEqual(
                view._pgb_progress.sizePolicy().verticalPolicy(),
                QSizePolicy.Policy.Fixed,
            )
            self.assertEqual(view._pgb_progress.maximumHeight(), 24)

            view.resize(1100, 700)
            view.show()
            self.application.processEvents()
            compact_height = view._splitter.height()
            self.assertEqual(view._tree.height(), view._editor_host.height())

            view.resize(1100, 900)
            self.application.processEvents()
            self.assertGreaterEqual(view._splitter.height(), compact_height + 190)

            view._pgb_progress.show()
            self.application.processEvents()
            self.assertLessEqual(view._pgb_progress.height(), 24)
            self.assertGreater(view._splitter.height(), view._pgb_progress.height())
            view.close()
            view.deleteLater()

    def test_project_action_shortcuts_are_window_scoped_and_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            view = ProjectView(
                ProjectManifest(
                    "Shortcuts",
                    directory,
                    version_prefix="mai",
                    executable=ExecutableManifest(
                        source_name="joey_pc.exe",
                        relative_path="mai/mai_pc.exe",
                    ),
                ),
                service,
                Mock(spec=CardService),
            )
            expected = {
                view._save_current_file_action: "Ctrl+S",
                view._export_files_action: "Ctrl+Shift+E",
                view._close_project_action: "Ctrl+W",
                view._build_action: "Ctrl+Shift+B",
                view._run_action: "F5",
            }
            self.assertTrue(view._card_list_action.shortcut().isEmpty())
            for action, shortcut in expected.items():
                self.assertEqual(action.shortcut(), QKeySequence(shortcut))
                self.assertEqual(
                    action.shortcutContext(),
                    Qt.ShortcutContext.WindowShortcut,
                )
                self.assertNotIn("\t", action.text())
            portable = [
                action.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
                for action in expected
            ]
            self.assertEqual(len(portable), len(set(portable)))
            view.deleteLater()

    def test_project_save_action_requires_an_editor_and_dispatches_once(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            view = ProjectView(
                ProjectManifest("Save action", directory, version_prefix="mai"),
                service,
                Mock(spec=CardService),
            )
            self.assertFalse(view._save_current_file_action.isEnabled())
            editor = Mock()
            editor.is_project_mutation_in_progress = False
            view._current_editor = editor
            view._refresh_artifact_action_states()
            self.assertTrue(view._save_current_file_action.isEnabled())
            with patch.object(QMessageBox, "information"):
                view._save_current_file_action.trigger()
            editor.save.assert_called_once_with()
            view.deleteLater()

    def test_image_editor_exposes_retained_replace_mutation_state(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.read_project_binary.return_value = b""
            manifest = ProjectManifest(
                "Image editor",
                directory,
                version_prefix="mai",
            )
            record = ProjectFileRecord(
                source_file="Data.dat",
                relative_path="card/example.bmp",
                workspace_path="data/card/example.bmp",
                file_kind="image",
                storage_format="binary",
            )
            editor = ImageEditor(service, manifest, record)
            editor._thread_pool = Mock()
            mutation_states = []
            editor.project_mutation_state_changed.connect(mutation_states.append)

            self.assertFalse(editor.is_project_mutation_in_progress)
            with patch.object(
                QFileDialog,
                "getOpenFileName",
                return_value=(str(Path(directory) / "replacement.bmp"), ""),
            ):
                editor.replace_button.click()

            runner = editor._thread_pool.start.call_args.args[0]
            self.assertTrue(editor.is_project_mutation_in_progress)
            self.assertFalse(editor.replace_button.isEnabled())
            runner.signals.finished.emit()
            self.assertFalse(editor.is_project_mutation_in_progress)
            self.assertTrue(editor.replace_button.isEnabled())
            self.assertEqual(mutation_states, [True, False])
            editor.deleteLater()

    def test_audio_editor_blocks_other_project_mutations_while_choosing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.project_resource_path.return_value = Path(directory) / "voice.wav"
            manifest = ProjectManifest(
                "Audio editor",
                directory,
                version_prefix="mai",
            )
            record = ProjectFileRecord(
                source_file="Voice.dat",
                relative_path="voice/example.wav",
                workspace_path="voice/example.wav",
                file_kind="audio",
                storage_format="binary",
            )
            editor = AudioEditor(service, manifest, record)
            mutation_states = []
            editor.project_mutation_state_changed.connect(mutation_states.append)

            def cancel_chooser(*_args):
                self.assertTrue(editor.is_project_mutation_in_progress)
                self.assertEqual(mutation_states, [True])
                return "", ""

            with patch.object(
                QFileDialog,
                "getOpenFileName",
                side_effect=cancel_chooser,
            ):
                editor.replace_button.click()

            service.replace_project_file.assert_not_called()
            self.assertFalse(editor.is_project_mutation_in_progress)
            self.assertEqual(mutation_states, [True, False])
            editor.deleteLater()

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
            run_action = view._run_action

            with (
                patch.object(QMessageBox, "information") as information,
                patch.object(QMessageBox, "critical") as critical,
            ):
                run_action.trigger()
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

    def test_project_view_export_runs_once_off_ui_thread_and_restores_state(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "export"
            destination.mkdir()
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            started = threading.Event()
            release = threading.Event()
            worker_threads = []

            def export(_manifest, selected_destination):
                worker_threads.append(QThread.currentThread())
                started.set()
                self.assertTrue(release.wait(5))
                return Path(selected_destination)

            service.export_project_files.side_effect = export
            manifest = ProjectManifest(
                "Export success",
                directory,
                version_prefix="mai",
                executable=ExecutableManifest(
                    source_name="joey_pc.exe",
                    relative_path="mai/mai_pc.exe",
                ),
            )
            view = ProjectView(manifest, service, CardService())
            view.show()
            export_action = view._export_files_action
            build_action = view._build_action
            run_action = view._run_action
            save_action = view._save_current_file_action
            card_list_action = view._card_list_action
            card_list = Mock()
            card_list.is_project_save_in_progress = False
            view._card_list_view = card_list

            with (
                patch.object(
                    QFileDialog,
                    "getExistingDirectory",
                    return_value=str(destination),
                ) as chooser,
                patch.object(QMessageBox, "information") as information,
                patch.object(QMessageBox, "critical") as critical,
            ):
                export_action.trigger()
                self.assertTrue(self.wait_until(started.is_set))
                export_action.trigger()
                self.assertTrue(view._export_in_progress)
                self.assertFalse(export_action.isEnabled())
                self.assertFalse(build_action.isEnabled())
                self.assertFalse(run_action.isEnabled())
                self.assertFalse(save_action.isEnabled())
                self.assertFalse(card_list_action.isEnabled())
                self.assertFalse(view._tree.isEnabled())
                self.assertFalse(view._editor_host.isEnabled())
                card_list.setEnabled.assert_called_with(False)
                self.assertTrue(view._pgb_progress.isVisible())
                view._close_project_action.trigger()
                self.application.processEvents()
                self.assertTrue(view.isVisible())
                release.set()
                self.assertTrue(self.wait_until(lambda: not view._export_in_progress))

            chooser.assert_called_once_with(view, "Select export folder")
            service.export_project_files.assert_called_once_with(
                manifest,
                str(destination),
            )
            self.assertNotEqual(worker_threads[0], self.application.thread())
            information.assert_any_call(
                view,
                "Export in Progress",
                "Wait for file export to finish before closing the project.",
            )
            information.assert_any_call(
                view,
                "Export Files",
                f"Reconstructed game files were exported to:\n{destination}",
            )
            self.assertEqual(information.call_count, 2)
            critical.assert_not_called()
            self.assertTrue(export_action.isEnabled())
            self.assertTrue(build_action.isEnabled())
            self.assertTrue(run_action.isEnabled())
            self.assertFalse(save_action.isEnabled())
            self.assertTrue(card_list_action.isEnabled())
            self.assertTrue(view._tree.isEnabled())
            self.assertTrue(view._editor_host.isEnabled())
            card_list.setEnabled.assert_called_with(True)
            self.assertFalse(view._pgb_progress.isVisible())
            self.assertEqual(view._active_runners, {})
            view.close()
            view.deleteLater()

    def test_project_view_rejects_artifacts_during_background_project_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            manifest = ProjectManifest(
                "Busy project",
                directory,
                version_prefix="mai",
            )
            view = ProjectView(manifest, service, CardService())
            card_list = Mock()
            card_list.is_project_save_in_progress = True
            view._card_list_view = card_list

            with (
                patch.object(QFileDialog, "getExistingDirectory") as chooser,
                patch.object(QMessageBox, "information") as information,
            ):
                view._export_files()
                chooser.assert_not_called()
                information.assert_called_once_with(
                    view,
                    "Export Files",
                    "Wait for the current project update to finish before continuing.",
                )

            card_list.is_project_save_in_progress = False
            editor = Mock()
            editor.is_project_mutation_in_progress = True
            view._current_editor = editor
            with patch.object(QMessageBox, "information") as information:
                view._pack_project()
                information.assert_called_once_with(
                    view,
                    "Pack Project",
                    "Wait for the current project update to finish before continuing.",
                )

            service.export_project_files.assert_not_called()
            service.pack_project.assert_not_called()
            self.assertFalse(view._export_in_progress)
            self.assertFalse(view._pack_in_progress)
            view._card_list_view = None
            view._current_editor = None
            view.deleteLater()

    def test_image_replace_blocks_navigation_artifacts_and_project_close(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            service.read_project_binary.return_value = b""
            manifest = ProjectManifest(
                "Retained replacement",
                directory,
                version_prefix="mai",
            )
            current_record = ProjectFileRecord(
                source_file="Data.dat",
                relative_path="card/current.bmp",
                workspace_path="data/card/current.bmp",
                file_kind="image",
                storage_format="binary",
            )
            next_record = ProjectFileRecord(
                source_file="Data.dat",
                relative_path="card/next.bmp",
                workspace_path="data/card/next.bmp",
                file_kind="image",
                storage_format="binary",
            )
            view = ProjectView(manifest, service, CardService())
            view.show()
            editor = ImageEditor(service, manifest, current_record)
            editor._thread_pool = Mock()
            view._set_editor(editor)
            card_list = Mock()
            card_list.is_project_save_in_progress = False
            view._card_list_view = card_list
            item = QTreeWidgetItem(view._tree, ["next.bmp"])
            item.setData(0, Qt.UserRole, "next")
            view._resource_items["next"] = next_record

            def choose_replacement(*_args):
                card_list.set_external_project_mutation_blocked.assert_called_with(True)
                self.assertFalse(view._build_action.isEnabled())
                return str(Path(directory) / "replacement.bmp"), ""

            with patch.object(
                QFileDialog,
                "getOpenFileName",
                side_effect=choose_replacement,
            ):
                editor.replace_button.click()
            runner = editor._thread_pool.start.call_args.args[0]

            with (
                patch("yugioh_editor.views.project_view.create_editor") as create,
                patch.object(QFileDialog, "getExistingDirectory") as chooser,
                patch.object(QMessageBox, "information") as information,
            ):
                view._open_tree_item(item)
                view._pack_project()
                view._export_files()
                view.close()
                self.application.processEvents()

            self.assertIs(view._current_editor, editor)
            create.assert_not_called()
            chooser.assert_not_called()
            service.pack_project.assert_not_called()
            service.export_project_files.assert_not_called()
            self.assertEqual(information.call_count, 3)
            information.assert_any_call(
                view,
                "Project Update in Progress",
                "Wait for the current project update to finish before closing.",
            )
            self.assertTrue(view.isVisible())

            runner.signals.finished.emit()
            card_list.set_external_project_mutation_blocked.assert_called_with(False)
            view._card_list_view = None
            view.close()
            view.deleteLater()

    def test_card_list_save_state_blocks_project_editor_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            service.read_project_binary.return_value = b""
            manifest = ProjectManifest(
                "Card save serialization",
                directory,
                version_prefix="mai",
            )
            record = ProjectFileRecord(
                source_file="Data.dat",
                relative_path="card/current.bmp",
                workspace_path="data/card/current.bmp",
                file_kind="image",
                storage_format="binary",
            )
            view = ProjectView(manifest, service, CardService())
            editor = ImageEditor(service, manifest, record)
            editor._thread_pool = Mock()
            view._set_editor(editor)
            card_service = Mock(spec=CardService)
            with patch.object(CardListView, "_reload"):
                card_list = CardListView(manifest, card_service, view)
            view._card_list_view = card_list
            card_list.project_save_state_changed.connect(
                view._refresh_artifact_action_states
            )

            card_list._save_pending = True
            card_list._notify_project_save_state()

            self.assertFalse(view._editor_host.isEnabled())
            self.assertFalse(view._tree.isEnabled())
            self.assertFalse(view._build_action.isEnabled())
            with patch.object(QFileDialog, "getOpenFileName") as chooser:
                editor.replace_button.click()
            chooser.assert_not_called()

            card_list._save_pending = False
            card_list._notify_project_save_state()
            self.assertTrue(view._editor_host.isEnabled())
            self.assertTrue(view._tree.isEnabled())
            self.assertTrue(view._build_action.isEnabled())
            card_list._closing = True
            card_list.reject()
            view._card_list_view = None
            card_list.deleteLater()
            view.deleteLater()

    def test_project_view_serializes_run_launch_with_pack_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            manifest = ProjectManifest(
                "Run serialization",
                directory,
                version_prefix="mai",
                executable=ExecutableManifest(
                    source_name="joey_pc.exe",
                    relative_path="mai/mai_pc.exe",
                ),
            )
            view = ProjectView(manifest, service, CardService())
            run_action = view._run_action
            export_action = view._export_files_action
            build_action = view._build_action

            with patch.object(view, "_run_task") as run_task:
                view._run_game()
                view._run_game()

            self.assertEqual(run_task.call_count, 1)
            self.assertTrue(view._run_in_progress)
            self.assertFalse(run_action.isEnabled())
            self.assertFalse(export_action.isEnabled())
            self.assertFalse(build_action.isEnabled())

            view._on_run_finished()
            self.assertFalse(view._run_in_progress)
            self.assertTrue(run_action.isEnabled())
            self.assertTrue(export_action.isEnabled())
            self.assertTrue(build_action.isEnabled())
            view.deleteLater()

    def test_project_view_export_cancel_and_failure_leave_ui_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Mock(spec=ProjectService)
            service.list_visible_resources.return_value = []
            manifest = ProjectManifest(
                "Export failure",
                directory,
                version_prefix="mai",
            )
            view = ProjectView(manifest, service, CardService())
            view.show()
            export_action = view._export_files_action
            build_action = view._build_action

            with patch.object(
                QFileDialog,
                "getExistingDirectory",
                return_value="",
            ):
                export_action.trigger()
            service.export_project_files.assert_not_called()
            self.assertFalse(view._export_in_progress)

            service.export_project_files.side_effect = ValueError(
                "The export destination is invalid."
            )
            with (
                patch.object(
                    QFileDialog,
                    "getExistingDirectory",
                    return_value=directory,
                ),
                patch.object(QMessageBox, "critical") as critical,
                self.assertLogs(level="ERROR"),
            ):
                export_action.trigger()
                self.assertTrue(self.wait_until(lambda: not view._export_in_progress))

            service.export_project_files.assert_called_once_with(manifest, directory)
            critical.assert_called_once_with(
                view,
                "Export Files Failed",
                "The export destination is invalid.",
            )
            self.assertTrue(export_action.isEnabled())
            self.assertTrue(build_action.isEnabled())
            self.assertFalse(view._pgb_progress.isVisible())
            self.assertEqual(view._active_runners, {})
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
            run_action = view._run_action

            with (
                self.assertLogs(level="ERROR"),
                patch.object(QMessageBox, "information") as information,
                patch.object(QMessageBox, "critical") as critical,
            ):
                run_action.trigger()
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
            pack_action = view._build_action
            callback_threads = []

            def show_success(*_args):
                callback_threads.append(QThread.currentThread())

            with (
                patch.object(QMessageBox, "information", side_effect=show_success),
                patch.object(QMessageBox, "critical") as critical,
            ):
                pack_action.trigger()
                self.assertFalse(pack_action.isEnabled())
                self.assertTrue(view._pgb_progress.isVisible())
                self.assertTrue(self.wait_until(lambda: not view._pack_in_progress))

            service.pack_project.assert_called_once_with(manifest)
            self.assertNotEqual(worker_threads[0], self.application.thread())
            self.assertEqual(callback_threads[0], self.application.thread())
            self.assertTrue(pack_action.isEnabled())
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
            pack_action = view._build_action

            with patch.object(view, "_run_task") as run_task:
                pack_action.trigger()
                pack_action.trigger()

            self.assertEqual(run_task.call_count, 1)
            self.assertFalse(pack_action.isEnabled())
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
            pack_action = view._build_action

            with (
                self.assertLogs(level="ERROR") as logs,
                patch.object(QMessageBox, "critical") as critical,
            ):
                pack_action.trigger()
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
            self.assertTrue(pack_action.isEnabled())
            self.assertFalse(view._pgb_progress.isVisible())
            self.assertEqual(view._active_runners, {})
            self.assertTrue(view.isVisible())

            service.pack_project.side_effect = None
            service.pack_project.return_value = Path(directory) / "bin"
            with patch.object(QMessageBox, "information"):
                pack_action.trigger()
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
            pack_action = view._build_action

            with patch.object(QMessageBox, "information") as information:
                pack_action.trigger()
                self.assertTrue(self.wait_until(started.is_set))
                view._close_project_action.trigger()
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
