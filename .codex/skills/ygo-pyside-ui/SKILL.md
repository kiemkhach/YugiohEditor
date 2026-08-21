---
name: ygo-pyside-ui
description: Implement or debug YugiohEditor PySide6 views, dialogs, progress/busy UX, retained background tasks, window state, Card List responsiveness, Add Card flow, Project/Start windows, or Run/Pack UI behavior.
---

# YugiohEditor PySide6 UI

Use for UI behavior and responsiveness. Pair with the domain skill that owns the underlying operation; UI code must not absorb repository/domain responsibilities.

## Files and ownership

- Designer files: `yugioh_editor/ui/*.ui`.
- View/controller-facing widget code: `yugioh_editor/views`.
- Long-running use cases remain in services/repositories and run through the established retained task/runner pattern.
- Do not hand-edit generated Python from `.ui` files unless explicitly requested.

Read `/AGENTS.md` and inspect existing UI-loading tests before changing widget names, labels, visibility, or layouts.

## Responsiveness rule

A window can look frozen even when work eventually moves to a worker if expensive preparation occurs before the event loop paints the busy state.

For Save, Add Card, Pack, Suggest, launch, and similar operations:

1. reject duplicate invocation when the operation must be serialized;
2. synchronously set controls/progress/busy state;
3. return/yield to the event loop or start the retained worker without expensive preparation on the GUI thread;
4. perform heavy staging, cloning, validation, network, image, and filesystem work off-thread;
5. emit progress/status via signals;
6. update widgets only on the GUI thread;
7. restore state on success/failure/cancellation.

A progress bar must become visible before the expensive phase starts. Do not use `QApplication.processEvents()` to paper over incorrect ownership.

## Card List Save

Save is a project mutation. Keep only one active save transaction. Progress should cover meaningful phases where available (prepare/stage, table/image work, validation/commit) rather than fake percentages. Filtering/sorting/selection state should remain stable unless the saved data itself requires a refresh.

## Add Card

Prefer opening Card Detail immediately with a clear processing/disabled state, then initialize new ID/index/draft/image metadata in a worker. Do not make the user wait on the Card List while the detail window has not appeared. Editable controls become active only after initialization succeeds.

## Window state

`ApplicationController.open_project()` owns `ProjectView.showMaximized()`. Do not maximize from the view constructor and do not maximize the Start window.

When Save/dialog/task completion unexpectedly loses maximized state, investigate `show()`, `showNormal()`, reparenting, modality, activation, close/reopen, and controller ownership. Do not repeatedly call `showMaximized()` as a symptom-level workaround unless the required product behavior explicitly calls for it.

## Project/Start behavior

- Project `Run` launches only the executable already packed under `bin`; it never triggers Build/Pack.
- Run remains a retained background task; success has no modal dialog; failure restores busy state and reports through the existing path.
- Pack uses a retained background task and disables duplicate Pack requests.
- Preserve existing splitter/active-editor layout constraints documented in `AGENTS.md`.

## Tests

Use `tests/test_ui_loading.py`, `tests/test_card_editor_ui.py`, and affected service tests. Test immediate busy-state changes separately from worker completion. For race-sensitive code, explicitly test duplicate clicks, cancellation/close, stale results, failure cleanup, and retained runner lifetime.
