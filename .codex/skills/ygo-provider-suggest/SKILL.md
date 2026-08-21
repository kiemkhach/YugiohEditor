---
name: ygo-provider-suggest
description: Work on YugiohEditor card reference providers, Suggest/Bulk Suggest, normalization, image fetching, caches, bounded concurrency, cancellation, candidate selection, and provider merge semantics.
---

# YugiohEditor Provider and Suggest Pipeline

Use for official/third-party card reference clients, Card Suggest, Bulk Suggest, password/name image lookup, caching, and concurrency.

## Domain contract

Read the Suggest/provider rules in `/AGENTS.md` before editing. Preserve touched-field semantics: provider data may fill eligible missing writable fields but must not overwrite user-touched fields.

Candidate selection must distinguish:

- applicability to Monster versus Spell/Trap;
- missing versus numeric zero;
- writable versus touched/non-writable;
- scalar/text completeness versus image-only candidacy.

Raw `monster_type_code` takes precedence when present; normalized type is fallback. Unknown/new cards use the conservative Monster-capable field set.

## Password and image lookup

Use the canonical eight-character uppercase `card_pass` representation internally. `FFFFFFFF` is missing and must not cause a direct password request. Provider-specific URL normalization belongs in the provider client, not in card-domain storage.

Card Detail and Bulk Suggest share the same one-card image strategy: effective staged password first, then canonical English name after direct lookup failure. A placeholder `token_sl.bmp` remains an independent image candidate; never overwrite a valid existing image.

## Normalization and merge

Normalize provider names/descriptions before merge using existing helpers. Do not add provider-specific normalization to views. Preserve deterministic merge order and staged values.

## Concurrency

Bulk Suggest operates on the complete source model, not proxy-visible rows.

Preserve RAM-aware bounded concurrency documented in `AGENTS.md`:

- reserve memory before choosing worker count;
- cap workers;
- bound submitted futures;
- independent worker clones resolve data;
- coordinator commits in source-card order;
- completion order must not determine filenames or output.

Cancellation stops new submission, cancels queued work, lets already-running timeout-bounded calls finish, preserves committed results, and shuts down the executor cleanly before dialog teardown.

## Cache behavior

Reference/image caches are bounded and thread-safe. Per-key in-flight futures deduplicate identical concurrent lookups. Never hold a global cache lock across network I/O. Different keys may proceed concurrently. Failures are not cached as successful images, and in-flight entries are removed on every exit path.

## UI interaction

While Bulk Suggest is active, keep non-mutating filter/sort/selection/display-language/export interactions available where documented, but disable model-mutating actions. Do not close/destroy UI that owns an active coordinator/executor until cancellation/shutdown completes.

## Tests

Use provider client tests, reference-data service tests, card editing tests, and Card List UI tests. Include deterministic concurrency tests for:

- duplicate same-key lookups;
- different-key parallelism;
- owner error propagation to waiters;
- timeout/cancellation;
- source-order filename allocation despite reversed completion order;
- zero values and touched missing fields;
- image-only candidates.
