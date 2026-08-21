from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, QThreadPool, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
)

from yugioh_editor.common.constants import APPLICATION_NAME, ui_path
from yugioh_editor.models.entities import ProjectManifest
from yugioh_editor.services.project_service import ProjectService
from yugioh_editor.views.ui_loader import load_ui
from yugioh_editor.workers.task_runner import TaskRunner


class StartView(QMainWindow):
    project_opened = Signal(object)

    SETTINGS_ORGANIZATION = "YugiohEditor"
    SETTINGS_APPLICATION = "YugiohEditor"
    WORKSPACE_SETTING_KEY = "workspace/last_folder"

    def __init__(
        self,
        project_service: ProjectService,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__()
        self._service = project_service
        self._thread_pool = QThreadPool.globalInstance()
        self._settings = self._resolve_settings(settings)
        self._game_folder_detection_attempted = False
        self.setWindowTitle(APPLICATION_NAME)
        root = load_ui(ui_path("start_window.ui"), self)
        self.setCentralWidget(root)
        self.setFixedSize(root.minimumSize())

        self._txt_project_name = self.findChild(QLineEdit, "txtProjectName")
        self._version_prefix = self.findChild(
            QLineEdit,
            "versionPrefixLineEdit",
        )
        self._txt_workspace = self.findChild(QLineEdit, "txtWorkspace")
        self._txt_game_folder = self.findChild(QLineEdit, "txtGameFolder")
        self._txt_icon = self.findChild(QLineEdit, "txtIcon")
        self._pgb_progress = self.findChild(QProgressBar, "pgbProgress")

        self.findChild(QPushButton, "btnBrowseWorkspace").clicked.connect(
            self._browse_workspace
        )
        self.findChild(QPushButton, "btnBrowseGame").clicked.connect(
            lambda: self._browse(self._txt_game_folder)
        )
        self.findChild(QPushButton, "btnBrowseIcon").clicked.connect(self._browse_icon)
        self.findChild(QPushButton, "btnCreateProject").clicked.connect(
            self._create_project
        )
        self.findChild(QPushButton, "btnLoadProject").clicked.connect(
            self._load_project
        )

        self._restore_workspace()
        self._detect_game_folder()

    def _create_project(self) -> None:
        version_prefix = self._version_prefix.text().strip()
        if not version_prefix:
            QMessageBox.warning(
                self,
                "Invalid Version Prefix",
                "Version prefix is required.",
            )
            self._version_prefix.setFocus()
            return
        try:
            self._service.validate_version_prefix(version_prefix)
        except Exception as error:
            QMessageBox.warning(self, "Invalid Version Prefix", str(error))
            self._version_prefix.setFocus()
            return
        self._persist_manual_workspace()
        icon_source = self._txt_icon.text().strip() or None
        self._run_task(
            lambda: self._service.create_project(
                self._txt_project_name.text(),
                self._txt_workspace.text(),
                self._txt_game_folder.text(),
                version_prefix,
                icon_source=icon_source,
            )
        )

    def _load_project(self) -> None:
        workspace = self._txt_workspace.text().strip()
        try:
            workspace_exists = bool(workspace) and Path(workspace).expanduser().is_dir()
        except (OSError, ValueError):
            workspace_exists = False
        if workspace_exists:
            directory = QFileDialog.getExistingDirectory(
                self,
                "Select project folder",
                workspace,
            )
        else:
            directory = QFileDialog.getExistingDirectory(
                self,
                "Select project folder",
            )
        if directory:
            self._run_task(lambda: self._service.load_project(directory))

    def _run_task(self, action) -> None:
        self._pgb_progress.show()
        runner = TaskRunner(action)
        runner.signals.succeeded.connect(self._on_project_opened)
        runner.signals.failed.connect(
            lambda error: QMessageBox.critical(
                self,
                "Operation Failed",
                str(error),
            )
        )
        runner.signals.finished.connect(self._pgb_progress.hide)
        self._thread_pool.start(runner)

    def _on_project_opened(self, manifest: ProjectManifest) -> None:
        self.project_opened.emit(manifest)

    def _browse(self, target: QLineEdit) -> str | None:
        directory = QFileDialog.getExistingDirectory(self, "Select folder")
        if directory:
            target.setText(directory)
            return directory
        return None

    def _browse_workspace(self) -> None:
        directory = self._browse(self._txt_workspace)
        if directory is not None:
            self._persist_workspace(directory)

    def _browse_icon(self) -> None:
        icon_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select icon file",
            "",
            "Icon files (*.ico)",
        )
        if icon_path:
            self._txt_icon.setText(icon_path)

    @classmethod
    def _create_default_settings(cls) -> QSettings:
        settings = QSettings(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            cls.SETTINGS_ORGANIZATION,
            cls.SETTINGS_APPLICATION,
        )
        settings.setFallbacksEnabled(False)
        return settings

    @classmethod
    def _resolve_settings(cls, settings: QSettings | None) -> QSettings | None:
        if settings is not None:
            return settings
        try:
            return cls._create_default_settings()
        except Exception:
            logging.exception("Unable to initialize application settings.")
            return None

    def _restore_workspace(self) -> None:
        if self._settings is None:
            return
        try:
            # QSettings parses INI input lazily; enumerate once so FormatError is
            # observable before treating a corrupt file as a missing preference.
            self._settings.allKeys()
            if self._log_settings_status("read"):
                return
            if not self._settings.contains(self.WORKSPACE_SETTING_KEY):
                logging.debug("No saved Workspace folder was found.")
                return
            workspace = self._settings.value(self.WORKSPACE_SETTING_KEY)
            if self._log_settings_status("read"):
                return
        except Exception:
            logging.exception("Unable to restore the saved Workspace folder.")
            return
        if not isinstance(workspace, str):
            logging.warning("Ignoring malformed saved Workspace folder value.")
            return
        self._txt_workspace.setText(workspace)

    def _persist_manual_workspace(self) -> None:
        workspace = self._txt_workspace.text().strip()
        if not workspace:
            return
        try:
            exists = Path(workspace).expanduser().is_dir()
        except (OSError, ValueError):
            exists = False
        if exists:
            self._persist_workspace(workspace)

    def _persist_workspace(self, workspace: str) -> None:
        if self._settings is None:
            return
        try:
            self._settings.setValue(self.WORKSPACE_SETTING_KEY, workspace)
            self._settings.sync()
            self._log_settings_status("write")
        except Exception:
            logging.exception("Unable to save the Workspace folder preference.")

    def _log_settings_status(self, operation: str) -> bool:
        if self._settings is None:
            return False
        status = self._settings.status()
        if status == QSettings.Status.NoError:
            return False
        logging.warning(
            "Application settings %s failed with status %s.",
            operation,
            status.name,
        )
        return True

    def _detect_game_folder(self) -> None:
        if self._game_folder_detection_attempted:
            return
        self._game_folder_detection_attempted = True
        if self._txt_game_folder.text().strip():
            return
        try:
            registered_folder = self._service.find_registered_game_folder()
        except Exception:
            logging.exception("Unable to detect the registered Game folder.")
            return
        if registered_folder is None:
            return
        if not isinstance(registered_folder, str) or not registered_folder.strip():
            logging.warning("Ignoring malformed registered Game folder value.")
            return
        self._txt_game_folder.setText(registered_folder.strip())
