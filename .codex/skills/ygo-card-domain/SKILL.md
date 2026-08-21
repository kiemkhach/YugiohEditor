---
name: ygo-card-domain
description: Change YugiohEditor card tables, Card List/Card Detail data behavior, card IDs/indexes, card_sort/card_intid generation, card images/catalogs, indexed text, passwords, enable/filter rules, or card save transactions.
---

# YugiohEditor Card Domain

Use for card-data semantics and editing workflows. Combine with `ygo-pyside-ui` when the task also changes widgets/threading and with `ygo-executable-re` when active-card capacity is involved.

## Core identity model

Keep these namespaces separate:

- project/card row or active card slot/index;
- `card_id.bin` value (internal/external game ID as defined by the current format);
- `card_intid.bin` reverse lookup from Card ID to card index;
- `card_sort[lang].bin` localized inverse sort rank;
- executable active-card record count/capacity.

Never infer record count from the maximum Card ID. Index zero/dummy behavior must be handled explicitly per table.

## Source-of-truth rules

Read `/AGENTS.md` plus card sections of `/FILE_FORMATS.md` and `/DEVELOPMENT.md`. Inspect table handlers and `CardService` before adding fields or registries.

The composite `cards` table is assembled/split by `ProjectRepository`; physical tables remain registered through subfile rules. Do not duplicate language, index, or reserved-state columns merely because a UI needs context.

## Important formats

- `card_id.bin`: signed 16-bit little-endian; `FFFF` decodes to `-1`.
- `card_prop.bin`: four-byte semantic records; use direct inverse formulas and preserve all supported monster/type/attribute/level bits.
- `card_pass.bin`: four raw bytes represented as exactly eight uppercase hex characters in byte order; preserve leading zeroes; `FFFFFFFF` is missing.
- indexed descriptions/dialogs: row position is index; reserved state is not equivalent to empty text; row zero remains active; physical blob and virtual offset table must use matching layout.
- `card_intid.bin`: virtual reverse lookup; last duplicate wins; natural size is the smallest containing power of two; no editor-side fixed record cap.
- `card_sort[lang].bin`: virtual; index zero dummy; real rows receive inverse ranks; output size follows `len(card_id)`, not max Card ID.

## Card images

Treat large and mini images as one logical pair:

- large catalog: `card/list_card.txt`;
- mini catalog: `mini/list_card.txt`.

New physical images require both records with correct case-preserved paths and manifest metadata. Replacement reuses existing record/path/compression state and must not by itself reorder unrelated entries. Batch save validates all changes before mutation and commits through staging atomically.

## Card List and Card Detail

Preserve one retained source model/proxy and in-memory navigation/index maps. Filtering/sorting is a presentation concern; bulk operations such as Enable All and Bulk Suggest operate on the complete source model.

Save through the shared `CardService.save_card_changes()` transaction. Repeated Save must not start concurrent staging transactions. Card Detail and Card List must not drift into separate persistence semantics.

## Add Card

When adding a new card, derive new index/ID/image/catalog state in the owning service/repository rather than in widget code. If initialization is expensive, show Card Detail first in a processing state and perform preparation asynchronously; only enable editable fields after a valid draft is ready. Failure must leave the project unchanged.

## Tests

Use the existing card codec/editing/UI/sort/password/indexed-text/repository tests as anchors. Add cross-table assertions whenever a change can affect more than one physical/virtual card resource. Test rollback after a late image/catalog/manifest failure.
