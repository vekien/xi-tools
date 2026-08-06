# 0x38 `CliEventModeLocal` — bit inventory

Opcode `0x38` sets the low word of the client global `CliEventModeLocal`
(`unsigned short`, [xiclient GameManager.h:196](../../thirdparty/xiclient/src/XIClient/include/Game/GameManager.h#L196)).

## ★ CORRECTION (2026-07-06): the client uses the HIGH byte, not the low byte

An earlier version of this doc concluded the **low** byte held the mask (based on
the raw stored-value distribution). That was **wrong** — it measured what's stored
in the operand, not what the handler applies. Both the retail decomp and the XI
server author confirm the actual handler is:

```
CliEventModeLocal = HIBYTE(val) | 0x20        # (val >> 8) & 0xFF, then | 0x20
```

Sources that agree:
- **XiEvents** `research/XiEvents/OpCodes/0x0038.md` (retail FFXiMain.dll decomp).
- **UE5** `EventSession.cpp`:
  `cmd.CameraMode = ((value >> 8) & 0xFF) | 0x20;` — this was RIGHT all along; the
  earlier "UE5 is an MVP simplification, don't copy it" note was mistaken.
- **XI server author**: confirmed `HIBYTE(val) | 0x20`, and that the value
  drives camera/UI mode only — the actual **player movement lock is server-side**
  (`player:startCutscene` → `setLocked(true)` / `FreezePlayerMovement`), NOT this
  opcode.

### Consequence

Because only the high byte is read, virtually every retail value collapses to the
same result:

| stored value | HIBYTE | → CliEventModeLocal |
|--------------|--------|---------------------|
| 0x0003 (Balasiel) | 0x00 | **0x20** |
| 0x2003 (Ailevia)  | 0x20 | **0x20** |
| 0x0013 (77% of retail) | 0x00 | **0x20** |
| 0x001B | 0x00 | **0x20** |
| 0x0093 | 0x00 | **0x20** |
| 0x0103 | 0x01 | 0x21 |
| 0x0413 | 0x04 | 0x24 |

So **99%+ of retail cutscenes set `CliEventModeLocal = 0x20`.** The low-byte
"bits" (0x13, 0x1B, 0x03…) tabulated below are what's *stored*, not what's applied
— they are effectively cosmetic to the 0x38 handler. The compiler now emits
`0x2003` (Ailevia's proven cinematic value), which is identical to Balasiel's
`0x0003` at the client.

## APPLIED-bit semantics (2026-07-20, from the xiclient reimplementation)

> ⚠️ Trust tier: **xiclient is a fan-made reimplementation, not a decompile of the real
> client** (see the [events README source-trust tiers](README.md)). Its class/file names
> below (`ActorTelemetry.cpp` etc.) are invented; treat these semantics as tier-3 until
> byte- or decompile-confirmed.

What the applied byte (`HIBYTE(stored) | 0x20`) actually gates, per
`ActorTelemetry.cpp` (the uninvolved-actor hide pass + `SomePosUpdater`):

| applied bit | stored mask | effect |
|-------------|-------------|--------|
| 0x10 | 0x1000 | **Hide every NPC not involved in the event.** `(CliEventMode\|CliEventModeLocal) & 0x10` → any actor with an NPC server id whose `Flags0` event-involved bit (0x80) is unset gets render-hidden (`Flags0 \|= 0x800000`). Cutscene cast members are exempt — event-init flags them involved (this is another reason the per-cast involvement blocks matter). |
| 0x02 | 0x0200 | Same, for **PC actors** (other players). The local player is always event-involved, so never hidden. |
| 0x04 | 0x0400 | `SomePosUpdater`: event actors update `TalkCameraPos`/`TalkPos` — the "camera tracks the talking NPC" behavior. |
| 0x20 | (forced) | Always set by the handler — base cinematic mode. |

So retail `0x1003` = cinematic + hide all other NPCs; `0x0413` = cinematic +
talk-camera tracking. The editor's Presentation checkboxes map to the STORED
masks (0x1000 / 0x0200 / 0x0400).

## The REAL player lock (what actually matters)

`0x38` does **not** freeze player movement. That happens server-side:
`player:startCutscene(id)` (not `startEvent`) sets the event type to `CUTSCENE`,
which triggers `setLocked(true)` in `charentity.cpp:3205` and puts the player in
`CUTSCENE_ONLY` status ("state 7"). Many event-VM funcs early-bail if the player
isn't in that state. **Always trigger custom cutscenes with `startCutscene`.**

---

## Historical: raw stored-value distribution (low byte)

Kept for reference — this is the distribution of *stored* operand values, useful
for matching a specific retail event byte-for-byte, but remember the client only
applies `HIBYTE | 0x20`.

### Method

1. **Retail distribution** — walked 21,646 events across 256 zones, extracted
   every static `0x38` operand (99% resolvable, 0% runtime-selector), tallied.
2. **xiclient bit tests** — real client checks against `CliEventMode | CliEventModeLocal`
   at [ActorTelemetry.cpp:2240,2246,2309](../../thirdparty/xiclient/src/XIClient/source/World/Actor/ActorTelemetry.cpp#L2240)
   and [GameManager.cpp:1809](../../thirdparty/xiclient/src/XIClient/source/Game/GameManager.cpp#L1809)
   — these read the APPLIED `CliEventModeLocal` (= 0x20 in almost all cases).

## Distribution across retail (21,646 events, 2,034 static ops)

Only **9.1%** of events use `0x38` at all — it's a cutscene-mode opcode, not a
general-purpose one.

| value  | count | share  | notes                                    |
|--------|-------|--------|------------------------------------------|
| 0x0013 | 1564  | 76.9%  | default cutscene mode                    |
| 0x001B | 284   | 14.0%  | 0x13 + bit 3                             |
| 0x0093 | 66    | 3.2%   | 0x13 + bit 7                             |
| 0x0000 | 36    | 1.8%   | reset (scene end)                        |
| 0x0008 | 25    | 1.2%   | bit 3 only                               |
| 0x0012 | 24    | 1.2%   | 0x13 minus bit 0                         |
| 0x0003 | 15    | 0.7%   | bits 0+1                                 |
| 0x0103 | 7     | 0.3%   | 0x03 + bit 8 (high byte)                 |
| 0x0004 | 7     | 0.3%   | bit 2 only                               |
| 0x0006 | 2     | —      | bits 1+2                                 |
| 0x0001 | 2     | —      | bit 0 only                               |
| 0x0413 | 2     | —      | 0x13 + bit 10                            |

## Bit meanings — low byte

Verified in xiclient decompile; the rest inferred from retail combinations.

| bit  | mask  | frequency | meaning                                                 | verified                     |
|------|-------|-----------|---------------------------------------------------------|------------------------------|
| 0    | 0x01  | ~93%      | event VM active — master gate                           | inferred                     |
| 1    | 0x02  | ~99%      | render non-server-id entities (NPCs) during event       | xiclient ActorTelemetry:2246 |
| 2    | 0x04  | ~1%       | allow talk-camera position update (camera tracks NPC)   | xiclient GameManager:1809, ActorTelemetry:2309 |
| 3    | 0x08  | ~15%      | (extra hide / cancel-disabled — appears w/ 0x13)        | inferred                     |
| 4    | 0x10  | ~98%      | render server-id entities (PCs/players) during event    | xiclient ActorTelemetry:2240 |
| 5    | 0x20  | 0%        | (never set in retail; UE5 force-sets when reading hi byte — coincidence) | — |
| 6    | 0x40  | 0%        | (unused)                                                | —                            |
| 7    | 0x80  | ~3%       | rare presentation flag (`0x93` = 0x13 + 0x80)           | inferred                     |

**Interpretation of the top values:**

- **`0x0013`** = event + NPCs visible + PCs visible → **default dialog / cutscene**
  where the player still sees other players and NPCs render normally.
- **`0x001B`** = 0x0013 + bit 3 → same as default plus an extra flag; correlates with
  cutscenes that lock cancellation or extend the entity-hide window.
- **`0x0093`** = 0x0013 + bit 7 → rare presentation variant (top-bit flag; behavior
  not pinned).
- **`0x0000`** = reset value emitted at scene end (all bits clear).
- **`0x0003`**, **`0x0012`**, **`0x0008`** = partial modes hiding either PCs or NPCs.

## High byte

Non-zero in only 0.4% of retail *stored* ops (`0x0103`, `0x0413`) — but per the
correction above it is the byte the client actually **applies** (`HIBYTE | 0x20`),
so it is load-bearing, not legacy. The compiler also derives the server-side
flags from it: `server_flags = (event_mode >> 8) & 0x12` (the stored 0x1000
hide-NPC / 0x0200 hide-PC masks mirrored to the server).

## Compiler default

The [event_cutscene](../../schema/event_cutscene.json) `flags.eventMode` field
accepts a raw u16. The compiler emits **one** default — `EVENT_MODE_DEFAULT =
0x2003` (Ailevia's proven cinematic value; identical to Balasiel's `0x0003` at
the client, since both apply as `0x20`) — with **no scene-shape branching**.

The user can override via `flags.eventMode: <raw u16>` in the JSON when they need
to reproduce an exact retail behavior; the STORED masks behind the Presentation
effects are 0x1000 (hide other NPCs), 0x0200 (hide other PCs), 0x0400 (talk-cam
tracking) — see the applied-bit table above.

## Open items

- **Bit 3 (0x08)** — appears in `0x1B` (14% of ops). Correlate with events that
  have `0x42 cancel_set` cleared vs armed to test the "uninterruptible" hypothesis.
- **Bit 7 (0x80)** — appears in `0x93` (3% of ops). Dump the 66 events that use it,
  check for a shared trait (multi-party cutscene? overlay-heavy?).
- **High byte** — dump the 9 retail events with stored hi != 0 (`0x0103`, `0x0413`)
  and see if they share a category. The compiler emits `0x2003` (hi = 0x20, the bit
  the handler force-sets anyway).

Cross-references: [opcodes.md](opcodes.md#0x20-0x3f--presentation-dialogue-entities-branching),
[cutscenes.md](cutscenes.md), [maat_93_study.md](maat_93_study.md).
