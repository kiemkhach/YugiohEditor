from __future__ import annotations

import re
from collections.abc import Callable

import pandas as pd
from PySide6.QtCore import Qt, QThreadPool, QUrl
from PySide6.QtGui import QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from yugioh_editor.models.entities import ProjectFileRecord, ProjectManifest
from yugioh_editor.services.project_service import ProjectService
from yugioh_editor.workers.task_runner import TaskRunner


class FileEditor(QWidget):
    def __init__(
        self,
        service: ProjectService,
        manifest: ProjectManifest,
        record: ProjectFileRecord,
    ) -> None:
        super().__init__()
        self.service = service
        self.manifest = manifest
        self.record = record

    def save(self) -> None:
        raise NotImplementedError


class TextEditor(FileEditor):
    def __init__(
        self,
        service: ProjectService,
        manifest: ProjectManifest,
        record: ProjectFileRecord,
    ) -> None:
        super().__init__(service, manifest, record)
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(service.read_project_text(manifest, record))
        layout = QVBoxLayout(self)
        layout.addWidget(self.editor)

    def save(self) -> None:
        self.service.write_project_text(
            self.manifest,
            self.record,
            self.editor.toPlainText(),
        )


class TableEditor(FileEditor):
    def __init__(
        self,
        service: ProjectService,
        manifest: ProjectManifest,
        record: ProjectFileRecord,
    ) -> None:
        super().__init__(service, manifest, record)
        self.table = QTableView()
        self.frame = service.read_project_table(manifest, record)
        configured_columns = service.project_table_editor_columns(manifest, record)
        self.editor_columns = (
            tuple(configured_columns) if isinstance(configured_columns, tuple) else ()
        )
        missing = [
            column for column in self.editor_columns if column not in self.frame.columns
        ]
        if missing:
            raise ValueError(
                "Table editor columns are missing from the resource: "
                + ", ".join(missing)
            )
        visible = (
            self.frame.loc[:, list(self.editor_columns)]
            if self.editor_columns
            else self.frame
        )
        self.model = dataframe_to_model(visible)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)

    def save(self) -> None:
        edited = model_to_dataframe(self.model)
        if len(edited) != len(self.frame):
            raise ValueError("Table editor cannot change indexed resource row count.")
        merged = self.frame.reset_index(drop=True).copy()
        for column in edited.columns:
            merged[column] = edited[column].tolist()
        self.frame = merged
        self.service.write_project_table(
            self.manifest,
            self.record,
            self.frame,
        )


class ImageEditor(FileEditor):
    def __init__(
        self,
        service: ProjectService,
        manifest: ProjectManifest,
        record: ProjectFileRecord,
    ) -> None:
        super().__init__(service, manifest, record)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(320, 320)
        self.replace_button = QPushButton("Replace Image")
        self.replace_button.clicked.connect(self._replace)
        self._thread_pool = QThreadPool.globalInstance()
        layout = QVBoxLayout(self)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.replace_button)
        self._refresh()

    def _replace(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select replacement image",
            "",
            "Images (*.bmp *.png *.jpg *.jpeg *.gif)",
        )
        if path:
            runner = TaskRunner(
                lambda: self.service.replace_project_image(
                    self.manifest,
                    self.record,
                    path,
                )
            )
            runner.signals.succeeded.connect(lambda _result: self._refresh())
            runner.signals.failed.connect(
                lambda error: QMessageBox.critical(
                    self,
                    "Replace Image Error",
                    str(error),
                )
            )
            self._thread_pool.start(runner)

    def _refresh(self) -> None:
        pixmap = QPixmap()
        pixmap.loadFromData(
            self.service.read_project_binary(
                self.manifest,
                self.record,
            )
        )
        self.preview.setPixmap(
            pixmap.scaled(
                self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def save(self) -> None:
        return None


class AudioEditor(FileEditor):
    def __init__(
        self,
        service: ProjectService,
        manifest: ProjectManifest,
        record: ProjectFileRecord,
    ) -> None:
        super().__init__(service, manifest, record)
        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.play_button = QPushButton("Play")
        self.replace_button = QPushButton("Replace Audio")
        self.play_button.clicked.connect(self._play)
        self.replace_button.clicked.connect(self._replace)
        layout = QHBoxLayout(self)
        layout.addWidget(self.play_button)
        layout.addWidget(self.replace_button)
        self._set_source()

    def _set_source(self) -> None:
        path = self.service.project_resource_path(
            self.manifest,
            self.record,
        )
        self.player.setSource(QUrl.fromLocalFile(str(path)))

    def _play(self) -> None:
        self.player.stop()
        self.player.play()

    def _replace(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select replacement audio",
            "",
            "Wave Audio (*.wav)",
        )
        if path:
            self.service.replace_project_file(
                self.manifest,
                self.record,
                path,
            )
            self._set_source()

    def save(self) -> None:
        return None


class BinaryEditor(FileEditor):
    PREVIEW_LIMIT = 64 * 1024

    def __init__(
        self,
        service: ProjectService,
        manifest: ProjectManifest,
        record: ProjectFileRecord,
    ) -> None:
        super().__init__(service, manifest, record)
        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        data, total_size = service.read_project_binary_preview(
            manifest,
            record,
            self.PREVIEW_LIMIT,
        )
        self._editable = total_size <= self.PREVIEW_LIMIT
        self.editor.setReadOnly(not self._editable)
        self.editor.setPlainText(self._format(data))
        layout = QVBoxLayout(self)
        if not self._editable:
            warning = QLabel(
                f"Read-only preview: showing {len(data):,} of {total_size:,} bytes."
            )
            layout.addWidget(warning)
        layout.addWidget(self.editor)

    def save(self) -> None:
        if not self._editable:
            raise ValueError("Large binary files are read-only in the preview editor.")
        compact = re.sub(r"\s+", "", self.editor.toPlainText())
        if len(compact) % 2 or re.search(r"[^0-9a-fA-F]", compact):
            raise ValueError(
                "Binary editor content must contain complete hexadecimal byte pairs."
            )
        self.service.write_project_binary(
            self.manifest,
            self.record,
            bytes.fromhex(compact),
        )

    @staticmethod
    def _format(data: bytes, width: int = 16) -> str:
        return "\n".join(
            " ".join(f"{value:02X}" for value in data[offset : offset + width])
            for offset in range(0, len(data), width)
        )


EDITOR_FACTORIES: dict[
    str, Callable[[ProjectService, ProjectManifest, ProjectFileRecord], FileEditor]
] = {
    "text": TextEditor,
    "table": TableEditor,
    "image": ImageEditor,
    "audio": AudioEditor,
    "binary": BinaryEditor,
    "exe": BinaryEditor,
}


def create_editor(
    service: ProjectService,
    manifest: ProjectManifest,
    record: ProjectFileRecord,
) -> FileEditor:
    factory = EDITOR_FACTORIES.get(record.file_kind, BinaryEditor)
    return factory(service, manifest, record)


def dataframe_to_model(frame: pd.DataFrame) -> QStandardItemModel:
    model = QStandardItemModel(len(frame), len(frame.columns))
    model.setHorizontalHeaderLabels([str(column) for column in frame.columns])
    for row_index, row in enumerate(frame.itertuples(index=False, name=None)):
        for column_index, value in enumerate(row):
            model.setItem(row_index, column_index, QStandardItem(str(value)))
    return model


def model_to_dataframe(model: QStandardItemModel) -> pd.DataFrame:
    columns = [
        str(model.headerData(index, Qt.Horizontal))
        for index in range(model.columnCount())
    ]
    rows = [
        [
            model.item(row, column).text() if model.item(row, column) else ""
            for column in range(model.columnCount())
        ]
        for row in range(model.rowCount())
    ]
    return pd.DataFrame(rows, columns=columns)
