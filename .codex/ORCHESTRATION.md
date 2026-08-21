# Codex multi-agent orchestration

This repository uses `.codex/skills/ygo-task-orchestrator/SKILL.md` as the mandatory planner for non-trivial prompts. The root `AGENTS.md` requires Codex to run task detection and planning before production edits.

## Goal

The normal user experience is intentionally simple:

```text
user prompt
  -> automatic task/domain detection
  -> automatic skill selection
  -> investigation when needed
  -> implementation agent(s)
  -> integration
  -> independent review
```

The user normally should not need to name a skill or agent.

## Available YugiohEditor skills

| Skill | Responsibility |
|---|---|
| `ygo-task-orchestrator` | Detect domains, choose/order skills, decide investigation, agents, dependencies and review |
| `ygo-investigate-change` | Root cause, evidence, current flow, design/specification before code |
| `ygo-implement-change` | Production implementation under repository architecture/invariants |
| `ygo-review-change` | Independent requirement/diff/test/regression review |
| `ygo-container-binary` | Containers, codecs, compression, virtual files, ordering, reconstructed/exported bytes |
| `ygo-card-domain` | Card tables, ID/index/sort/reverse lookup, images/catalogs, card save semantics |
| `ygo-pyside-ui` | PySide6 windows/dialogs, busy state, workers, event loop, progress and UI lifecycle |
| `ygo-provider-suggest` | Provider lookup, Suggest/Bulk Suggest, caches, concurrency, cancellation |
| `ygo-executable-re` | `joey_pc.exe`, ASM/RE, capacity/effect patches, executable profiles, runtime evidence |
| `ygo-project-lifecycle` | Start/Create/Load, manifest, workspace, staging, Pack/Build/Export/Run |

## Default orchestration patterns

### Small mechanically clear change

```text
ygo-task-orchestrator
  -> ygo-implement-change + domain skill
  -> targeted verification/review
```

Example: add known launch arguments to the existing Run command.

### Unknown bug or performance issue

```text
ygo-task-orchestrator
  -> Investigator [ygo-investigate-change + domain skills]
  -> Implementer [ygo-implement-change + domain skills]
  -> Reviewer [ygo-review-change + domain skills]
```

Example: determine why Save blocks before a progress bar becomes visible.

### Cross-domain feature

```text
ygo-task-orchestrator
  -> Investigator/Architect
  -> establish contracts
  -> independent implementation streams where safe
       |- Backend/Domain agent
       `- UI agent
  -> Integration
  -> Reviewer
```

Example: Export Files combines project lifecycle, container reconstruction and UI progress.

### Executable/reverse-engineering task

```text
ygo-task-orchestrator
  -> Executable Investigator [investigate + executable-re]
  -> optional Card/Container Investigator when data structures are involved
  -> Executable Implementer [implement + executable-re]
  -> static regression verification
  -> independent Reviewer [review + executable-re]
  -> explicit Windows/runtime verification when the claim requires it
```

Do not collapse static verification into a claim of runtime correctness.

## Ownership-oriented agent decomposition

Agents are ephemeral workers; skills are persistent procedures. Split agents by responsibility:

- investigation/evidence;
- backend/container;
- card domain;
- UI;
- provider/concurrency;
- executable RE;
- integration;
- independent review.

Do not split work by arbitrary file count.

When parallel implementation is useful, assign non-overlapping ownership and use separate worktrees/branches where available. Establish shared APIs/schema/transaction contracts before parallel work begins.

## Replanning

Every plan is evidence-driven. If current code, logs, binary data, tests, or runtime observation disprove the initial hypothesis, the orchestrator must update:

- the domain classification;
- selected skills;
- agent split;
- execution order;
- acceptance tests.

Rejected hypotheses stay documented as rejected rather than being quietly implemented.

## Completion gate

A substantive task is complete only when:

1. the original requirement is addressed;
2. architecture ownership is respected;
3. relevant invariants remain valid;
4. targeted regression tests pass;
5. cross-domain integration is checked where applicable;
6. independent review has no unresolved blocking finding;
7. static versus runtime verification is reported accurately.

For repository-wide validation follow the commands in `AGENTS.md`.