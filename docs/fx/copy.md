# xi fx copy

Duplicate an effect — within a DAT, or **cross-DAT** with `--from`. The copy is
inserted as a new `0x05` section (or `--replace`s an existing slot). With `--from`,
**every dependency the effect references is copied too** — its texture (`0x20`), the
sprite-sheet companion (`0x21`), particle meshes (`0x1F`), keyframe data (`0x19`), and
zone meshes (`0x2E`) — for anything the destination DAT lacks.

---

## Usage

```
uv run xi fx copy <dat> <src> [params]
```

| Argument | Description |
|---|---|
| `<dat>` | Destination DAT path or ROM spec |
| `<src>` | FourCC of the effect to copy |

| Flag | Meaning |
|---|---|
| `--from SRC_DAT` | Copy `<src>` from another DAT (path or ROM spec); brings its deps |
| `--name NEW` | FourCC for the copy (default: auto-derived, e.g. `tki5`→`tki6`). Ignored with `--replace` |
| `--replace E` | Overwrite effect `E`'s slot (the copy inherits its spawn behaviour) |
| `--pos X Y Z` / `--at-pos X Y Z` | Absolute position (`--at-pos` matches the `/xi pos` output) |
| `--at REF` | Place at an existing **effect** (FourCC) or **placed object** (mesh id) |
| `--offset DX DY DZ` | Nudge from the source / `--at` / `--pos` |

`--pos`/`--at-pos` and `--at` are mutually exclusive.

---

## Examples

```bash
# same-DAT: a 6th fountain jet, nudged 6 units along X
uv run xi fx copy ROM/1/41 tki5 --offset 6 0 0

# cross-DAT: a Castle Zvahl wall torch onto the fountain (brings fire texture + light deps)
uv run xi fx copy ROM/1/41 lb09 --from ROM/0/60 --at-pos -20.7 -2 5.2 --name fir0

# place at an existing object's spot, nudged up 2 (remember −Y is up)
uv run xi fx copy ROM/1/41 lb09 --from ROM/0/60 --at funsui --offset 0 -2 0 --name fir0

# overwrite a spawning slot so a scheduler-gated foreign effect actually fires
uv run xi fx copy ROM/1/41 a133 --from ROM/0/73 --replace grid
```

---

## Example output

```
copied lb09 from ROM/0/60.DAT -> fir0 (pos [-20.7, -2.0, 5.2]) in ROM/1/41.DAT
  brought deps: fire(0x20), fire(0x21), hiaa(0x19)
Wrote: <output DAT path>
```

---

## Notes

- **`/xi pos` in-game** (the `xitools` Ashita addon, `addon/xitools/`) prints a
  ready `--at-pos X Y Z` line and a full `xi fx copy … --at-pos …` command — stand
  where you want the effect, paste, done. The addon already swaps Ashita's axes to DAT
  order (`DAT(x,y,z) = Ashita(X, Z, Y)`).
- **Coordinates: `−Y` is UP** (FFXI is Y-down). The fountain jets sit at `y=−7.2`; a
  positive `y` buries an effect underground. Move things up by going *more negative*.
- **Spawning with full deps:**
  - Effects are enumerated sequentially, so same-DAT copies and native effects spawn.
  - **Full deps are enough for `autoRun=true` effects** — ambient *and* boss/ability
    sources. *Verified*: ROM/0/73 dungeon flame `a133` (`autoRun=true`) and Castle
    Zvahl torches both render at the Lower Jeuno fountain once `0x20`+`0x21`+`0x19`
    come along.
  - Only generators that are genuinely **`autoRun=false`** need `--replace` onto a
    spawning slot or [`fx set --autorun`](set.md) afterward.
- The earlier failure mode for transplants was a **missing dependency** (a missed
  `0x21` SpriteSheetMesh = nothing to draw), not the trigger layer. `fx copy --from`
  now brings the full `_DEP_TYPES` set (`0x20, 0x21, 0x1F, 0x19, 0x2E`), including the
  textures referenced by any copied mesh. It does **not** bring the `0x07`
  EffectRoutine that might trigger the effect — see
  [effect_system.md](effect_system.md#3-0x07-effectroutine--the-sequencer--trigger-layer).
- Keeps a `<dat>.base` backup; the DAT grows (rebuilt, all sections preserved).

---

## Related commands

- **[`xi fx set`](set.md)** — after copying, reposition / recolour / `--autorun` it
- **[`xi fx export`](export.md)** — preview an effect's mesh + texture before copying
- **[`xi fx delete`](delete.md)** — remove an effect (or a botched copy)

Deep dive on cross-DAT transplant and the dependency graph:
[effects.md](effects.md) and
[effect_system.md](effect_system.md#cross-dat-references).

---

# xi fx copy-group

Copy an entire **fixture group** in one command — all effects sharing the same
numeric suffix as the seed (e.g. all `*04` effects: `fd04 fl04 fr04 fs04 fm04 fi04`).

FFXI light fixtures are composed of multiple sibling effects with a shared numeric
suffix (one per role: diffuse light, glow, rim, spark, …). `copy-group` detects them
by matching the **alpha stem + numeric suffix** of the seed name so you never have to
copy each member individually.

---

## Usage

```
uv run xi fx copy-group <dat> <seed> [params]
```

| Argument | Description |
|---|---|
| `<dat>` | Destination DAT path or ROM spec |
| `<seed>` | Any member of the group (e.g. `fd04`) |

| Flag | Meaning |
|---|---|
| `--from SRC_DAT` | Copy group from another DAT (path or ROM spec); brings deps |
| `--pos X Y Z` / `--at-pos X Y Z` | Place the whole group at this absolute position |
| `--at REF` | Place at an existing effect or placed object |
| `--offset DX DY DZ` | Nudge from `--pos` / `--at` / source |

`--pos`/`--at-pos` and `--at` are mutually exclusive.

---

## Auto-naming

Cross-DAT copies are named `x<family><role><index>` — e.g. `xfd0`, `xfl0`, `xfr0`.

| Pattern | Meaning |
|---|---|
| `x` | cross-zone marker |
| `fd` (2 chars) | role from the original FourCC |
| `0` | copy index — increments automatically if already taken |

A second `copy-group` call produces `xfd1`, `xfl1`, … allowing multiple instances of
the same fixture type in one zone (up to 62 per role).

The editor displays these names as `xi_fd0`, `xi_fl0`, etc. for readability.

Effects with no patchable position field (runtime-only) are automatically copied
without `--pos` and a warning is printed — the group copy does not abort.

---

## Examples

```bash
# copy a Norg flame torch fixture into Lower Jeuno
uv run xi fx copy-group "ROM\1\41.DAT" fd04 --from "ROM2\0\27.DAT" --pos -17.0274 -2.8627 6.7949

# second torch at a different position — auto-named xfd1, xfl1, ...
uv run xi fx copy-group "ROM\1\41.DAT" fd04 --from "ROM2\0\27.DAT" --pos 5.0 -2.6 10.0
```

---

## Example output

```
copied fm04 from ROM2\0\27.DAT -> xfm0 (pos [-17.027, -2.863, 6.795])
  brought deps: kfl1(0x19)
copied fi04 from ROM2\0\27.DAT -> xfi0 (pos [-17.027, -2.863, 6.795])
copied fd04 from ROM2\0\27.DAT -> xfd0 (pos [-17.027, -2.863, 6.795])
  brought deps: klt1(0x19)
copied fl04 from ROM2\0\27.DAT -> xfl0 (pos [-17.027, -2.863, 6.795])
copied fr04 from ROM2\0\27.DAT -> xfr0 (pos [-17.027, -2.863, 6.795])
copied fs04 from ROM2\0\27.DAT -> xfs0 (pos [-17.027, -2.863, 6.795])
Wrote: <output DAT path>
New names: xfm0, xfi0, xfd0, xfl0, xfr0, xfs0
To delete: xi fx delete-group "<output DAT path>" xfm0
```

---

## Notes

- **Group matching** uses stem + suffix: `fd04` → stem `f`, suffix `04` → matches
  `f[x]04` only. `l004` (different stem length) is excluded even though it ends in `04`.
- **Runtime effects** (`fr`/`fs` rim/spark roles, `builder: runtime`) have their
  position baked into the `StandardSetup` opcode rather than a DAT section reference.
  `copy-group` patches these correctly; `xi fx copy` alone would error with
  *"has no position field"*.
- The editor shows an **RT badge** next to runtime effects in the VFX list to indicate
  their position is driven at runtime and publish will not write it back.
- **`−Y` is UP** — same as `xi fx copy`.

---

## Related commands

- **[`xi fx delete-group`](delete.md#xi-fx-delete-group)** — undo a group copy by any member name
- **[`xi fx copy`](copy.md)** — single-effect copy with full options
- **[`xi fx set`](set.md)** — reposition / recolour after copying
