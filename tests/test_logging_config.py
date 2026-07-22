import io
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yugioh_editor.common.logging_config import (
    LOG_BACKUP_COUNT,
    LOG_FILE_NAME,
    LOG_MAX_BYTES,
    configure_logging,
)


class LoggingConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger(f"{__name__}.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.propagate = False

    def tearDown(self):
        for handler in self.logger.handlers:
            handler.close()
        self.logger.handlers.clear()

    def test_logging_uses_console_and_rotating_file_without_duplicates(self):
        console = io.StringIO()
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        directory = temporary_directory.name
        with patch(
            "sys.stderr",
            console,
        ):
            root = Path(directory)
            log_path = configure_logging(root, self.logger)
            for handler in self.logger.handlers:
                self.addCleanup(handler.close)
            handler_count = len(self.logger.handlers)
            second_path = configure_logging(root, self.logger)

            self.assertEqual(
                log_path.resolve(),
                (root / "logs" / LOG_FILE_NAME).resolve(),
            )
            self.assertEqual(second_path, log_path)
            self.assertTrue(log_path.is_file())
            self.assertEqual(len(self.logger.handlers), handler_count)
            self.assertEqual(handler_count, 2)

            try:
                raise RuntimeError("controlled logging failure")
            except RuntimeError:
                self.logger.exception("Background task failed.")
            for handler in self.logger.handlers:
                handler.flush()

            file_output = log_path.read_text(encoding="utf-8")
            self.assertIn("Background task failed.", file_output)
            self.assertIn("Traceback", file_output)
            self.assertIn("controlled logging failure", file_output)
            self.assertIn("Background task failed.", console.getvalue())
            self.assertEqual(list(root.glob("*.log")), [])

            file_handlers = [
                handler
                for handler in self.logger.handlers
                if hasattr(handler, "maxBytes")
            ]
            self.assertEqual(len(file_handlers), 1)
            self.assertEqual(file_handlers[0].maxBytes, LOG_MAX_BYTES)
            self.assertEqual(file_handlers[0].backupCount, LOG_BACKUP_COUNT)

    def test_repository_gitignore_excludes_runtime_log_directory(self):
        repository_root = Path(__file__).resolve().parents[1]
        rules = (
            (repository_root / ".gitignore").read_text(encoding="utf-8").splitlines()
        )
        self.assertIn("/logs/", rules)
