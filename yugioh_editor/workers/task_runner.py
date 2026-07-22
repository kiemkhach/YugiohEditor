from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


@dataclass(frozen=True, slots=True)
class TaskError:
    title: str
    message: str
    details: str
    exception_type: str
    resource: str | None = None

    def __str__(self) -> str:
        return self.message


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()


class ProgressTaskSignals(TaskSignals):
    progress = Signal(int, int)


class TaskRunner(QRunnable):
    def __init__(self, action: Callable[[], object]) -> None:
        super().__init__()
        self._action = action
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self._action())
        except Exception as error:
            logging.exception("Background task failed.")
            resource = getattr(error, "resource", None)
            self.signals.failed.emit(
                TaskError(
                    title="Operation failed",
                    message=str(error) or error.__class__.__name__,
                    details=traceback.format_exc(),
                    exception_type=error.__class__.__name__,
                    resource=str(resource) if resource is not None else None,
                )
            )
        finally:
            self.signals.finished.emit()


class CancellableProgressTaskRunner(QRunnable):
    def __init__(
        self,
        action: Callable[
            [Callable[[], bool], Callable[[int, int], None]],
            object,
        ],
    ) -> None:
        super().__init__()
        self._action = action
        self._cancelled = Event()
        self.signals = ProgressTaskSignals()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self._action(
                self._cancelled.is_set,
                self.signals.progress.emit,
            )
            self.signals.succeeded.emit(result)
        except Exception as error:
            logging.exception("Background progress task failed.")
            resource = getattr(error, "resource", None)
            self.signals.failed.emit(
                TaskError(
                    title="Operation failed",
                    message=str(error) or error.__class__.__name__,
                    details=traceback.format_exc(),
                    exception_type=error.__class__.__name__,
                    resource=str(resource) if resource is not None else None,
                )
            )
        finally:
            self.signals.finished.emit()
