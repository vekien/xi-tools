# Ability Mixer — when a published action looks wrong in game

A published action goes through several layers, and a bug in any one shows up the
same way: the action *plays*, but the pose or the effect is wrong. The DATs can be
**each individually correct** while the client loads the wrong one. So diagnose
from the layers, and from what the client actually **opens** — not from the bytes
you composed.

This page is the checklist that would have found the 2026-09-20 stretched-model
bug on the first pass instead of the third.

## The layers, and how each one fails

| Layer | What it decides | How a bug here looks | How to check it |
| --- | --- | --- | --- |
| **Recipe** (`*.mix.json`) | which motion / vfx / sound each lane pulls | wrong clip family, missing part | `xi ability inspect`, read the `sources` |
| **Compose** (`xi_compose`) | the DAT bytes: clips, `main`, folders, waist split | wrong joints, missing waist, bad timing | `xi anim json` (joint counts), `xi ability inspect --routine main` |
| **Placement** (`weapon_skill_slot`) | the **file_id** each race/role lands at | right DAT, **wrong file_id** → wrong race loads | `placements.json`; `resolve_weapon_skill` |
| **FTABLE** (`FTABLE10.DAT`) | file_id → ROM path | file_id maps nowhere or to a stale DAT | read `FTABLE10[file_id*2]` = dir<<7\|file |
| **cexislots** (client plugin) | the runtime file_id from the animation + race | plugin not loaded, or its math ≠ placement | `/cexislots` in game; `cexislots.log` |
| **Client cache** | which DAT is in memory | rebuilt DATs ignored until re-zone | re-zone / relog after every rebuild |
| **Server DB** | the `animation` number the packet carries | wrong band, or number the client can't reach | `xi server check`; the row's `animation` |

The trap: **compose and placement can both be right while their conventions
disagree.** The stretched-model bug was compose emitting correct HumeMale DATs,
placement putting them at a 0-based file_id, and the client asking for the 1-based
one — three correct-looking layers, one wrong result.

## Diagnose from what the client *opens*

When "it plays but it's wrong", the single most useful capture is **Process
Monitor** (procmon) on `pol.exe`, filtered to `ReadFile` on paths containing
`ROM` / `ROM10` `.DAT`:

1. Trigger the action once in game.
2. In the log, find the `.DAT` the client opened right after the action fired.
3. Cross-reference that path against `placements.json` (`file_id` → `dat`). If the
   client opened a **different race's** DAT than the character is, it is a
   placement / resolution off-by-one, not a compose bug.

`cexislots.log` and `/cexislots effect …` also trace a number to the file it
loads, without procmon.

## The preview/game parity gap — read this before trusting the preview

The viewer's mixer preview composes and poses the motion **correctly** and even
hides the weapon for an emote — so it will look right even when the game is
broken. The preview does **not** exercise:

- **cexislots file-id resolution** — the browser has no client register, so a
  race off-by-one in placement is invisible there.
- **the real weapon-drawn state** — the preview hides the weapon by heuristic; the
  game keeps it drawn unless the routine stows it.
- **the client's DAT cache** — the preview always reads fresh.

Treat the preview as a check of **compose** (pose, joints, region split, timing),
never as proof the client will **open the right file**. "Looks right in the mixer"
and "plays right in game" are two different tests.

## Recurring gotchas (the ones that have bitten)

- **Custom-band race is 1-based.** `file_id = WS_BASE + (anim-first)*24 + block*8 +
  (ri+1)` — the client indexes by RaceGenderConfig 1..8 (HumeMale=1). Placing
  0-based loads the next race. ([weapon-skills.md](../anim/weapon-skills.md))
- **Per-race joint numbering differs.** HumeMale waist = 38–47, HumeFemale = 4,5,18–25;
  HumeMale right hand = joint 68. Compare joint coverage **same-race only**.
- **A weapon skill's waist (part 2) lives in the companion DATs, not the body.** The
  client reads a WS's waist only from the companion.
- **An emote never animates the weapon hand.** A drawn weapon hangs; the mixer stows
  it with `0x75` `hwmg` tags for emote/dance motions.
- **The client caches loaded DATs.** Re-zone or relog after every `xi dats build`,
  or you are testing the old bytes.

## Recommendations for the flow and the UI

Grounded in the three-pass debug above; each closes a gap that let a wrong build
look right:

1. **Surface the resolved file-ids in Manage/publish, per race.** The publish
   already prints them; showing the player's own race first (and a one-click "copy
   the procmon filter for these paths") turns the opaque resolution layer into
   something checkable. The bug lived entirely in a number the UI never showed.
2. **Make "re-zone and verify" an explicit step after a build**, not folklore. A
   short post-build note ("relog, then use the skill; if the model tears, capture
   procmon") would have saved two passes.
3. **Keep the preview honest about its scope.** A small "preview checks compose,
   not client resolution" affordance stops "it looked fine in the mixer" from
   being read as "it will work in game".
4. **Prefer a same-race, retail-vs-mix joint diff as the compose sanity check.**
   `xi anim json` counts and the joint-set diff against a real WS of the same race
   catch a region-split or joint bug before publish — offline, no client.
5. **When a fix is speculative, say so and gate on the client test.** The waist and
   weapon fixes were both real and both necessary, but neither was *the* bug;
   calling them "should fix it" cost trust. Verify from what the client opens
   before claiming a fix.

## On a subagent for this

A dedicated subagent is not the highest-leverage move — the knowledge that keeps
this smooth belongs where **every** session sees it (this repo's docs, the memory
bank, Hindsight), not in an agent the main session has to remember to call. A
subagent earns its place only for **fan-out** work (e.g. "verify this build across
all 8 races in parallel") or a repeatable **review** ("audit a recipe before
publish against the gotcha list"). If either of those recurs, a small
`.claude/agents/` definition scoped to it is worth adding; a general "mixer helper"
agent is not.
