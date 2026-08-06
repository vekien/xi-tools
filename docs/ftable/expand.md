# xi ftable expand

Reserve space for your custom models in FFXI's model lookup tables — in **one
command** — and keep custom **entities** (monsters / NPCs / objects) and custom
**gear** safely apart.

> **TL;DR**
> ```
> uv run xi ftable expand     # run once — sets up BOTH entity + gear
> uv run xi ftable info       # see the ranges + what's free
> ```
> Put custom **entities** at **modelid 15,000+**, custom **gear** at **modelid
> 3,000+** (per race + slot). You never get close to the ceilings — that's the point.

---

## The one big idea

FFXI finds every model through **one shared lookup table** (`FTABLE` + `VTABLE`),
indexed by a number called a **file_id**. Picture it as a single giant array:

```
file_id:   0      1      2     ...    423,151
value:   [DAT]  [DAT]  [DAT]   ...   [empty]
```

Two *different* kinds of model id get translated into that one array:

- **Entity** (monster / NPC / object) `modelid` → `file_id = modelid + 98,239`
- **Gear** `modelid` (per race + slot) → a windowed `file_id` high up in the array

Because they translate into **different regions** of the array, an entity and a
gear piece can share the same raw number and never collide — the **file_id is the
only address that matters**.

"Expanding" just makes that array **longer** (appends empty slots) so the higher
file_ids exist. It never moves or renumbers anything already in the table.

---

## Recommended ranges — where to put custom content

| Content | Use modelid | Why this number | Lands at file_id |
|---|---|---|---|
| **Custom entity** (mob / NPC / object) | **15,000+** | Retail's highest is ~11,241 — miles below | 113,239+ |
| **Custom gear** (per race + slot) | **3,000+** | Retail per-slot tops out ~1,196 — well below | 128,240+ |

These are **buffers**: you deliberately start high so the original game's future
content never reaches your numbers. You'll only ever use a tiny slice (a few
hundred ids), so the gap stays enormous.

The ceilings are just the **walls**, not where you work:

| Space | Default ceiling | Usable slots |
|---|---|---|
| Entity modelid | **30,000** | 15,000 – 30,000 = **15,001** |
| Gear modelid (per race + slot) | **4,095** (12-bit hardware max) | up to 4,095 per window |

---

## Quick start

```
uv run xi ftable expand
```

Run it **once** after a fresh client install — it sets up both the entity buffer
and the gear windows. Run it again any time a retail update shrinks the tables
back to retail size (it detects this and re-pads them).

Check what you have:

```
uv run xi ftable info       # ranges, what's used, recommended starts
uv run xi ftable tables     # raw per-ROM table sizes
```

Then add content through the package workflow:

```
uv run xi dats prepare workspaces/foo/import.json projects/update.json --type mesh
uv run xi dats build projects/update.json --dry-run
uv run xi dats build projects/update.json
```

---

## The layout after expanding

```
file_id
      0 ──────── 109,700   retail content (never touch)
113,239 ──────── 128,239   custom ENTITY band   (modelid 15,000 – 30,000)
128,240 ──────── 423,151   custom GEAR bands    (72 windows: 8 races × 9 slots)
```

The gear floor sits **exactly one slot above** the entity ceiling. That isn't a
coincidence — it's derived (see [Config](#config--one-source-of-truth)).

---

## The commands

`xi ftable expand` is the all-in-one. Two focused subcommands exist for doing
just one side:

| Command | What it does |
|---|---|
| `uv run xi ftable expand` | **Both** — grows the tables and wires up gear. The normal choice. |
| `uv run xi ftable expand --no-gear` | Entity buffer only (skips gear setup + its `FFXiMain.dll` check). |
| `uv run xi ftable expand entity [N]` | Entity buffer only, up to an explicit modelid `N`. |
| `uv run xi ftable expand gear [N]` | Gear windows only, up to an explicit per-slot max `N`. |

Flags on the unified command:

| Flag | Default | Description |
|---|---|---|
| `--no-gear` | off | Provision only the entity buffer |
| `--no-backup` | off | Skip the timestamped backup snapshot |
| `--dry-run` | off | Print the plan, write nothing |
| `--debug` / `-v` | off | Timed per-step diagnostics (spot slow disk I/O) |
| `--force` | off | Re-run gear setup even if it's already been done |
| `--pivot` / `--no-pivot` | **on** | Also sync/grow the pivot/override pack tables (`FFXI_PIVOT_DIR`) to match |

**`--pivot` / `--no-pivot` apply only to bare `expand` and `expand entity`.** The gear
path (`expand gear`, and the gear half of bare `expand`) **always** runs
`sync_pivot_from_base()` when a pivot root is configured — there is no `--no-pivot` on
`expand gear`.

Default ceilings (config): **entity modelid 30000** + **gear modelid 4095** per slot
(`xi ftable expand` → entity 30000 + gear 4095).

Gear's table size is always larger than the entity buffer, so the unified
command grows once to the gear target — which provisions the entity band for
free — and then writes the gear pointers.

---

## Every table must be the same size (why `--pivot` exists)

The client doesn't read one FTABLE — it loads **every FTABLE/VTABLE pair it can
find**, looping over each file's length. (Earlier revisions described this as an
xim-style **OR-merge** into one combined table; external byte evidence — 2026-06-24,
adjudicated via ~33 file-ids present in more than one volume — says the real client
is **volume-direct**: the VTABLE byte names the ROM volume to read, and overlay
entries **shadow** the base rather than OR into it. The combine model lives on only
in xim/`dump_event.py`.) Either way the load loop runs over table length, so if any
table is **larger** than the first one loaded, the pass writes past the end of the
buffer and the **game crashes on load**.

So the hard rule: **every lookup table the client loads must be byte-for-byte the
same size.** That includes two places:

- the base install — `FFXI_DIR` (`…/Game/FINAL FANTASY XI/`)
- the **pivot / override pack** — `FFXI_PIVOT_DIR` (Ashita's pivot files),
  which ships its own `FTABLE.DAT` +
  `ROM10/FTABLE10.DAT` that get merged on top

`expand` handles this for you:

- **`--pivot` is on by default** and grows the pivot pack's tables to the same
  size as the base install (set the path with `FFXI_PIVOT_DIR` in your `.env`).
- It **never shrinks** below the largest existing table — the target is clamped
  up to the biggest table found across *both* roots, then everything grows to it.
- After expanding it **verifies** all tables (base + pivot) are equal size and
  aborts if not, so a mismatch never reaches the client.

> If the game crashes right after expanding, a stray table of a different size is
> the usual cause. `xi ftable tables` shows every size; re-run `xi ftable
> expand` (with `--pivot`) to bring them back in line, or `xi ftable reset`.

---

## How it works (under the hood, briefly)

1. **Backs up** every FTABLE/VTABLE to `backups/ftable_<timestamp>/` first.
2. **Grows** the base table + every `ROM2`–`ROM9` pair by appending zero slots up
   to the target size. Zero means "unused" — the client ignores it. Idempotent:
   a table already big enough is left alone.
3. **Creates** `ROM10/FTABLE10.DAT` + `VTABLE10.DAT` — the clean custom namespace
   (no retail table ever writes VTABLE value `10`, so there's zero conflict).
4. **(Gear)** Re-points each race/slot's existing retail armor into the bottom of
   its gear window so existing gear keeps resolving — **no DAT files are copied**,
   it just duplicates the table entries.
5. **(Pivot)** Grows the override pack's tables (`FFXI_PIVOT_DIR`) to the same
   size, then **verifies** base + pivot are all equal — because a size mismatch
   between any two tables crashes the client (see above).

`expand` only ever changes the **size/structure** plus the gear armor pointers.
Your actual custom models get registered later by `xi dats build`.

---

## Config — one source of truth

The ranges live in `src/xi/xi_config.py` (override any with an env var):

| Setting | Default | Env var | Meaning |
|---|---|---|---|
| `MAX_ENTITY_MODELID` | `30000` | `XI_MAX_ENTITY_MODELID` | Entity ceiling — top of the entity buffer |
| `MAX_GEAR_MODELID` | `4095` | `XI_MAX_GEAR_MODELID` | Gear ceiling per race + slot (12-bit max) |
| `GEAR_RECOMMENDED_START` | `3000` | `XI_GEAR_RECOMMENDED_START` | Suggested first custom gear modelid |

**The entity↔gear boundary is *derived*, so it can never drift:**

```
gear floor (CUSTOM_GEAR_BASE) = 98,239 + MAX_ENTITY_MODELID + 1
```

Raise `MAX_ENTITY_MODELID` and the gear region slides up automatically — entity
and gear can never overlap. Builders validate their target file ids against this
layout so content cannot collide by accident.

> ⚠️ Changing the layout (e.g. bumping `MAX_ENTITY_MODELID`) shifts every gear
> file_id. Set it **before** injecting gear. To change it afterwards: run
> `xi ftable reset`, then `xi ftable expand` again.

---

## Undo

```
uv run xi ftable reset
```

Restores every FTABLE/VTABLE from its `.base` backup and deletes injected ROM10
gear. ⚠️ Entity and gear share the same tables, so a reset reverts **all** custom
registrations (monsters included), not just gear.

---

## See also

- [info.md](info.md) — `xi ftable info`: the range / layout dump (start here to see what's free)
- [lookup.md](lookup.md) — resolve a single file_id / modelid to its DAT path
- [../reference/model-file-ids.md](../reference/model-file-ids.md) — the deep file_id / formula reference
