# Joey Executable Architecture

This document is the long-lived executable reference for the supported
**Yu-Gi-Oh! Power of Chaos: Joey the Passion** baseline used by YugiohEditor.
It separates executable behavior from `Data.dat` formats and editor
implementation details.

The document currently covers two major executable subsystems:

1. **Extended Card Capacity Architecture** — the runtime changes required to
   move beyond the stock 1114 active-card limit, with the currently verified
   maximum of 4094 active cards.
2. **Card Effect Architecture** — the effect-record table, callback dispatch,
   external trigger families, hardcoded dependencies, reuse patterns, and the
   limits of current reverse-engineering knowledge.

Additional executable subsystems can be added as independent parts without
renumbering historical research work.

## Baseline and evidence discipline

Unless a section explicitly says otherwise, virtual addresses in this document
refer to this exact baseline:

| Property | Value |
|---|---|
| Executable | `joey_pc.exe` |
| Format | PE32 Windows GUI, Intel i386 |
| ImageBase | `0x00400000` |
| File size | `3,919,872` bytes (`0x3BD000`) |
| MD5 | `75461d4ac813ecd1aa8aeca1ac526629` |
| SHA-256 | `c5749eb934a1cf68d9236e44ff81e98b8aaee486b4f8ebd417440505d44ac1ea` |

For the `.text`, `.rdata`, and `.data` regions documented by the current
research corpus, the baseline commonly satisfies:

```text
VA = file_offset + 0x00400000
file_offset = VA - 0x00400000
```

That relationship must not be copied to another executable build without
verification. Runtime globals such as `0x00A53CCC` or `0x00C24000` are memory
addresses, not raw file offsets.

### Evidence labels

The following labels describe the strength of a statement:

- **Runtime Verified** — observed in the running game with the relevant
  modification or boundary exercised.
- **Disassembly Verified** — confirmed from complete local instructions and
  control/data flow in the supported baseline.
- **Binary Verified** — confirmed directly from executable bytes or data
  layout, without claiming complete consumer semantics.
- **Verified Local Signature** — an external-build semantic has a matching
  instruction/data signature located in the supported baseline.
- **Strong Evidence** — several consistent sources support the semantic, but
  not every execution path or handler ABI has been traced.
- **Hypothesis** — a working interpretation that still needs direct
  disassembly/runtime confirmation.
- **Unknown** — current evidence does not establish the answer.

External wiki addresses, tutorial notes, author notes, and names proposed by
the research documentation remain references until their local semantics are
verified.

---

# Part I — Extended Card Capacity Architecture

## 1. Stock card-capacity model

The stock Joey data set uses:

```text
slot 0                  dummy
active slots             1..1114
active card count        1114
physical record count    1115
```

The executable does not have one universal "card number". At minimum, keep
these namespaces separate:

- **card slot/index** — row position in slot-indexed card tables;
- **Card/Internal ID** — the numeric identifier stored by `CARD_ID.bin` and
  consumed by effects, Fusion logic, voice branches, and other gameplay code;
- **record count** — physical slot-table length including slot zero;
- **reverse-lookup index** — an index into generated `CARD_IntID`;
- **effect-table record count** — independent from card-slot capacity;
- **runtime-state capacity** — the number of per-slot state WORDs available in
  memory.

A literal `1114` or `0x45A` therefore cannot be classified as a card-capacity
bound from the immediate value alone. The same value can be a real internal
Card ID in card-specific gameplay logic.

A simplified verified relationship is:

```text
slot/index
    |
    | GetInternalIdByCardSlot-like path
    v
Card/Internal ID
    |
    +--> effect / Fusion / voice / card-specific logic

Card/Internal ID
    |
    | CARD_IntID reverse lookup
    v
slot/index
```

## 2. Original capacity blockers

Increasing the active-card count required more than replacing one `1114` or
`1115` literal. The verified blocker classes are:

| Blocker | Why it matters |
|---|---|
| Active enumeration bounds | Several loops stop after the stock active range. |
| `CARD_ID.bin` slot guard | Out-of-range slots are rejected before table lookup. |
| Card Construction hover/detail clamps | The last slot can exist yet still be rejected by UI detail paths. |
| `CARD_Prop` 11-bit mask | `AND 0x7FF` wraps slot values above 2047. |
| Fixed runtime card-state storage | Expanding the old live range in place would overlap other globals. |
| High-byte snapshot logic | The original function snapshots only the legacy active state range and uses stack-local storage. |
| Save/load compatibility | Legacy `system.dat` serializes only the original lower state region. |
| Central Card-ID resolver | The old resolver rejects IDs at/above 4000 and applies 2000-based alias logic. |
| Generic 2000-based canonicalizers | Treating every ID >= 2000 as an alias prevents independent high IDs. |
| Packed 12-bit ID paths | At least two verified consumers retain a 12-bit representation and reserve all-ones as invalid. |

These constraints belong to different namespaces and execution paths. A
capacity patch is complete only when all required consumers agree on the same
slot and ID contract.

## 3. Supported extended-capacity contract

**Runtime Verified**

The currently supported architecture has this contract:

```text
slot 0                          dummy

active slots                    1..4094
maximum active cards            4094
maximum active slot             4094 / 0xFFE

maximum physical record count   4095 / 0xFFF

slot 4095 / 0xFFF               reserved / invalid
```

Card IDs use the same upper sentinel boundary but remain a separate namespace:

```text
valid Card IDs                  0x000..0xFFE
maximum valid Card ID           4094
Card ID 0xFFF                   reserved / invalid
```

`4093` is an ordinary valid Card ID. It is not reserved.

The all-ones 12-bit value `0xFFF` is deliberately kept out of the active slot
and Card-ID namespaces.

## 4. Relocated runtime state

### 4.1 Why the stock state array cannot simply grow

The stock per-card state array begins at:

```text
0x00A53CCC
```

Each entry is one 16-bit WORD. Historical analysis found code that operates
over as many as 2048 WORD entries in that region, but memory after the old
array is also used by other live global structures. Therefore expanding the
active range in place to 4095 records is unsafe.

The extended architecture relocates live state instead of overwriting adjacent
globals.

### 4.2 Extended state layout

**Runtime Verified**

```text
relocated state base            0x00C24000
state entry size                2 bytes
structural state capacity       4096 WORDs

slot 0                          0x00C24000
slot 2048                       0x00C25000
slot 4094                       0x00C25FFC
slot 4095                       0x00C25FFE  reserved
state structural end            0x00C26000
maximum active end              0x00C25FFE
```

The active end is exclusive. For a physical card record count `R`:

```text
active_state_end = 0x00C24000 + (R * 2)
```

At the supported maximum `R = 4095`:

```text
active_state_end = 0x00C25FFE
```

### 4.3 PE sections used by the extended runtime

The supported structural patch adds two PE sections:

| Section | RVA | Virtual size | Raw size | Runtime purpose |
|---|---:|---:|---:|---|
| `.ygst` | `0x824000` | `0x3000` | `0` | relocated card state plus global snapshot |
| `.ygsx` | `0x827000` | `0x1000` | `0x1000` | injected helper code and compatibility data |

With ImageBase `0x00400000`:

```text
.ygst state        0x00C24000..0x00C25FFF
.ygst snapshot     0x00C26000..0x00C26FFF
.ygsx helpers      begins 0x00C27000
```

The initialized `.ygsx` bytes are appended to the supported stock file. `.ygst`
is uninitialized runtime storage.

## 5. State-reference relocation

**Disassembly Verified / Runtime Verified as an architecture**

The relocation audit classified references semantically instead of replacing
every occurrence of a numeric address.

The important classes are:

- `STATE_BASE`
- `STATE_HIGH_BYTE_BASE`
- `STATE_SLOT1_BASE`
- `ACTIVE_END`
- `STRUCTURAL_END`

Fixed mappings are:

```text
STATE_BASE             -> 0x00C24000
STATE_HIGH_BYTE_BASE   -> 0x00C24001
STATE_SLOT1_BASE       -> 0x00C24002
STRUCTURAL_END         -> 0x00C26000
```

`ACTIVE_END` is derived from the current physical record count.

The research audit also identified numeric matches that are not genuine
card-state references. Therefore a production patcher must validate complete
instructions/windows from the exact supported source and apply an explicit
classified patch list. Global search-and-replace of `0x00A53CCC`-family
addresses is unsafe.

## 6. Fixed high-byte snapshot

The stock snapshot routine used a stack-local copy sized for the legacy active
range. Merely increasing its loop bound would read or write beyond the
allocated stack snapshot.

The extended architecture replaces the variable stack snapshot with a fixed
global buffer:

```text
snapshot base       0x00C26000
snapshot capacity   4096 bytes
```

It stores one high byte for each structural state slot `0..4095` and restores
the relevant state bit from that snapshot.

The verified rewrite windows are:

```text
snapshot copy      0x0047DCEA..0x0047DCFF
successor          0x0047DD00

snapshot restore   0x0047DD71..0x0047DD8F
successor          0x0047DD90
```

The old dynamic stack-size, DWORD-count, and trailing-WORD capacity model is
superseded by this fixed global snapshot.

## 7. 12-bit `CARD_Prop` indexing

**Runtime Verified**

The stock property accessor masked the slot index to 11 bits:

```asm
0x0040262E
AND ECX, 0x7FF
```

The extended architecture uses:

```asm
0x0040262E
AND ECX, 0xFFF
```

This allows slot `0xFFE` to remain `0xFFE` rather than wrap into the lower
2048-slot range.

The mask intentionally remains 12-bit; slot `0xFFF` is reserved and must never
become active.

## 8. 12-bit Card-ID resolution architecture

### 8.1 Historical resolver

**Disassembly Verified**

The stock central reverse lookup accepts a 16-bit ID, rejects values at or
above `4000`, then applies a 2000-based modulo/alias transformation before
reading `CARD_IntID`.

That behavior was suitable for the stock ID namespace but prevents independent
Card IDs in the upper portion of the new 12-bit range.

### 8.2 Extended resolver

**Runtime Verified**

The extended semantic contract is:

```text
Card ID 0x000..0xFFE
    -> CARD_IntID[Card ID]

Card ID >= 0xFFF
    -> invalid / slot-zero result according to the lookup contract
```

The original lazy-loading path for the reverse table remains part of the
supported executable behavior; only the lookup semantics are generalized.

At the upper verified boundary:

```text
CARD_IntID[0xFFE] -> slot 0xFFE
```

The following masks elsewhere in the executable remain intentional 12-bit
operations:

```text
0x005B91D7  AND EAX, 0xFFF
0x005B9214  AND EAX, 0xFFF
```

## 9. Legacy alias compatibility

The original executable contains a small set of high IDs that represent stock
legacy aliases. Only these nine IDs retain the verified `ID - 2000`
equivalence:

```text
2000 -> 0
2014 -> 14
2034 -> 34
2037 -> 37
2040 -> 40
2063 -> 63
2068 -> 68
2387 -> 387
2389 -> 389
```

These IDs are legitimate existing stock Card IDs, not globally forbidden
values.

Compatibility code therefore uses an explicit alias set at audited consumers
instead of the historical blanket rule:

```text
if id >= 2000:
    id -= 2000
```

That blanket rule is invalid for the extended namespace.

A literal `2000` elsewhere in the executable may instead be an ATK value, DEF
value, effect constant, count, or unrelated immediate. Never globally patch
the literal `2000`.

## 10. Count-dependent patch points

After the fixed structural architecture is installed, exactly 17 audited
capacity sites vary with the physical card record count.

For record count `R`:

```text
maximum_active_slot   = R - 1
exclusive_upper_bound = R
active_state_end      = 0x00C24000 + (R * 2)
```

### 10.1 Maximum active slot — 5 sites

| VA | Semantic value |
|---|---|
| `0x00402315` | `R - 1` |
| `0x0046E5C7` | `R - 1` |
| `0x0046E5CE` | `R - 1` |
| `0x00476339` | `R - 1` |
| `0x00476340` | `R - 1` |

### 10.2 Exclusive upper bound — 6 sites

| VA | Semantic value |
|---|---|
| `0x0043A9B2` | `R` |
| `0x0043AA9D` | `R` |
| `0x00445703` | `R` |
| `0x0047DBFD` | `R` |
| `0x0046E5B3` | `R` |
| `0x00476327` | `R` |

### 10.3 Active-state end — 6 sites

| VA | Semantic value |
|---|---|
| `0x00463F18` | `0x00C24000 + 2R` |
| `0x00463F8B` | `0x00C24000 + 2R` |
| `0x0047DA75` | `0x00C24000 + 2R` |
| `0x0047DC7D` | `0x00C24000 + 2R` |
| `0x005BED0D` | `0x00C24000 + 2R` |
| `0x005BEE16` | `0x00C24000 + 2R` |

At `R = 4095`:

```text
maximum_active_slot   = 0xFFE
exclusive_upper_bound = 0xFFF
active_state_end      = 0x00C25FFE
```

Literal matching alone must not be used to invent additional capacity sites.

## 11. Legacy save-state compatibility bridge

**Runtime Verified for the lower legacy range**

The legacy `system.dat` format persists only the original lower 2048 state
slots. The extended runtime therefore keeps a compatibility bridge between the
relocated state and the old RAM block.

### Save

```text
relocated slots 0..2047
    -> legacy block beginning 0x00A53CCC
    -> original checksum/write routine
```

The copy must occur before the original checksum/write routine computes the
save integrity fields.

### Load

```text
legacy block beginning 0x00A53CCC
    -> relocated slots 0..2047
```

The current architecture does **not** serialize relocated slots `2048..4094`
across a full process restart. That is a separate save-format extension problem
and must not be described as implemented.

## 12. Verification status

The architecture has several distinct verification layers:

- **Runtime Verified:** stable card-list, Card Construction, high-slot lookup,
  state indexing, and duel use were exercised with **4094 active cards**.
- **Disassembly/Binary Verified:** the stock blocker classes, effect of the
  relevant masks/bounds, relocation reference classes, and supported baseline
  layout were audited against the local executable.
- **Production static verification:** YugiohEditor validates the supported
  stock executable, structural PE changes, helper fragments, fixed patch
  regions, and all dynamic capacity sites while packing.
- **Not implemented:** restart persistence for relocated slots `2048..4094`.

Runtime validation confirmed stable operation with 4094 active cards. That is
the currently verified active-card ceiling of this architecture, not a claim
that every unrelated game subsystem has an unbounded 12-bit design.

---

# Part II — Card Effect Architecture

## 13. Effect-system boundary

The executable effect system is not a single table.

**Disassembly Verified / Strong Evidence**

A central `EffectRecord` table provides a multi-stage callback dispatch layer,
but complete card behavior can additionally depend on:

- trigger or activation-family membership;
- Spell/Trap subtype and spell speed;
- continuous-rule evaluation;
- summon-state hooks;
- phase and battle-event hooks;
- target/selection-list lifecycle;
- named-card constants and direct Card-ID comparisons;
- Fusion/Ritual data outside the effect table;
- runtime flags/counters;
- AI-specific behavior.

Copying a 24-byte effect record therefore does not necessarily clone an entire
card effect.

## 14. Effect table

### 14.1 Location and size

**Disassembly Verified**

```text
file start       0x001ED0A8
VA start         0x005ED0A8

file end         0x001EFA30  exclusive
VA end           0x005EFA30  exclusive

record size      0x18 / 24 bytes
record count     443
last index       442 / 0x1BA
```

The exact span is:

```text
0x001EFA30 - 0x001ED0A8 = 10632 bytes
10632 / 24 = 443 records
```

### 14.2 Record layout

A useful verified representation is:

```c
typedef struct EffectRecord {
    uint32_t internal_id;   // +0x00; consumers compare the low WORD
    void *handler_B;        // +0x04
    void *handler_C;        // +0x08
    void *handler_D;        // +0x0C
    void *handler_E;        // +0x10
    void *handler_F;        // +0x14
} EffectRecord;             // sizeof = 0x18
```

**Binary Verified:** non-zero B-F values point into executable `.text`, not to
a compact effect-enum namespace.

The research corpus observed:

```text
non-zero B-F references     1023
unique non-zero addresses   482
minimum observed pointer    0x00547CE0
maximum observed pointer    0x005999D0
```

These counts describe the audited baseline table; they are not a guarantee that
every unique address is the start of a universally callable function.

## 15. Effect lookup and dispatch wrappers

### 15.1 Binary-search lookup

**Disassembly Verified**

`0x0059DBE0` performs a binary search over record indexes `0..442`.

A proposed descriptive name is:

```text
FindEffectRecordIndexByInternalId
```

The name is documentation terminology, not an original symbol.

Because the lookup is binary search, records must remain sorted by internal
Card ID. A record that exists physically but is inserted out of order may not
be found.

### 15.2 Important wrappers

The following addresses are verified local consumers of specific record
fields:

| VA | Field | Verified behavior | Semantic interpretation |
|---|---|---|---|
| `0x0059DEB0` | B / `+0x04` | invokes B callback | **Strong Evidence:** primary effect/resolution action |
| `0x0059DC50` | C / `+0x08` | invokes C through encoded card/side/slot context | **Strong Evidence:** applicability/target-filter stage |
| `0x0059DCA0` | C / `+0x08` | C-wrapper family | **Strong Evidence:** applicability/target-filter stage |
| `0x0059DD20` | C / `+0x08` | C-wrapper family | **Strong Evidence:** applicability/target-filter stage |
| `0x0059DD90` | D / `+0x0C`, then C | invokes D before C-dependent processing | **Strong Evidence:** activation/eligibility condition stage |
| `0x0059DE30` | E / `+0x10` | direct callback wrapper | **Strong Evidence:** cost stage |
| `0x0059DE70` | F / `+0x14` | direct callback wrapper | **Strong Evidence:** target/selection/finalization stage |

The semantic names B=Effect, C=AppliesTo, D=Condition, E=Cost, and F=Target
are useful normalized terminology and agree with tutorial/toolset evidence, but
they do **not** define one universal ABI. Exact prototypes, input structs,
required pre-state, and return semantics can differ by handler.

## 16. Zero-pointer records are not necessarily effectless

The baseline contains exactly 15 effect records whose B-F pointers are all
zero.

That is useful spare-record capacity for some modifications, but it is not an
engine-level "15 new effects" guarantee and does not imply that the associated
cards have no behavior.

Some cards execute relevant behavior through:

- hardcoded card-ID checks;
- continuous/stat hooks;
- phase hooks;
- battle rules;
- summon logic;
- other external registries.

Therefore:

```text
B == C == D == E == F == 0
```

must not be interpreted as proof that the card is behaviorless.

## 17. Trigger and activation families outside `EffectRecord`

**Strong Evidence**

Research and external Toolset structure identify separate registration/hook
families for behavior such as:

- FLIP effects;
- manually activatable/click monster effects;
- inherent Special Summon procedures;
- Special Summon conditions;
- Normal Summon triggers;
- Special Summon triggers;
- sent-to-Graveyard triggers;
- banish-on-leaving-field replacement;
- phase events;
- stat-change evaluation;
- after-damage-calculation behavior;
- summon-state initialization;
- selection-list population;
- spell speed.

The critical architectural consequence is that **activation and resolution are
separate layers**.

For example, assigning a manually activated monster's resolution callback to a
FLIP monster does not by itself change the recipient's trigger into a click
effect.

Any editor or patch design that exposes reusable effects must therefore track
activation/trigger dependencies separately from the B-F callback tuple.

## 18. Hardcoded card-specific dependencies

**Strong Evidence; exact locations vary by case**

The executable contains many direct Card-ID comparisons and supporting logic
outside the central effect table.

Known dependency families in the research corpus include combinations such as:

- Multiply -> Kuriboh;
- Thousand Knives -> Dark Magician;
- Cyber Shield / Elegant Egotist -> Harpie-family cards;
- Cyclon Laser -> Gradius;
- Flute of Summoning Dragon -> Lord of D.;
- Jam Defender -> Revival Jam;
- Cocoon evolution -> Petit Moth-family state;
- Umi-related cards and continuous rules;
- Toon World -> Toon-family behavior.

These examples demonstrate the dependency pattern; they do not establish that
every named relationship is controlled by one easily replaceable immediate.

When modifying a card effect, a callback tuple can be correct while an
unpatched named-card comparison, trigger list, timing check, or state-machine
dependency still produces incorrect behavior.

## 19. Reusable/common effect families

The baseline table and supporting research show substantial handler reuse.

The following are **Strong Evidence** reuse families rather than claims of one
universal function per semantic:

- target + destroy;
- discard costs;
- tribute costs;
- LP payment;
- burn / LP damage;
- LP healing;
- draw / discard / hand manipulation;
- Deck or Graveyard search/add-to-hand;
- Deck, Graveyard, or Fusion Deck selection and movement;
- Special Summon;
- token creation;
- generic Equip handling;
- Field Spell / global aura handling;
- Ritual Spell execution;
- ATK/DEF modification;
- battle-position and face-state changes;
- battle modifiers;
- negate/suppression/action locks;
- sent-to-Graveyard and leave-field behavior;
- phase/delayed effects;
- coin/dice/random branches;
- control-changing behavior;
- specialized multi-step state machines.

### 19.1 Examples of strong callback reuse

Research over the complete table found repeated structures for:

- many Equip Spells sharing common B/C/F callback patterns;
- classic Field Spells and later attribute field spells sharing a common core;
- Ritual Spells sharing core effect/condition handlers;
- burn Spells sharing resolver/finalizer patterns;
- healing Spells sharing resolver families;
- recruiter monsters sharing a summon-from-Deck core with different filters.

This reuse is the foundation for safe effect templating, but parameters,
triggers, target UI state, and external constants still require compatibility
analysis.

## 20. Native helper primitives

**Strong Evidence from the Toolset and local effect architecture**

The broader PoC modding corpus exposes wrappers around operations including:

- card metadata lookup;
- LP payment and effect damage;
- moving/sending a field card;
- discarding from hand;
- Graveyard banish;
- selection-list lifecycle;
- adding a selected card to hand;
- Special Summon;
- summon-zone checks;
- tribute selection;
- card copying;
- effect invocation;
- selectors by card type, attribute, ATK/DEF, or position;
- generic command/move-card operations.

These primitives support the conclusion that many original effects are
compositions of shared engine operations. They are evidence of reusable engine
capabilities, not proof that arbitrary callback combinations are ABI-safe.

## 21. Safe modification patterns

### 21.1 Reusing a complete known-compatible record

The lowest-risk table-level modification is to reuse a complete callback tuple
whose activation family and external dependencies are already compatible with
the recipient.

### 21.2 Repointing the record key

A spare/repurposed record can be associated with another internal Card ID only
if:

- the table remains sorted;
- the target ID resolves to the intended card;
- external trigger/continuous dependencies are also satisfied.

### 21.3 Combining known components

Tutorial and research evidence show that some cost/target/resolution pieces can
be combined. A combination is not safe merely because each pointer works on its
source card.

Before combining components, verify at least:

- activation family;
- expected callback ABI;
- selection producer/consumer lifecycle;
- target encoding;
- card location/player-side context;
- required state initialized by earlier stages;
- cancellation/finalization behavior;
- named-card dependencies;
- spell speed/chain class where relevant.

### 21.4 Patching external dependencies

Some effect changes require modifying `.text` comparisons, trigger membership,
constant sources, or supporting tables in addition to the 24-byte record.

Such changes are executable-version-specific and require exact local source
instruction verification.

### 21.5 What not to do

Do not:

- assume B-F pointers are independent plug-ins;
- deduplicate binary records solely because two cards look semantically equal;
- infer a callback's numeric parameter source from the card's printed text;
- copy an address from a different executable build;
- treat a zero callback tuple as proof of no behavior;
- treat one successful player-controlled duel as proof that AI usage is safe.

## 22. Version-specific address policy

All concrete virtual addresses in this document are scoped to the supported
baseline fingerprint listed at the beginning.

External references for other Joey builds are useful for:

- locating semantic signatures;
- identifying likely structures;
- comparing instruction/data patterns.

They are not local patch addresses.

Before applying a patch to another build:

1. fingerprint the executable;
2. locate the complete instruction/function/table by signature and context;
3. verify its consumers and operand widths;
4. establish the equivalent semantic role;
5. record the new build-specific location separately.

A constant delta observed for one group of UI instructions must not be assumed
for unrelated code/data regions.

## 23. Effect-capacity boundary

The stock effect table is:

```text
443 records
24 bytes per record
binary-search range 0..442
```

The 15 all-zero B-F records are existing reusable records, not evidence that
the table has unlimited spare capacity.

Expanding beyond the original 443-record array is a separate executable
engineering problem because it can require:

- moving or extending the table;
- changing the hardcoded binary-search bound;
- auditing every direct table-base reference;
- preserving sort order;
- ensuring adjacent `.data` structures are not overwritten.

The card-slot expansion to 4094 active cards does not by itself expand the
number of effect records.

## 24. Known unknowns

The following remain unresolved or incomplete and should be explicit future
reverse-engineering targets.

### 24.1 Callback ABI

**Unknown:** exact prototypes and complete semantic contracts for all 482
unique non-zero B-F handler addresses.

### 24.2 Compatibility groups

**Unknown:** the complete set of B/C/D/E/F tuples that can be mixed safely
beyond combinations already observed in stock cards or demonstrated by known
experiments.

### 24.3 Numeric parameter sources

**Unknown:** the exact source of every amount used by shared heal, burn, stat,
counter, and similar handler families. Some amounts are likely outside the
callback tuple.

### 24.4 Trigger membership maps

**Incomplete:** full baseline mapping of card IDs registered for
activatable/FLIP/phase/summon/Graveyard/battle hook families.

### 24.5 Spell speed and subtype interaction

**Incomplete:** exact relationship between `card_prop` subtype, spell-speed
registries, and every activation-dispatch path.

### 24.6 Selection ABI

**Unknown:** which selection-list producers and consumers can be safely
parameterized or recombined without hidden UI/runtime state assumptions.

### 24.7 Table-only versus code patches

**Incomplete:** complete classification of effect changes that need only a
record edit versus those that also require `.text`, trigger lists, constants,
or external data structures.

### 24.8 AI compatibility

**Unknown for many templates:** a player-controlled effect can function while
AI activation, targeting, or summon behavior remains wrong or absent.

## 25. Effect verification guidance

For a modified effect, gameplay verification should cover more than successful
resolution:

- activation is offered at the correct time;
- false conditions prevent activation;
- costs are paid exactly once;
- cancellation leaves valid state;
- target lists contain the correct cards;
- selection confirm/cancel works;
- resolution behaves on the correct player/zone;
- no stale highlight/dialog/selection state remains;
- Spell/Trap chain behavior is correct;
- once-per-turn or delayed state behaves correctly;
- phase transitions and leave-field cleanup remain stable;
- AI is smoke-tested when AI compatibility is claimed.

Static pointer validity is not sufficient runtime evidence.

---

# Future executable subsystems

The document is intentionally structured for later additions such as:

- **Part III — Duel Runtime Structures**
- **Part IV — AI Logic**
- **Part V — UI and Card-List Executable Logic**
- **Part VI — Deck and Save Interactions**
- **Part VII — Animation and Sound Dispatch**
- **Part VIII — Other Hardcoded Limits**

Those sections should be added only when their evidence is sufficiently
consolidated.

---

# YugiohEditor integration boundary

YugiohEditor owns the supported executable transformation through the existing
physical `*_pc.exe` binary rule and its Pack-time `pre_encode` pipeline.

Application-level rules remain:

- source and workspace executable bytes are immutable;
- only Pack staging is transformed;
- card capacity is derived from the physical `card_ids` row count;
- count `1115` keeps the executable unchanged;
- counts `1116..4095` require the exact supported stock baseline;
- unsupported topology or executable fingerprints fail closed;
- optional icon-resource updates occur after the structural executable patch;
- structural executable invariants are reverified after resource updates.

This document owns the executable semantics and patch architecture.
`ARCHITECTURE.md`, `FILE_FORMATS.md`, and `DEVELOPMENT.md` should reference this
document rather than duplicate executable address maps.

---

# Sources and evidence

The durable research corpus is organized under the project's `yugioh` Google
Drive folder.

## Core executable research

- [Executable knowledge root](https://drive.google.com/drive/folders/1E7Rp5nm8ejxpLq_8Lv0j25ORkvrM53Vc)
- [Core executable knowledge](https://drive.google.com/drive/folders/18sjl-pwZTzWjf1EIksb-By46SB0sX8Im)
- [Reverse-engineering knowledge base](https://drive.google.com/file/d/1R_RBkHQFo6AIHMNj3pwd8HSD-8XOX1YI/view?usp=drivesdk)
- [Executable modification map](https://drive.google.com/file/d/1vufEdhHsM8yAHeXymFirmeZyVgh9_7hW/view?usp=drivesdk)
- [Executable patch-point catalog](https://drive.google.com/file/d/1hLn-GU7uCvhgAWfS5On_EPW8bckCc0ff/view?usp=drivesdk)
- [Core offset index](https://drive.google.com/file/d/1Ss9kd5BoC-Eq6iKknLCFUFDcncDgfkcR/view?usp=drivesdk)
- [Cross-version signature map](https://drive.google.com/file/d/1Qv-Imd5Kc7Bn_pnEpzTNox4ElhnsmfRz/view?usp=drivesdk)
- [Author-note location catalog](https://drive.google.com/file/d/1UnPYRdq4ogObPQWOgNfPZMEMk7qXjv25/view?usp=drivesdk)
- [Video-derived research notes](https://drive.google.com/file/d/1-GEP49p0zBUSdlVKBWH7Bt7yA5PB0TRQ/view?usp=drivesdk)

## Card-capacity research

- [Card-capacity knowledge folder](https://drive.google.com/drive/folders/1os13yR6bfdDVLkVDb2QvilnAxSfsiQMx)
- [Internal Card-ID namespace](https://drive.google.com/file/d/1zlTFbhY27OnMj9k6HUVl9lg_64tCLzGr/view?usp=drivesdk)
- [Executable >2048 blocker analysis](https://drive.google.com/file/d/121zfyG0UtColAWIwrabhHPAQ52vrOJt9/view?usp=drivesdk)
- [Card-capacity patch map](https://drive.google.com/file/d/1tuLZIdgeh2Jl5KPSE1-Vv3cX2HEu2NnI/view?usp=drivesdk)
- [Card-slot memory model](https://drive.google.com/file/d/1kV3vs8AZbffq-SdncawerTyKRNVwjn79/view?usp=drivesdk)
- [State-relocation reference audit](https://drive.google.com/file/d/10qIPsEN9myNZvsXl_pX11e5Zd3e9wGbQ/view?usp=drivesdk)
- [Internal-ID blocker audit](https://drive.google.com/file/d/1GdfMn4Mtq3I7cII9c9oM-QNPVPiph1rh/view?usp=drivesdk)
- [Extended-capacity blocker table](https://drive.google.com/file/d/1XFG1QIrix4uNzJIErwDGgBz8Uf4LDEo-/view?usp=drivesdk)
- [Card-list capacity model](https://drive.google.com/file/d/1kKZ0o4h__BQ6Jc25WwzkUNDHVkNtcukg/view?usp=drivesdk)
- [Card/data slot contract](https://drive.google.com/file/d/1g6ACOBvzd1BQeaSscuwEZx7ByQPUnfLa/view?usp=drivesdk)

## Save/runtime-state research

- [Save/runtime-state knowledge](https://drive.google.com/drive/folders/1sc0sDhLal315Gq-yDRaVThaPKRP8bERL)
- [`system.dat` save format](https://drive.google.com/file/d/11K6qzOIwbtSjX3QVOxUQt5GRohV2zaur/view?usp=drivesdk)
- [`system.dat` offset map](https://drive.google.com/file/d/1HnB-WRG5Xgq6ZRgT9Obv390KrEeRod6P/view?usp=drivesdk)

## Effect research

- [Effect research root](https://drive.google.com/drive/folders/1bb1VMuIuXE9R1lh1QW1bzybtXqJ0JYYq)
- [Effect inventory](https://docs.google.com/document/d/1vmG5Zum9qX3oeJf8hpwgjLJfS4Z7Joxj22zT6G1L708/edit?usp=drivesdk)
- [Normalized effect taxonomy](https://docs.google.com/document/d/116vKDvDciqKmLoCMPQGsyKyde6fc-f0Q5nSTcYmFp6w/edit?usp=drivesdk)
- [Effect component reuse map](https://docs.google.com/document/d/13gMTuwZLy-dggFnd_sFMTN2mxNHrUiMonDRWeJ-zw_0/edit?usp=drivesdk)
- [Compatibility constraints and open questions](https://docs.google.com/document/d/1elNOv3kW13W_pX6iNq3plfw9zbqwLiogig1JEdUeh1w/edit?usp=drivesdk)

The future SDK proposal branch is intentionally not used as evidence for the
original executable architecture. Proposal documents may reference this file,
but proposed SDK abstractions must not be promoted into facts about
`joey_pc.exe`.
