# Development Guide

## Environment

Recommended local environment:

- Windows 10 or later;
- Python 3.11 or later;
- Visual Studio Code with the Python and Python Debugger extensions;
- a project-local virtual environment;
- Qt Designer provided with PySide6.

## Setup

From the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m pip install "coverage>=7" "ruff>=0.9"
```

Verify imports:

```powershell
python -c "import PySide6, pandas, PIL; print('Dependencies OK')"
```

Run the application:

```powershell
python main.py
```

## VS Code configuration

Select the interpreter:

```text
Ctrl+Shift+P
→ Python: Select Interpreter
→ .venv\Scripts\python.exe
```

Run with the included debug configuration:

```text
Run and Debug
→ YGO Editor - venv
→ F5
```

## Qt Designer workflow

Open Designer from the activated virtual environment:

```powershell
pyside6-designer
```

The editable UI files are:

```text
yugioh_editor/ui/start_window.ui
yugioh_editor/ui/project_window.ui
yugioh_editor/ui/card_list_window.ui
yugioh_editor/ui/card_editor_dialog.ui
```

The application loads `.ui` XML at runtime through `views/ui_loader.py`. Do not generate or manually maintain duplicated `Ui_*` Python files unless the loading strategy is intentionally changed.

When changing a `.ui` file:

1. preserve existing widget object names used by the corresponding view;
2. keep application logic in `views/*.py`;
3. open the window and exercise all connected actions;
4. run the test suite.

Long-running Pack work must remain in `TaskRunner`. Retain the runner until
`finished`, keep widget access on the Qt main thread, disable duplicate Pack
requests, and restore all busy controls in the finished callback. Log the full
traceback while keeping dialogs short and resource-aware.

Keep the Project window's `Run` action separate from Pack. Its visible label is
`Run`; it dispatches `ProjectService.run_packed_game()` in the existing retained
background runner and only launches an executable already packed under `bin`.
It must not call `pack_project()`, block on the process lifetime, or show a
success modal. Launch exceptions continue through the existing failure dialog
and traceback logging, and both success and failure must clear the progress
state and retained runner.

## Tests

Run all tests:

```powershell
$env:PYTHONPATH = "."
python -m unittest discover -s tests -v
```

Run coverage over the complete package:

```powershell
python -m coverage run --source=yugioh_editor -m unittest discover -s tests -v
python -m coverage report -m
```

Run lint and formatting checks:

```powershell
python -m ruff check .
python -m ruff format --check .
```

Run one test module:

```powershell
python -m unittest tests.test_lzss -v
```

Compile-check all Python files:

```powershell
python -m compileall main.py yugioh_editor tests
```

### Test areas

- LZSS literal and match token behavior;
- repetitive and random compression round trips;
- compressed and uncompressed container round trips;
- deck encoding and decoding;
- project connection table and typed-list storage;
- project creation and packing pipeline;
- card property encoding and decoding;
- canonical password persistence versus YGO Vietnam URL projection;
- semantic Card Suggest candidate filtering and touched-field protection;
- bounded, deterministic Bulk Suggest concurrency and cancellation;
- batch card-image save, manifest ownership, and rollback.

Binary format changes should include a focused round-trip test and at least one malformed-input test.

## Coding conventions

### General Python style

- Use `pathlib.Path` for paths.
- Use dataclasses for passive data models.
- Use type annotations on public methods.
- Prefer composition over deep inheritance.
- Keep functions focused and avoid hidden global state.
- Keep UI text and code comments in English.
- Raise specific errors when data cannot be represented or parsed.

### Dispatch and registries

Do not add long `if/elif` or `match` blocks for file types.

Subfile dispatch has one source of truth:

```text
common/SUBFILE_RULE_CONFIGS
-> repository/SubfileRuleFactory
-> runtime SubfileRule
-> GameRepository rule pipeline
-> connection
-> codec
```

The common config contains plain dictionaries only. Do not import runtime
rules, repositories, connections, or codecs there. `codec_name` is a generic
connection operation. Put broad fallback rules first and specific overrides
later; the repository scans in reverse, so the later rule wins. Keep virtual
state, dependency templates, and processing steps in the same dictionary.
There is no generator registry and codec `encode_params` must not contain a
generator field.

`CODEC_OPERATIONS` is the canonical codec-operation set shared by factory
validation and both connection registries. A rule may define `pre_decode`, `post_decode`,
`pre_encode`, and `post_encode`; omitted pipelines become empty tuples. A
pipeline is a list or tuple of plain dictionaries:

```python
{
    "method_name": "dataframe_column_to_list",
    "params": {
        "column": "value",
        "fill_value": 0,
        "cast": "int",
    },
}
```

Every method must be in `ALLOWED_RULE_METHODS` and must be an actual
`staticmethod` on `GameRepository`. The factory validates the declarative name
without importing the repository; `GameRepository` validates implementations
after rule construction. Steps execute in listed order, with each output passed to
the next step. Static methods may resolve dependencies through repository
helpers but must not access `_connection`, the filesystem, services, or views
directly.

Pipeline order is:

```text
decode: raw -> pre_decode -> connection decode -> post_decode
encode: project value -> pre_encode -> connection encode -> post_encode
virtual: None -> pre_encode loads dependencies
         -> pre_encode constructs logical values
         -> connection encode -> post_encode
```

`virtual=True` controls workspace persistence only. Physical and virtual
resources use the same `_encode_rule_value()` path; their initial values are
the workspace value and `None`, respectively. Offset, sort-rank, and reverse
lookup construction are static pipeline methods in `GameRepository`, never
connection operations.

If a new sub-table uses an existing codec and processing methods, and needs no
new logical table or editor, adding its config is sufficient. A new binary
layout requires a codec and connection registration. A new logical
construction requires a whitelisted repository static method. A new composite
logical table or editor may require repository, service, or UI code.

The factory safely compiles `*` without crossing path separators and compiles
`[lang]` as a named, case-insensitive group derived from the ordered registry:

```text
eng, fra, jpn, spa, ita, ger
```

### Layer boundaries

- Views may call services and update widgets.
- Services may coordinate repositories and multiple project records.
- Repositories may use private connections but must not import or instantiate
  codecs.
- Connections may access the filesystem.
- Connections invoke generic codecs.
- Codecs may only transform data.
- Codecs must not open files or depend on PySide6.
- Views must not parse binary formats.
- Game-folder connections treat `.bin` files as raw binary resources.
- The game repository creates runtime rules from `SUBFILE_RULE_CONFIGS` and
  selects generic operations. It does not construct codecs.
- The connection dispatches generic decode and encode operations and calls
  codecs.
- Services and views must not import either connection module.
- Repository connection fields are private and must not be exposed.

## Adding a structured subfile codec

1. Implement a codec under:

```text
yugioh_editor/repositories/game/codecs/
```

2. Use a generic interface that does not receive a filename:

```python
class ExampleCodec:
    def decode(
        self,
        data: bytes,
        *,
        encoding: str,
        **parameters: object,
    ) -> object: ...

    def encode(
        self,
        value: object,
        *,
        encoding: str,
        **parameters: object,
    ) -> bytes: ...
```

3. Expose the generic operation through `GameFolderConnection`.
4. Add a plain dictionary to `SUBFILE_RULE_CONFIGS`; do not instantiate
   `SubfileRule` in common config.
5. Define the project table columns.
6. Add round-trip and invalid-input tests.
7. Document the binary layout in `FILE_FORMATS.md`.

Paired formats use the generic offset-string operation. Ordered pre-decode
methods resolve the payload/index dependency and active-record limit before
the repository passes bytes, offsets, and a concrete encoding to the
connection.

Keep filename patterns and declarative regex syntax in common config. The
repository factory compiles filename patterns, and the repository selects
language encodings, compiles record syntax, and resolves generated-resource
dependencies. Text and regex codecs receive concrete encodings and
caller-provided syntax only. Match complete basenames so similarly named
unknown binaries keep their raw-byte fallback.

Localized filename rules must be built from `LANGUAGE_PREFIXES`, which derives
from the ordered `LANGUAGE_ENCODINGS` registry. The canonical sequence is
`eng`, `fra`, `jpn`, `spa`, `ita`, and `ger`; Spanish uses `spa`.
Use only canonical prefixes. Add case-insensitive recognition tests,
manifest-validation tests, and a pack test for every localized rule change.

## Adding a generated-on-pack file

Use this for data derived entirely from another editable project file.

1. Add a generated filename pattern.
2. Set `virtual` to true in that same subfile config dictionary.
3. Start `pre_encode` with `load_dependency_table`.
4. Add static steps that construct and pad the logical value before codec
   encode.
5. Keep only codec parameters in `encode_params`.
6. Create a manifest record with no workspace path; do not copy pipeline
   metadata into the manifest.
7. Preserve original container order and compression state.
8. Test that unpack → edit source table → pack produces the generated file.

Do not create redundant editable workspace files for data that can be deterministically rebuilt.

## Adding a file editor

1. Derive from `FileEditor` in `views/editors.py`.
2. Accept `ProjectService`, `ProjectManifest`, and `ProjectFileRecord`.
3. Load and save only through service resource methods.
4. Implement `save()`.
5. Register the class in the editor factory dictionary.
6. Ensure save preserves the declared project storage format.

Binary editors may display hexadecimal text, but they must convert it back to
raw bytes before calling the project service.

## Adding a top-level game file type

1. Add the filename to `SUPPORTED_GAME_FILES` only when it is required for project creation.
2. Add typed operations to `GameFolderConnection` when the format has reusable semantics.
3. Expose the operation through `GameRepository`.
4. Add create and pack handlers to `ProjectService` dictionaries.
5. Add manifest records and tests for both directions.

## Version executable

Project creation requires the caller to pass a non-empty version prefix
containing only ASCII letters, numbers, underscores, and hyphens. The start
view reads and trims the current Designer-backed widget value; the service
validates it again. There is no Python default or manifest fallback. Executable
candidates must match `<name>_pc.exe` case-insensitively. When several
candidates exist, the first case-insensitively sorted filename is selected.
The workspace path is `<prefix>/<prefix>_pc.exe`, and packing writes
`bin/<prefix>_pc.exe`.

## Working with project tables

`ProjectFolderConnection.read_table()` loads CSV columns as generic object
values and disables automatic NA conversion. Repositories perform explicit
table conversion and validation.

When writing tables:

- preserve deterministic column order;
- avoid implicit index columns;
- fill missing required columns deliberately;
- use one row per original list element;
- do not silently drop unknown columns unless the format requires a fixed schema.

## Atomic writes and path safety

Both connection types resolve paths under their configured root. Do not bypass connection path resolution with direct unvalidated user paths.

Use connection write methods for project and game output. They write to a temporary file, flush it, and replace the destination atomically.

Project tree paths are normalized to relative POSIX form for identity checks.
Empty and `.` segments are removed, `..` is rejected, and folder/resource keys
are deduplicated case-insensitively while retaining the first display spelling.
Manifest validation also rejects duplicate normalized workspace paths and
normalized `(source_file, relative_path)` identities, negative or duplicate
per-source orders, non-contiguous per-source order sequences, and physical
resources marked with virtual storage or file kinds. Repository load/save also
requires every physical record's workspace file to exist.

Project creation writes a sibling staging directory and atomically renames it
to the final root. Project packing builds a sibling staging directory and
atomically replaces `bin`; the previous output is restored if replacement
fails.

Card saves use the same all-or-nothing principle. Create one staging clone,
apply the complete image batch and logical table/catalog changes there, validate
and write the final manifest once, then commit the clone. Do not save the
manifest per image, expose a physical file before its record, or expose a record
before its file. On preparation, write, catalog, manifest, or commit failure,
discard staging and leave the original project untouched.

## Logical project tables

Services request stable logical names:

```python
names = repository.get_table("card_names", language="eng")
cards = repository.get_table("cards", language="spa")
repository.save_table("cards", cards, language="spa")
```

Do not pass physical filenames to services. For a physical table, add
`table_name` and optional `table_parameters` to its subfile rule:

```python
{
    "pattern": "card_name[lang].bin",
    "table_name": "card_names",
    "table_parameters": ("language",),
    ...
}
```

`ProjectRepository` creates generic physical handlers from runtime rules.
`_table_handlers` is the only registry used by `list_tables()`, `has_table()`,
`get_table()`, and `save_table()`. Do not add `TABLE_NAMES`, `_table_readers`,
or `_table_writers`. Keep only composite assembly/splitting such as `cards`,
`card_catalog`, and `deck_cards` in specialized code.

Required parameters must be present, unknown parameters are rejected, and
`language` must be one of the canonical prefixes. Adding a simple physical
table with existing codec/pipeline capabilities requires config and tests, not
a new project-repository reader or writer.

Rule config is recursively frozen. Each processing context receives an
independent mutable thawed copy. Pipeline failures retain exception chaining
and identify the resource, pattern, phase, step, and method. Dependency
resolution rejects self and multi-hop cycles.

Tests for description and sort sidecars must construct physical and virtual
`ProjectResource` values and use `encode_archive()`/`decode_archive()`. Do not
restore the removed `decode_description_table`, `generate_description_files`,
`generate_sort_sidecar`, or speculative `load_dependency_tables` helpers.

## LZSS development rules

The codec's compression and decompression parameters are part of the file format:

- 4096-byte ring buffer;
- 18-byte maximum match;
- minimum encoded match of 3 bytes;
- cursor start `0xFEE`;
- zero-filled initial ring buffer;
- LSB-first flag bits;
- 12-bit offsets and 4-bit length codes.

Do not change these values as a performance optimization.

When editing the compressor:

- preserve token ordering;
- preserve overlapping-copy behavior in decompression;
- preserve binary-tree insertion and deletion invariants;
- run random and repetitive round-trip tests;
- validate container round trips as well as raw codec round trips.

The container must decide whether an entry requires compression before calling
the LZSS codec. Under `preserve`, call `compress()` only for entries recorded as
compressed; under `never`, do not call it. Keep a spy-codec regression test with
a large raw entry so discarded compression work cannot return silently.

## Card Suggest development rules

The canonical password domain value is exactly eight uppercase hexadecimal
characters in raw byte order. Preserve leading zeros in `card_pass.bin`, CSV,
`CardEditDraft`, provider data, validation, logs, and image cache keys. Do not
parse it as an integer or reverse its bytes. At the YGO Vietnam infrastructure
boundary only, build the direct-image path with:

```python
url_password = normalized_password.lstrip("0") or "0"
```

Thus `08783685` uses `/8783685.jpg`, while its cache key remains
`password:08783685`. `00000000` uses `/0.jpg`. `FFFFFFFF` has no direct URL and
must be rejected before an HTTP call. Direct success must not invoke the name
fallback; direct failure may invoke the canonical-English-name fallback once.

Keep semantic applicability separate from missing state and writability. The
candidate helpers must follow these rules:

- prefer `monster_type_code`: `1..20` Monster, `21` Trap, `22` Spell;
- fall back to normalized `card_type` only when no usable raw code exists;
- require localized names/descriptions, password, type, category/subtype, and
  image where applicable for known kinds;
- consider level, attack, defense, and monster attribute applicable to Monster,
  but never use them to make a known Spell/Trap a candidate;
- use the conservative Monster-capable set for unknown/new cards;
- treat numeric zero as a legitimate value rather than falsey missing data;
- require `applicable AND missing AND not touched` for a writable text/scalar
  candidate; Suggest never overwrites a touched field;
- treat a usable `token_sl.bmp` placeholder as an independent image candidate,
  including for an otherwise complete card, and never replace a valid image.

If every missing applicable text/scalar field is touched and no image can be
replaced, skip the card without a provider request. Bulk Suggest operates on the
complete source model regardless of proxy filtering, sorting, selection, or
display language. Its progress total is the semantic post-filter candidate
count, with source count and skipped-complete count reported separately.

### Bulk Suggest concurrency and cancellation

Keep one `CancellableProgressTaskRunner` at the view boundary. Use
`ThreadPoolExecutor` only inside the service use case; worker threads must not
mutate the Qt model, repository, manifest, shared image-name inventory, or emit
UI signals outside the runner callback contract.

Worker selection belongs in the shared worker-limit helper and uses available,
not total, memory:

```text
reserve = max(512 MiB, available_memory / 4)
usable = max(0, available_memory - reserve)
memory limit = max(1, usable / 64 MiB)
workers = min(candidate_count, 8, memory limit)
unknown-memory fallback cap = 4
```

Use `psutil` only if already available, otherwise Windows
`GlobalMemoryStatusEx`, POSIX `sysconf`, then the conservative fallback. CPU
count alone must not set concurrency. Submit at most `workers * 2` pending or
running futures rather than the whole candidate set.

Workers resolve independent clones and return position-tagged outcomes. The
coordinator applies results in candidate/source order, allocates and reserves
image names case-insensitively, updates counters, stages the correct row, and
reports progress. Future completion order must not affect drafts, image names,
or result order. Isolate each card so a failed result has no partial mutation
and cannot roll back completed cards.

On cancellation, stop submitting, cancel futures that have not started, allow
active HTTP work to finish under existing timeouts, retain already coordinated
cards, and shut the executor down before returning. Do not block indefinitely
or leave background work alive after the method, dialog close, or failure.
The Card List must disable model-mutating controls for the run and defer close
until this shutdown completes; filter/sort/selection/language remain view-only.
Retain and guard the Save runner so duplicate clicks never start concurrent
staging transactions on the same repository.

`CardReferenceDataService` caches and in-flight registries must remain bounded
and thread-safe. Deduplicate identical lookup and image keys with a per-key
`Future`/event: one owner performs network work, same-key waiters receive the
same result or exception, and different keys run concurrently. Never hold the
global cache lock across network I/O. Do not cache a failed image fetch as a
success, remove every in-flight entry in a `finally` path, and give waiters a
finite timeout.

## Card image batch Save

Card Detail and Card List must both call `CardService.save_card_changes()` and
the same repository batch API. Collect new staged images as ordered
`NamedCardImagePair` values. Keep the one-item repository method only as a thin
wrapper around the batch implementation.

For each batch:

1. validate every plain BMP filename, complete large/mini pair, and duplicate
   case-insensitively before mutation;
2. read the existing image inventory once, including manifest, both catalogs,
   both workspace folders, and names duplicated within the batch;
3. prepare all outputs, plan all records and orders, and validate the planned
   manifest before changing the live staging manifest;
4. write distinct staging destinations, validate that every planned physical
   record exists, then mutate the manifest once;
5. update logical cards and both catalogs and let the outer service transaction
   perform one final manifest write and staging commit.

Independent image decode/conversion and destination writes may use the shared
RAM-aware worker selector with 64 MiB per item, hard cap 4, fallback 2, and
minimum 1. Avoid mandatory executor overhead for a one-pair Card Detail save.
Keep manifest mutation, order assignment, catalog mutation, logical table save,
final manifest write, and staging commit serial. Every file write remains
atomic, and all executors must shut down before a failed staging clone is
discarded.

New image records use the manifest's case-preserved `Data.dat` name,
`file_kind="image"`, `storage_format="binary"`, no language, nonvirtual physical
workspace paths, and `compressed=False`. Replacement reuses the existing record
without changing its compression state or creating a duplicate record.

When a batch adds new physical files, obtain the actual case-preserved
`Data.dat` name from the repository and select its records case-insensitively.
Combine every existing record for that source with every new large and mini
record, then sort them by
`normalize_project_path(record.relative_path).as_posix().casefold()`. This is
ordinary deterministic lexicographical ordering over the complete normalized
path, not basename-only, natural, case-sensitive, group-append, or batch-input
ordering. Keep each stored path's original spelling/casing, renumber the sorted
`Data.dat` records from `0` through `N-1`, and do not change any other source.
Do not sort `card/list_card.txt` or `mini/list_card.txt` as a side effect.

Manifest `ProjectFileRecord.order` must reach `ContainerEntry.order` unchanged
through project export and game-repository archive construction; the container
encoder uses that order for the physical entry table. Do not introduce a
second path sort in the codec. A preparation, write, catalog, manifest, or
commit failure must restore all prior record orders as part of staging
rollback.

Tests must cover one and many unsorted new pairs, replacement metadata
preservation, mixed text/new/replacement saves, case-preserved and mixed-
separator paths, complete-path case-insensitive alphabetical ordering,
contiguous `Data.dat` order, isolation of every other source,
manifest/catalog reload, the real packed entry sequence, and rollback at
preparation, write, catalog, manifest, and commit failures. Use call-count
assertions to keep inventory/catalog reads and final manifest writes constant
per batch rather than proportional to image count.

## Card consistency rules

The card index is the row position across all card-related lists. Any supported
card mutation must keep the same row count and order across:

- card IDs;
- passcodes;
- pack assignments;
- properties;
- every available localized name list;
- every available localized description list;
- list-card metadata.

Description index files, `card_intid.bin`, and sort files are generated during
packing and must not be edited independently.

The Spanish dependency pairs are:

```text
card_descspa.bin -> card_indxspa.bin
card_namespa.bin -> card_sortspa.bin
dlg_textspa.bin -> dlg_indxspa.bin
```

Existing archive entry casing may be preserved. A newly assigned filename must
use canonical lowercase.

Card image-list operations must pass a semantic `large` or `mini` selector.
Large resolves only `card/list_card.txt`; mini resolves only
`mini/list_card.txt`. A low-level single-variant list mutation must not update
the other variant implicitly; the shared card-save orchestration intentionally
updates both explicit variants together for a staged image pair.

`card_prop.bin` rows must retain the complete semantic format. Category is not
padding: the upper two bits of the low nibble in byte 2 distinguish normal,
effect, fusion, and ritual. Numeric monster type codes include the full
secondary range through non-game card and divine beast. Encode attack and
defense with direct inverse formulas; do not search the nibble space or use a
raw-record baseline as the primary representation.

The level is the source of truth for the shared high-level/two-tribute bit.
Card List edits labels, while `ProjectRepository` regenerates code columns and
the derived tribute flag before saving. Pack logging must summarize record
count, columns, category distribution, and monster-type distribution without
logging individual rows.

`card_id.bin` uses signed 16-bit little-endian `integer_list`; `FF FF` is `-1`.
`card_sort[lang].bin` remains unsigned 16-bit, and
`card_indx[lang].bin` remains unsigned 32-bit.
`dlg_indx[lang].bin` is also unsigned 32-bit and has no hard-coded capacity.
Card descriptions and dialogs use the shared pointer-bounded indexed-text
codec. Decode validates aligned offsets, an in-region NUL, strict text
encoding, and exact region length while treating the configured padding slots
as opaque compatibility bytes. Encode always writes zero-filled canonical
padding. Description payload and sidecar rules use `minimum_padding=2`;
dialog payload and sidecar rules use `minimum_padding=1`; both use
`alignment=2`. Indexed-text DataFrames and CSV files contain `text` and
`is_reserved`; row position is the index and language stays in resource
context. The rule's `editor_columns=("text",)` keeps metadata out of the UI and
save merges visible edits into the full frame. Row zero is always active.
Later reserved rows are identified by explicit state and offset zero. A later
active empty row retains a physical NUL record; empty text is not a reserved
marker. These values come from
`CARD_DESCRIPTION_TEXT_LAYOUT` and
`DIALOG_TEXT_LAYOUT`. Expand the selected profile into a fresh dictionary for
decode, encode, and `generate_string_offsets`; the rule factory rejects missing
fields or mismatched profiles.

Generic `*.txt` and `*.text` use fixed CP932 with strict decode and encode.
Do not add language detection, a default-language resolver, replacement error
handling, or manifest language metadata for these rules. The specific
`list_card.txt` regex-table rule must remain later in configuration so it keeps
UTF-8-SIG decode and UTF-8 encode behavior.

Open a project through `ApplicationController.open_project()` using
`ProjectView.showMaximized()`. Do not maximize the Start window or move this
presentation policy into the `ProjectView` constructor.

Evidence labels used for this area are: **Confirmed** for indexed-text and
large/mini selector regression vectors; **Audited** for current `card_sort`
mechanics; **Inferred** for NFKD/case-fold intent; and **Unresolved** for exact
Japanese, punctuation, and accent collation.

`card_intid.bin` is virtual. Generate `card_intid[card_id] = card_index` from
the physical `card_id.bin` table, ignoring negative IDs and allowing the last
duplicate row to win. Its natural count is
`1 << max_non_negative_id.bit_length()`. Missing slots contain zero. Pass the
complete valid natural sequence to the unsigned 16-bit little-endian integer
codec, retain its value-range checks, and do not add an editor-side fixed record
count or replacement cap. A larger generated file alone does not demonstrate
support in the original executable; executable-limit analysis or patching
belongs to a separate task.

`card_sort[lang].bin` depends on localized card names and `card_id.bin`. Keep
index zero as dummy rank zero. Stable-sort all real rows `1..N-1`, write inverse
ranks `0..N-2`, and zero-pad to
`find_next_power_of_two(len(card_id))`. Use localized name plus Card ID as the
key. Do not derive target length from the maximum Card ID or `card_intid.bin`,
and do not add unsupported collation rules. Virtual dependency values and cycle
stacks remain scoped to one Pack operation.

## Application resources

Resolve packaged resources with `yugioh_editor.resources.get_resource_path()`.
The title-bar icon must remain named `resources/app.icon`. Startup creates a
`QIcon`, rejects `QIcon.isNull()`, and sets the icon on `QApplication` once.
Package data must include both `resources/*.icon` and runtime-loaded `ui/*.ui`.

## Validation checklist

Before considering a change complete:

1. Run `compileall`.
2. Run the entire test suite.
3. Create a project from a representative game folder.
4. Load the created project again.
5. Edit one file of each affected type.
6. Pack the project.
7. Reopen the packed containers and compare entry count, paths, order, and decompressed payloads.
8. Launch the packed executable when the change affects runtime game data.

## Build output

The project `bin` folder is generated output and should not be treated as source. Local Python packaging output such as `build/` and `dist/` is ignored by Git.
