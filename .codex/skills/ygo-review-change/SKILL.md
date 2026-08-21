---
name: ygo-review-change
description: Independently review YugiohEditor changes for correctness, architecture violations, binary regressions, UI races, transaction failures, and missing tests. Use after implementation and before merge.
---

# YugiohEditor Independent Review

Review from the original requirement and repository invariants, not from the implementer's explanation.

## Review order

1. Read `/AGENTS.md`.
2. Read the original requirement/design if available.
3. Inspect the complete diff and identify all behavior-changing paths.
4. Reconstruct the relevant execution/data flow independently.
5. Run targeted tests and inspect whether they actually prove the requested behavior.
6. Look for regressions outside the happy path.

## Architecture checklist

Reject or flag:

- view/service imports of connection or codec code;
- repository-owned behavior moved into UI/service for convenience;
- codec filename/path knowledge;
- duplicate registries or sources of truth;
- direct filesystem access bypassing the owning connection/repository;
- new architecture layers without a distinct responsibility;
- hand-edited generated UI Python.

## Binary/container checklist

Verify when applicable:

- signedness, width, endianness, padding, alignment, terminators;
- compressed versus decompressed comparison layer;
- physical versus virtual resource behavior;
- manifest path spelling and source ownership;
- deterministic container order;
- compression-state preservation;
- byte-identical preservation of untouched resources;
- correct round-trip and malformed-input behavior.

For ordering changes, use adversarial path pairs such as a parent-level filename versus a similarly prefixed subfolder path; do not accept a test that only compares already-separated names.

## Card-domain checklist

Verify that code does not conflate:

- row/card slot/index;
- external/internal Card ID;
- reverse lookup (`card_intid`);
- localized sort rank (`card_sort`);
- executable active-card capacity.

Check image pair/catalog/manifest consistency, reserved indexed-text semantics, raw password representation, and language order/encoding when touched.

## UI/concurrency checklist

Look for:

- expensive preparation still running before the progress/busy UI can paint;
- widgets touched from worker threads;
- repeated Save/Pack/Suggest starting concurrent mutations;
- stale image/result races;
- cancellation that leaves executors/runners alive;
- close/teardown while work still owns UI state;
- maximize/window-state regressions caused by show/activate/reparent behavior.

## Executable checklist

For Pack-time executable changes, require exact baseline/profile validation, full original-instruction validation, deterministic formulas, non-overlapping writes, unchanged workspace/source executable, and explicit separation of static verification from runtime verification. Never accept a global replacement of the literal `1114`/`0x45A`.

## Failure and atomicity checklist

Inject or reason through failures before final commit/replace:

- encode error;
- invalid provider result;
- image write failure;
- manifest validation failure;
- executable profile/hash mismatch;
- cancellation;
- launch failure.

The prior valid project/bin state must survive where the architecture promises atomicity.

## Output

List findings in severity order with file/function evidence. Distinguish correctness defects from optional cleanup. If no blocking findings remain, state what was verified and what still requires manual/runtime testing.
