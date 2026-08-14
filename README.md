# Yu-Gi-Oh! Power of Chaos Editor

A Python and PySide6 desktop application for inspecting, editing, and rebuilding files used by **Yu-Gi-Oh! Power of Chaos: Joey the Passion**.

The application creates an editable project workspace from a game installation folder. Container files are unpacked, structured binary data is converted to editable CSV-backed tables, text is decoded with the appropriate language encoding, and media or binary data is preserved as raw files. The project can later be packed into a runnable game folder under the project's `bin` directory.

## Main capabilities

- Create a project from a game folder containing:
  - `data.dat`
  - `Voice.dat`
  - `deck.ydc`
  - `Region.dat`
  - an optional executable matching `<name>_pc.exe`
- Load an existing project through `project.json`.
- Browse analyzed files in a tree view.
- Edit structured lists and tables with a table editor.
- Edit free-form text files.
- Preview and replace images.
- Play and replace WAV audio.
- Inspect and edit raw binary or executable data through a hexadecimal editor.
- Aggregate card data from multiple `card_*` files into one Card List.
- Edit card details and replace card images.
- Rebuild generated card indexes and sorting tables during packing.
- Repack the required game data and the selected version executable into the project `bin` directory.
- Launch the packed version executable from the application.

The implementation follows:

```text
View → Service → Repository → Connection → Codec → raw bytes / filesystem
```

Services use repository public APIs only. Repositories own private connections,
connections invoke codecs, and codecs do not know filenames or paths.

Subfile dispatch follows one configuration path:

```text
Config dictionaries
→ SubfileRuleFactory
→ runtime SubfileRule
→ GameRepository pipeline
→ Connection
→ Codec
```

`yugioh_editor/common/subfile_rules_config.py` contains only plain dictionary
configuration. The factory validates it, recursively freezes nested
parameters, compiles wildcard and `[lang]` patterns, and creates runtime rules.
The repository checks rules from last to first so later rules override earlier
fallbacks. `CODEC_OPERATIONS` is the shared source of truth for factory
validation and connection registries. Every rule may declare ordered
`pre_decode`, `post_decode`, `pre_encode`, and `post_encode` pipelines. Each
pipeline contains dictionary steps naming an allowed `GameRepository`
staticmethod plus keyword parameters. A step receives the preceding step's
output. Factory validation does not import `GameRepository`; the repository
validates the configured static method implementations after rule creation.

Decode order is pre-decode, connection codec decode, then post-decode. Encode
order is pre-encode, connection codec encode, then post-encode.
`virtual=True` only means that a resource is not persisted in the editable
workspace. A virtual encode starts with `None`; its `pre_encode` steps load
dependencies and construct the complete logical value before the connection
encodes it.

There is no virtual-generator registry and no generator field in
`encode_params`. Offset construction, sort ranks, and reverse lookup are
whitelisted static pipeline methods on `GameRepository`. Physical and virtual
resources share the same encode orchestration.

Physical logical tables add `table_name` and optional `table_parameters` to
their rule. `ProjectRepository` builds physical handlers from those rules and
keeps only composite tables in specialized code. Its single
`_table_handlers` registry drives `list_tables()`, `has_table()`,
`get_table()`, and `save_table()`; there is no separate `TABLE_NAMES`, reader
map, or writer map.

Adding a physical table is config-only when its codec and processing methods
already exist and it does not require a new composite table or editor. A new
binary format still requires a codec and connection operation; a new logical
construction method requires a whitelisted repository static method; a new
composite table or UI normally requires repository or view code.

Top-level `Data.dat`, `Voice.dat`, and `Region.dat` names are matched
case-insensitively. Their original filename casing is stored in the manifest and
preserved during packing. `Data.dat` and `Voice.dat` must have the `KCEJYUGI`
signature and use the `container` operation; `Region.dat` uses `binary` and
remains raw bytes. Unknown `.bin` files also use `binary`. `card_id.bin` uses
the `integer_list` operation with signed 16-bit little-endian values, so
`FF FF` decodes as `-1` and encodes back to `FF FF`.

Localized resources derive from the ordered language registry: `eng`, `fra`,
`jpn`, `spa`, `ita`, and `ger`. Spanish uses `spa`, including `card_namespa.bin`,
`card_descspa.bin`, `card_indxspa.bin`, and `card_sortspa.bin`. Reading is
case-insensitive; newly named derived resources use canonical lowercase.

## Technology

- Python 3.11 or later
- PySide6 for the desktop UI
- pandas for CSV-backed structured project files
- Pillow for card image and mini-image processing
- Standard-library `unittest` for tests

## Project workflow

### Create a project

1. Enter a project name.
2. Enter the required version prefix, such as `mai`.
3. Select a workspace directory.
4. Select the original game directory.
5. Start project creation.

The project is created at:

```text
<workspace>/<project-name>/
```

The original game folder path is not stored in the project. The project contains
one manifest named `project.json` and one analyzed workspace tree. Matching
executables are sorted case-insensitively and the first match is copied to
`<project>/<prefix>/<prefix>_pc.exe`. The prefix is read from the current UI
field, trimmed, validated, and stored without any application-level fallback.

### Edit project files

Open a file from the project tree. The editor is selected from the file type recorded in the manifest:

- structured data → table editor;
- text → text editor;
- image → image preview and replacement;
- audio → simple audio player and replacement;
- executable or binary → hexadecimal editor.

The tree normalizes separators and deduplicates paths case-insensitively. It has
one root each for data, voice, region, deck, and the version executable. Derived
sidecars are virtual resources and are not shown or written to the workspace.
They are regenerated when the project is packed.

The virtual `card_intid.bin` sidecar is generated as a reverse Card ID lookup,
naturally sized to the containing power of two and encoded in full as unsigned
16-bit little-endian records. The editor imposes no fixed record-count cap,
while the codec still validates each record's representable range. A longer
generated file does not guarantee support from an arbitrary game executable.
For the supported Joey executable, Pack independently applies a SHA-identified
capacity profile described below.

Manifest loading validates localized paths and explicit language metadata.
Unsupported language codes are reported with the affected resource path; the
loader does not silently rename project data.

Unknown binary files remain byte-for-byte binary resources even when their
names contain a language-like suffix. Structured filename rules match complete
path segments, so a similarly named file such as `customcard_id.bin` remains
raw.

### Edit cards

Open the Card List to work with a combined table containing card identifiers, localized names and descriptions, passcodes, pack assignments, properties, and image metadata.

Open a row by double-clicking it or selecting it and choosing Update. Card
Detail saves validated changes through the card service and refreshes the list.
The list can display localized names and descriptions in any supported
language, and its optional Unused filter shows cards whose pack is `disabled`.
Card Detail Suggest resolves one canonical card ID through Konami's official
card database, fills only missing localized text and compatible Power of Chaos
properties, and can stage a large/mini BMP pair from YGO Vietnam. Suggested
images remain in memory until Save commits both variants atomically.
Closing either Card List or Card Detail fully releases that dialog so it can be
opened again; requesting an already-open dialog focuses the existing instance.

The project repository exposes logical tables such as `card_ids`,
`card_names`, `card_descriptions`, and the composite `cards` table. Card
services do not locate or merge physical `card_*.bin` workspace files.
Localized physical tables require a canonical `language` parameter; missing
or unknown parameters fail before resource lookup.

PNG and JPEG card replacements are decoded with Pillow and saved as real BMP
payloads. Mini images use the dimensions of an existing mini image when one is
available. Custom image names are checked case-insensitively against both the
manifest and workspace files. When new physical images are added, all records
for the actual `Data.dat` source are sorted lexicographically by normalized,
case-insensitive complete relative path and renumbered contiguously. Stored path
casing is preserved, other source files are unchanged, and new image records
remain raw with `compressed=false`. The manifest order is the order written to
the packed container; a failed staged Save restores the previous project and
record order.

### Pack and run

Packing creates:

```text
<project>/bin/
├── data.dat
├── Voice.dat
├── deck.ydc
├── Region.dat
└── <prefix>_pc.exe
```

When an executable is present, Pack derives its active card capacity from the
actual row count of the logical `card_ids` table. The count is not hard-coded to
1116 and is not stored in `project.json`. The original game executable and the
project's workspace executable remain byte-identical source files; only the
copy written into Pack staging passes through the physical `*_pc.exe` rule,
which uses the existing generic binary codec and a declarative pre-encode
profile.

The current Joey profile preserves bytes for counts through 1115, supports
formula-driven encoding for counts 1116 through its statically inferred safe
maximum 2166, and rejects larger counts without truncating card data. A patch
requires the exact whole-file SHA-256 and validates every original instruction
site before changing only declared immediates and the odd/even trailing-WORD
instruction. The count-1116 output has a known regression hash; other counts
are not rejected merely because they lack a known output hash. Unknown
executables fail Pack when a patch is required, and normal Pack rollback keeps
the previous `bin` intact.

The formulas and one-card binary output are statically verified. One-card
Windows runtime behavior is not yet dynamically verified. Counts above 1116
are formula-driven but not runtime verified, and 2166 is inferred from the next
known global address rather than being a general editor limit.

The Project window's **Run** button only launches this already-packed
executable; it never starts Pack or Build implicitly. A successful launch does
not show a modal dialog. A missing or unlaunchable executable is still reported
through the existing error dialog, and both outcomes clean up the background
task state without waiting for the game process to exit.

Project creation and packing use staging directories. Successful work is
committed with an atomic directory rename; a failure removes staging data and
preserves the previous packed output.
Pack runs in the background. The Pack button remains disabled until the task
finishes, full failures go to the application log, and the project window
shows a short resource-aware error without closing.
When the application is launched from a console, bounded Pack progress reports
the current source and compressed-entry count. Raw entries are not sent through
LZSS merely to discard the result.

## Installation

Create and activate a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For development tooling:

```powershell
python -m pip install -r requirements-dev.txt
```

Run the application:

```powershell
python main.py
```

The application loads `yugioh_editor/resources/app.icon` relative to the
installed package, validates it with Qt, and sets it on `QApplication`.

## Visual Studio Code

The repository includes `.vscode/launch.json`.

1. Open the repository folder in VS Code.
2. Select `.venv\Scripts\python.exe` as the Python interpreter.
3. Open **Run and Debug**.
4. Select **YGO Editor - venv**.
5. Press `F5`.

## Tests

Run the complete test suite from the repository root:

```powershell
$env:PYTHONPATH = "."
python -m unittest discover -s tests -v
```

The tests cover architecture boundaries, generic codecs, LZSS round trips,
container validation, logical table mapping, CardService operations, BMP
conversion, raw binary preservation, manifest validation, atomic workflows,
and offscreen UI loading.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — application structure, responsibilities, and data flows.
- [FILE_FORMATS.md](FILE_FORMATS.md) — container, LZSS, deck, card, text, and workspace formats.
- [DEVELOPMENT.md](DEVELOPMENT.md) — development workflow, conventions, testing, and extension guides.

## Current compatibility boundaries

The application implements the known file structures and preserves unknown binary data without interpretation. Original game files should remain backed up. Before distributing a modified build, validate the packed output against the intended game installation and test all affected card, audio, and text content in the game.
