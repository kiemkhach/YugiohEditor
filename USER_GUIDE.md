# YugiohEditor User Guide

This guide explains the normal end-user workflow for **Yu-Gi-Oh! Power of Chaos Editor**, from creating a project to editing cards, rebuilding the game files, and launching the modified game.

> **Important:** Keep a backup of the original game installation. YugiohEditor works in a project workspace and Pack/Build produces runnable output under the project's `bin` directory, but modified game data should still be tested before distribution.

The screenshots in this guide are maintained with the project evidence in Google Drive under `yugioh/evident/user_guide_images`.

## 1. Start the application

Run the editor with:

```powershell
python main.py
```

The start window contains these fields:

- **Project name** — name of the mod/project. The project directory is created as `<workspace>/<project-name>`.
- **Version prefix** — executable prefix such as `mai`; the project executable is stored as `<prefix>/<prefix>_pc.exe` and the packed executable as `bin/<prefix>_pc.exe`.
- **Workspace folder** — parent directory where editor projects are stored.
- **Game folder** — original Yu-Gi-Oh! Power of Chaos game directory.
- **Icon** — optional `.ico` file for the project executable.

Use **Browse...** to select paths when necessary. The application remembers the last valid workspace folder and may discover the game folder from the Windows Konami registry.

![Start window and Create Project fields](https://drive.google.com/uc?export=view&id=1WWRvWJl6OpOJ13vQu56oFK1ijsD7AH-o)

## 2. Create a project

Before selecting **Create Project**, make sure the game folder contains the files required by the editor:

```text
data.dat
Voice.dat
deck.ydc
Region.dat
<prefix>_pc.exe
```

The editor analyzes the source installation and creates an editable workspace. Container contents are unpacked, known structured binary resources are decoded into editable logical data, and unknown binary resources are preserved.

Project creation uses staging, so a failed operation should not replace an already valid project with partially generated output.

### Load an existing project

To reopen a project, use **Load Project** and select its project folder/manifest as requested by the current UI. Do not manually depend on the original game-folder path: the project is designed to contain the data required for subsequent editing and packing.

## 3. Project window

After a project is opened, the main project window provides the primary actions:

- **Card List** — opens the combined card editor.
- **Save Current File** — saves edits made in the currently selected file editor.
- **Export Files** — reconstructs editable/decompressed project files to a selected destination.
- **Build** — packs the current project into runnable game files under `bin/`.
- **Run** — launches the already-built executable. Run does **not** automatically start Build.
- **Close Project** — closes the current project.

The left side contains the project resource tree. Typical roots include `data`, `deck`, the version executable, `region`, and `voice`. Selecting a file opens the appropriate editor in the main area.

![Project window with text resource selected](https://drive.google.com/uc?export=view&id=1I4dBUGfSeMm7OT9sRWv7haBQ0voX3MNI)

## 4. Browse and edit project files

Select a resource in the left tree. YugiohEditor chooses an editor according to the resource type recorded in the project manifest.

### Structured binary data

Known structured resources are displayed as tables. Examples include `card_prop.bin`, card-property resources, and localized card resources. Edit the supported cells and use **Save Current File** when the file editor requires an explicit save.

![Structured table editor for a project resource](https://drive.google.com/uc?export=view&id=1EJqDJKoOUacM4lkHJ4RR0Jw-uW0YMSDt)

### Text resources

Decoded text resources are shown in a text/table-oriented editor appropriate for the underlying logical data and language encoding.

Supported localized card resources use the language codes:

```text
eng, fra, jpn, spa, ita, ger
```

### Images

Selecting an image displays a preview. Use **Replace Image** to choose replacement artwork. PNG and JPEG card replacements are converted to real BMP payloads by the application when saved through the card workflow.

![Image preview and Replace Image action](https://drive.google.com/uc?export=view&id=1Bi2rSzMFtylhPk0FAPstCyU7c8evs4pC)

### Audio

Selecting a WAV resource displays **Play** and **Replace Audio** controls. Use Play to preview the current sound and Replace Audio to substitute it in the project workspace.

![Audio resource with Play and Replace Audio controls](https://drive.google.com/uc?export=view&id=1hzzUvmgkGwF5Re-dYriecR5kFJd2lH-y)

### Binary and executable files

Unknown binary resources and executable data are preserved as binary resources and can be inspected through the application's binary/hex editing path.

## 5. Card List

Select **Card List** to open the combined card database. The table aggregates information that is physically stored across multiple game resources.

Typical columns include:

- Card Index
- Card ID
- Card Name
- Description
- Password
- Level
- Attack
- Defense
- Attribute
- Card Type
- Category
- Pack

Use **Display Language** to switch localized names/descriptions. The optional unused/empty filter can be used to focus on disabled cards.

The buttons at the bottom provide card-level operations such as **Add Card**, **Update Card**, **Import**, **Export**, and **Suggest** where supported by the current build.

![Card List showing the combined card data](https://drive.google.com/uc?export=view&id=1CXvAad5N9cb79tCK8qjxrNMT8wcazENv)

## 6. Edit an existing card

Double-click a card row, or select it and choose **Update Card**, to open **Card Detail**.

Card Detail exposes the logical card fields together in one window, including localized text, Card ID, image name, password, monster statistics, attribute/type/category, pack assignment, and card artwork.

Use **Previous** and **Next** to move between cards without repeatedly closing the dialog. After changing a card, choose **Save** to validate and persist the change.

The editor validates card data through the card service rather than directly editing unrelated physical `card_*.bin` files.

![Card Detail for an existing card](https://drive.google.com/uc?export=view&id=1mkCSgQ2vC65phIRjDgj4etVoz3ebOpql)

## 7. Add a card

Choose **Add Card** from Card List. The Card Detail dialog opens first and may temporarily show a lookup/initialization state while the editor resolves the new logical slot, Card ID, and reference information.

![Add Card while reference data is being resolved](https://drive.google.com/uc?export=view&id=1SZLLuCfpvwJfRNFm8XESfkCciidCpz4L)

The supported Joey extended-capacity model allows active Card IDs in `0..4094`. ID `4095` (`0xFFF`) is reserved and is never allocated as a normal card ID. Add Card chooses the lowest safe free Card ID and reports a capacity error if no valid slot remains.

Fill or verify the card fields and select **Save** when ready.

### Suggest reference data

The **Suggest** workflow can resolve a canonical card through Konami's official card database and fill missing compatible metadata. Depending on available reference data, it can populate localized names/descriptions and Power of Chaos-compatible properties, and can stage card artwork.

Suggested values should still be reviewed before saving. Staged images are committed with the card only when Save succeeds.

![Add Card after Suggest populated reference data and artwork](https://drive.google.com/uc?export=view&id=1O5eCjmdBQhNIC7QU5uNXE95AYoMLjDRv)

## 8. Replace card artwork

Card Detail shows the large card image and its mini-image preview. Replacement images are processed by Pillow and stored as BMP data compatible with the project resource pipeline.

When a new physical card image is introduced, YugiohEditor updates the Data.dat manifest ordering required by the game. Do not manually reorder generated manifest entries to compensate for image changes.

## 9. Save your work

There are two relevant save levels:

1. **Dialog/file save** — commits the currently edited card or selected resource to the project workspace.
2. **Build** — reconstructs and packs the project into runnable game archives/executable under `bin/`.

Saving a card does not mean that the runnable game in `bin/` has already been rebuilt.

## 10. Export Files

**Export Files** reconstructs the current project into ordinary directories such as:

```text
data/
voice/
deck/
region/
```

For Data and Voice resources, exported entries are final decompressed/re-encoded bytes from the same pre-compression stage used by Build. They are not merely copies of CSV workspace files and are not raw extracts of the original archives.

Export overwrites the files it owns but does not clear unrelated files from the destination directory.

## 11. Build the project

Select **Build** after saving the edits that should be included in the game.

Build runs in the background and temporarily locks conflicting project mutations. The progress indicator at the bottom of the project window shows that packing is in progress. While Build is active, conflicting actions are disabled. Wait for the operation to finish before closing the project or starting another project mutation.

![Build running in the background with the progress indicator](https://drive.google.com/uc?export=view&id=1rJDph0nPxVUZR9EopXA-MTxP65c0xu2v)

Successful packing produces approximately:

```text
<project>/bin/
├── data.dat
├── Voice.dat
├── deck.ydc
├── Region.dat
└── <prefix>_pc.exe
```

The operation uses staging and commits completed output atomically so that a failed build does not intentionally replace the previous valid packed directory with partial output.

### Card-capacity behavior

Build derives card capacity from the physical `card_ids` row count.

- **1115 records** — stock-capacity topology; the executable remains byte-identical with respect to the capacity patch.
- **1116..4095 records** — requires the supported stock Joey executable and installs the extended card-capacity runtime.
- Invalid topology, duplicate/out-of-range active IDs, unsupported executable identity, or an unsupported record count causes Build to fail rather than silently truncate the card table.

Extended card slots above the legacy save-state range require special care: slots `2048..4094` are not persisted by the original `system.dat` format.

## 12. Run the modified game

After a successful Build, select **Run**.

Run launches the executable that already exists in the project's `bin` directory, with that directory as the working directory. It passes the game arguments:

```text
-full -speedy
```

Run never performs an implicit Build. If you change project data after the last Build, build again before running if you want those changes included.

A missing or unlaunchable packed executable is reported as an error.

![Running the packed game from the Project window](https://drive.google.com/uc?export=view&id=1VDb443FbcagTlsif-nDHh1wIYu6FDbIy)

## 13. Recommended workflow

For normal mod development, use this sequence:

```text
Create/Load Project
        ↓
Edit files or cards
        ↓
Save the current card/resource
        ↓
Review Card List / project data
        ↓
Build
        ↓
Run
        ↓
Test in Card List / Card Construction / Duel
        ↓
Return to editor and iterate
```

For projects that extend the original 1115-card topology, runtime testing is especially important. Test opening and leaving Card List/Card Construction, saving/loading decks, restarting the game, and using newly added cards in actual duels.

## 14. Troubleshooting

### Build button is disabled

A project mutation, export, or existing background build may still be active. Wait for the current operation and progress indicator to finish.

### Run does not include my latest changes

Run does not build automatically. Save the edit, select **Build**, wait for it to complete successfully, then select **Run**.

### Card cannot be added

Check whether the project has exhausted the supported active Card ID/slot range or whether the candidate ID conflicts with a protected legacy alias.

### Localized text is missing or rejected

Use one of the supported canonical language codes (`eng`, `fra`, `jpn`, `spa`, `ita`, `ger`) and verify that the project's localized resources are valid. The loader intentionally rejects unsupported language metadata instead of silently renaming it.

### Modified game behaves incorrectly

Do not assume a successful static Build guarantees runtime compatibility. Keep the original game files, verify that the intended Joey executable is being used, and test the affected card, text, image, audio, save/load, and duel paths in game.

## 15. Further documentation

For implementation and file-format details, see:

- [README.md](README.md) — feature overview, installation, and current behavior.
- [ARCHITECTURE.md](ARCHITECTURE.md) — application architecture and responsibility boundaries.
- [FILE_FORMATS.md](FILE_FORMATS.md) — Power of Chaos container and resource formats.
- [DEVELOPMENT.md](DEVELOPMENT.md) — development workflow and extension conventions.
- [JOEY_EXECUTABLE_ARCHITECTURE.md](JOEY_EXECUTABLE_ARCHITECTURE.md) — Joey executable baseline, capacity runtime, patch points, and effect architecture.
