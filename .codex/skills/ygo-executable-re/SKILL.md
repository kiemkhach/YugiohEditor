---
name: ygo-executable-re
description: Reverse engineer or safely modify Yu-Gi-Oh Power of Chaos Joey executable behavior for YugiohEditor, including card-capacity expansion, ASM patch points, effect logic, runtime state, system.dat relationships, and Pack-time executable profiles.
---

# Joey Executable Reverse Engineering and Patching

Use only for executable-level work. Do not use it to justify changing card-data formats unless executable evidence actually requires that change.

## Baseline contract

Current reverse-engineering knowledge is tied to the known `joey_pc.exe` baseline:

- PE32 x86 Windows GUI;
- ImageBase `0x00400000`;
- size `3,919,872` bytes;
- MD5 `75461d4ac813ecd1aa8aeca1ac526629`;
- SHA-256 `c5749eb934a1cf68d9236e44ff81e98b8aaee486b4f8ebd417440505d44ac1ea`.

Never apply an address from another Joey build/version without relocating and verifying it against the actual baseline. Cross-version wiki addresses are references, not local patch points.

## Evidence priority

Prefer, in order, direct disassembly/runtime/binary evidence over tutorial notes or inference. Keep labels such as:

`DISASM VERIFY > EXE VERIFY > RUNTIME VERIFY > verified local signature > VIDEO/IDA metadata/CARD LIST > AUTHOR NOTE/external-build wiki > INFERENCE > UNCERTAIN`.

Do not convert an author-note line/column notation into an exact byte offset without verifying the bytes/disassembly.

## Critical card-capacity model

Never equate literal `1114` with the card-count limit automatically.

Keep separate:

- active card slot/index;
- internal Card ID;
- effect/recipe/restriction table counts;
- runtime card-state storage capacity;
- UI/list clamps;
- save-format storage.

Verified project knowledge includes a `uint16`-sized per-slot runtime state region at base `0x00A53CCC` with structural storage for 2048 WORD records. Stock active consumers commonly stop at the range corresponding to slots `0..1114`, but structural storage does not by itself prove all 2048 slots are safely usable.

`system.dat` research indicates storage exists for all 2048 structural card-state WORDs; this does not prove every executable consumer supports them.

Some `0x45A`/1114 literals are named-card logic (for example Giant Germ-related effect branches) and are explicitly not capacity constants. Never globally replace them.

## Reverse-engineering procedure

1. Fingerprint the executable.
2. Identify the complete containing instruction/function and its callers/consumers.
3. Record file offset, VA/RVA, full original instruction bytes, operand width, semantic hypothesis, dependencies, and evidence level.
4. Search for related bounds by data-flow/structure, not only by repeated immediate value.
5. Distinguish inclusive maximum from exclusive upper bound and byte-end pointers.
6. Check adjacent globals/stack frames before increasing a range.
7. For table relocation/expansion, enumerate all consumers and references before patching.
8. Use runtime breakpoints/watchpoints to resolve uncertain semantics before marking a patch safe.

## Pack-time patch implementation

YugiohEditor currently owns supported capacity patching through the existing `*_pc.exe` binary rule and `patch_executable_card_capacity` pre-encode pipeline. Preserve this architecture:

- source and workspace executable bytes remain unchanged;
- only Pack staging is transformed;
- `card_record_count` comes from `len(ProjectRepository.get_table("card_ids"))`;
- profile data stays declarative;
- validate whole-file hash for expanded counts and every complete original instruction before mutation;
- derive immediates with integer formulas;
- validate widths, bounds, overlap, and conditional trailing `MOVSW` behavior;
- fail before mutation when the profile safety limit is exceeded.

Do not add a parallel patch script/class/manifest count unless a new requirement cannot fit the existing pipeline and the architecture change is justified first.

## Verification language

Separate:

- static byte/disassembly verification;
- formula-driven extrapolation;
- runtime verification in Windows/gameplay.

Never claim runtime support for a count or patch that has only been generated and hash-checked.

## Reference material

When available in the project research corpus, consult the latest versions of the Video Notes, Reverse Engineering Knowledge Base, Executable Modification Map, Card Slot Memory Model, System DAT Save Format, patch-point/capacity CSVs, `Assembly.txt`, `change card effect.txt`, `card_list.xlsx`, and the IDA database. Treat author/tutorial notes as leads until locally verified.

## Tests

For production patch changes require:

- baseline/hash/profile rejection tests;
- complete original-instruction mismatch tests;
- exact changed-byte assertions for known counts;
- unchanged output at legacy counts;
- odd/even copy-tail behavior;
- boundary maximum and maximum+1;
- deterministic output hash where a verified fixture exists;
- explicit manual/runtime checklist for behavior not automated.
