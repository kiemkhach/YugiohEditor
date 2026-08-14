# Project overview

This is a Python 3 and PySide6 desktop application for analyzing and editing
Yu-Gi-Oh! Power of Chaos: Joey the Passion game files.

# Architecture

- Use a simple Python-style architecture.
- Preserve the dependency flow:
  `View -> Service -> Repository -> Connection -> Codec -> raw bytes/filesystem`.
- UI Designer files are stored in `yugioh_editor/ui`.
- Python view classes are stored in `yugioh_editor/views`.
- Game folder access is implemented in
  `yugioh_editor/repositories/game`.
- Project workspace access is implemented in
  `yugioh_editor/repositories/project`.
- Codecs must remain inside their corresponding repository subfolders.
- Views and services must not import connections or codecs.
- Services coordinate use cases through repository public APIs only.
- Repositories own connections in private attributes and must not import or
  instantiate codecs.
- Connections perform filesystem access and call generic codecs.
- Codecs transform bytes and structured values without knowing filenames,
  paths, repositories, services, views, or the filesystem.
- Repositories select concrete encodings and regex syntax before invoking
  generic connection operations.
- Subfile configuration lives only in
  `yugioh_editor/common/subfile_rules_config.py` as plain dictionaries.
- The repository-layer `SubfileRuleFactory` validates those dictionaries,
  compiles wildcard and `[lang]` patterns, and creates runtime `SubfileRule`
  objects. Later rules override earlier rules.
- `codec_name` is a generic connection operation. Physical and virtual
  persistence, dependencies, and processing pipelines belong to the same rule.
- `CODEC_OPERATIONS` is the canonical codec-operation set shared by the
  factory and connection registries.
- Rules may define ordered `pre_decode`, `post_decode`, `pre_encode`, and
  `post_encode` pipelines. Config steps remain plain dictionaries.
- Pipeline methods must be whitelisted static methods on `GameRepository`.
  The factory validates names without importing the repository; the repository
  validates implementations. Each method receives the previous output.
- Decode order is pre-decode, connection decode, post-decode. Encode order is
  pre-encode, connection encode, post-encode.
- `virtual=True` only prevents workspace persistence. Virtual encode starts
  with `None`; `pre_encode` loads dependencies and constructs logical values.
- Do not add a connection generator registry or a generator field to
  `encode_params`. Offset, sort, reverse-lookup, validation, and padding belong
  to whitelisted `GameRepository` static pipeline methods.
- Existing codec and processing capabilities may be composed by config alone.
  New binary formats, logical construction methods, composite tables, or
  editors still require implementation code.
- Project repositories expose logical tables through `list_tables()`,
  `has_table()`, `get_table()`, and `save_table()`.
- Physical logical tables are registered with `table_name` and optional
  `table_parameters` in `SUBFILE_RULE_CONFIGS`; composite tables remain
  specialized handlers in `ProjectRepository`.
- `_table_handlers` is the only logical-table registry. Do not add
  `TABLE_NAMES`, reader maps, or writer maps.
- The logical `cards` table is assembled and split by the repository.
- Rule parameters are recursively frozen, operation contexts receive
  independent mutable copies, dependency cycles are rejected, and manifest
  virtual metadata must match the selected rule in both directions.

# Important rules

- Do not remove or simplify LZSS compression.
- The LZSS codec must support both `compress()` and `decompress()`.
- Preserve binary, image, audio, and YGA files as raw bytes. Preserve source
  and workspace executable bytes exactly; only the executable written to Pack
  staging may be transformed by its configured binary `pre_encode` pipeline.
- Structured lists and tables in the project workspace may be stored as CSV.
- Do not save binary files as hexadecimal text.
- Do not use long if/elif or match/case blocks for file dispatch.
- Prefer registries, dictionaries, codec mappings, and handler collections.
- Preserve relative paths of container subfiles.
- Unknown binary resources remain raw bytes.
- Spanish resources use only the `spa` prefix.
- Preserve the selected casing of top-level game filenames.
- Require the version prefix from the current UI value; do not add a Python or
  manifest fallback.
- `LANGUAGE_ENCODINGS` is the single ordered language registry. Its canonical
  order is `eng`, `fra`, `jpn`, `spa`, `ita`, and `ger`; `[lang]`, manifests,
  localized table columns, and UI selectors derive from it.
- `Data.dat` and `Voice.dat` use the container operation, while `Region.dat`
  uses binary. Unknown `.bin` resources use binary.
- `card_id.bin` uses signed 16-bit little-endian integers, so `FF FF` is `-1`.
- Language-dependent card and dialog text uses only `LANGUAGE_ENCODINGS`:
  `jpn` is `cp932`; `eng`, `fra`, `ger`, `spa`, and `ita` are `cp1252`.
- `card_desc[lang].bin` and `dlg_text[lang].bin` share the pointer-bounded
  indexed-text codec. Decode requires aligned pointers, an in-region NUL, and
  exact profile length but treats padding contents as opaque. Encode writes
  canonical zero padding: descriptions use `minimum_padding=2`, dialogs use
  `minimum_padding=1`, and both use `alignment=2`. Indexed-text logical tables
  and CSV files contain `text` and `is_reserved`; row position is the string
  index and language remains resource context. The UI exposes only `text` and
  merges edits back into the full table. Row zero is always active. Later
  offset-zero rows are reserved, while a later active empty string has a
  nonzero offset and a physical NUL record. Empty text alone never determines
  reserved state.
- `card_indx[lang].bin` and `dlg_indx[lang].bin` are virtual unsigned 32-bit
  little-endian sidecars generated from the same logical rows as their blobs.
- Keep the card-description and dialog layouts as separate plain-data sources
  of truth. Physical decode, physical encode, and virtual offset generation
  must use matching explicit encoding, terminator, alignment, and minimum
  padding; the rule factory rejects mismatches.
- Generic `*.txt` and `*.text` use fixed CP932 strict encoding without language
  metadata, detection, fallback, or a default-language resolver. Project text
  I/O preserves CRLF, LF, CR, mixed endings, and trailing-newline state exactly.
  The specific `list_card.txt` rule retains UTF-8-SIG decode, UTF-8 encode, and
  canonical CRLF output.
- `ApplicationController.open_project()` opens `ProjectView` with
  `showMaximized()`; do not maximize it in the view constructor or maximize the
  Start window.
- The active `ProjectView` editor expands to the same height as the project
  tree. Keep the editor's top and bottom margins at zero while preserving the
  splitter widths and horizontal margins.
- Resolve card image catalogs by semantic variant and complete path:
  `large` is `card/list_card.txt`; `mini` is `mini/list_card.txt`.
- Treat indexed-text behavior as Confirmed, current `card_sort` mechanics as
  Audited, NFKD/case-fold intent as Inferred, and exact Japanese, punctuation,
  and accent collation as Unresolved.
- `card_prop.bin` uses four-byte semantic records. Preserve card category,
  every monster type code through `0x18`, attribute family, and the shared
  level/two-tribute bit. Encode with direct inverse formulas, not brute-force
  search or a raw-record baseline.
- `card_pass.bin` is a fixed-width raw-byte table. Each four-byte record is
  represented in the project as exactly eight uppercase hexadecimal characters
  in byte order; preserve leading zeros. `FFFFFFFF` is the missing-password
  sentinel. Do not interpret current values as integers or reverse their bytes.
  The same canonical eight-character value is used by drafts, provider matching,
  validation, logging, and password-image cache keys. Only the YGO Vietnam CDN
  URL segment removes leading zeroes with `value.lstrip("0") or "0"`;
  `FFFFFFFF` has no direct URL and must not cause an HTTP request.
- The `card_prop.bin` DWORD uses DEF bits 0..8, ATK bits 9..17, Monster
  category bits 18..19 (or non-Monster subtype bits 17..19), class bits
  20..24, level bits 25..28, and attribute bits 29..31. Current project schema
  v4 migrates v2 property codes and legacy numeric passcodes, or only passcodes
  for v3, through one atomic staging update. Do not retain legacy conversion in
  current codecs.
- Card List initializes one empty model/proxy and loads data in a retained
  worker. Navigation and selection use its in-memory index map, image pairs use
  stale-result tokens plus a bounded cache, and a bound `CardService` reuses the
  same project repository instance.
- Card List uses the exact toggle labels `filter empty` and `un-filter empty`,
  plus `enable all`. Enable All changes only `disabled` eligible rows to
  `joey`; it protects non-game cards, the three canonical English Egyptian God
  names, and canonical English names ending in ` token`. It operates on the
  complete source model even while filtered and leaves already enabled rows
  unchanged.
- Card Suggest normalizes provider names and descriptions before merging and
  never overwrites touched fields. Candidate selection distinguishes field
  applicability, missing state, and writability. Raw `monster_type_code` takes
  precedence: `1..20` is Monster, `21` is Trap, and `22` is Spell; normalized
  `card_type` is the fallback. Monster fields include level, ATK, DEF, and
  attribute, while those fields are inapplicable to Spell/Trap and must not make
  an otherwise complete Spell/Trap a candidate. Unknown/new cards use the
  conservative Monster-capable field set. Numeric zero is a value, not a
  missing marker. A missing touched field is not writable and cannot by itself
  trigger a request.
- A placeholder `token_sl.bmp` is an independent image candidate when an
  effective non-`FFFFFFFF` password or query name is available. Thus a card
  with complete text/scalars may remain an image-only candidate; a valid image
  is never overwritten. A valid provider password fills only a current missing
  password. Card Detail and Bulk Suggest share the same one-card image pipeline:
  use the canonical staged password first, then the canonical English name only
  after direct lookup failure. YGO Vietnam card paths are encoded once, and
  main-image parsing prefers scoped card markup and structured JSON-LD over
  social fallbacks.
- Bulk Suggest always prefilters and processes the complete source model, never
  proxy-visible rows. Internal I/O concurrency is bounded and RAM-aware: reserve
  `max(512 MiB, 25% of available RAM)`, budget 64 MiB per worker, cap at 8,
  fall back to 4 when available RAM is unknown, and always use one worker when
  work exists but memory is low. Keep no more than `workers * 2` submitted
  futures. Workers resolve independent clones; the coordinator commits results
  and allocates case-insensitively unique image names in source-card order, so
  completion order cannot affect output. Cancellation stops submission, cancels
  queued futures, lets running timeout-bounded calls finish, retains already
  committed cards, and shuts the executor down cleanly.
- While Card List Bulk Suggest is active, keep filter, sort, selection, display
  language, and export available but disable every model-mutating action. Defer
  dialog teardown until Suggest has cancelled and its executor has shut down.
  Serialize Card List Save so repeated clicks cannot start concurrent staging
  transactions on the retained repository.
- Reference and image caches are bounded and thread-safe. Per-key in-flight
  futures deduplicate concurrent identical lookups without holding the global
  cache lock over network I/O; different keys may run concurrently, waiters
  receive the owner's result or error under a finite wait timeout, failures are
  not cached as image successes, and in-flight entries are always removed.
- Card Detail and Card List save through the same `CardService.save_card_changes()`
  staging transaction and repository batch-image API. Validate a complete batch
  before mutation, scan image inventory and load each catalog once, prepare and
  write independent image pairs with bounded RAM-aware workers, update catalogs
  and manifest in one coordinated pass, write the final manifest once, and then
  atomically commit the staging clone. Failure discards staging and leaves the
  original project unchanged.
- Every new physical card image has matching `card/` and `mini/` records using
  the manifest's case-preserved `Data.dat` spelling, binary/image metadata, and
  `compressed=False`. Replacing an existing image reuses its record, path, and
  compression state; replacement alone does not replan container order. After
  adding one or more new physical files, sort every record belonging to the
  actual `Data.dat` source lexicographically by
  `normalize_project_path(record.relative_path).as_posix().casefold()`. This is
  a case-insensitive comparison of the complete normalized relative path;
  preserve the stored path spelling and casing. Renumber only that source
  contiguously from zero and leave every other source unchanged. Catalog order
  is independent and is not alphabetized by this rule. The manifest order is
  the packed-container entry order, and rollback restores the prior orders.
  Manifest/repository validation rejects negative, duplicate, or non-contiguous
  per-source orders, duplicate normalized resource/workspace paths, incomplete
  image pairs, and physical records whose workspace files do not exist.
- Card Detail uses equal outer preview frames. The mini preview is centered and
  may scale down while preserving aspect ratio, but it must never upscale above
  the source pixels or resize its outer frame.
- `card_intid.bin` is virtual and generated as
  `card_intid[card_id] = card_index`. Ignore negative IDs, size the natural
  table to the smallest containing power of two, initialize missing slots to
  zero, and let the last duplicate card index win. Encode the complete valid
  natural reverse lookup as unsigned 16-bit little-endian records; retain codec
  range validation and do not impose an editor-side record-count cap. Producing
  a longer table does not prove that the original game executable supports
  additional cards. Executable compatibility is controlled independently by
  the explicit Pack-time executable profile below.
- Executables matching `*_pc.exe` use the generic `binary` codec and the
  `patch_executable_card_capacity` `pre_encode` rule. Create Project and card
  Save never patch an executable. Pack derives `card_record_count` from
  `len(ProjectRepository.get_table("card_ids"))`, passes it only as operation
  metadata, and writes transformed bytes only beneath Pack staging. Do not
  persist the derived count in the manifest or derive it from external Card
  IDs, `cards`, `card_intid.bin`, or `card_sort`.
- The Joey executable profile identifies its supported source with the whole-
  file SHA-256, validates every complete original instruction before mutation,
  and declares all integer immediates plus the conditional trailing `MOVSW`.
  Counts at or below 1115 preserve the executable byte-for-byte without a hash
  requirement. Counts 1116 through 2166 use formula-driven bounds, state end,
  snapshot size, DWORD count, and odd/even trailing-WORD behavior. Counts above
  2166 fail Pack; this is a profile safety limit inferred from the next known
  global address, not an editor or card-format limit. The known count-1116
  output hash is a regression check, not a supported-count allowlist.
- Executable patch formulas and the one-card binary output are statically
  verified. One-card Windows runtime behavior is not yet dynamically verified;
  counts above 1116 are formula-driven but not runtime verified.
- `card_sort[lang].bin` is virtual. Index zero is a dummy with rank zero; all
  real rows `1..N-1` participate and receive inverse ranks `0..N-2`. Sort keys
  use the localized card name and Card ID. Size the output to
  `find_next_power_of_two(len(card_id))`; do not use the maximum Card ID or
  `card_intid.bin` as the target length.
- The application title-bar icon is `yugioh_editor/resources/app.icon`.
- Project creation and project packing must use staging directories and atomic
  directory replacement.
- Pack must run through a retained background task; disable duplicate Pack
  requests and restore UI state after both success and failure.
- The Project window's `Run` button only launches the executable already packed
  under `bin`; it never invokes Build or Pack. Launch remains a retained
  background task. Successful launch shows no modal dialog, while launch
  failure still uses the existing error reporting and always cleans up the busy
  state and retained runner.
- Do not create a new architecture layer unless it has a clear responsibility.

# Generated files

- Do not edit generated Python files from `.ui` files unless explicitly asked.
- Prefer loading `.ui` files through the existing UI loader.

# Testing

Before completing a change, run:

```bash
python -m compileall .
python -m unittest discover -s tests -v
```

Do not claim a change works unless the relevant tests pass.

# Change policy

- Make focused changes.
- Do not rewrite unrelated files.
- Preserve public method names unless a rename is requested.
- Show a summary of changed files after each task.
