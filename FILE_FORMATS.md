# File Formats and Codec Logic

This document defines the file formats and packing rules implemented by the
editor. Game filenames are matched case-insensitively for discovery and codec
selection. Original archive paths and the selected casing of existing files are
retained in project metadata. Canonical lowercase names are used when a derived
resource needs a new name.

## Game folder

A source game folder contains these required logical files:

```text
Data.dat
Voice.dat
deck.ydc
Region.dat
```

It may also contain an executable matching `<name>_pc.exe`.

The logical names `Data.dat`, `Voice.dat`, and `Region.dat` are discovered
case-insensitively. For example, `DATA.DAT` and `data.dat` both identify
`Data.dat`. The selected source spelling is recorded in `project.json` and used
for packed output. If a filesystem exposes more than one spelling of the same
logical name, discovery selects deterministically and logs a warning.

### `Data.dat`

`Data.dat` is the main `KCEJYUGI` container. It commonly holds structured card
tables, localized text, card images, and other binary resources.

### `Voice.dat`

`Voice.dat` uses the same `KCEJYUGI` container format. Its entries are commonly
WAV audio and related resources, but entry classification is based on each
subfile rather than on the owning container.

### `Region.dat`

`Region.dat` is preserved as raw bytes. It is not parsed as a container and is
not required to have a `KCEJYUGI` signature.

### Version executable

Executable candidates match this pattern case-insensitively:

```text
^[A-Za-z0-9_-]+_pc\.exe$
```

Candidates are sorted by case-folded filename and the first one is selected.
The required version prefix comes from the project-creation UI and is stored
verbatim after surrounding whitespace is removed and validation succeeds.
There is no implicit prefix when the field or manifest property is missing.
The project stores executable bytes at `<prefix>/<prefix>_pc.exe` and packs
them as `bin/<prefix>_pc.exe`. The original game file and editable workspace
file always retain the source bytes. Only Pack staging receives the result of
the physical `*_pc.exe` rule's generic-binary `pre_encode` pipeline.

#### Step 8 card-capacity profile

Pack validates the physical `card_ids` table and derives one immutable capacity
plan before rebuilding containers:

```text
card_record_count       = len(card_id.bin records)
active_count            = card_record_count - 1
maximum_active_slot     = card_record_count - 1
exclusive_upper_bound   = card_record_count
active_state_end        = 0x00C24000 + card_record_count * 2
```

Slot zero is the dummy. The row count, maximum active slot, and Card ID are
separate values; no bound is derived from the maximum Card ID,
`card_intid.bin`, `card_sort`, the Cards UI, or image count. The plan is
operation metadata and is never persisted in the manifest.

The supported count contract is exact:

| Physical records | Result |
|---:|---|
| Below 1115 | Reject as an unsupported Joey topology |
| 1115 | Preserve the executable byte-for-byte |
| 1116..4095 | Install the Step 8 extended runtime |
| Above 4095 | Reject without truncating card data |

At most 4094 cards occupy active slots `1..4094`. Card IDs are `0..4094`
(`0x000..0xFFE`); `4095`/`0xFFF` remains the all-ones 12-bit invalid value and
is never an active slot or Card ID. At the maximum, the exclusive bound is
`0xFFF` and the active state ends at `0x00C25FFE`.

Extended counts require the exact supported stock executable:

| Field | Value |
|---|---:|
| Source size | 3,919,872 bytes / `0x3BD000` |
| ImageBase | `0x00400000` |
| Source sections | 8 |
| Source SHA-256 | `c5749eb934a1cf68d9236e44ff81e98b8aaee486b4f8ebd417440505d44ac1ea` |

The patch validates the DOS/PE32 headers, complete stock section layout, header
slack, alignments, whole-file hash, and every complete source instruction or
rewrite window before mutation. It installs two sections:

| Section | RVA | Virtual size | Raw data | Purpose |
|---|---:|---:|---:|---|
| `.ygst` | `0x824000` | `0x3000` | none | live state plus snapshot |
| `.ygsx` | `0x827000` | `0x1000` | `0x1000` NOP-filled bytes appended at `0x3BD000` | helper code and alias table |

With ImageBase applied, `.ygst` maps 4096 two-byte state slots at
`0x00C24000..0x00C25FFF` and a 4096-byte high-byte snapshot at
`0x00C26000..0x00C26FFF`. `.ygsx` begins at `0x00C27000`. Before an optional
icon update, the output has ten sections, `SizeOfImage=0x828000`, and size
`0x3BE000`.

The structural patch has these audited layers:

- 69 direct state-reference relocations: 59 state-base, four high-byte-base,
  five slot-1-base, and one structural-end reference;
- two complete fixed snapshot-loop rewrites, independent of active count;
- the `CARD_Prop` mask at `0x0040262E` changed from `0x7FF` to `0xFFF`;
- save/load bridge hooks, direct 12-bit Card ID lookup, declarative helper
  fragments, and 11 audited legacy-alias consumer patches;
- exactly 17 count-dependent sites.

The supported stock snapshot windows are complete instruction ranges. Copy is
rewritten at `0x0047DCEA..0x0047DCFF`, with the successor at `0x0047DD00`;
restore is rewritten at `0x0047DD71..0x0047DD8F`, with the successor at
`0x0047DD90`.

The 17 dynamic sites are:

| Derived value | Virtual addresses |
|---|---|
| `maximum_active_slot` | `0x00402315`, `0x0046E5C7`, `0x0046E5CE`, `0x00476339`, `0x00476340` |
| `exclusive_upper_bound` | `0x0043A9B2`, `0x0043AA9D`, `0x00445703`, `0x0047DBFD`, `0x0046E5B3`, `0x00476327` |
| `active_state_end` | `0x00463F18`, `0x00463F8B`, `0x0047DA75`, `0x0047DC7D`, `0x005BED0D`, `0x005BEE16` |

The direct lookup accepts IDs through `0xFFE`, rejects `0xFFF` and above, and
indexes `CARD_IntID` directly. Only these stock aliases canonicalize at the 11
audited consumers:

```text
2000->0  2014->14  2034->34  2037->37  2040->40
2063->63  2068->68  2387->387  2389->389
```

These nine IDs remain valid existing Card IDs, but a currently free alias is
not assigned to an unrelated new card. ID 4093 is ordinary and is not reserved.
Other occurrences of the literal 2000 are not capacity patches.

The compatibility bridge copies relocated slots `0..2047` to the original
legacy state block before the checksum/write routine and copies them back after
load. Slots `2048..4094` are intentionally not persisted by `system.dat`.

Historical experimental builds runtime-verified the Step 8 bridge, lookup, and
4094-card architecture semantics. The production helper fragments were newly
assembled against complete stock instructions and are covered by static byte,
disassembly, PE-layout, and invariant tests; they are not claimed to be a
byte-identical retained experimental artifact. Native Windows icon updates are
followed by the same structural verifier because an icon changes the whole-file
hash. Actual gameplay/runtime verification of the production-packed executable
remains manual.

## `KCEJYUGI` container

The container format applies to `Data.dat` and `Voice.dat`.

### Main header

| Offset | Size | Type | Meaning |
|---:|---:|---|---|
| `0x00` | 8 | ASCII | Signature `KCEJYUGI` |
| `0x08` | 4 | `u32le` | Entry count |
| `0x0C` | `count × 268` | entry table | Ordered entry headers |

The first possible payload offset is:

```text
12 + entry_count × 268
```

No alignment, checksum, footer, or separate filename table is used by the
implemented codec.

### Entry header

Each entry header is 268 bytes:

| Relative offset | Size | Type | Meaning |
|---:|---:|---|---|
| `0x000` | 256 | byte array | Nibble-swapped, NUL-terminated relative path |
| `0x100` | 4 | `u32le` | Absolute payload offset from container start |
| `0x104` | 4 | `u32le` | Decompressed size |
| `0x108` | 4 | `u32le` | Stored size |

Payloads follow the complete entry table. Packing sorts entries by their
manifest `order`, recalculates every absolute offset, then writes payloads in
the same order.

### Relative-path encoding

Every byte in the 256-byte path field swaps its high and low nibbles:

```text
swap(value) = ((value & 0x0F) << 4) | ((value & 0xF0) >> 4)
```

The operation is its own inverse. Reading performs these steps:

1. nibble-swap all 256 bytes;
2. stop at the first NUL;
3. decode strictly as UTF-8, falling back to CP932;
4. retain the decoded relative path.

Writing normalizes separators to backslashes, encodes the path, NUL-pads the
field to 256 bytes, and nibble-swaps the complete field. An encoded path may use
at most 255 bytes.

Codec matching is case-insensitive and separator-insensitive. The entry's
stored spelling is not lowercased when an existing archive is repacked.

### Offset, size, and compression state

An entry has no explicit compression flag:

```text
stored_size == full_size  -> raw payload
stored_size != full_size  -> LZSS payload
```

For a compressed entry, the codec reads exactly `stored_size` bytes at
`payload_offset` and requires decompression to produce exactly `full_size`
bytes.

### Container validation

Decoding rejects a container when:

- it is smaller than 12 bytes;
- its signature is not `KCEJYUGI`;
- the entry table extends beyond the file;
- a relative path is empty, unsafe, duplicated, or cannot be decoded;
- two paths differ only by case or separators;
- an entry order is negative or duplicated during packing;
- an entry payload starts inside the header table;
- an entry payload range extends beyond the file;
- payload ranges overlap;
- LZSS output does not match the declared decompressed size.

Extracted paths are also resolved beneath the selected project or game root so
that a resource cannot escape through `..` traversal.

## LZSS

The container uses an Okumura-style LZSS stream. Both compression and
decompression are required for project round trips.

### Parameters

| Parameter | Value |
|---|---:|
| Ring-buffer size | 4096 bytes |
| Maximum match length | 18 bytes |
| Minimum encoded match | 3 bytes |
| Match threshold | 2 |
| Initial cursor | `4096 - 18 = 0xFEE` |
| Initial ring contents | zero-filled |
| Tokens per flag group | at most 8 |
| Flag-bit order | least significant to most significant |

### Token groups

Each group begins with one flag byte. Up to eight tokens follow:

- flag bit `1`: one literal byte;
- flag bit `0`: one two-byte back-reference.

For back-reference bytes `b0` and `b1`:

```text
offset = b0 | ((b1 & 0xF0) << 4)
length = (b1 & 0x0F) + 3
```

The inverse encoding is:

```text
b0 = offset & 0xFF
b1 = ((offset >> 4) & 0xF0) | (length - 3)
```

### Decompression

1. Create a zero-filled 4096-byte ring buffer.
2. Set the write cursor to `0xFEE`.
3. Read a flag byte.
4. Process bits 0 through 7 while input remains.
5. Copy a literal directly to output and the current ring cursor.
6. For a reference, copy `length` bytes beginning at `offset`, wrapping at
   4096 bytes.
7. Write each copied byte back through the current cursor before copying the
   next byte. This preserves overlapping-reference behavior.
8. Require the declared output size when a container supplies one.

### Compression

The compressor maintains the same ring buffer plus an 18-byte look-ahead
region. Candidate matches are indexed by binary search trees keyed by the first
byte and compared lexicographically.

For each input position:

1. find the longest match available in the active window;
2. emit a literal for lengths 1 and 2;
3. emit a back-reference for lengths 3 through 18;
4. set flag bits for literal tokens only;
5. flush after eight tokens;
6. delete expired tree nodes and insert new look-ahead positions as the window
   advances.

### Compression policy

The container writer exposes four policies:

| Policy | Behavior |
|---|---|
| `preserve` | Recompress entries marked compressed; store other entries raw |
| `auto` | Use LZSS only when its output is smaller |
| `always` | Store LZSS output |
| `never` | Store raw payload |

Policy selection occurs before compression. In particular, `preserve` does not
invoke LZSS for an entry whose recorded compression state is false, and `never`
does not invoke LZSS for any entry. This avoids discarded compression work for
large raw BMP, WAV, executable, and unknown-binary payloads.

Project packing uses `preserve`. The logical subfile codec always receives the
decompressed payload; compression is only the outer storage layer.

## `deck.ydc`

The deck layout is:

| Offset | Size | Meaning |
|---:|---:|---|
| `0x00` | 8 | Preserved header bytes |
| `0x08` | 1 | Card-ID count |
| `0x09` | `count × 2` | Little-endian unsigned card IDs |

At most 255 card IDs can be encoded. The project stores the list as a
one-column CSV-backed table and keeps the game file itself under the logical
`deck` root.

## Language prefixes

`LANGUAGE_ENCODINGS` is the sole ordered language registry. Its canonical
prefix sequence is:

```text
eng
fra
jpn
spa
ita
ger
```

| Prefix | Language | Encoding |
|---|---|---|
| `eng` | English | CP1252 |
| `fra` | French | CP1252 |
| `jpn` | Japanese | CP932 |
| `spa` | Spanish | CP1252 |
| `ita` | Italian | CP1252 |
| `ger` | German | CP1252 |

Spanish uses the prefix `spa`. The same canonical collection builds every
localized filename regular expression, codec rule, Card List column, and
manifest validation rule.

Localized filename matching is case-insensitive. These names therefore resolve
to the same Spanish codec:

```text
card_namespa.bin
CARD_NAMESPA.BIN
Card_NameSpa.bin
```

Generated Spanish resource names use canonical lowercase:

```text
card_namespa.bin
card_descspa.bin
card_indxspa.bin
card_sortspa.bin
```

A manifest language value outside the canonical set is rejected. A
noncanonical Spanish card-resource suffix is rejected with an error that
identifies the offending resource path and instructs the user to use `spa`.
Loading never silently renames a manifest or workspace resource.

Free text without a detected language uses UTF-8.

## Subfile classification

The only subfile configuration source is
`yugioh_editor/common/subfile_rules_config.py`:

```text
SUBFILE_RULE_CONFIGS dictionaries
-> repository-layer SubfileRuleFactory
-> runtime SubfileRule objects
-> GameRepository rule pipeline
-> GameFolderConnection
-> codecs
```

The config module contains strings, bytes, numbers, booleans, tuples, and
dictionaries only. It does not import or instantiate `SubfileRule`, compile
regular expressions, or create codecs. The factory validates configuration and
compiles `*` and `[lang]` patterns. Matching is case-insensitive and uses the
complete basename. The repository scans rules from last to first, so a later
specific rule overrides an earlier fallback.

`codec_name` is always a generic operation such as `binary`, `integer_list`, or
`offset_string_table`; it is never a card-table name. A rule's physical or
virtual state, dependency templates, and pipeline steps are in the same
dictionary. There is no separate codec, virtual-resource, or generator
registry, and `encode_params` contains codec parameters only.

`CODEC_OPERATIONS` is the canonical set for factory validation and both
connection operation registries. Rules can declare
`pre_decode`, `post_decode`, `pre_encode`, and `post_encode`. Each pipeline is
an ordered list or tuple of plain dictionaries with a `method_name` and a
mapping of keyword `params`. The factory permits only whitelisted method names
without importing `GameRepository`; the repository validates the static method
implementations. Nested rule and step parameters are recursively frozen.
Per-operation contexts receive independent mutable copies.
Description and sort behavior is exercised through the archive/rule pipeline;
there are no filename-specific compatibility helpers that bypass it.

The output of one method is the input of the next. Physical decoding runs
pre-decode before the connection codec and post-decode afterward. Physical
encoding runs pre-encode before the connection codec and post-encode
afterward. Virtual encoding runs:

```text
None
-> pre_encode loads project dependencies
-> pre_encode constructs and pads logical values
-> connection codec encode
-> post_encode
-> bytes
```

The current pipelines perform DataFrame/list/record conversion, pack value
mapping, integer-column casting, derived catalog fields, regex compilation, and
description offset dependency injection. Static pipeline methods also construct
string offsets, sort ranks, and reverse lookups for virtual resources. A new
physical logical table adds `table_name` and, for `[lang]` patterns,
`table_parameters = ("language",)`. Its project handler is then registered
from the rule without a filename-specific reader or writer. New binary
layouts, logical construction methods, composite tables, or editors still
require the corresponding implementation.

Pipeline failures identify resource, pattern, phase, step index, and method,
while retaining the original exception as the cause.

The game repository owns table meaning and resolves dependencies. The
game-folder connection dispatches generic operations and invokes codecs without
knowing filenames, languages, logical tables, or virtual dependencies.

The canonical structured rules are:

| Filename pattern | Representation |
|---|---|
| `*_pc.exe` | physical binary with Pack-time capacity pre-encode |
| `card_id.bin` | signed 16-bit table (`-1` is the card-back ID) |
| `card_intid.bin` | generated reverse-ID table |
| `card_pack.bin` | pack-mask table |
| `card_pass.bin` | fixed raw four-byte uppercase-hex table |
| `card_name<language>.bin` | fixed-string table |
| `card_desc<language>.bin` | description table |
| `card_indx<language>.bin` | generated offset table |
| `dlg_text<language>.bin` | dialog text table |
| `dlg_indx<language>.bin` | generated dialog offset table |
| `card_sort<language>.bin` | generated rank table |
| `card_prop.bin` | four-byte property table |
| `card/list_card.txt` | large-image catalog table |
| `mini/list_card.txt` | mini-image catalog table |

All other binary formats, including YGA resources and unknown `.bin` files,
remain raw bytes. The executable workspace representation is also raw binary;
its configured transformation exists only in the Pack encode pipeline.

### Generic structured operations

Connections expose reusable operations rather than filename-specific methods:

```text
integer list:
  data, byte_width, signed, byte_order

fixed hex list:
  data or values, byte_width

fixed string list:
  data, record_size, encoding, terminator

offset string table:
  data, offsets, encoding, terminator, alignment, minimum padding

terminated string list:
  data or values, encoding, terminator

record table:
  data, record_size, row decoder or row encoder

regex record table:
  data, caller-provided pattern or output template, encoding

text:
  data or value, encoding, error policy
```

For descriptions and dialogs, the repository resolves the language-matched
text/index pair and passes the payload and offsets to the same generic
offset-string operation. Neither the connection nor the codec knows the
logical table name. Payload encoding and virtual sidecar generation use the
same immutable indexed-string layout builder, so encoded length, terminator,
alignment, empty-row reservation, and offsets cannot use different algorithms.
`CARD_DESCRIPTION_TEXT_LAYOUT` and `DIALOG_TEXT_LAYOUT` in
`subfile_rules_config.py` are the plain-data sources of truth. Each physical
decode, physical encode, and virtual offset-generator location receives a new
dictionary expanded from its profile. Rule construction rejects a missing
layout field or any decode/encode/generator mismatch before Pack can start.

Repository filename rules match a complete filename or path segment. A name
such as `customcard_id.bin` does not match `card_id.bin` and therefore remains
raw binary. The repository also owns the `list_card.txt` regular expression and
output template; the regex codec only applies caller-provided syntax.

## Card-table relationships

The row position is the card index shared by:

```text
card_id.bin
card_pack.bin
card_pass.bin
card_prop.bin
card_name<language>.bin
card_desc<language>.bin
dlg_text<language>.bin
```

Derived tables use these relationships:

```text
if card_id[index] != -1:
    card_intid[card_id[index]] = index
card_indx<language>[index] = description byte offset
dlg_indx<language>[index] = dialog byte offset
card_sort<language>[index] = localized name rank
```

The physical card record count is the number of two-byte records in
`card_id.bin`. Slot zero is the dummy, so a valid Joey project's active card
count is one less than that row count. `card_intid.bin` has the complete
dynamically generated power-of-two length. `card_sort<language>.bin` instead
uses the next power of two containing the physical Card ID row count. Their
physical lengths do not define the source record count.

## Structured card subfiles

### `card_id.bin`

`card_id.bin` is a contiguous little-endian signed 16-bit list:

```text
record_size = 2
record_count = file_size / 2
```

The value at position `i` is the external card ID for card index `i`. The file
size must be divisible by two. `FF FF` represents `-1`, the card-back ID:

```text
FF FF -> -1
-1 -> FF FF
```

The signed-16-bit file format is generic. The supported Joey Pack policy is
narrower: row zero must be `-1`; rows `1..N-1` must contain unique integer Card
IDs in `0..4094`; and no active row may be negative or use the reserved value
`4095`/`0xFFF`.

### `card_intid.bin`

`card_intid.bin` is a virtual reverse lookup from card ID to card index:

```text
record = unsigned 16-bit little-endian
missing slot = 0
card_intid[card_id] = card_index
```

`card_id[card_index]` stores the external card ID. Packing ignores negative
IDs, finds the maximum non-negative ID, and creates the smallest power-of-two
table that contains `max_id + 1` slots:

```text
natural_count = 1 << max_id.bit_length()
```

All slots start at zero. Duplicate IDs use the last card index because later
rows overwrite earlier mappings. If no non-negative ID exists, generation
fails. The generic integer codec derives record count from byte length and
rejects odd byte lengths. Packing passes the entire valid natural sequence to
that codec as unsigned 16-bit little-endian records. The codec continues to
reject values outside the representable record domain, but the editor does not
apply a cap to the number of records.

Generating a longer `card_intid.bin` establishes only the editor's data output
behavior. The supported Joey executable uses the independent, SHA-identified
Pack profile above; this does not imply compatibility for other executables and
does not change the `card_intid.bin` format.

For the Step 8 boundary, Card ID `4094` produces a 4096-record reverse lookup,
and `card_intid[4094]` contains that card's high slot. This is a Joey runtime
namespace check, not a new fixed cap in the reusable generator.

### `card_pack.bin`

Each record is one little-endian unsigned 16-bit pack mask:

| Value | Project label |
|---:|---|
| 0 | `disabled` |
| 1 | `yugi` |
| 2 | `kaiba` |
| 3 | `yugi_kaiba` |
| 4 | `joey` |
| 5 | `yugi_joey` |
| 6 | `kaiba_joey` |
| 7 | `yugi_kaiba_joey` |

The low three bits represent Yugi, Kaiba, and Joey respectively. Packing rejects
an unsupported project label.

### `card_pass.bin`

Each passcode is one opaque four-byte record:

```text
record_size = 4
record_count = file_size / 4
project_value = raw_record.hex().upper()
```

The current project value is exactly eight uppercase hexadecimal characters in
the same order as the bytes on disk. Packing validates exact width and hex
syntax, then writes each pair of characters as one byte. It performs no integer
conversion and no byte reversal, so leading zeros are significant:

```text
12 34 56 78 <-> "12345678"
00 00 00 01 <-> "00000001"
FF FF FF FF <-> "FFFFFFFF"
```

`FFFFFFFF` is the canonical missing-password sentinel. It is not stored as an
empty cell, decimal `4294967295`, or signed `-1`. Semantic input normalization
may trim outer whitespace and uppercase a valid eight-digit value before
persistence; the generic fixed-hex codec itself accepts only the canonical
uppercase representation when encoding.

The canonical eight-character raw-order string is also the domain value used
by card drafts, provider matching, validation, logs, CSV, and password-image
cache keys. YGO Vietnam direct-image addressing is the only projection that
removes leading zeroes:

```text
url_segment = normalized_password.lstrip("0") or "0"
```

| Canonical password/cache value | CDN path segment |
|---|---|
| `08783685` | `8783685` |
| `00001234` | `1234` |
| `12345678` | `12345678` |
| `00000000` | `0` |
| `FFFFFFFF` | no direct URL |

This transformation occurs only while constructing
`https://cdn.ygovietnam.com/storage/Card/<segment>.jpg`. It does not parse the
value as an integer, reverse bytes, shorten the persisted value, or change the
cache key (`password:08783685`). The missing sentinel and malformed values are
rejected before HTTP is attempted.

Project schema v4 migrates the previous numeric CSV representation atomically
and exactly once. The legacy value was decoded as an unsigned little-endian
32-bit integer, so migration reconstructs `value.to_bytes(4, "little")` and
stores the resulting uppercase hex. For example, decimal `2018915346` becomes
`12345678`, while `4294967295` becomes `FFFFFFFF`. A v2 project receives the
property migration and passcode migration in the same staging transaction; a
v3 project receives only the passcode migration. Invalid input discards the
staging update and leaves the original project untouched.

### `card_name<language>.bin`

Each localized card name occupies exactly 64 bytes:

1. read one 64-byte record;
2. stop at the first NUL;
3. decode with the filename's language encoding;
4. retain one project row per record.

Packing encodes strictly. A name may use at most 63 encoded bytes so at least
one NUL byte remains. The remainder of the record is NUL padding.

Before Card Save validates or stages localized names and descriptions,
`CardService` creates a target-encoding-normalized draft projection. A
character that already encodes strictly is preserved. An unencodable character
changes only when that exact target encoding has an approved fallback; the
current table maps CP1252 `U+25CF BLACK CIRCLE` (`●`) to `U+2022 BULLET` (`•`).
CP932 preserves `●` because it encodes directly as `81 9C`. Unmapped
characters remain unchanged and therefore still fail strict validation; the
codecs never use replacement or ignore behavior.

The Official Card Database canonical Japanese name for CID `21966`,
`熒焅聖 アレクゥス`, demonstrates a verified legacy CP932 limitation: `熒`
(`U+7192`) and `焅` (`U+7105`) are not representable by CP932, and no safe
CP932 equivalent has been established. Card Save therefore rejects this value
strictly instead of altering it. Its centralized diagnostic identifies the
card index and Card ID plus the field, language, encoding, first offending
character position, character, Unicode code point, and Unicode name.

Spanish names use `card_namespa.bin`.

### `card_desc<language>.bin`

**Confirmed by the specification and regression tests:** the description file
uses the common indexed-text format. Its logical table and workspace CSV retain
`text` and `is_reserved`. Row position is the string index, while language is
resource-level context resolved from the localized filename. The table editor
projects only `text` and merges edits into the full frame. Record boundaries
come from `card_indx<language>.bin`, not from heuristic splitting.

Index zero at offset zero is always active, including when its logical text is
empty. At a later index, offset zero decodes as `is_reserved=True` and creates
no physical record. A later nonzero offset may decode an empty active record
with `is_reserved=False`; packing preserves its physical NUL record. Empty
text does not determine reserved state. Reserved offsets are skipped while
finding the next active boundary. Active nonzero offsets are nondecreasing and
within the blob.

```text
start = offsets[i]
end = next non-reserved active offset or payload_size
```

Packing each active description performs:

```text
offsets.append(current_payload_size)
encoded = description encoded strictly
odd encoded length  -> append three NULs
even encoded length -> append two NULs
```

Canonical card-description output reserves at least two padding bytes after the
encoded payload and pads to a two-byte boundary. Each pointer-bounded region
therefore has even total size. Input decoding requires an aligned start, a NUL
terminator inside the region, and exactly the region length implied by this
padding profile. Padding-byte contents are opaque on input because the original
French data contains one nonzero byte in a valid padding slot. Payload decoding
remains strict, never crosses the next pointer, and does not use an unlimited
NUL strip. Every active empty row retains a physical empty record; explicitly
reserved rows do not. Duplicate active strings are not deduplicated.

This layout was verified against the original 2004 `Data.dat`. English
description record 1 starts at offset 2 and ends at offset 138. Its 133-byte
CP1252 payload is followed by `00 00 00`; record 0 is the empty active record
`00 00`. A separately packed archive has the same record-1 boundary and
padding. These raw vectors, not an encoder-generated fixture, are retained in
the codec and repository-pipeline regression tests.

The original French record 369 starts at 45736, terminates at absolute offset
45990, and has byte `75` in its final padding slot at 45991. Record 370 starts
at the authoritative pointer 45992 with `44 E9` (`Dé`). Starting it at 45991
would incorrectly produce `uDé`. The padding byte is therefore not part of
either logical string. It is not retained in the editable table; Pack
canonicalizes that slot to `00`.

Spanish descriptions use `card_descspa.bin`.

### `card_indx<language>.bin`

The index is a little-endian unsigned 32-bit offset list:

```text
capacity = max(2048, find_next_power_of_two(logical_count))
encoded_size = capacity * 4 bytes

logical_count <= 2048  -> 2048 records / 8192 bytes
2049..4096             -> 4096 records / 16384 bytes
4097..8192             -> 8192 records / 32768 bytes
```

The active offsets are generated from the same `text`/`is_reserved` logical
rows as the related description blob. Reserved rows after index zero remain
zero and generate no blob bytes; active empty rows receive their actual nonzero
offset. The remaining capacity entries are zero-padded. Packing chooses the
larger of the legacy 2048-record minimum and the smallest power of two that
contains every generated offset. The editor does not impose a replacement
record-count cap on this DATA-side table; the unsigned 32-bit integer codec
continues to validate every generated offset value.

The 2048-record band preserves the complete legacy sequence and its 8192-byte
encoding. Larger power-of-two bands describe DATA-side representability only.
They do not change the separate executable policy: the Step 8 profile permits
at most 4095 physical card records and Card IDs through `0xFFE`, and generated
DATA alone does not establish Windows runtime compatibility.

The dependency is:

```text
card_descspa.bin
    -> generate card_indxspa.bin
```

The index is virtual and is not written to the editable workspace.

### `dlg_text<language>.bin` and `dlg_indx<language>.bin`

**Confirmed by raw data in all six languages:** dialog text uses the same
pointer-bounded indexed-string codec and offset generator as card descriptions,
but its canonical output profile is `minimum_padding=1`, `alignment=2`.
Odd-length dialog payloads therefore have one NUL byte; even-length payloads
have two. All 611 active records in each original dialog table match this
profile.
`dlg_text<language>.bin` is a physical editable table retaining `text` and
`is_reserved`, while its default editor exposes only `text`. Row position
supplies the dialog string index, filename context supplies the language, and
explicit state distinguishes active empty records from reserved rows.
`dlg_indx<language>.bin` is a virtual unsigned 32-bit little-endian table
derived from the matching language's dialog table. Its entry count comes from
the actual index table; no wiki-derived capacity is hard-coded.

Control text such as `@0`, `@2`, `@3`, `%s`, and `%d` is preserved as ordinary
text. English, French, German, Spanish, and Italian use CP1252; Japanese uses
CP932. Encoding is strict.

### `card_sort<language>.bin`

The sidecar is rank-per-card, not a list of card indexes sorted by name.

The sort sidecar contains little-endian unsigned 16-bit ranks:

```text
card index -> localized alphabetical rank
record_count = find_next_power_of_two(len(card_id))
```

Index zero is a dummy and always has rank zero. Every real row `1..N-1`
participates, and its ranks form the inverse permutation `0..N-2`. The audited
key combines the localized name and Card ID so duplicate names remain
deterministic. Ranks are padded with zeros to the next power of two containing
the Card ID row count; the generator does not load `card_intid.bin`, and the
maximum Card ID does not determine capacity.

The dependency is:

```text
card_namespa.bin -----> card_sortspa.bin
card_id.bin ----------> card_sortspa.bin
```

The sort table is virtual and is not written to the editable workspace.

Current mechanics are audited. NFKD/case-fold intent is inferred. Exact
Japanese, punctuation, and accent collation remains unresolved; remaining
language-specific binary differences must be reported, not hidden with
unsupported normalization.

### `card_prop.bin`

Each card property is one unsigned 32-bit little-endian bitfield:

```text
bits  0..8   defense / 10
bits  9..17  attack / 10
bits 18..19  monster category (classes 1..20)
bits 17..19  spell/trap subtype (non-monsters)
bits 20..24  card class
bits 25..28  level
bits 29..31  attribute
```

Classes `1..20` are monsters. Their category is `0` normal, `1` effect,
`2` fusion, or `3` ritual. Class `21` is Trap, `22` is Spell, `23` is
non-game, and `24` is divine. Classes `0` and `25..31` retain a blank semantic
label so their numeric identity survives round trips.

```text
attribute: 0 blank, 1 light, 2 dark, 3 water, 4 fire, 5 earth,
           6 wind, 7 divine
spell/trap subtype: 0 normal, 1 counter, 2 field, 3 equip,
                    4 continuous, 5 quick_play, 6 ritual, 7 blank
```

Non-monsters always serialize attack, defense, and level as zero. The derived
`requires_two_tributes` flag is true only for monsters whose level is at least
8. Encoding uses the direct inverse bit formulas and never searches for an
equivalent nibble representation or copies a raw baseline record.

The project table columns are:

```text
attack
defense
monster_type_code
monster_type
card_category_code
card_category
attribute_code
attribute
level
requires_two_tributes
```

Encoding uses direct inverse formulas rather than searching for a first
matching nibble representation. Attack and defense are representable in steps
of ten from 0 through 5110; level is in `0..15`. Composite Card List edits use
the semantic labels and regenerate their numeric codes before persistence.
When schema v2 is opened, this legacy property conversion runs atomically with
the passcode conversion and commits current schema v4. Legacy even attribute
codes are divided by two; legacy monster category codes `0/4/8/12` are divided
by four. For Spell/Trap rows the old subtype is recovered with
`old_low_bits = (old_attack // 1280) & 3` and
`subtype = (old_card_category_code | old_low_bits) >> 1`, after which
non-monster attack, defense, and level are reset to zero. Schema v3 already has
the current property representation and therefore migrates only passcodes.

### `list_card.txt`

**Confirmed:** there are two physical resources with the same regex codec and
schema:

```text
card/list_card.txt -> large image catalog
mini/list_card.txt -> mini image catalog
```

Repository and service APIs accept the semantic selector `large` or `mini`.
Resolution is case-insensitive but uses the complete path, never the shared
basename. Writing one variant does not write the other.

The catalog uses UTF-8 and contains records in this form:

```text
//\t<Card name>\r\n
//\t<Card index as 4 digits>:[<Card ID as 4 digits>][ optional " Back"]\r\n
<Image filename>\r\n
\r\n
```

For example:

```text
//	Armored Lizard
//	0003:[0050]
MRD005.bmp

```

The project table contains:

```text
name
index
card_id
image_name
note
```

The optional `Back` marker is stored in `note` and restored when encoded.
Reading accepts CRLF or LF. Writing uses CRLF. The card index must be active and
the recorded card ID must equal `card_id[index]`.

When the composite `cards` table is saved, catalog names have two sources. A
normal nonnegative Card ID uses the edited English logical name. A negative-ID
sentinel/card-back row keeps the existing source catalog name for that variant,
such as `Generated by Getallcard.exe`, even when its logical English name is
empty. The serialized catalog Card ID behavior is unchanged.

Separate full-size and mini-image catalogs may use the same record format. An
image filename is relative to its corresponding `card/` or `mini/` folder.
Card Suggest replaces only the `token_sl.bmp` placeholder. The placeholder is
an image candidate when a canonical non-`FFFFFFFF` password or query name is
available, even when every text/scalar field is complete; a valid existing image
is never overwritten. Card Detail and Card List Bulk Suggest share the same
password-first, canonical-English-name-fallback lookup and conversion pipeline.
Direct password success suppresses name fallback; direct failure permits it
once. A successful replacement stages a matching large/mini pair.

Bulk Suggest applies semantic completeness rather than one scalar checklist to
all rows. Monster classes `1..20` may require level, ATK, DEF, and attribute;
Trap class `21` and Spell class `22` do not. Localized text, password, type,
category/subtype, and image remain applicable to known card kinds. Raw type code
takes precedence over the normalized label, and unknown/new cards use a
conservative Monster-capable set. Zero-valued statistics are present values.
Only fields that are applicable, missing, and untouched can trigger a text or
scalar request. Candidate selection always covers the complete source model,
not the currently visible proxy rows.

The I/O stage is bounded by candidate count, available RAM, and a hard cap of
8, with a 64 MiB per-worker estimate, a `max(512 MiB, 25%)` reserve, and a
fallback cap of 4 when available RAM is unknown. No more than twice the selected
worker count is submitted. Independent workers return results for source
positions; a serial coordinator applies results and reserves image names
case-insensitively in source order. Cancellation stops submission and queued
work while retaining already coordinated rows and cleaning up the executor.
Concurrent identical reference or image keys share one in-flight request;
different keys remain concurrent, and waiters use a finite timeout. Card List
disables model mutations for the run, defers close until cancellation cleanup,
and serializes Save requests against its retained repository.

Saving from Card Detail or Card List uses one common service transaction and
one repository image batch. The batch inventories manifest, catalogs, and
workspace once; validates all names and complete pairs before mutation; writes
both image variants; updates both catalogs; and gives final manifest ownership
to the staging save. `project.json`, catalogs, logical card tables, and physical
images commit together or not at all.

## Media, text, and raw resources

### BMP and card images

Files under `card/` and `mini/`, and other known image extensions, are stored as
complete image-file bytes without a game-specific wrapper. Common names include
`card_ura.bmp` and set-based names such as `MRD005.bmp`.

Image paths and extensions are matched case-insensitively. Existing relative
paths retain their archive spelling. The UI may decode an image for preview or
convert a replacement to BMP, but workspace and container storage remain
binary.

Each newly added named card image creates two physical manifest records using
the case-preserved spelling of the logical `Data.dat` source:

| Field | Large record | Mini record |
|---|---|---|
| `relative_path` | `card/<name>` | `mini/<name>` |
| `workspace_path` | `data/card/<name>` | `data/mini/<name>` |
| `file_kind` | `image` | `image` |
| `storage_format` | `binary` | `binary` |
| `language` | `null` | `null` |
| `generated_on_pack` | `false` | `false` |
| `virtual` | `false` | `false` |
| `compressed` | `false` | `false` |

Under the Pack `preserve` policy, these new entries are therefore stored raw
and LZSS is not invoked for them. Replacing an existing image retains its
manifest record, path, and original compression state. A replacement-only Save
does not replan order; when the same Save also adds physical files, the existing
record participates in the complete `Data.dat` alphabetical replan below.

After adding one or more new physical files, record order is replanned as
follows:

1. Resolve the selected source's actual case-preserved `Data.dat` spelling and
   match `source_file` case-insensitively.
2. Combine every existing record for that source with all new large and mini
   image records.
3. Sort the entire sequence lexicographically by the normalized,
   case-insensitive complete relative path using Windows separators:
   `"\\".join(normalize_project_path(record.relative_path).parts).casefold()`.
4. Use normalization only for comparison. Preserve the stored path spelling,
   casing, and existing separator policy; do not use recursive files-first,
   basename-only, or natural numeric sorting.
5. Renumber the sorted `Data.dat` sequence contiguously from zero.
6. Do not change record order for `Voice.dat`, `Region.dat`, or any other source
   file, and do not alphabetize either `list_card.txt` catalog as a side effect.

The complete original `data_org.dat` sequence confirms global lexical ordering
with backslash keys for the known prefix collisions. All observed original
paths are lowercase, so the game's mixed-case comparison rule is unresolved;
the editor retains `casefold()` for deterministic behavior without rewriting
stored casing.

`ProjectFileRecord.order` is also the physical packed-container order. Project
export keeps the record metadata, archive construction maps it to
`ContainerEntry.order`, and the container encoder sorts its entry table and
payload sequence by that value. There is no independent pathname sort in the
codec, so `project.json` and packed `Data.dat` share the same order. Failure
restores the previous orders together with the rest of the staging transaction.

Image preparation and independent staging writes may run with RAM-aware workers
(64 MiB per item, hard cap 4, fallback 2), but manifest/catalog mutation, order
assignment, final manifest writing, and staging commit remain serial. Each
destination file is written atomically, and any batch failure discards the
staging project rather than exposing orphan files or records.

No single mandatory card or mini-image dimension, bit depth, palette, BMP
compression mode, or orientation is established for all game data. A
replacement should be checked against representative images from the target
container.

### WAV audio

A `.wav` entry is stored as the complete WAV file bytes. There is no additional
container-level audio wrapper. Playback is a UI concern; project storage and
packing preserve binary bytes.

### Free text

`.txt` and `.text` entries are decoded as free text except for structured
`list_card.txt`. Binary entries are decoded only when an explicit repository
rule matches.

Generic `*.txt` and `*.text` rules use fixed CP932 for both decode and encode.
This is a fixed encoding, not language detection or a default-language
resolver; their manifest language remains unset. Decode and encode are strict,
so invalid CP932 raises instead of producing a replacement character.
Project-folder text I/O disables universal-newline conversion. CRLF, LF, CR,
mixed line endings, consecutive endings, and the presence or absence of a final
line ending are retained exactly; unchanged generic text round-trips with its
original line-ending bytes.

The later, more-specific `list_card.txt` rule overrides generic `*.txt` by
reverse-order rule matching. Both `card/list_card.txt` and
`mini/list_card.txt` therefore remain `regex_record_table` resources using
UTF-8-SIG on decode and UTF-8 on encode. This structured exception writes its
canonical CRLF record layout rather than preserving arbitrary input endings.

### Raw binary, YGA, and unknown formats

Unknown `.bin` files, YGA resources, `Region.dat`, and any unclassified payload
remain raw bytes. Executables also remain raw in the original game folder and
project workspace; a matching `*_pc.exe` is transformed only while its binary
rule encodes Pack staging. Hexadecimal is only an editor presentation. A save
operation converts displayed byte pairs back to binary; hexadecimal text is
never used as the persisted representation.

## Virtual resources and pack dependencies

Virtual persistence and construction pipelines are stored in the same
dictionary as codec selection:

| Pattern | `pre_encode` construction | Primary resource |
|---|---|---|
| `card_intid.bin` | complete dynamically sized reverse lookup | `card_id.bin` |
| `card_indx[lang].bin` | `generate_string_offsets` + padding | `card_desc[lang].bin` |
| `dlg_indx[lang].bin` | `generate_string_offsets` | `dlg_text[lang].bin` |
| `card_sort[lang].bin` | inverse name/Card-ID ranks + card-count padding | `card_name[lang].bin`, `card_id.bin` |

A virtual manifest record has:

```text
workspace_path = null
storage_format = virtual
generated_on_pack = true
virtual = true
```

Virtual resources do not appear in the project tree and are not editable
workspace files. Their original container order, relative path, casing, and
compression state remain in the manifest. `virtual=true` controls only this
persistence behavior. Packing passes `None` into the same encode flow used by
physical resources; ordered `pre_encode` steps resolve dependencies and produce
the logical integer sequence. No virtual file is read from disk.

The manifest flag and matched rule flag must be equal. Both
`manifest=true/rule=false` and `manifest=false/rule=true` are rejected.
Dependency decoding and virtual generation carry operation-local path stacks
and report direct or multi-hop cycles before recursion. Generated logical
values are cached only for the current Pack, so a virtual resource can depend
on another virtual resource without uncontrolled global state.

For Spanish, the canonical generated names are `card_indxspa.bin` and
`card_sortspa.bin`.

## Unpack validation

Unpacking applies these checks:

- required top-level logical files are present;
- `Data.dat` and `Voice.dat` have the `KCEJYUGI` signature;
- container headers and payload ranges are valid;
- compressed output matches the declared size;
- structured integer-list sizes align to their record widths;
- fixed-name data aligns to 64-byte records;
- property data aligns to four-byte records;
- a description has a matching same-language index entry;
- active description offsets fit inside the payload;
- project paths remain beneath the project root;
- language-bearing paths use a canonical prefix;
- unknown formats are preserved without conversion.

Where `card_id.bin` is available, its row count limits the active portion of
description index tables.

## Pack validation

Packing applies these checks:

- every nonvirtual manifest record has a workspace path;
- every physical manifest record resolves to an existing workspace file;
- resource paths and workspace paths are unique after case-insensitive
  normalization;
- orders are non-negative, unique, and contiguous independently within each
  `source_file`;
- each manifest record's `virtual` flag matches its rule in both directions;
- each virtual record matches a rule with a nonempty value-creating
  `pre_encode` pipeline;
- a virtual pipeline can resolve its same-language dependency;
- integer values fit their declared widths;
- fixed names fit within 63 encoded bytes;
- localized text encodes without replacement;
- `card_intid.bin` has at least one non-negative source ID, uses last-wins for
  duplicates, is naturally sized to the next containing power of two, and is
  encoded in full subject to unsigned 16-bit record-value validation;
- localized name and Card ID tables cover the same active rows;
- `card_sort<language>.bin` count equals the next power of two containing the
  `card_id.bin` row count;
- card property values are representable;
- the physical `card_ids` table passes Joey topology checks before large
  container reconstruction: 1115..4095 rows, dummy `-1` at row zero, unique
  active integer IDs in `0..4094`, and no other negative value;
- an extended project has exactly one supported executable resource, whose
  preflight validates the stock hash, PE layout, complete source regions, and
  derived capacity plan before Pack staging begins;
- the staged extended executable passes section, helper, fixed-patch, and all
  17 dynamic-site checks both before and after any native Windows icon update;
- output entry order and paths follow manifest metadata;
- output offsets and sizes are recalculated;
- container compression follows the requested policy.

The packed container can be reopened with the same decoder to validate its
signature, table boundaries, payload sizes, entry order, and decompressed
payloads.

## Project workspace

### Manifest

`project.json` is the project metadata file. Its main fields include:

```json
{
  "name": "Project Name",
  "root_path": "D:/workspace/Project Name",
  "version": 4,
  "version_prefix": "mai",
  "game_files": {
    "data.dat": "Data.dat",
    "voice.dat": "Voice.dat",
    "region.dat": "Region.dat",
    "deck.ydc": "deck.ydc"
  },
  "files": [],
  "executable": {
    "source_name": "joey_pc.exe",
    "relative_path": "mai/mai_pc.exe"
  },
  "icon_path": "project.ico"
}
```

`version_prefix` is required. The value `mai` above is only an example, not a
schema or application default.
`icon_path` is optional and omitted when no icon is configured, so older
manifests remain valid. When present it is project-relative and points to the
validated project-owned ICO copy; missing or unsafe paths fail Pack/validation.
The manifest path is authoritative, so existing projects that explicitly use
the former `project.icon` filename continue to load and Pack without migration.

Each file record stores its source game file, archive-relative path, workspace
path, editor kind, storage format, language, compression state, order, and
virtual persistence state. Codec selection and processing pipelines are
derived from the matched config rule and are not written to new manifests. The
current format does not store generation metadata; loading discards derivable
legacy fields. The source game folder path is not retained.

Manifest loading validates both resource paths and explicit `language` fields.
It reports an unsupported language against the exact resource path and does not
modify project data. It also rejects duplicate normalized workspace paths,
duplicate normalized `(source_file, relative_path)` identities, unsafe source
paths, negative/duplicate/non-contiguous per-source orders, and physical
resources carrying virtual metadata. Repository load/save additionally verifies
that every physical record points to an existing workspace file.

### Structured workspace files

Tables and lists are stored as UTF-8-with-BOM CSV through pandas. The workspace
filename may retain its original `.bin`, `.ydc`, or `.txt` extension because
`storage_format` and the repository's matched config rule define the project
representation.

Services access structured card data by logical name:

```text
card_ids
card_passcodes
card_packs
card_properties
card_names
card_descriptions
card_catalog
cards
deck_cards
```

`cards` is a composite table assembled by the project repository. Saving it
validates row counts and splits it back into physical workspace tables.

### Raw workspace files

Images, audio, executables, region data, YGA files, and unknown binary resources
are written as raw bytes after container decompression. The executable
workspace file is always the source version; its Pack-time encoded output is
never written back to this path.

Project creation uses a sibling staging directory and an atomic final rename.
Packing builds a complete sibling output directory, validates it, and
atomically replaces `bin`. A failure removes staging and preserves the previous
output. Executable hash, patch-site, capacity, or output-hash failure uses this
same rollback boundary, so already-written staging containers are not exposed.
After the executable's Pack-time binary pipeline, an optional icon update
replaces its icon groups in staging while retaining unrelated PE resources.
The original game executable and editable workspace copy are never updated.

Export Files uses the same reconstruction as Pack but stops before container
compression. It writes final decompressed/re-encoded Data and Voice entry bytes
beneath `data/` and `voice/`, and the encoded deck/region files beneath `deck/`
and `region/`. Virtual resources are included. Export creates or overwrites only
its managed files and never removes the selected destination tree.

Card edits use the same transaction boundary. The project is cloned to staging,
all new/replacement large and mini images are applied in one batch, logical
cards and both catalogs are saved, the final manifest is validated and written
once, and only then is staging atomically committed. New image records default
to `compressed=false`; existing replacements retain their metadata. Any failure
before or during commit leaves the original workspace and its `project.json`
unchanged.

## Packaged application resource

The Qt title-bar icon is stored exactly as
`yugioh_editor/resources/app.icon`. Startup resolves it relative to the package,
rejects a null `QIcon`, and sets it once on `QApplication`; it never depends on
the process working directory.

## Format points requiring representative game data

These details are intentionally not treated as fixed without representative
files from the target game installation:

- exact Western encoding behavior outside the implemented CP1252 profile;
- collation rules used by the game for every localized sort table;
- mandatory card and mini-image dimensions and pixel formats;
- semantic labels for all unused or special `card_prop.bin` type combinations;
- whether real data uses non-ASCII archive paths;
- structures of localized binary resources without registered codecs;
- semantics of unknown YGA and region payloads.

When a format is not registered, the editor preserves its path, order,
compression intent, and raw bytes instead of inferring a structure from the
extension.
