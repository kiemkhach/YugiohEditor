---
name: ygo-implement-change
description: Implement an approved YugiohEditor change while preserving architecture, binary invariants, atomic staging, and existing repository conventions. Use after investigation/design is sufficiently settled.
---

# YugiohEditor Implementation

Use this skill for production-code changes after the behavior and owning layer are understood.

## Before editing

1. Read `/AGENTS.md` and the relevant repository documentation.
2. Inspect the current code and nearby tests; never rely on an old prompt/spec when the repository differs.
3. If the task still depends on an unverified binary rule, unknown ordering rule, unclear threading root cause, or executable semantic, stop implementation and use `ygo-investigate-change` first.
4. Preserve the dependency flow:
   `View -> Service -> Repository -> Connection -> Codec -> raw bytes/filesystem`.

## Implementation rules

- Make the smallest coherent change in the layer that owns the responsibility.
- Reuse registries, `SUBFILE_RULE_CONFIGS`, table handlers, pipeline hooks, `TaskRunner`, staging transactions, and existing helpers instead of adding parallel mechanisms.
- Views/services must not reach into connections/codecs.
- Repositories must not instantiate codecs.
- Codecs stay generic and must not know filenames or project paths.
- Never add a special-case filename dispatch when a rule/registry already owns dispatch.
- Preserve source/workspace binary data unless the configured Pack pipeline explicitly transforms it.
- Keep project creation, card save, and Pack atomic. A failed operation must not leave a partially mutated workspace or replace a valid previous `bin` output.
- Generated Python files from `.ui` files are not hand-edited unless explicitly requested.

## UI and concurrency

For expensive operations:

1. enter busy/processing state synchronously before expensive work begins;
2. yield control to the event loop or start the retained worker immediately;
3. run filesystem-heavy, staging, provider, or image work off the GUI thread;
4. update widgets only on the GUI thread via signals;
5. serialize operations that mutate the same project/repository;
6. restore controls and retained-runner state on success, failure, and cancellation.

Do not use `QApplication.processEvents()` as a substitute for correct worker ownership.

## Binary and card data

Before changing a binary codec or generator, state the relevant width, signedness, endianness, alignment, padding, terminator, record-count, ID/index, and virtual/physical invariants. Keep `card_id`, card slot/index, internal Card ID, `card_intid`, `card_sort`, and executable capacity as distinct concepts.

When adding/replacing card images, preserve pair/catalog/manifest invariants and compression state. Do not replan unrelated container records unless the operation explicitly requires canonical replanning.

## Tests while implementing

Add the narrowest regression test that fails before the fix. Then run:

1. the new/affected test module;
2. adjacent domain tests;
3. architecture tests when dependencies changed;
4. the full test suite for cross-cutting changes.

Use `ruff`/formatting according to `pyproject.toml` for changed Python code.

## Documentation

Update durable documentation only for behavior that is now implemented and verified. Preserve evidence qualifiers for reverse-engineered facts. Do not document runtime verification that did not happen.

## Completion report

Report:

- files changed and why;
- behavioral result;
- tests run and result;
- documentation updated;
- any deviation from the approved design;
- remaining runtime/manual verification.
