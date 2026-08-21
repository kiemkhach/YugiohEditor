from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from yugioh_editor.common.constants import APPLICATION_NAME, ui_path
from yugioh_editor.models.entities import ProjectFileRecord, ProjectManifest
from yugioh_editor.services.card_service import CardService
from yugioh_editor.services.project_service import ProjectService
from yugioh_editor.views.card_list_view import CardListView
from yugioh_editor.views.editors import FileEditor, create_editor
from yugioh_editor.views.ui_loader import load_ui
from yugioh_editor.workers.task_runner import TaskError, TaskRunner, TaskSignals


class ProjectView(QMainWindow):
    project_closed = Signal()

    def __init__(
        self,
        manifest: ProjectManifest,
        project_service: ProjectService,
        card_service: CardService,
    ) -> None:
        super().__init__()
        self._manifest = manifest
        self._project_service = project_service
        self._card_service = card_service
        self._thread_pool = QThreadPool.globalInstance()
        self._current_editor: FileEditor | None = None
        self._card_list_view: CardListView | None = None
        self._active_runners: dict[TaskSignals, TaskRunner] = {}
        self._pack_in_progress = False
        self._export_in_progress = False
        self._run_in_progress = False

        self.setWindowTitle(f"{APPLICATION_NAME} - {manifest.name}")
        self.setCentralWidget(load_ui(ui_path("project_window.ui"), self))
        self.resize(1280, 800)

        self._tree = self.findChild(QTreeWidget, "treeFiles")
        self._splitter = self.findChild(QSplitter, "splitter")
        self._editor_host = self.findChild(QWidget, "editorHost")
        self._editor_layout = self._editor_host.layout()
        self._pgb_progress = self.findChild(QProgressBar, "pgbProgress")
        self._export_button = self.findChild(QPushButton, "btnExportFiles")
        self._build_button = self.findChild(QPushButton, "btnBuild")
        self._build_and_run_button = self.findChild(QPushButton, "btnBuildAndRun")
        self._save_file_button = self.findChild(QPushButton, "btnSaveFile")
        self._card_list_button = self.findChild(QPushButton, "btnCardList")
        self._splitter.setSizes([280, 1000])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)

        self._tree.itemClicked.connect(self._open_tree_item)
        self._save_file_button.clicked.connect(self._save_current_file)
        self._card_list_button.clicked.connect(self._open_card_list)
        self._export_button.clicked.connect(self._export_files)
        self._build_button.clicked.connect(self._pack_project)
        self._build_and_run_button.clicked.connect(self._run_game)
        self.findChild(QPushButton, "btnCloseProject").clicked.connect(self.close)
        self._configure_build_and_run_button()
        self._populate_tree()

    def closeEvent(self, event) -> None:
        if self._pack_in_progress or self._export_in_progress:
            if self._pack_in_progress:
                status = "Packing is still in progress. Wait for it to finish."
                title = "Packing in Progress"
                message = "Wait for packing to finish before closing the project."
            else:
                status = "File export is still in progress. Wait for it to finish."
                title = "Export in Progress"
                message = "Wait for file export to finish before closing the project."
            event.ignore()
            self.statusBar().showMessage(status)
            QMessageBox.information(self, title, message)
            return
        if self._project_mutation_in_progress():
            event.ignore()
            self.statusBar().showMessage(
                "A project update is still in progress. Wait for it to finish."
            )
            QMessageBox.information(
                self,
                "Project Update in Progress",
                "Wait for the current project update to finish before closing.",
            )
            return
        self.project_closed.emit()
        super().closeEvent(event)

    def _populate_tree(self) -> None:
        self._tree.clear()
        folder_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
        self._resource_items: dict[str, ProjectFileRecord] = {}
        resources = self._project_service.list_visible_resources(self._manifest)
        for position, record in enumerate(resources):
            if not record.workspace_path:
                continue
            normalized_path = record.relative_path.replace("\\", "/").casefold()
            resource_id = (
                f"{record.source_file.casefold()}:{normalized_path}:{position}"
            )
            self._resource_items[resource_id] = record
            root_name, parts = self._project_service.tree_resource_parts(
                self._manifest,
                record,
            )
            root_key = (root_name.casefold(),)
            root = folder_nodes.get(root_key)
            if root is None:
                root = QTreeWidgetItem(self._tree, [root_name])
                folder_nodes[root_key] = root
            parent = root
            accumulated = root_key
            for part in parts[:-1]:
                accumulated += (part.casefold(),)
                node = folder_nodes.get(accumulated)
                if node is None:
                    node = QTreeWidgetItem(parent, [part])
                    folder_nodes[accumulated] = node
                parent = node
            file_name = parts[-1] if parts else record.relative_path
            file_item = QTreeWidgetItem(parent, [file_name])
            file_item.setData(0, Qt.UserRole, resource_id)
        self._tree.expandToDepth(1)

    def _configure_build_and_run_button(self) -> None:
        has_executable = self._manifest.executable is not None
        self._build_and_run_button.setEnabled(has_executable)
        self._build_and_run_button.setToolTip(
            "" if has_executable else "This project does not contain an executable."
        )

    def _open_tree_item(self, item: QTreeWidgetItem) -> None:
        if (
            self._artifact_generation_in_progress()
            or self._project_mutation_in_progress()
        ):
            self.statusBar().showMessage(
                "Wait for the current project operation before changing files."
            )
            return
        resource_id = item.data(0, Qt.UserRole)
        if resource_id is None:
            return
        record = self._resource_items.get(str(resource_id))
        if record is None:
            return
        self._set_editor(
            create_editor(
                self._project_service,
                self._manifest,
                record,
            )
        )

    def _set_editor(self, editor: FileEditor) -> None:
        if self._current_editor is not None:
            self._editor_layout.removeWidget(self._current_editor)
            self._current_editor.deleteLater()
        self._current_editor = editor
        editor.project_mutation_state_changed.connect(
            self._refresh_artifact_action_states
        )
        editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._editor_layout.addWidget(editor, 1)
        editor_layout = editor.layout()
        if editor_layout is not None:
            margins = editor_layout.contentsMargins()
            editor_layout.setContentsMargins(
                margins.left(),
                0,
                margins.right(),
                0,
            )
        self._refresh_artifact_action_states()

    def _save_current_file(self) -> None:
        if (
            self._artifact_generation_in_progress()
            or self._project_mutation_in_progress()
        ):
            return
        if self._current_editor is None:
            QMessageBox.information(self, "Save File", "Select a project file first.")
            return
        try:
            self._current_editor.save()
            QMessageBox.information(
                self, "Save File", "The file was saved successfully."
            )
        except Exception as error:
            logging.exception("Saving the current project resource failed.")
            QMessageBox.critical(self, "Save File Error", str(error))

    def _open_card_list(self) -> None:
        if (
            self._artifact_generation_in_progress()
            or self._project_mutation_in_progress()
        ):
            return
        if self._card_list_view is not None:
            self._card_list_view.showMaximized()
            self._card_list_view.raise_()
            self._card_list_view.activateWindow()
            return
        dialog = CardListView(self._manifest, self._card_service, self)
        self._card_list_view = dialog
        dialog.dirty_changed.connect(self._card_list_dirty_changed)
        dialog.project_save_state_changed.connect(self._refresh_artifact_action_states)
        dialog.finished.connect(self._on_card_list_closed)
        dialog.showMaximized()

    def _on_card_list_closed(self, _result: int) -> None:
        self._card_list_dirty_changed(False)
        self._populate_tree()
        dialog = self.sender()
        if dialog is self._card_list_view:
            self._card_list_view = None
        if isinstance(dialog, CardListView):
            dialog.deleteLater()
        self._refresh_artifact_action_states()

    def _card_list_dirty_changed(self, dirty: bool) -> None:
        suffix = " *" if dirty else ""
        self.setWindowTitle(f"{APPLICATION_NAME} - {self._manifest.name}{suffix}")

    def _pack_project(self) -> None:
        if (
            self._artifact_generation_in_progress()
            or self._run_in_progress
            or not self._workspace_is_stable_for_artifact("Pack Project")
        ):
            return
        self._pack_in_progress = True
        self._refresh_artifact_action_states()
        self.statusBar().showMessage("Packing project...")
        self._run_task(
            lambda: self._project_service.pack_project(self._manifest),
            self._on_pack_succeeded,
            failure=self._on_pack_failed,
            finished=self._on_pack_finished,
        )

    def _on_pack_succeeded(self, path) -> None:
        self.statusBar().showMessage("Packing completed successfully.", 5000)
        QMessageBox.information(
            self,
            "Pack Project",
            f"Packed game files were written to:\n{path}",
        )

    def _on_pack_failed(self, error: TaskError) -> None:
        self.statusBar().showMessage("Packing failed.", 5000)
        resource = (
            str(error.resource).replace("\\", "/").rsplit("/", 1)[-1]
            if error.resource
            else None
        )
        message = (
            f"Packing failed while processing '{resource}'. "
            "Check the application log for details."
            if resource
            else "The project could not be packed. "
            "Check the application log for details."
        )
        QMessageBox.critical(self, "Packing Failed", message)

    def _on_pack_finished(self) -> None:
        self._pack_in_progress = False
        self._refresh_artifact_action_states()

    def _export_files(self) -> None:
        if (
            self._artifact_generation_in_progress()
            or self._run_in_progress
            or not self._workspace_is_stable_for_artifact("Export Files")
        ):
            return
        destination = QFileDialog.getExistingDirectory(
            self,
            "Select export folder",
        )
        if not destination:
            return
        self._export_in_progress = True
        self._refresh_artifact_action_states()
        self.statusBar().showMessage("Exporting project files...")
        self._run_task(
            lambda: self._project_service.export_project_files(
                self._manifest,
                destination,
            ),
            self._on_export_succeeded,
            failure=self._on_export_failed,
            finished=self._on_export_finished,
        )

    def _on_export_succeeded(self, path) -> None:
        self.statusBar().showMessage("File export completed successfully.", 5000)
        QMessageBox.information(
            self,
            "Export Files",
            f"Reconstructed game files were exported to:\n{path}",
        )

    def _on_export_failed(self, error: TaskError) -> None:
        self.statusBar().showMessage("File export failed.", 5000)
        QMessageBox.critical(self, "Export Files Failed", str(error))

    def _on_export_finished(self) -> None:
        self._export_in_progress = False
        self._refresh_artifact_action_states()

    def _refresh_artifact_action_states(self, *_args) -> None:
        artifact_busy = self._artifact_generation_in_progress()
        card_save_busy = (
            self._card_list_view is not None
            and self._card_list_view.is_project_save_in_progress
        )
        editor_mutation_busy = (
            self._current_editor is not None
            and self._current_editor.is_project_mutation_in_progress
        )
        project_mutation_busy = card_save_busy or editor_mutation_busy
        artifact_actions_enabled = (
            not artifact_busy
            and not self._run_in_progress
            and not project_mutation_busy
        )
        self._export_button.setEnabled(artifact_actions_enabled)
        self._build_button.setEnabled(artifact_actions_enabled)
        self._build_and_run_button.setEnabled(
            self._manifest.executable is not None
            and not artifact_busy
            and not self._run_in_progress
        )
        self._save_file_button.setEnabled(
            not artifact_busy and not project_mutation_busy
        )
        self._card_list_button.setEnabled(
            not artifact_busy and not project_mutation_busy
        )
        self._tree.setEnabled(not artifact_busy and not project_mutation_busy)
        self._editor_host.setEnabled(not artifact_busy and not card_save_busy)
        if self._card_list_view is not None:
            self._card_list_view.setEnabled(not artifact_busy)
            self._card_list_view.set_external_project_mutation_blocked(
                editor_mutation_busy
            )

    def _artifact_generation_in_progress(self) -> bool:
        return self._pack_in_progress or self._export_in_progress

    def _workspace_is_stable_for_artifact(self, title: str) -> bool:
        if not self._project_mutation_in_progress():
            return True
        QMessageBox.information(
            self,
            title,
            "Wait for the current project update to finish before continuing.",
        )
        return False

    def _project_mutation_in_progress(self) -> bool:
        return bool(
            (
                self._card_list_view is not None
                and self._card_list_view.is_project_save_in_progress
            )
            or (
                self._current_editor is not None
                and self._current_editor.is_project_mutation_in_progress
            )
        )

    def _run_game(self) -> None:
        if self._artifact_generation_in_progress() or self._run_in_progress:
            return
        if self._manifest.executable is None:
            QMessageBox.information(
                self,
                "Run Game",
                "This project does not contain an executable.",
            )
            return
        self._run_in_progress = True
        self._refresh_artifact_action_states()
        self._run_task(
            lambda: self._project_service.run_packed_game(self._manifest),
            lambda _process: None,
            finished=self._on_run_finished,
        )

    def _on_run_finished(self) -> None:
        self._run_in_progress = False
        self._refresh_artifact_action_states()

    def _run_task(
        self,
        action,
        success,
        *,
        failure=None,
        finished=None,
    ) -> None:
        self._pgb_progress.show()
        runner = TaskRunner(action)
        self._active_runners[runner.signals] = runner
        runner.signals.succeeded.connect(success)
        runner.signals.failed.connect(failure or self._on_task_failed)
        if finished is not None:
            runner.signals.finished.connect(finished)
        runner.signals.finished.connect(self._on_task_finished)
        self._thread_pool.start(runner)

    def _on_task_failed(self, error: TaskError) -> None:
        QMessageBox.critical(self, "Operation Failed", str(error))

    def _on_task_finished(self) -> None:
        signals = self.sender()
        if isinstance(signals, TaskSignals):
            self._active_runners.pop(signals, None)
        if not self._active_runners:
            self._pgb_progress.hide()
