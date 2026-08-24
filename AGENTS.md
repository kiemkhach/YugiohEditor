# Project overview

This is a Python 3 and PySide6 desktop application for analyzing and editing
Yu-Gi-Oh! Power of Chaos: Joey the Passion game files.

# Codex task orchestration

- For every non-trivial new user task, use
  `.codex/skills/ygo-task-orchestrator/SKILL.md` as the mandatory first planning
  step before production edits. The user should normally only need to describe
  the task; do not require them to choose skills manually.
- The orchestrator must classify complexity and affected domains, select the
  relevant YugiohEditor skills, decide whether investigation is required,
  choose single-agent versus multi-agent execution, order dependencies, and
  define verification/review before implementation begins.
- Use `ygo-investigate-change` before implementation whenever root cause,
  binary semantics, executable behavior, ordering, performance bottlenecks,
  concurrency/rollback behavior, or cross-layer contracts are not already
  established by current code and verified project knowledge.
- Every production implementation uses `ygo-implement-change` plus the relevant
  domain skills. Medium/high-risk changes and all binary, executable, container,
  transaction, concurrency, or card-capacity changes must end with an
  independent `ygo-review-change` pass.
- Split agents by responsibility/ownership, not arbitrary file count. Parallel
  agents are allowed only after their contracts and dependencies are explicit
  and their edit ownership is sufficiently independent. Prefer one coherent
  implementation agent when parallelism would create overlapping changes.
- If investigation or tests disprove an initial assumption, stop and re-plan:
  revise the selected skills, agent split, execution order, and acceptance tests
  instead of forcing the original plan. Mark rejected hypotheses as rejected.
- For Medium/High tasks, surface a concise plan before editing that includes
  complexity, selected skills in order, agent roles/dependencies, phases,
  verification, and unresolved assumptions. Trivial/Small tasks may use a
  compact plan and should not be over-orchestrated.
- See `.codex/ORCHESTRATION.md` for the repository workflow and
  `.codex/skills/ygo-task-orchestrator/SKILL.md` for the complete routing and
  handoff rules.

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
- When the logical `cards` table is saved, normal catalog rows take the editable
  English card name, while rows with a negative Card ID retain the existing
  per-variant `list_card.txt` name. This preserves source sentinel/card-back
  labels without freezing normal card names.
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
- Card List Save enters its busy state and yields to the Qt event loop before
  creating the dirty-card snapshot. Add Card opens a disabled, indeterminate
  Card Detail first and initializes its draft in a retained worker. Card List's
  normal visible state is maximized; minimizing is allowed, but restore returns
  to maximized without maximizing child dialogs.
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
  `"\\".join(normalize_project_path(record.relative_path).parts).casefold()`.
  This is a global comparison of the complete normalized relative path using a
  Windows backslash key, not recursive file-first traversal. Preserve the
  stored path spelling and casing. Renumber only that source
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
- Joey slot zero is the dummy. Active slots are `1..4094`, so the maximum is
  4094 active cards / 4095 physical `card_id` records. Active Card IDs are
  unique integers `0..4094`; `4095`/`0xFFF` is reserved and must never be an
  active slot or Card ID. ID 4093 is ordinary and must not be reserved.
- Centralize these limits in `common/joey_card_capacity.py`. Add Card selects
  the next slot only through 4094 and the lowest safe free Card ID below
  `0xFFF`. Protect currently free legacy alias IDs `2000`, `2014`, `2034`,
  `2037`, `2040`, `2063`, `2068`, `2387`, and `2389` from unrelated new
  allocation while accepting legitimate existing rows. Card Save reloads and
  revalidates the resulting topology so stale drafts cannot cross capacity or
  introduce duplicate IDs.
- Executables matching `*_pc.exe` use the generic `binary` codec and the
  `patch_executable_card_capacity` `pre_encode` rule. Create Project and card
  Save never patch an executable. Pack validates physical `card_ids` and
  preflights the executable before creating output staging or rebuilding large
  containers. It derives `card_record_count` from
  `len(ProjectRepository.get_table("card_ids"))`, passes the validated plan only
  as operation metadata, and writes transformed bytes only beneath Pack
  staging. Do not persist the count/plan or derive it from maximum Card ID,
  `cards`, `card_intid.bin`, `card_sort`, the UI, or images.
- An optional Create Project icon is copied to `project.ico` and recorded as a
  project-relative `icon_path`; an absent property remains backward compatible,
  and existing manifests may retain an authoritative `project.icon` path. Pack
  validates the configured file and updates icon groups only on the staged
  executable after its binary pre-encode pipeline, preserving unrelated PE
  resources and the source/workspace executable bytes. After native Windows
  icon mutation, re-open the staged executable and verify `.ygst`, `.ygsx`,
  helper fragments, masks, hooks, aliases, and all 17 dynamic sites; a
  post-icon whole-file hash is not stable.
- The Joey executable profile identifies its supported source with the whole-
  file SHA-256 and exact PE32 baseline and validates every complete stock
  instruction/window before mutation. Counts below 1115 fail; count 1115 alone
  preserves source bytes without requiring the input hash; counts 1116..4095
  install the structural Step 8 runtime; counts above 4095 fail.
- The extended executable adds `.ygst` for 4096 state WORDs at `0x00C24000`
  plus the 4096-byte snapshot at `0x00C26000`, and `.ygsx` for helpers at
  `0x00C27000`. Apply only the declarative 69 direct state relocations, two
  complete snapshot rewrites, fixed 12-bit masks/hooks/helpers, 11 audited
  alias-consumer patches, and exactly 17 count-dependent sites. Never scan for
  address-shaped values or globally patch literal 2000.
- Preserve the lower-2048 save/load bridge: copy relocated slots `0..2047` to
  the legacy block before its checksum/write call and copy them back on load.
  Slots `2048..4094` are not persistent. Do not ship autocollect behavior.
- Historical experimental builds runtime-verified the Step 8 bridge, lookup,
  and high-slot architecture semantics. Production helper bytes are newly
  assembled against complete stock instructions and statically verified; do
  not claim byte identity to a removed experiment or actual production game
  runtime. Native icon-resource verification is also separate from gameplay.
- `card_sort[lang].bin` is virtual. Index zero is a dummy with rank zero; all
  real rows `1..N-1` participate and receive inverse ranks `0..N-2`. Sort keys
  use the localized card name and Card ID. Size the output to
  `find_next_power_of_two(len(card_id))`; do not use the maximum Card ID or
  `card_intid.bin` as the target length.
- The application title-bar icon is `yugioh_editor/resources/app.icon`.
- Project creation and project packing must use staging directories and atomic
  directory replacement.
- Export Files reconstructs physical and virtual project resources with the
  same encode stage as Pack, but writes each container entry's final
  decompressed bytes beneath `data/` and `voice/`, plus encoded files beneath
  `deck/` and `region/`. It must not extract the original containers or delete
  the selected destination tree. While Pack or Export is reading the project,
  disable project mutations, Card List access, and Run; do not begin either
  artifact operation while a retained project mutation is still running.
  Card List Save and Project editor replacements are mutually exclusive across
  their modeless windows; their retained busy signals disable the other
  surface's mutation controls until completion.
- Pack must run through a retained background task; disable duplicate Pack
  requests and restore UI state after both success and failure.
- The Project window's `Run` button only launches the executable already packed
  under `bin` with exact arguments `-full -speedy`; it never invokes Build or
  Pack. Launch remains a retained
  background task. Successful launch shows no modal dialog, while launch
  failure still uses the existing error reporting and always cleans up the busy
  state and retained runner.
- Start restores only the last valid Workspace through user-scope application
  settings. Load Project uses that existing directory as its chooser start.
  Game-folder discovery reads `InstallDirJ` from the logical Konami registry key
  with the 32-bit HKLM view and never overwrites a nonempty UI value.
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
