---
name: ygo-project-lifecycle
description: Change YugiohEditor Create Project, project workspace, manifest, Save staging, Pack/Build, Export Files, executable discovery, Run, version prefix, or atomic directory replacement workflows.
---

# YugiohEditor Project Lifecycle

Use for project creation, workspace persistence, manifest handling, Build/Pack, export, launch, and cross-resource transactions.

## Ownership

Read `/AGENTS.md` and project lifecycle sections of `/ARCHITECTURE.md` and `/DEVELOPMENT.md`. Trace through `ProjectView`/controller, `ProjectService`, `ProjectRepository`, `GameRepository`, and connections before changing behavior.

## Atomicity

Project creation and Pack use sibling staging directories followed by atomic replacement. Card save uses a staging clone/transaction. Preserve the prior valid state on every failure path.

Never write partially rebuilt resources directly into the final `bin` directory. Never mutate the original game installation during project creation/editing/Pack.

## Version and executable

The required version prefix comes from the current UI value; there is no Python or manifest fallback. Preserve selected top-level filename casing.

Executable discovery uses the documented `<prefix>_pc.exe` behavior. Source/workspace executable bytes remain exact; Pack-time transformation occurs only through the configured executable pre-encode rule.

An optional project icon is validated before Pack staging, applied only to the
already-patched staged executable, and followed by structural Step 8
verification. Native resource APIs may move raw section data, so verification
must use the current PE section mapping rather than a fixed file offset.

## Pack/Build

Pack reconstructs resources from the project workspace and manifest through repository APIs. Before `begin_pack()` or large container reconstruction, require and validate the physical `card_ids` table with the centralized Joey policy. Derive capacity only from its row count, never maximum Card ID or a generated sidecar. Counts above 1115 require exactly one supported executable and a successful read-only repository preflight. Reuse the exact validated count/plan as ephemeral executable metadata; never persist it in the manifest.

Pack is a retained background task. Reject duplicate Pack requests, surface processing state before expensive work, and restore state on success/failure.

## Run

Run launches only the executable already present under `bin`. It must not silently Pack/Build first. Launch is retained/background work; success has no modal success dialog; failure uses existing reporting and always clears busy/runner state.

## Export Files

When implementing Export Files, define the artifact precisely. If the requirement is to export the decompressed/re-encoded sub-files that would feed container reconstruction:

- reuse the same rule encode pipeline as Pack up to the decompressed sub-file boundary;
- include virtual resources generated from dependencies;
- do not export project CSV/logical tables as substitutes;
- do not copy compressed payloads from the source container;
- preserve relative paths/casing expected by the reconstructed container;
- avoid duplicating encoding logic in the view/service.

Where possible, refactor a repository-level reusable preparation step consumed by both Pack and Export so byte equality can be tested directly.

## Manifest

Manifest records are authoritative for resource identity, source container, relative/workspace path, virtual/physical state, compression, and order. Validation must reject duplicates, invalid/non-contiguous per-source orders, missing physical files, and incomplete card-image pairs as documented.

## Tests

Use project pipeline, manifest validation, repository, discovery/virtual, and UI tests. For Pack/Export refactors assert:

- identical decompressed sub-file bytes between Export and Pack input;
- virtual resources included correctly;
- unchanged source/workspace executable;
- invalid card topology and unsupported extended executable fail before staging/container reconstruction;
- count/plan metadata comes from physical row count and is identical at preflight and staged write;
- post-icon structural-verifier failure discards staging;
- previous `bin` survives failure;
- deterministic repeated Pack;
- duplicate Pack/Run actions are serialized;
- launch does not invoke Pack.
