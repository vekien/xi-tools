# xi fx set

Edit an effect's parameters **in place** — position, scale, color, draw distance,
spawn interval, particle count, flow speed, and the autoRun flag. Match one or more
effects by **exact FourCC or prefix**.

Params are located by their **opcode tag** (color/scale/draw-distance/flow) or a
**fixed header field** (spawn-interval/count/autorun), validated against xim — so the
same edit works on **any** effect sharing the format, not just the fountain. `0x05`
sections are unencrypted, so this is a direct byte write (no reimport).

---

## Usage

```
uv run xi fx set <dat> <name>... [params]
```

| Argument | Description |
|---|---|
| `<dat>` | DAT path or ROM spec (e.g. `ROM/1/41`) |
| `<name>...` | One or more effect names; each matches by exact FourCC **or prefix** (so `tki` hits `tki1`–`tki5`) |

At least one param flag is required:

| Flag | Effect | How it's found |
|---|---|---|
| `--pos X Y Z` (alias `--at-pos`) | local position (`--at-pos` matches the `/xi pos` output) | 3×f32 after the placed-mesh/texture ref |
| `--scale X Y Z` | scale (width height depth) | opcode `0x0F` ScaleInitializer (tag `0f 04`) |
| `--scale-mul F` | multiply current scale by F | opcode `0x0F` |
| `--color RRGGBB` | tint color (or `r,g,b`) | opcode `0x16` ColorSetup (written B,G,R) |
| `--range NEAR FAR` | draw distance (sets maxEmitDistance = FAR; NEAR unused) | opcode `0x0A` GeneratorCull |
| `--spawn-interval FRAMES` | framesPerEmission — frames between spawns (lower = denser) | header u16 `@0x76` |
| `--count N` | particlesPerEmission (0–255) — particles per spawn | header u8 `@0x78` |
| `--autorun / --no-autorun` | set/clear the autoRun flag (auto-spawn vs scheduler-triggered) | header u8 `@0x79` bit `0x10` |
| `--flow F` | multiply texture/position flow speed by F (observed; opcode TBD) | tags `02e4` / `0708` (.Y) |

`--scale` and `--scale-mul` are mutually exclusive.

---

## Examples

```bash
# recolour + enlarge + extend the draw distance of the fountain spray
uv run xi fx set ROM/1/41 tki --color 00FF00 --scale-mul 4 --range 100 500

# make the jets gush continuously and emit more per spawn
uv run xi fx set ROM/1/41 tki --spawn-interval 2 --count 40

# move a single effect
uv run xi fx set ROM/1/41 grid --pos -16 -0.4 5

# make a transplanted effect actually spawn (see fx copy)
uv run xi fx set ROM/1/41 fir0 --autorun
```

---

## Example output

```
Edited 5 effect(s) in ROM/1/41.DAT:
  tki1     color=505050->00ff00; scale=(0.3, 1.5, 0.3)->(1.2, 6.0, 1.2); draw_distance=15.0->500.0
  tki2     color=505050->00ff00; scale=(0.4, 1.5, 0.4)->(1.6, 6.0, 1.6); draw_distance=15.0->500.0
  ...
Wrote: <output DAT path>
```

Each line is a per-effect change log of `field=old->new`.

---

## Notes

- **`--pos` on a prefix sets *every* matched effect to the same point** — use a single
  name for position. `--scale`/`--color`/`--range` apply uniformly and are fine across
  a group.
- **Coordinates: `−Y` is UP** (FFXI is Y-down). A positive `y` buries an effect
  underground. To raise something, make `y` *more negative*.
- **Color byte order is written B,G,R** (FFXI convention). The color **multiplies**
  the texture — gray/white tints shift fully; pre-colored textures barely move. The
  green channel is confirmed in-game (the fountain spray went green); pure red vs blue
  ordering is assumed.
- **`--range`** writes `0x0A` GeneratorCull `maxEmitDistance` (the real draw-distance
  knob, per xim). `NEAR` is currently unused.
- **`--autorun`** is the fix for a transplanted effect that doesn't render because it's
  genuinely scheduler-triggered (`autoRun=false`). Note it does *not* fix every
  transplant — a missing dependency is the more common cause (see
  [copy.md](copy.md) and [effects.md](effects.md#how-effects-are-triggered--0x07-effectroutine)).
- Edits are written to the DAT in place, with a `<dat>.base` backup of the
  pristine bytes. Restore from `.base` or `xi zone reset` to undo.

### Caveat — ordering with `zone import`

`fx set` edits the DAT **in place** (it does not reset to `.base`), so it stacks on
mesh-merge/placement edits. But `xi zone import` **rebuilds from `.base`** (which
still has the original effects) — so re-running `zone import` **reverts** your edits.
Run `fx set` *after* import. (Effect editing isn't wired into the import pipeline yet.)

---

## Related commands

- **[`xi fx json`](json.md)** — see the current value of every param before editing
- **[`xi fx copy`](copy.md)** — duplicate/transplant an effect (then `set --autorun`)
- **[`xi fx delete`](delete.md)** — remove effects instead of editing them
