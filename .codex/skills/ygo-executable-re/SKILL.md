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

The final Step 8 contract keeps slot zero as the dummy, supports active slots
`1..4094`, and reserves slot/Card ID `0xFFF`. Valid Card IDs are
`0x000..0xFFE`; ID 4093 is ordinary. Extended builds relocate 4096 state WORDs
to `0x00C24000`, place the fixed 4096-byte high-byte snapshot at `0x00C26000`,
and place helpers at `0x00C27000` in new `.ygst` and `.ygsx` sections.

The original state block at `0x00A53CCC` remains only as the lower-2048
`system.dat` compatibility bridge. Save copies relocated slots `0..2047` to
that block before the original checksum/write call; load copies them back.
Slots `2048..4094` are not persistent.

The blanket historical alias-range rule is obsolete. Only the nine audited
aliases `2000`, `2014`, `2034`, `2037`, `2040`, `2063`, `2068`, `2387`, and
`2389` canonicalize by subtracting 2000, and only at audited consumers.

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
- counts below 1115 fail, count 1115 is byte-identical without a hash
  requirement, counts 1116..4095 require the exact supported stock source and
  install Step 8, and larger counts fail;
- validate the whole-file hash, exact PE32 baseline, complete original
  instructions/windows, section geometry, widths, bounds, and overlap;
- install only the declared 69 state relocations, two complete snapshot
  rewrites, fixed masks/hooks/helpers, 11 alias-consumer patches, and exactly
  17 formula-driven capacity sites;
- preserve the verified 12-bit masks at `0x0040262E`, `0x005B91D7`, and
  `0x005B9214`;
- after an optional native icon update, use current PE section mappings and
  reverify `.ygst`, `.ygsx`, helpers, hooks, masks, aliases, and dynamic values.

Do not add a parallel patch script/class/manifest count unless a new requirement cannot fit the existing pipeline and the architecture change is justified first.

## Verification language

Separate:

- static byte/disassembly verification;
- formula-driven extrapolation;
- runtime verification in Windows/gameplay.

Never claim runtime support for a count or patch that has only been generated and hash-checked.

Historical experimental builds runtime-tested the Step 8 architecture's
bridge, direct lookup, and high-slot semantics. Production helper fragments are
newly assembled against complete stock instructions and statically verified;
do not claim byte identity to removed experiments or actual production gameplay
verification. Native Windows resource verification is a separate result.

## Reference material

When available in the project research corpus, consult the latest versions of the Video Notes, Reverse Engineering Knowledge Base, Executable Modification Map, Card Slot Memory Model, System DAT Save Format, patch-point/capacity CSVs, `Assembly.txt`, `change card effect.txt`, `card_list.xlsx`, and the IDA database. Treat author/tutorial notes as leads until locally verified.

## Tests

For production patch changes require:

- baseline/hash/profile rejection tests;
- complete original-instruction mismatch tests;
- PE geometry, changed-region, helper, relocation, snapshot, hook, alias,
  invariant, and all-17-dynamic-site assertions;
- exact unchanged output at 1115;
- structural outputs at 1116, an intermediate count, and 4095;
- rejection before mutation at 1114 and 4096;
- native Windows icon-update re-verification when a supported fixture exists;
- explicit manual/runtime checklist for behavior not automated.
