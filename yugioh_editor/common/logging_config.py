from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

APPLICATION_ROOT = Path(__file__).resolve().parents[2]
LOG_DIRECTORY_NAME = "logs"
LOG_FILE_NAME = "yugioh_editor.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s %(levelname)s:%(threadName)s:%(message)s"
_HANDLER_MARKER = "_yugioh_editor_runtime_handler"


def configure_logging(
    application_root: str | Path | None = None,
    logger: logging.Logger | None = None,
) -> Path:
    target_logger = logger or logging.getLogger()
    existing_file_handler = next(
        (
            handler
            for handler in target_logger.handlers
            if getattr(handler, _HANDLER_MARKER, None) == "file"
        ),
        None,
    )
    if existing_file_handler is not None:
        return Path(existing_file_handler.baseFilename)

    root = (
        Path(application_root).expanduser().resolve()
        if application_root is not None
        else APPLICATION_ROOT
    )
    log_directory = root / LOG_DIRECTORY_NAME
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / LOG_FILE_NAME

    formatter = logging.Formatter(LOG_FORMAT)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    setattr(console_handler, _HANDLER_MARKER, "console")

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_MARKER, "file")

    target_logger.setLevel(logging.INFO)
    target_logger.addHandler(console_handler)
    target_logger.addHandler(file_handler)
    return log_path
