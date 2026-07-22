from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from yugioh_editor.common.logging_config import configure_logging
from yugioh_editor.repositories.project.repository import ProjectRepository
from yugioh_editor.resources import get_resource_path
from yugioh_editor.services.card_reference_data_service import (
    CardReferenceDataService,
)
from yugioh_editor.services.card_service import CardService
from yugioh_editor.services.project_service import ProjectService
from yugioh_editor.views.project_view import ProjectView
from yugioh_editor.views.start_view import StartView

WINDOWS_APP_USER_MODEL_ID = "YGOEditor.YugiohEditor"


def configure_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        WINDOWS_APP_USER_MODEL_ID
    )
    if result != 0:
        raise OSError(
            result,
            "Unable to configure the Windows AppUserModelID.",
        )


def configure_application_icon(application: QApplication) -> QIcon:
    icon_path = get_resource_path("app.icon")
    application_icon = QIcon(str(icon_path))
    if application_icon.isNull():
        raise RuntimeError(f"Unable to load application icon: {icon_path}")
    application.setWindowIcon(application_icon)
    return application_icon


class ApplicationController:
    def __init__(self) -> None:
        self._card_reference_data_service = CardReferenceDataService()
        self.project_service = ProjectService(self._card_reference_data_service)
        self.card_service = CardService(
            card_reference_data_service=self._card_reference_data_service
        )
        self.start_view = StartView(self.project_service)
        self.project_view: ProjectView | None = None
        self.start_view.project_opened.connect(self.open_project)

    def show(self) -> None:
        self.start_view.show()

    def open_project(self, manifest) -> None:
        self.card_service = CardService(
            ProjectRepository(manifest),
            self._card_reference_data_service,
        )
        self.project_view = ProjectView(
            manifest,
            self.project_service,
            self.card_service,
        )
        self.project_view.project_closed.connect(self.start_view.show)
        self.start_view.hide()
        self.project_view.showMaximized()


def main() -> int:
    configure_logging()
    logging.info("Application startup.")
    configure_windows_app_id()
    application = QApplication(sys.argv)
    configure_application_icon(application)
    controller = ApplicationController()
    controller.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
