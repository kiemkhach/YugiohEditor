---
name: ygo-task-orchestrator
description: Mandatory first-step planner for non-trivial YugiohEditor prompts. Classifies the task, detects affected domains, selects and orders YugiohEditor skills, decides whether investigation is required, decomposes work into safe agents/worktrees, defines handoffs and verification, and replans when evidence changes.
---

# YugiohEditor Task Orchestrator

Use this skill first for every non-trivial YugiohEditor task. Its job is to plan and route work before production code is changed.

Do not implement while the task still depends on an unresolved hypothesis, unknown root cause, binary semantic, executable behavior, ordering rule, concurrency behavior, or cross-layer contract.

## 1. Build a task map

Read the user request, `/AGENTS.md`, and current repository state. Treat the current code as authoritative over old prompts. Extract:

- requested outcomes and explicit non-goals;
- affected user workflows;
- likely architecture layers;
- data/binary/runtime invariants at risk;
- unknowns that must be proved;
- independent work boundaries;
- acceptance criteria and required tests.

Classify complexity:

- **Trivial**: localized mechanical change with known behavior and negligible regression surface.
- **Small**: one domain, behavior known, limited files/tests.
- **Medium**: multiple layers or a root cause that must be traced.
- **High**: binary/reverse-engineering uncertainty, cross-domain architecture, concurrency, container reconstruction, executable work, or multiple independently implementable streams.

## 2. Detect domains and select skills

Select every relevant domain skill; do not force one task into only one domain.

### Container / binary -> `ygo-container-binary`

Use for `Data.dat`, `Voice.dat`, `Region.dat`, `KCEJYUGI`, LZSS, compression, entry ordering, sub-file rules, raw/decoded/decompressed representations, virtual resources, codecs, Build reconstruction, or decompressed Export Files.

### Card domain -> `ygo-card-domain`

Use for Card List/Card Detail data, `card_id`, `card_intid`, `card_sort`, `card_prop`, `card_pass`, indexed card text, card images/catalogs, Add Card, card save transactions, logical cards, card index/ID semantics, and card-record growth.

### PySide UI -> `ygo-pyside-ui`

Use for windows/dialogs, `.ui` layout, progress/loading, UI freezes, maximized state, button/action state, `TaskRunner`, `QThreadPool`, signals, event-loop sequencing, cancellation UI, or background work ownership.

### Provider / Suggest -> `ygo-provider-suggest`

Use for Suggest/Bulk Suggest, external provider clients, image download, name/description merging, caches, in-flight deduplication, RAM-aware concurrency, cancellation, timeouts, and provider fallbacks.

### Project lifecycle -> `ygo-project-lifecycle`

Use for Start, Create/Load Project, workspace, `project.json`, migration, staging, Pack/Build, Export Files, Run, output executable, application preferences, registry discovery, and atomic project/bin replacement.

### Executable RE -> `ygo-executable-re`

Use for `joey_pc.exe`, PE resources, ASM/disassembly, executable profiles, card-capacity expansion, hard-coded card/effect logic, runtime state, `system.dat` relationships, patch points, IDA evidence, or runtime verification.

### Workflow skills

- Investigation: `ygo-investigate-change`
- Production implementation: `ygo-implement-change`
- Independent verification/review: `ygo-review-change`

## 3. Decide whether investigation is required

Investigation is mandatory before production implementation when any of these is true:

- root cause is unknown;
- the prompt asks to investigate, infer, reverse engineer, compare, diagnose, or discover a rule;
- binary layout/semantics or original-game behavior is not already proven;
- executable behavior or patch safety is involved;
- ordering, encoding, compression, virtual-generation, or data-corruption behavior is uncertain;
- the performance bottleneck or GUI-thread blocking location is unknown;
- concurrency, cancellation, transaction, race, or rollback behavior is involved;
- a change crosses architecture boundaries and the ownership/API contract is not already clear;
- the requested design relies on a hypothesis or stale documentation;
- multiple agents need a shared contract before they can work safely.

Investigation may be folded into implementation only for Trivial/Small changes whose current behavior, owner, and expected result are mechanically clear.

## 4. Choose the skill order

Default non-trivial sequence:

1. `ygo-task-orchestrator`
2. `ygo-investigate-change` when required
3. relevant domain skill(s) during investigation
4. `ygo-implement-change`
5. relevant domain skill(s) during implementation
6. integration verification when multiple agents changed related behavior
7. `ygo-review-change`
8. relevant domain skill(s) during review

Domain skills are context, not sequential ceremonies. When two domain skills apply to the same coherent change, one agent can use both.

Do not start implementation before mandatory investigation has produced a sufficiently concrete contract.

## 5. Decide single-agent vs multi-agent

Prefer one implementation agent when the change is coherent and files/behavior overlap heavily. Multi-agent is useful only when it creates real independence.

Split by responsibility/ownership, never by arbitrary file count.

Good independent roles include:

- **Investigator/Architect**: read-only root-cause/evidence/design work;
- **Container/Backend Implementer**: repositories/services/codecs/container pipeline;
- **Card-domain Implementer**: logical card model/table/image semantics;
- **UI Implementer**: `.ui` and view/event/thread behavior after backend contracts are known;
- **Executable RE Agent**: evidence/patch-profile work isolated from unrelated UI/domain changes;
- **Provider Agent**: network/cache/concurrency behavior;
- **Integration Agent**: reconcile contracts when independently produced changes meet;
- **Reviewer**: independently inspect requirement, plan, diff, tests, invariants, and unresolved assumptions.

Do not create multiple agents for a change that can be safely completed by one owner faster and with less merge risk.

## 6. Parallelism rules

Parallel work is allowed only after dependencies are explicit.

Run agents in parallel when:

- ownership boundaries are independent;
- they do not need to edit the same core files;
- their APIs/contracts are already established;
- one agent's unresolved findings are not prerequisites for the other;
- results can be integrated and tested deterministically.

Run sequentially when:

- investigation must establish the architecture or binary rule first;
- backend API/schema must exist before UI can correctly bind to it;
- agents would modify the same service/repository/manifest transaction;
- executable semantics are unresolved;
- ordering or representation semantics are unresolved;
- one output is a direct prerequisite of the next task.

For parallel work, use separate branches/worktrees where available. Assign explicit file/behavior ownership and prohibit agents from silently changing another agent's contract.

## 7. Agent handoff contract

Every agent handoff must include enough information for the next agent to proceed without reconstructing hidden assumptions.

### Investigation handoff

Include:

- original requirement;
- current behavior/trace;
- evidence and evidence level;
- confirmed root cause or clearly marked unresolved points;
- affected layers/files/functions;
- invariants/non-goals;
- proposed contract/design;
- tests/acceptance criteria;
- recommended skill set and agent split.

### Implementation handoff

Include:

- changed files;
- behavior implemented;
- deviations from the approved design and why;
- tests run/results;
- remaining risks or unverified runtime behavior.

### Review handoff

Include findings ordered by severity, evidence, required fixes, tests run, and whether the task meets acceptance criteria. Review must not simply trust the implementer's summary.

## 8. Required plan format

For Medium/High tasks, expose a concise plan before editing with this structure:

```text
Task assessment
- Complexity: <...>
- Primary domain: <...>
- Secondary domains: <...>
- Investigation required: Yes/No + reason

Selected skills
1. ygo-task-orchestrator
2. ...

Agent strategy
- <role>: <ownership> [sequential/parallel dependency]

Execution phases
1. ...
2. ...

Verification
- targeted tests
- cross-domain/integration checks
- full suite if warranted

Open assumptions
- ...
```

For Trivial/Small tasks, the plan may be compact, but skill/domain detection still occurs internally.

## 9. Re-plan triggers

The plan is provisional until evidence supports it. Stop and revise the plan when:

- investigation rejects a hypothesis;
- current code differs materially from the prompt/design;
- an unexpected architecture owner is discovered;
- a supposedly local change affects a binary/container/card/executable invariant;
- two planned parallel agents would edit overlapping contracts;
- tests expose a different root cause;
- a required runtime fact cannot be statically verified.

Record rejected hypotheses as rejected; never keep coding toward them for consistency with the initial plan.

## 10. Review is mandatory for substantive changes

Every Medium/High production change must end with `ygo-review-change`. Small changes should still receive a targeted self/independent review when practical. Review uses the original user requirement, not only the implementation plan.

For binary, executable, card-capacity, container, transaction, and concurrency changes, independent review is mandatory.

## 11. Do not over-orchestrate

The orchestration system is intended to reduce mistakes, not generate ceremony.

- Do not spawn an architect, coder, tester, and reviewer for a two-line mechanical change.
- Do not duplicate investigation already proven in durable project knowledge unless current evidence conflicts.
- Do not ask the user to choose skills when the repository can route them automatically.
- Do not block progress on details that can be safely resolved from current code/tests.

The user should normally be able to provide only the task. The repository instructions and this skill are responsible for selecting the workflow.