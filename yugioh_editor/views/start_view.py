from __future__ import annotations

from PySide6.QtCore import QThreadPool, Signal
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

    def __init__(self, project_service: ProjectService) -> None:
        super().__init__()
        self._service = project_service
        self._thread_pool = QThreadPool.globalInstance()
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
        self._pgb_progress = self.findChild(QProgressBar, "pgbProgress")

        self.findChild(QPushButton, "btnBrowseWorkspace").clicked.connect(
            lambda: self._browse(self._txt_workspace)
        )
        self.findChild(QPushButton, "btnBrowseGame").clicked.connect(
            lambda: self._browse(self._txt_game_folder)
        )
        self.findChild(QPushButton, "btnCreateProject").clicked.connect(
            self._create_project
        )
        self.findChild(QPushButton, "btnLoadProject").clicked.connect(
            self._load_project
        )

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
        self._run_task(
            lambda: self._service.create_project(
                self._txt_project_name.text(),
                self._txt_workspace.text(),
                self._txt_game_folder.text(),
                version_prefix,
            )
        )

    def _load_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select project folder")
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

    def _browse(self, target: QLineEdit) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select folder")
        if directory:
            target.setText(directory)
