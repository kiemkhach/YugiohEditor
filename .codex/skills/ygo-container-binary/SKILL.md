---
name: ygo-container-binary
description: Analyze or change Data.dat, Voice.dat, Region.dat, KCEJYUGI entry discovery/order, compression, virtual resources, codecs, Build/Pack reconstruction, or decompressed-file export in YugiohEditor.
---

# YugiohEditor Container and Binary Work

Use for `Data.dat`, `Voice.dat`, `Region.dat`, LZSS, `KCEJYUGI`, sub-file rules, virtual sidecars, raw binary resources, Build/Pack reconstruction, and Export Files behavior.

## Start here

Read `/AGENTS.md`, then the relevant `KCEJYUGI`, codec, subfile-rule, manifest, and project-pipeline sections of `/FILE_FORMATS.md`, `/ARCHITECTURE.md`, and `/DEVELOPMENT.md`.

Inspect:

- `yugioh_editor/common/subfile_rules_config.py`;
- game repository/factory/connection/codecs;
- project repository/connection;
- project service Pack/Create flows;
- `tests/test_container_and_deck.py`;
- `tests/test_dat_discovery_and_virtual.py`;
- `tests/test_subfile_rules.py`;
- `tests/test_project_pipeline.py`;
- `tests/test_manifest_validation.py`.

## Four representations

Never conflate:

1. stored bytes inside the packed container;
2. decompressed bytes for a container entry;
3. decoded structured/logical value;
4. project workspace file/table representation.

When a user requests export of game sub-files, establish which layer is required before coding. An export of re-encoded decompressed sub-files is not a CSV workspace export and is not the compressed payload copied from the original container.

## Rule ownership

- File matching/configuration belongs in `SUBFILE_RULE_CONFIGS`.
- Factory validates and materializes rules.
- Repository owns dependency resolution and whitelisted processing pipelines.
- Connection owns filesystem/container I/O and generic codec invocation.
- Codec owns byte transformation only.
- Virtual resources are regenerated through their configured encode pipeline; they are not empty placeholders and are not copied blindly from the source game.

## Ordering investigations and changes

Container order is semantic data stored in manifest records. Do not rely on OS/filesystem traversal order.

When comparing to an original log/container:

1. normalize only for comparison, not stored spelling;
2. preserve the game's path-separator semantics;
3. compare the full relative path globally unless evidence proves a hierarchical traversal rule;
4. locate the first mismatch;
5. test ambiguous prefix cases where `/` versus `\\` changes lexical ordering;
6. preserve source-specific contiguous order and leave unrelated sources untouched.

Do not encode an ordering hypothesis into `AGENTS.md` until the original container/log demonstrates it.

## Reconstruction invariants

For untouched entries preserve as required by the operation:

- relative path spelling/casing;
- source container;
- compressed flag;
- decompressed logical bytes;
- raw stored bytes when doing strict-preservation probes;
- manifest metadata;
- deterministic order.

Project Build/Pack may canonically re-encode changed entries, but tests must compare the correct representation.

## Virtual/indexed resources

Keep physical indexed-text blobs and virtual offset sidecars synchronized through explicit shared layout parameters. Keep `card_intid` and `card_sort` generation independent of unrelated maxima. Virtual encode starts from logical dependencies, not from a workspace file that does not exist.

## LZSS

Never remove or bypass compression to make tests pass. Test both `compress()` and `decompress()` and include round-trip fixtures for any LZSS change.

## Tests

Prefer byte-level fixtures and deterministic comparisons. For Build/Export changes, reconstruct both paths and assert equality of each exported decompressed sub-file that is expected to match the bytes used to construct the packed output.

Add negative tests for malformed headers, invalid rule configuration, dependency cycles, duplicate/non-contiguous orders, and failed staging where relevant.
