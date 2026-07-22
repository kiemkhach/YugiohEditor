# Architecture

## Dependency flow

The application uses one dependency direction:

```text
View
  -> Service
  -> Repository
  -> Connection
  -> Codec
  -> raw bytes / filesystem
```

Every layer has one responsibility:

- Views read widgets, invoke services, render results, manage signals, and
  display short user-facing errors.
- Services implement use cases and business validation through repository
  public APIs.
- Repositories map business concepts and resource rules to generic connection
  operations.
- Connections own filesystem access and invoke codecs.
- Codecs transform bytes and structured values without accessing files.

Views and services do not import connections or codecs. Services do not receive
connections or codecs through constructors and do not access repository private
attributes. Repositories store connections only in private attributes and do
not import or instantiate codecs.

## Package layout

```text
yugioh_editor/
├── common/
│   └── subfile_rules_config.py
├── models/
├── repositories/
│   ├── game/
│   │   ├── connection.py
│   │   ├── repository.py
│   │   ├── subfile_rule.py
│   │   ├── subfile_rule_factory.py
│   │   └── codecs/
│   └── project/
│       ├── connection.py
│       └── repository.py
├── services/
├── ui/
├── views/
└── workers/
```

## Models

`models/entities.py` contains passive dataclasses:

- `ContainerEntry` and `ContainerArchive` represent decoded containers.
- `DeckFile` represents the deck header and card IDs.
- `NamedCardImagePair` carries one named large/mini pair across the service to
  repository batch boundary.
- `ProjectFileRecord` describes one physical or virtual project resource.
- `ProjectResource` pairs a record with its table, text, or binary value.
- `ProjectManifest` stores project metadata, resource records, selected
  top-level filename casing, and executable metadata.

The version prefix is required input: `StartView` reads the current widget
value, `ProjectService` validates it again, and `ProjectManifest` stores that
validated value. No layer supplies a fallback prefix. Manifest validation
rejects missing or invalid version prefixes, empty paths, absolute workspace or
source paths, traversal, normalized resource and workspace duplicates, invalid
language prefixes, inconsistent physical/virtual metadata, invalid virtual
pipelines, and unsafe executable paths.

## Game repository

`GameRepository` owns a private `GameFolderConnection`. It knows:

- top-level logical game files;
- explicit subfile patterns;
- which generic operation applies to a matched subfile;
- description/index dependencies;
- virtual persistence and logical-value construction pipelines;
- language-dependent filenames;
- unknown-resource raw fallback.

The only subfile source of truth is the tuple of plain dictionaries named
`SUBFILE_RULE_CONFIGS` in `common/subfile_rules_config.py`. The common module
does not import repository, connection, codec, or runtime rule types.
`SubfileRuleFactory` belongs to the game repository layer. It validates each
dictionary, safely compiles `*` and `[lang]` into case-insensitive full-match
regular expressions, recursively freezes parameter mappings, and creates immutable runtime
`SubfileRule` objects:

```text
Config dictionaries
-> SubfileRuleFactory
-> runtime SubfileRule
-> GameRepository rule pipeline
-> GameFolderConnection
-> Codec
```

The repository checks the runtime tuple in reverse order, so a later specific
rule overrides an earlier fallback. `codec_name` is the generic operation:

```text
container
binary
text
integer_list
fixed_string_list
record_table
offset_string_table
regex_record_table
image
audio
```

Virtual state, dependency templates, and logical-value construction steps are
fields in the same dictionary. There is no second virtual or codec mapping.
There is no generator registry and no generator encode parameter. The
repository never imports a codec; it asks the connection to perform the
selected generic operation.

`CODEC_OPERATIONS` is the canonical codec-operation set used by the factory
and validated against both connection registries. A rule may contain four ordered pipelines:
`pre_decode`, `post_decode`, `pre_encode`, and `post_encode`. Each is a list or
tuple of plain step dictionaries:

```python
{
    "method_name": "sequence_to_dataframe",
    "params": {"column": "value"},
}
```

The factory converts steps into deeply immutable `RuleMethodCall` values. It
rejects unknown step fields, non-mapping parameters, and methods outside
`ALLOWED_RULE_METHODS`. It does not import `GameRepository`; after construction
the repository verifies that every configured implementation is an actual
staticmethod. Config cannot call arbitrary repository methods.

`RuleProcessingContext` owns independent, recursively thawed decode and encode parameter copies,
the active rule/path/language, and dependency metadata. Pipeline static methods
receive only the current value, the context, and configured keyword arguments.
They do not access the connection directly; dependency methods use explicit
repository helpers, which retain the repository-to-connection boundary.

Execution is:

```text
decode: raw value -> pre_decode -> Connection decode -> post_decode
encode: project value -> pre_encode -> Connection encode -> post_encode
virtual: None -> pre_encode loads dependencies
         -> pre_encode constructs logical values
         -> Connection encode -> post_encode
```

Within each pipeline, methods execute in configuration order and every output
becomes the next input. Failures raise `RulePipelineError` with resource,
pattern, phase, zero-based step, method, and the original chained exception.
This supports config-only sub-tables when the required
codec operation and processing methods already exist. A new binary format needs
a codec/connection implementation, a new logical construction requires a
whitelisted repository static method, and a new logical/composite table or
editor may require repository or UI code.

Unknown `.bin`, YGA, executable, image, and audio payloads are preserved as raw
bytes unless an explicit rule matches. A language-looking suffix does not turn
an unknown binary into text.

## Game connection and codecs

`GameFolderConnection` performs path-safe filesystem access, atomic file
writes, container and deck I/O, and dispatches generic operations through
decoder and encoder dictionaries:

```python
connection.decode_resource(codec_name, data, **parameters)
connection.encode_resource(codec_name, value, **parameters)
```

The card codec module provides pure generic codecs:

- `IntegerListCodec`;
- `FixedStringListCodec`;
- `OffsetStringTableCodec`;
- `TerminatedStringListCodec`;
- `RecordTableCodec`;
- `NibbleStatisticsCodec`;
- `RegexRecordCodec`.

The nibble property transform is supplied to the generic record-table
operation. Codecs receive bytes, values, widths, offsets, encodings,
terminators, and row transforms. They do not receive filenames or paths.
Regex syntax and output templates are owned by the repository and passed
through the connection. `TextCodec` receives a concrete encoding rather than a
language or filename. The connection assigns the source display name after a
container has been decoded.

The LZSS codec retains both `compress()` and `decompress()` with the documented
4096-byte ring-buffer algorithm.
The container codec owns compression-policy selection and bypasses LZSS for raw
entries under `preserve` and for every entry under `never`. The LZSS codec only
receives payloads whose selected container policy can store compressed output.

## Project repository

`ProjectRepository` owns a private `ProjectFolderConnection`. It provides:

- manifest create/load/save and validation;
- physical resource import/export;
- visible-resource deduplication;
- typed resource read, save, replace, and preview operations;
- atomic project and output staging;
- logical table APIs;
- card image batch creation, replacement, and deletion.

The canonical table API is backed by one `_table_handlers` registry:

```python
repository.list_tables()
repository.has_table(table_name)
repository.get_table(table_name, **parameters)
repository.save_table(table_name, table, **parameters)
```

Available logical tables are:

```text
card_ids
card_passcodes
card_packs
card_properties
card_names
card_descriptions
dialog_texts
card_catalog
cards
deck_cards
```

Physical handlers are created from rule `table_name` and `table_parameters`
metadata. The physical tables are `card_ids`, `card_passcodes`, `card_packs`,
`card_properties`, `card_names`, `card_descriptions`, and `dialog_texts`. There is no
`TABLE_NAMES`, reader map, or writer map. The composite `card_catalog`, `cards`,
and `deck_cards` handlers remain specialized code.

Physical tables map closely to one workspace resource. The composite `cards`
table is assembled from IDs, passcodes, pack labels, properties, localized
names, localized descriptions, and image catalog metadata:

```python
cards = repository.get_table("cards", language="eng")
repository.save_table("cards", cards, language="eng")
```

Saving `cards` validates required columns, resets card indexes, splits values
back into physical tables, updates every present localized table, and leaves
virtual sidecars absent from the workspace.

`card_passcodes` stores each `card_pass.bin` record as an eight-character
uppercase hexadecimal string in raw byte order. It preserves leading zeros,
and `FFFFFFFF` is the explicit missing-password sentinel. Current reads and
writes validate that representation instead of interpreting it as a 32-bit
number. Drafts, provider matching, validation, logs, and password-image cache
keys use this same canonical representation. URL-specific shortening is not a
project-repository concern.

The `card_properties` physical table contains the full semantic
`card_prop.bin` schema: numeric and labeled monster type, numeric and labeled
card category, numeric and labeled attribute, attack, defense, level, and the
derived two-tribute flag. The composite `cards` table exposes semantic labels
for editing and regenerates the numeric codes before writing the physical
table. Card List hides the code columns and derived tribute flag while keeping
Category and the complete monster-type labels visible.

The current project schema is v4. Opening v2 runs both legacy property
normalization and numeric-passcode conversion; opening v3 runs only the
passcode conversion. Both paths use one staging clone and atomic directory
replacement, then commit v4 exactly once. The passcode migration reconstructs
the original four little-endian bytes from each legacy unsigned integer and
stores their uppercase raw-order hex. Property migration converts the old even
attribute and `0/4/8/12` category codes, recovers Spell/Trap subtype bits, and
zeros non-monster stats. Current bit packing remains inside the generic
`nibble_statistics` row codec; legacy conversions do not leak into codecs.

Examples:

```python
names = repository.get_table("card_names", language="spa")
descriptions = repository.get_table(
    "card_descriptions",
    language="eng",
)
dialogs = repository.get_table("dialog_texts", language="jpn")
```

An unknown logical table raises an error listing the available table names.
Missing and unknown table parameters are rejected. `[lang]` resolves from the
canonical `language` parameter without changing the manifest's original path
casing.
Card-table lookup is restricted to the manifest's case-preserved logical
`Data.dat` source and matches complete path segments, so similarly named raw
resources and resources in `Voice.dat` cannot be selected accidentally.

## Virtual resources

The repository recognizes these generated resources:

```text
card_id.bin             -> card_intid.bin
card_name<language>.bin -> card_sort<language>.bin
card_id.bin             -> card_sort<language>.bin (key and row count)
card_desc<language>.bin -> card_indx<language>.bin
dlg_text<language>.bin  -> dlg_indx<language>.bin
```

Virtual records retain source filename, relative path, order, compression
intent, and language. They have no workspace path, are not shown in the tree,
and cannot be edited directly. This is the complete meaning of `virtual=True`;
it does not select a codec or generation mechanism.

Packing supplies the workspace value for a physical record and `None` for a
virtual record. Both call the same `_encode_rule_value()` orchestration. A
virtual rule's `pre_encode` pipeline loads its dependency and runs
`generate_string_offsets`, `generate_sort_indices`, or
`generate_reverse_lookup`, followed by dependency-driven padding where
configured. The connection only dispatches the resulting logical value to a
codec.

The reverse lookup is naturally sized to the smallest power of two containing
the maximum non-negative `card_id`, initializes missing IDs to zero, and uses
the last card index for duplicates. The complete valid natural sequence is
encoded as unsigned 16-bit little-endian records, with the generic integer codec
still enforcing its value range; the editor does not cap the record count.
This permits output longer than the original data but does not establish that
the original executable can address additional cards. Executable compatibility
or patching is a separate concern. `card_sort` keeps index zero as a dummy rank
zero and sorts every real row `1..N-1` into inverse ranks `0..N-2`. It uses
localized name and Card ID as its sort key, then pads to the next power of two
containing `len(card_id)`. It has no `card_intid` dependency, and the maximum
Card ID does not determine output length.
Manifest records and matched rules must agree on `virtual` in both directions.
Dependency decoding and virtual generation carry operation-local stacks and
report cycles such as `A -> B -> C -> A` before recursion can occur. Generated
logical values are cached only within one archive encode, preventing duplicate
generation or global mutable state. Offset-string payload encoding
and sidecar offset generation both use the codec layer's canonical string
layout calculation through a generic connection operation.

Indexed-text physical tables and workspace CSV files retain `text` and
`is_reserved`. Row position is the string index and localized filename context
supplies language; neither value is repeated in table rows. The declarative
`editor_columns` projection exposes only `text`, and save merges it into the
full frame without losing internal state. Row zero is always active. A later
offset-zero row is reserved; a later active empty row has a nonzero offset and
a physical NUL record. Empty text does not imply reservation. Input records
are bounded by aligned pointers, require an in-region NUL terminator, and must
have the exact length implied by their configured padding profile. Padding
contents are opaque input compatibility bytes and are
not part of logical text. Card descriptions and dialogs share this codec and
pipeline, while canonical output uses minimum padding 2 for descriptions and 1
for dialogs.

The two plain-data layouts live once in `subfile_rules_config.py` and are
expanded into independent parameter dictionaries for physical decode,
physical encode, and virtual index generation. `SubfileRuleFactory` validates
that all four layout fields (`encoding`, `terminator`, `alignment`, and
`minimum_padding`) are explicit and equal across each text/index dependency.

Generic `*.txt` and `*.text` rules use fixed CP932 strict encoding without
language metadata or a default-language resolver. Project-folder text reads
and writes disable universal-newline translation, preserving CRLF, LF, CR,
mixed endings, and the presence or absence of a final line ending. Unchanged
generic text therefore returns to the game with the same line-ending bytes.
The later `list_card.txt` rule retains priority and its UTF-8-SIG/UTF-8
regex-table behavior, including canonical CRLF output.

Card catalog access uses a semantic `large`/`mini` selector. The repository,
not the service or view, resolves it to `card/list_card.txt` or
`mini/list_card.txt`; basename-only lookup is not used.

New card images cross the repository boundary as an ordered sequence of
`NamedCardImagePair` values. `add_named_card_images_batch()` validates every
plain BMP name and complete pair before mutation, obtains one case-insensitive
inventory from manifest records, both catalogs, and both workspace folders,
and rejects conflicts inside the batch or with that inventory. The legacy
one-item method delegates to this batch implementation.

Image preparation and independent staging-file writes may use a bounded,
RAM-aware executor (64 MiB budget per item, hard cap 4, fallback cap 2).
Manifest mutation, catalog mutation, order assignment, and final transaction
commit remain coordinated serial operations. Prepared outputs are validated
against the planned physical records before the in-memory manifest is changed.
New records use the manifest's case-preserved `Data.dat` spelling and have:

```text
relative_path       card/<name> or mini/<name>
workspace_path      data/card/<name> or data/mini/<name>
file_kind           image
storage_format      binary
language            None
generated_on_pack   False
virtual             False
compressed          False
```

After one or more new physical files are added, ordering is replanned for every
record belonging to the actual, case-preserved `Data.dat` source. The exact
comparison key is
`normalize_project_path(record.relative_path).as_posix().casefold()`: separators
are normalized, the complete relative path participates, and comparison is
case-insensitive deterministic lexicographical ordering. The stored
`relative_path` spelling and casing are not rewritten. The sorted `Data.dat`
records are renumbered contiguously as `0..N-1`; records belonging to
`Voice.dat`, `Region.dat`, or any other source are untouched. This does not
alphabetize either `list_card.txt` catalog. Replacement reuses the existing
record, path, and compression state and, when no new file is added, does not
replan order.

`ProjectFileRecord.order` remains the single container-order source of truth.
Project export retains the record on `ProjectResource`; archive construction
copies its path, compression state, and order into `ContainerEntry`; and the
container encoder sorts those entries by `ContainerEntry.order` before writing
the entry table and payloads. Consequently, the saved manifest and packed
`Data.dat` have the same alphabetical physical-entry order. A failed batch or
outer staging commit restores the pre-transaction record orders and leaves the
original project unchanged.

Manifest validation treats `(source_file, relative_path)` and workspace paths
case-insensitively, rejects negative or duplicate orders, and requires orders
to be contiguous independently for every source file. Before a staging update
can commit, every physical record must have a corresponding workspace file.

`ApplicationController.open_project()` owns window presentation: it hides the
Start window and opens one retained `ProjectView` with `showMaximized()`.
`ProjectView` does not maximize itself in its constructor.

The selected Project editor uses an expanding size policy and tracks the
project tree's height. Its top and bottom layout margins are zero, while the
existing left/right margins, splitter sizes, and table width are preserved as
the window grows.

Evidence status: indexed-text byte rules and catalog paths are **Confirmed**;
current `card_sort` mechanics are **Audited**; NFKD/case-fold intent is
**Inferred**; exact Japanese, punctuation, and accent collation remains
**Unresolved**.

The former `decode_description_table`, `generate_description_files`, and
`generate_sort_sidecar` compatibility helpers were removed because only tests
called them and the unified archive/rule pipeline provides the same behavior.
The unused speculative `load_dependency_tables` step was also removed.

## Services

`ProjectService` coordinates game and project repositories. It does not decode
binary data, serialize CSV, or use Pillow.

Project creation uses:

```text
create sibling staging directory
-> validate and decode source resources
-> write workspace resources
-> validate and write manifest
-> atomic rename to the project root
```

Project packing uses:

```text
create sibling output staging directory
-> export physical and virtual records
-> encode and validate outputs
-> reopen generated containers
-> atomic replace the project bin directory
```

Failures remove staging data. A failed pack preserves the previous `bin`
directory.

`CardService` uses only:

```python
repository.get_table("cards", language=...)
repository.save_table("cards", cards, language=...)
```

Image use cases call repository resource methods. The service does not know card
subfile paths, virtual construction pipelines, connections, or codecs.

Card Detail and Card List both persist edits through
`CardService.save_card_changes()`. The service creates one staging clone, sends
all new image pairs through the repository batch API without per-image manifest
writes, applies replacements, saves the logical cards table and its two
catalogs, writes the final validated manifest once, and atomically commits the
staging directory. Batch inventory/catalog work is constant per save rather
than proportional to the number of images. Any preparation, file, catalog,
manifest, or commit failure discards staging and leaves the original project
and `project.json` unchanged; files and manifest records cannot be committed
independently.

`SubfileService` is a small compatibility use-case facade that delegates archive
decode/encode and workspace import/export to repositories.

## UI and workers

Qt Designer XML remains in `yugioh_editor/ui` and is loaded at runtime.

Project creation, loading, packing, Card List loading/save, Suggest, and card
image-pair reads run through retained `TaskRunner` jobs and `QThreadPool`.
Bulk Suggest remains one UI background task; its service-layer I/O executor is
an internal implementation detail and never mutates Qt models. Workers log full
tracebacks and emit short messages for dialogs. Card List creates its empty
model/proxy once, uses an O(1) card-index map for selection/navigation, and
never sizes columns by scanning every cell. Image results carry card/image
request tokens and use a small in-memory cache so stale worker results cannot
replace the current preview.

During Bulk Suggest, Card List keeps view-only filter, sort, selection,
language, and export interactions available while disabling Add, Update,
Import, Enable All, Suggest, and Save. Closing requests cancellation and defers
dialog teardown until the retained runner finishes. Save has one retained
runner guard, so repeated clicks cannot run concurrent repository transactions.

Reference lookup tries Official directly, resolves explicit English redirects
through the Yugipedia MediaWiki API, retries Official with the canonical name,
then falls back to YGOCDB. Positive and negative reference results use a
bounded, thread-safe session cache. YGOCDB English exact matches retain
provider order and choose the first distinct candidate; non-English ambiguity
remains an error rather than a guessed match.
Provider text is HTML-decoded and NFC-normalized before it reaches either
single-card or bulk Suggest. The Official parser keeps card-text blocks separate
from rename-history `Info` blocks; a conservative locale-aware fallback removes
only complete rename notices and leaves unrelated parentheticals and effect
sentences intact. A reference password fills only a current `FFFFFFFF`
sentinel and must itself be valid and non-missing.

Suggest candidate selection is semantic. Raw `monster_type_code` takes
precedence (`1..20` Monster, `21` Trap, `22` Spell), with normalized
`card_type` as a fallback. Localized names/descriptions, password, type,
category/subtype, and placeholder image apply to all known kinds. Level, ATK,
DEF, and monster attribute apply to Monsters but not Spell/Trap; numeric zero
remains a legitimate value. Unknown/new cards use a conservative
Monster-capable set until their kind is known. A text/scalar field contributes
to candidacy only when it is applicable, missing, and not touched, because
Suggest cannot overwrite touched fields. A card with no writable missing field
is skipped without provider traffic.

Image candidacy is independent: `token_sl.bmp` remains an image-only candidate
when the effective password or a query name can be used, even if all
text/scalars are complete. A non-placeholder image is never replaced. Card
Detail and Bulk Suggest use the same one-card pipeline. It merges the reference,
tries the effective canonical eight-character password first, and falls back
only to the provider's canonical English name after direct lookup failure. The
YGO Vietnam boundary transforms only the CDN path segment:

```text
persisted/cache value 08783685 -> URL .../8783685.jpg
persisted/cache value 00001234 -> URL .../1234.jpg
persisted/cache value 00000000 -> URL .../0.jpg
persisted/cache value FFFFFFFF -> no direct URL and no HTTP request
```

It neither parses the password as an integer nor reverses bytes. Password image
cache keys therefore remain, for example, `password:08783685`; name and
password results have separate bounded keys and only successful image payloads
are cached.

Bulk Suggest prefilters the complete source model, independent of proxy filter,
sort, selection, or display language. Worker selection uses available rather
than total RAM: reserve `max(512 MiB, available / 4)`, budget 64 MiB per worker,
cap I/O concurrency at 8, and use a fallback cap of 4 when memory cannot be
measured. Low memory still permits one worker. At most `workers * 2` futures are
submitted at a time. Workers resolve independent card clones and return
per-card outcomes; they do not mutate the source model, project, manifest, or
the shared image-name set. The coordinator consumes outcomes in candidate/source
order, reserves image names case-insensitively, updates counters, stages cards,
and reports progress. Results and names are therefore deterministic even when
futures finish out of order.

Cancellation stops new submissions, cancels queued work, and allows active
HTTP calls to finish under their configured timeout before executor shutdown.
Already coordinated cards remain staged; an unfinished or failed card leaves no
partial mutation, and per-card failures do not affect other cards. The progress
total and `total_candidates` are the semantic post-filter count, while the
result separately reports source count, skipped-complete count, selected worker
count, available-memory estimate, image outcomes, and cancellation.

The reference-data service also keeps per-key in-flight futures for lookup and
image keys. One owner performs an identical concurrent request while waiters
receive the same result or exception; different keys proceed in parallel. The
global lock protects only cache/registry transitions, never network I/O, and
every success or failure removes its in-flight entry. Waiters use a finite
in-flight timeout rather than an unbounded `Future.result()` call.

YGO Vietnam card-page paths are encoded exactly once as one path segment. Main
image discovery prefers an image scoped to the primary card component, then the
page's top-level JSON-LD card image, and uses Open Graph only as a fallback.
Relative URLs resolve against the final redirected page URL; response status,
size, content type, final URL, and image bytes are validated before use.

Card List exposes `filter empty`/`un-filter empty` and `enable all`. Enable All
clones and updates the complete source model, not merely visible proxy rows,
then changes eligible `disabled` packs to `joey` in one model update. Non-game
cards, the three canonical English Egyptian God names, and canonical English
names ending in ` token` are protected; rows whose pack is already non-disabled
are skipped. The operation is idempotent and preserves the active language,
filter, and any selection that remains visible.

Card Detail renders the large and mini previews inside equal-size outer frames.
The mini label is centered with stable inner padding and preserves native pixel
size unless the available area requires scale-down; window growth never
upscales it. Loading, empty, and error states change only inner content, while
the existing image request token prevents stale results from replacing the
current card.

`ProjectView` retains one live Card List dialog, and `CardListView` retains one
live Card Detail dialog. A repeated open request focuses the live dialog. Each
owner clears its reference from the dialog's `finished` signal, schedules the
closed dialog for deletion, and creates a fresh instance on the next request.

`ProjectView` retains each active Pack runner until its `finished` signal,
disables duplicate Pack requests, prevents the project window from closing
mid-Pack, and restores the button and busy indicator on success or failure.
Pack-resource errors retain source, resource, rule, codec, virtual state, and
pipeline step context; dialogs display only the affected basename.

The Project window's visible `Run` action is separate from Pack. It dispatches
`ProjectService.run_packed_game()` through the retained background-task path and
only launches the executable already present under `bin`; it never invokes
`pack_project()` or waits for the game process to exit. Successful launch ends
the task and clears the busy state without an information, warning, or other
modal dialog. Missing or unlaunchable executables still flow through the
existing failure handler, retain traceback logging, show an error, and clean up
the runner and progress state.

The project tree stores stable resource IDs rather than repeatedly searching the
manifest. Virtual resources are excluded.

The binary editor displays at most 64 KiB. Larger resources open as a read-only
preview with their total size, avoiding conversion of an entire executable to a
hexadecimal string.

## Naming and casing

Localized resources derive from the ordered `LANGUAGE_ENCODINGS` registry:

```text
eng, fra, jpn, spa, ita, ger
```

Spanish uses `spa`. Top-level `Data.dat`, `Voice.dat`, and `Region.dat` are
found case-insensitively. The selected spelling is stored in the manifest,
resource grouping uses `casefold()`, and packed output preserves the original
casing.

`Data.dat` and `Voice.dat` match the `container` fallback and still require the
`KCEJYUGI` signature. The later `Region.dat` rule overrides that fallback with
`binary`. Unknown `.bin` files use the binary fallback; the later
`card_id.bin` rule uses signed 16-bit little-endian `integer_list`, where
`FF FF` represents `-1`.

The application resolves `resources/app.icon` from the package directory,
rejects a null Qt icon, and sets it once on `QApplication`.
