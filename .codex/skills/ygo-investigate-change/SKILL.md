---
name: ygo-investigate-change
description: Investigate YugiohEditor bugs, regressions, binary mismatches, performance problems, and feature requests before implementation. Use when root cause, current behavior, invariants, or design must be proven from repository evidence.
---

# YugiohEditor Investigation

Use this skill before coding whenever the request contains uncertainty, reverse-engineered behavior, a suspected root cause, a binary mismatch, a performance regression, or a cross-layer feature.

## Required context

1. Read `/AGENTS.md` first.
2. Read only the relevant sections of `/ARCHITECTURE.md`, `/DEVELOPMENT.md`, and `/FILE_FORMATS.md`.
3. Inspect the current implementation and tests. Do not design from documentation alone.
4. Treat logs, original game files, reference outputs, and reverse-engineering notes as evidence, not automatically as specification.

## Trace the real path

For application behavior, trace the complete ownership chain when relevant:

`View -> Service -> Repository -> Connection -> Codec -> bytes/filesystem`

For card operations also inspect the logical-table assembly/split path, manifest records, image catalogs, and executable Pack metadata when affected.

For background UI work trace signal/slot ownership, retained runners, cancellation, busy state, and the exact work still occurring on the GUI thread.

## Evidence discipline

Classify important conclusions:

- **Confirmed**: directly demonstrated by code plus test/log/binary evidence.
- **Audited**: implementation inspected and internally consistent, but not independently reproduced.
- **Inferred**: best explanation from available evidence; requires another check before becoming a hard rule.
- **Unresolved**: evidence is insufficient or contradictory.

Never silently promote a hypothesis into a repository invariant. When reverse engineering, preserve the stronger source vocabulary when available, such as `DISASM VERIFY`, `EXE VERIFY`, `RUNTIME VERIFY`, `AUTHOR NOTE`, and `INFERENCE`.

## Investigation procedure

1. Restate the observable failure or requested behavior in testable terms.
2. Locate the current entry point and all state it reads/writes.
3. Identify the current source of truth. Reject duplicate registries or derived-state guesses.
4. Establish invariants before proposing changes: byte layout, ordering, path separators, encodings, IDs versus indexes, threading ownership, transaction boundaries, or UI state as applicable.
5. Reproduce or compare using the smallest reliable fixture.
6. For ordering or binary problems, find the first divergence rather than inspecting only examples near the end.
7. Test competing hypotheses explicitly. Record rejected hypotheses when they are plausible enough to mislead future work.
8. Identify the smallest architectural owner of the fix. Do not move behavior upward merely because it is easier to reach from a view/service.
9. Define regression tests before implementation.
10. Call out documentation in `AGENTS.md`, `ARCHITECTURE.md`, `DEVELOPMENT.md`, or `FILE_FORMATS.md` that would become stale after the fix.

## Binary/container investigations

Always distinguish:

1. packed/stored container bytes;
2. decompressed sub-file bytes;
3. decoded logical representation;
4. project workspace representation.

Do not compare the wrong layer. Preserve original path spelling and separator semantics when investigating `KCEJYUGI` ordering. Do not infer traversal rules from filesystem enumeration.

## Performance investigations

Measure where the delay occurs before adding progress UI. Separate:

- GUI-thread preparation;
- worker startup;
- repository staging/clone work;
- validation;
- network/provider I/O;
- image processing;
- final commit/manifest work.

A progress bar is feedback, not a substitute for moving expensive work off the GUI thread.

## Deliverable

Return a compact engineering report with:

- current behavior;
- evidence and confidence;
- root cause or remaining hypotheses;
- affected modules and ownership boundaries;
- proposed design;
- migration/compatibility concerns;
- exact tests to add or update;
- unresolved items.

Do not implement unless the task explicitly includes implementation or a follow-up authorizes it.
