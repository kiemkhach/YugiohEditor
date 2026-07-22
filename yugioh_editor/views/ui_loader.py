from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFile
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QWidget


def load_ui(path: str | Path, parent=None) -> QWidget:
    ui_file_path = Path(path).expanduser().resolve()
    if not ui_file_path.exists():
        raise FileNotFoundError(f"UI file does not exist: '{ui_file_path}'")

    file = QFile(str(ui_file_path))
    if not file.open(QFile.ReadOnly):
        raise OSError(f"Unable to open UI file '{ui_file_path}': {file.errorString()}")
    loader = QUiLoader()
    try:
        try:
            widget = loader.load(file, parent)
        except Exception as error:
            details = loader.errorString() or str(error)
            raise RuntimeError(
                f"Unable to load UI file '{ui_file_path}': {details}"
            ) from error
    finally:
        file.close()
    if widget is None:
        raise RuntimeError(
            f"Unable to load UI file '{ui_file_path}': {loader.errorString()}"
        )
    return widget
