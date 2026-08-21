from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHeaderView,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from yugioh_editor.common.constants import DEFAULT_LANGUAGE, LANGUAGE_PREFIXES, ui_path
from yugioh_editor.models.card_editing import (
    BulkSuggestionResult,
    CardEditDraft,
    CardImportApplyResult,
)
from yugioh_editor.models.entities import ProjectManifest
from yugioh_editor.services.card_service import CardService
from yugioh_editor.views.card_editor_dialog import CardEditorDialog
from yugioh_editor.views.card_list_model import (
    CardListModel,
    UnusedCardFilterProxyModel,
)
from yugioh_editor.views.ui_loader import load_ui
from yugioh_editor.workers.task_runner import (
    CancellableProgressTaskRunner,
    TaskError,
    TaskRunner,
)


class CardListView(QDialog):
    dirty_changed = Signal(bool)
    project_save_state_changed = Signal(bool)

    def __init__(
        self,
        manifest: ProjectManifest,
        card_service: CardService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._manifest = manifest
        self._service = card_service
        self._thread_pool = QThreadPool.globalInstance()
        self._active_runners: set[TaskRunner] = set()
        self._suggest_runner: CancellableProgressTaskRunner | None = None
        self._add_runner: TaskRunner | None = None
        self._save_pending = False
        self._save_runner: TaskRunner | None = None
        self._last_project_save_state = False
        self._external_project_mutation_blocked = False
        self._editor_dialog: CardEditorDialog | None = None
        self._dirty = False
        self._loading = False
        self._close_after_save = False
        self._reject_after_add = False
        self._reject_after_suggest = False
        self._closing = False
        self._maximize_policy_active = False
        self._maximize_restore_pending = False
        self.setWindowTitle("Card List")
        root = load_ui(ui_path("card_list_window.ui"), self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(root)
        self.resize(1280, 760)

        self._table = self.findChild(QTableView, "tableCards")
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._pgb_progress = self.findChild(QProgressBar, "pgbProgress")
        self._display_language = self.findChild(QComboBox, "cmbDisplayLanguage")
        self._display_language.addItems(LANGUAGE_PREFIXES)
        self._display_language.setCurrentText(DEFAULT_LANGUAGE)
        self._display_language.currentTextChanged.connect(
            self._display_language_changed
        )
        self._unused_filter_button = self.findChild(QPushButton, "btnUnusedFilter")
        self._unused_filter_button.toggled.connect(self._unused_filter_toggled)
        self._enable_all_button = self.findChild(QPushButton, "btnEnableAll")
        self._enable_all_button.clicked.connect(self._enable_all_cards)
        self._add_button = self.findChild(QPushButton, "btnAdd")
        self._update_button = self.findChild(QPushButton, "btnUpdate")
        self._save_button = self.findChild(QPushButton, "btnSave")
        self._import_button = self.findChild(QPushButton, "btnImport")
        self._suggest_button = self.findChild(QPushButton, "btnSuggest")
        self._cancel_suggest_button = self.findChild(
            QPushButton,
            "btnCancelSuggest",
        )
        self._add_button.clicked.connect(self._add_card)
        self._update_button.clicked.connect(self._update_card)
        self._import_button.clicked.connect(self._import_cards)
        self.findChild(QPushButton, "btnExport").clicked.connect(self._export_cards)
        self._suggest_button.clicked.connect(self._suggest_cards)
        self._cancel_suggest_button.clicked.connect(self._cancel_suggest)
        self._save_button.clicked.connect(self._save)
        self.findChild(QPushButton, "btnClose").clicked.connect(self.close)
        self._table.doubleClicked.connect(self._open_index)
        self._model = CardListModel((), self)
        self._model.set_display_language(self._display_language.currentText())
        self._proxy_model = UnusedCardFilterProxyModel(self)
        self._proxy_model.setSourceModel(self._model)
        self._proxy_model.set_unused_only(self._unused_filter_button.isChecked())
        self._table.setModel(self._proxy_model)
        self._table.selectionModel().selectionChanged.connect(self._selection_changed)
        for column, width in enumerate(
            (75, 75, 190, 300, 95, 55, 70, 70, 90, 120, 100, 95, 140)
        ):
            self._table.setColumnWidth(column, width)
        self._reload()

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def is_project_save_in_progress(self) -> bool:
        """Return whether Card List is writing an atomic project transaction."""

        return self._save_pending or self._save_runner is not None

    def set_external_project_mutation_blocked(self, blocked: bool) -> None:
        """Block Card List mutations owned by another project surface."""

        self._external_project_mutation_blocked = blocked
        if self._editor_dialog is not None:
            self._editor_dialog.setEnabled(not blocked)
        self._refresh_action_states()

    def _notify_project_save_state(self) -> None:
        busy = self.is_project_save_in_progress
        if busy == self._last_project_save_state:
            return
        self._last_project_save_state = busy
        self.project_save_state_changed.emit(busy)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() != QEvent.WindowStateChange:
            return
        state = self.windowState()
        if state & Qt.WindowState.WindowMinimized:
            return
        if state & Qt.WindowState.WindowMaximized:
            self._maximize_policy_active = True
            self._maximize_restore_pending = False
            return
        if self._maximize_policy_active and self.isVisible() and not self._closing:
            self._schedule_maximized_restore()

    def _schedule_maximized_restore(self) -> None:
        if self._maximize_restore_pending:
            return
        self._maximize_restore_pending = True
        QTimer.singleShot(0, self._restore_maximized_state)

    def _restore_maximized_state(self) -> None:
        self._maximize_restore_pending = False
        state = self.windowState()
        if (
            self._closing
            or not self.isVisible()
            or state & Qt.WindowState.WindowMinimized
            or state & Qt.WindowState.WindowMaximized
        ):
            return
        self.showMaximized()

    def _reload(self, selected_card_index: int | None = None) -> None:
        self._set_loading(True)
        self._execute(
            lambda: self._service.load_card_details(self._manifest),
            lambda cards: self._reload_succeeded(cards, selected_card_index),
        )

    def _reload_succeeded(self, cards, selected_card_index: int | None) -> None:
        if self._closing:
            return
        self._model.reset_from_project(cards)
        self._set_dirty(False)
        self._set_loading(False)
        if selected_card_index is not None:
            self._select_card(selected_card_index)
        self._selection_changed()

    def _display_language_changed(self, language: str) -> None:
        if hasattr(self, "_model"):
            self._model.set_display_language(language)

    def _unused_filter_toggled(self, enabled: bool) -> None:
        self._unused_filter_button.setText(
            "un-filter empty" if enabled else "filter empty"
        )
        if not hasattr(self, "_proxy_model"):
            return
        selected = self._selected_card_index()
        self._proxy_model.set_unused_only(enabled)
        if selected is not None and not self._select_card(selected):
            self._table.clearSelection()
        self._selection_changed()

    def _enable_all_cards(self) -> None:
        if self._model_mutation_blocked():
            return
        selected = self._selected_card_index()
        result = self._model.enable_all_eligible_cards()
        if selected is not None and not self._select_card(selected):
            self._table.clearSelection()
        self._selection_changed()
        self._set_dirty(self._model.has_dirty_cards())
        QMessageBox.information(
            self,
            "Enable All Cards",
            f"Updated: {result.updated}\n"
            f"Protected: {result.protected}\n"
            f"Skipped: {result.skipped}",
        )

    def closeEvent(self, event) -> None:
        if self._save_pending or self._save_runner is not None:
            self._close_after_save = True
            event.ignore()
            return
        if self._add_runner is not None:
            self._reject_after_add = True
            if self._editor_dialog is not None:
                self._editor_dialog.reject()
            event.ignore()
            return
        if self._reject_after_add:
            event.ignore()
            return
        if not self._dirty:
            if self._defer_reject_until_suggest_finishes():
                event.ignore()
                return
            self._closing = True
            event.ignore()
            self.reject()
            return
        answer = QMessageBox.warning(
            self,
            "Unsaved Card List Changes",
            "Imported or suggested card changes have not been saved.",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Save:
            self._close_after_save = True
            if self._suggest_runner is not None:
                self._cancel_suggest()
                self._refresh_action_states()
            else:
                self._save()
            event.ignore()
        elif answer == QMessageBox.Discard:
            self._set_dirty(False)
            if self._defer_reject_until_suggest_finishes():
                event.ignore()
                return
            self._closing = True
            event.ignore()
            self.reject()
        else:
            event.ignore()

    def _selection_changed(self, *_args) -> None:
        self._refresh_action_states()

    def _add_card(self) -> None:
        if self._model_mutation_blocked():
            return
        dialog = self._open_card_detail(None)
        if dialog is None:
            return
        runner = TaskRunner(lambda: self._service.create_card_draft(self._manifest))
        self._add_runner = runner
        self._refresh_action_states()
        runner.signals.succeeded.connect(
            lambda draft: self._add_succeeded(runner, dialog, draft)
        )
        runner.signals.failed.connect(
            lambda error: self._add_failed(runner, dialog, error)
        )
        runner.signals.finished.connect(lambda: self._add_finished(runner))
        QTimer.singleShot(0, lambda: self._start_add_runner(runner, dialog))

    def _start_add_runner(
        self,
        runner: TaskRunner,
        dialog: CardEditorDialog,
    ) -> None:
        if self._add_runner is not runner:
            return
        if self._editor_dialog is not dialog:
            self._add_runner = None
            self._refresh_action_states()
            self._resume_close_after_add()
            return
        self._thread_pool.start(runner)

    def _add_succeeded(
        self,
        runner: TaskRunner,
        dialog: CardEditorDialog,
        draft: CardEditDraft,
    ) -> None:
        if self._add_runner is runner and self._editor_dialog is dialog:
            dialog.initialize_draft(draft)

    def _add_failed(
        self,
        runner: TaskRunner,
        dialog: CardEditorDialog,
        error: TaskError,
    ) -> None:
        if self._add_runner is not runner:
            return
        if self._editor_dialog is dialog:
            dialog.initialization_failed()
            dialog.reject()
        self._task_failed(error)

    def _add_finished(self, runner: TaskRunner) -> None:
        if self._add_runner is runner:
            self._add_runner = None
            self._refresh_action_states()
            self._resume_close_after_add()

    def _resume_close_after_add(self) -> None:
        if not self._reject_after_add:
            return
        QTimer.singleShot(0, self._continue_close_after_add)

    def _continue_close_after_add(self) -> None:
        self._reject_after_add = False
        self.close()
        if not self._closing:
            self._refresh_action_states()

    def _update_card(self) -> None:
        if self._model_mutation_blocked():
            return
        card = self._selected_card()
        if card is not None:
            self._open_card_detail(card)

    def _open_index(self, index: QModelIndex) -> None:
        if index.isValid() and not self._model_mutation_blocked():
            source_index = (
                self._proxy_model.mapToSource(index)
                if index.model() is self._proxy_model
                else index
            )
            self._open_card_detail(self._model.card_at(source_index.row()))

    def _open_card_detail(
        self,
        draft: CardEditDraft | None,
    ) -> CardEditorDialog | None:
        if self._editor_dialog is not None:
            self._editor_dialog.show()
            self._editor_dialog.raise_()
            self._editor_dialog.activateWindow()
            return None
        dialog = CardEditorDialog(
            self._manifest,
            self._service,
            draft,
            self,
            card_lookup=self._model.card_by_index,
            card_bounds=self._model.card_index_bounds,
        )
        dialog.setModal(True)
        dialog.saved.connect(self._card_saved)
        dialog.finished.connect(self._editor_finished)
        self._editor_dialog = dialog
        dialog.open()
        return dialog

    def _card_saved(self, card: CardEditDraft) -> None:
        if self._model.card_by_index(card.card_index) is None:
            self._model.insert_card(card)
        else:
            self._model.update_card(card)
        self._select_card(card.card_index)
        self._set_dirty(self._model.has_dirty_cards())

    def _editor_finished(self, _result: int) -> None:
        dialog = self.sender()
        if dialog is self._editor_dialog:
            self._editor_dialog = None
        if isinstance(dialog, CardEditorDialog):
            dialog.deleteLater()

    def _import_cards(self) -> None:
        if self._model_mutation_blocked():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Cards CSV",
            "",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        selected = self._selected_card_index()
        try:
            parsed = self._service.parse_card_import_csv(self._manifest, path)
            result = self._service.apply_import_to_drafts(
                parsed,
                self._model.cards(),
            )
        except Exception as error:
            logging.exception("Importing card CSV failed.")
            QMessageBox.critical(self, "Import Cards Error", str(error))
            return
        self._apply_import_result(result, selected)
        QMessageBox.information(
            self,
            "Import Cards",
            "Import staged successfully.\n"
            f"Rows: {result.total_rows}\n"
            f"Matched: {result.matched}\n"
            f"Unknown IDs skipped: {result.skipped_unknown_ids}\n"
            f"Image-name changes ignored: {result.ignored_image_name_changes}\n"
            f"Updated: {result.updated}",
        )

    def _apply_import_result(
        self,
        result: CardImportApplyResult,
        selected_card_index: int | None,
    ) -> None:
        self._model.reset_from_project(result.cards)
        self._set_dirty(result.updated > 0)
        if selected_card_index is not None:
            self._select_card(selected_card_index)

    def _export_cards(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Cards CSV",
            "cards.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        destination = Path(path)
        if destination.suffix.casefold() != ".csv":
            destination = destination.with_suffix(".csv")
        try:
            self._service.export_cards_csv(
                self._manifest,
                destination,
                self._displayed_cards(),
            )
        except Exception as error:
            logging.exception("Exporting card CSV failed.")
            QMessageBox.critical(self, "Export Cards Error", str(error))
            return
        QMessageBox.information(
            self,
            "Export Cards",
            f"Exported the current staged card state to:\n{destination}",
        )

    def _suggest_cards(self) -> None:
        if self._model_mutation_blocked():
            return
        cards = self._model.cards()
        runner = CancellableProgressTaskRunner(
            lambda is_cancelled, report_progress: (
                self._service.bulk_suggest_missing_text(
                    cards,
                    manifest=self._manifest,
                    is_cancelled=is_cancelled,
                    report_progress=report_progress,
                )
            )
        )
        self._suggest_runner = runner
        self._refresh_action_states()
        self._cancel_suggest_button.show()
        # Candidate selection belongs to the service because it depends on
        # semantic field applicability. Stay indeterminate until the service
        # reports the post-filter candidate count.
        self._pgb_progress.setRange(0, 0)
        self._pgb_progress.setValue(0)
        self._pgb_progress.show()
        runner.signals.progress.connect(self._suggest_progress)
        runner.signals.succeeded.connect(self._suggest_succeeded)
        runner.signals.failed.connect(self._task_failed)
        runner.signals.finished.connect(self._suggest_finished)
        self._thread_pool.start(runner)

    def _suggest_progress(self, processed: int, total: int) -> None:
        if total <= 0:
            self._pgb_progress.setRange(0, 0)
            return
        self._pgb_progress.setRange(0, total)
        self._pgb_progress.setValue(processed)

    def _suggest_succeeded(self, result: BulkSuggestionResult) -> None:
        if self._closing or self._close_after_save or self._reject_after_suggest:
            return
        selected = self._selected_card_index()
        self._model.reset_from_project(result.cards)
        if result.resolved:
            self._set_dirty(True)
        if selected is not None:
            self._select_card(selected)
        QMessageBox.information(
            self,
            "Suggest Cards",
            f"Source cards: {result.total_source_cards}\n"
            f"Candidates: {result.total_candidates}\n"
            f"Skipped complete: {result.skipped_complete}\n"
            f"Resolved: {result.resolved}\n"
            f"Unchanged: {result.unchanged}\n"
            f"Partially filled: {result.partially_filled}\n"
            f"Not found: {result.not_found}\n"
            f"No query name: {result.skipped_no_query_name}\n"
            f"Failed: {result.failed}\n"
            f"Images staged: {result.image_staged}\n"
            f"Images failed: {result.image_failed}\n"
            f"Cancelled: {'yes' if result.cancelled else 'no'}",
        )

    def _cancel_suggest(self) -> None:
        if self._suggest_runner is not None:
            self._suggest_runner.cancel()

    def _suggest_finished(self) -> None:
        self._suggest_runner = None
        self._cancel_suggest_button.hide()
        self._pgb_progress.hide()
        if self._close_after_save:
            self._save()
        elif self._reject_after_suggest:
            self._reject_after_suggest = False
            self._closing = True
            self.reject()
        else:
            self._refresh_action_states()

    def _save(self) -> None:
        if self._model_mutation_blocked():
            return
        self._save_pending = True
        self._notify_project_save_state()
        self._refresh_action_states()
        self._pgb_progress.setRange(0, 0)
        self._pgb_progress.setValue(0)
        self._pgb_progress.show()
        QTimer.singleShot(0, self._prepare_save)

    def _prepare_save(self) -> None:
        if not self._save_pending:
            return
        if self._closing:
            self._save_pending = False
            self._close_after_save = False
            self._notify_project_save_state()
            self._refresh_action_states()
            self._pgb_progress.hide()
            return
        runner: TaskRunner | None = None
        try:
            changes = list(self._model.dirty_cards())
            if not changes:
                self._save_pending = False
                self._notify_project_save_state()
                self._refresh_action_states()
                self._pgb_progress.hide()
                if self._close_after_save:
                    self._close_after_save = False
                    self._closing = True
                    self.accept()
                return
            selected = self._selected_card_index()
            runner = TaskRunner(
                lambda: self._service.save_card_changes(self._manifest, changes)
            )
            self._save_pending = False
            self._save_runner = runner
            self._notify_project_save_state()
            self._active_runners.add(runner)
            runner.signals.succeeded.connect(
                lambda _result: self._save_succeeded(changes, selected)
            )
            runner.signals.failed.connect(self._save_failed)
            runner.signals.finished.connect(lambda: self._save_finished(runner))
            self._thread_pool.start(runner)
        except Exception as error:
            logging.exception("Preparing the Card List save failed.")
            if runner is not None:
                self._active_runners.discard(runner)
                if self._save_runner is runner:
                    self._save_runner = None
            self._save_pending = False
            self._close_after_save = False
            self._notify_project_save_state()
            self._refresh_action_states()
            self._pgb_progress.hide()
            QMessageBox.critical(self, "Card List Error", str(error))

    def _save_succeeded(
        self,
        changes: list[CardEditDraft],
        selected_card_index: int | None,
    ) -> None:
        for card in changes:
            self._model.update_card(card)
        self._set_dirty(False)
        if selected_card_index is not None:
            self._select_card(selected_card_index)
        QMessageBox.information(
            self,
            "Save Cards",
            "All staged card changes were committed successfully.",
        )
        if self._close_after_save:
            self._close_after_save = False
            self._closing = True
            self.accept()

    def _save_failed(self, error: TaskError) -> None:
        self._close_after_save = False
        self._task_failed(error)

    def _save_finished(self, runner: TaskRunner) -> None:
        self._active_runners.discard(runner)
        if self._save_runner is runner:
            self._save_runner = None
        self._notify_project_save_state()
        self._refresh_action_states()
        if not self._closing:
            self._pgb_progress.hide()

    def _selected_card(self) -> CardEditDraft | None:
        rows = self._table.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        source = self._proxy_model.mapToSource(rows[0])
        return self._model.card_at(source.row())

    def _selected_card_index(self) -> int | None:
        card = self._selected_card()
        return None if card is None else card.card_index

    def _select_card(self, card_index: int) -> bool:
        row = self._model.row_for_card_index(card_index)
        if row is None:
            return False
        proxy = self._proxy_model.mapFromSource(self._model.index(row, 0))
        if not proxy.isValid():
            return False
        self._table.selectRow(proxy.row())
        self._table.scrollTo(proxy)
        return True

    def _displayed_cards(self) -> tuple[CardEditDraft, ...]:
        return tuple(
            self._model.card_at(
                self._proxy_model.mapToSource(self._proxy_model.index(row, 0)).row()
            )
            for row in range(self._proxy_model.rowCount())
        )

    def _set_dirty(self, dirty: bool) -> None:
        changed = self._dirty != dirty
        self._dirty = dirty
        self._refresh_action_states()
        self.setWindowTitle("Card List *" if dirty else "Card List")
        if changed:
            self.dirty_changed.emit(dirty)

    def _set_loading(self, loading: bool) -> None:
        self._loading = loading
        self._table.setEnabled(not loading)
        self._unused_filter_button.setEnabled(not loading)
        self._display_language.setEnabled(not loading)
        self._refresh_action_states()

    def _model_mutation_blocked(self) -> bool:
        return (
            self._loading
            or self._external_project_mutation_blocked
            or self._suggest_runner is not None
            or self._add_runner is not None
            or self._reject_after_add
            or self._save_pending
            or self._save_runner is not None
            or self._closing
        )

    def _refresh_action_states(self) -> None:
        if not hasattr(self, "_model"):
            return
        blocked = self._model_mutation_blocked()
        selected = self._table.selectionModel().selectedRows()
        self._add_button.setEnabled(not blocked)
        self._update_button.setEnabled(not blocked and len(selected) == 1)
        self._import_button.setEnabled(not blocked)
        self._enable_all_button.setEnabled(not blocked)
        self._suggest_button.setEnabled(not blocked)
        self._save_button.setEnabled(self._dirty and not blocked)

    def _defer_reject_until_suggest_finishes(self) -> bool:
        if self._suggest_runner is None:
            return False
        self._reject_after_suggest = True
        self._cancel_suggest()
        self._refresh_action_states()
        return True

    def _execute(self, action, succeeded) -> None:
        self._pgb_progress.setRange(0, 0)
        self._pgb_progress.show()
        runner = TaskRunner(action)
        self._active_runners.add(runner)
        runner.signals.succeeded.connect(succeeded)
        runner.signals.failed.connect(self._task_failed)
        runner.signals.finished.connect(lambda: self._runner_finished(runner))
        self._thread_pool.start(runner)

    def _task_failed(self, error: TaskError) -> None:
        if (
            self._closing
            or self._close_after_save
            or self._reject_after_add
            or self._reject_after_suggest
        ):
            return
        self._set_loading(False)
        QMessageBox.critical(self, "Card List Error", str(error))

    def _runner_finished(self, runner: TaskRunner) -> None:
        self._active_runners.discard(runner)
        if not self._closing:
            self._pgb_progress.hide()
