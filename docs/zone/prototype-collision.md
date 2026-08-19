# Working on a prototype zone: ROM/0/41 as zone 501

A worked example of getting an unreleased `0x54` zone playable on a private server —
what fixed it, what looked like a fix and wasn't, and the limits you hit. Companion to
[prototype-zones.md](prototype-zones.md), which documents the formats themselves.

---

## 1. Convert the stride — and convert `.base` too

The zone will not render correctly until its placement records are widened to `0x64`
(see [prototype-zones.md §4](prototype-zones.md)). The part that is easy to miss:

> **Convert `<dat>.base` as well as the live DAT.**

Publish is *reset-from-pristine, then apply changes*. If `.base` is still `0x54`, every
publish resets the zone back to the broken layout and then writes into it at whatever
stride the tools assume — shredding names and scales. That looks like "publishing
randomly moves my objects", and it will happen on every publish until `.base` is
converted.

```python
from xi.zone.xi_zonedef import convert_zonedef_to_retail_stride
# apply to BOTH <dat> and <dat>.base
```

Symptoms of getting this wrong: mesh ids with float bytes in them (`sd10hÀÈ1`,
`tu1         ~|`, `iwa ~|`), scales like `3.44`, objects jumping to positions nobody
set. Recover by copying the placement array back out of `.base` — collision lives
elsewhere in the section and survives:

```python
live[dst : dst + n*0x64] = base[src : src + n*0x64]   # placement table only
```

## 2. Collision on these zones

**The collision is the visible geometry.** On ROM/0/41 both are exactly 97,699
triangles — one collision triangle per rendered triangle. Every wall, floor and stair
tread you can see is already solid. There is no missing "collision pass" to author.

**But it is sparse.** ROM/0/41 fills 851 of 40,000 grid cells. The holes are real gaps
in an unfinished map, not a tooling failure: where nothing was built, there is nothing
to stand on, and you fall out of the world.

**It is heavily instanced.** 44,507 unique triangles serve 97,699 placements via shared
meshes plus per-object transforms. Anything that re-bakes collision through
`add_collision` flattens that sharing and multiplies the byte cost — which is what makes
the size ceiling below so easy to hit.

### The hard ceiling

The `0x1C` section size field is 19 bits: **8,388,592 bytes maximum**. Exceed it and
`encode_section_meta` raises `SectionTooLargeError` rather than writing a file whose size
overflows into the `is_shadow` flag and desynchronises the client's chunk walk.

Measured on ROM/0/41 (~72 bytes per authored triangle after bucketing and
double-siding), that is roughly **117,000 authored triangles** for the whole section:

| approach | authored tris | section | fits? |
|---|---:|---:|---|
| subdivide all collision to 3u max edge | 214,771 | 15.4 MB | no |
| subdivide all collision to 5u max edge | 113,149 | 8.1 MB | marginal |
| subdivide only coarse walkable (`\|ny\|>0.7`, edge>2) to 1u | 71,408 added | 5.1 MB added | yes |

### What did not work

- **Filtering collision to walkable slopes.** Dropping steep triangles leaves
  disconnected fragments and slivers — structurally valid, visually and practically junk.
- **A flat plane across the zone.** Fixes falling, but it is a solid floor at one height:
  you cannot descend below it and stairs become unclimbable because you stand on the
  plane instead of the treads. Do not do this on a multi-level map.
- **Subdividing everything.** Blocked by the section ceiling above.

Subdividing *only the coarse walkable surfaces* does work and is worth doing: on this
zone half the collision has edges over 2 units and 22k triangles are wider than a 4×4
grid cell, which the client's per-cell test can miss at the edges.

### On the wall flag

`hit_wall` (bit `0x4000` on the third vertex index) is **not** simply "you cannot stand
on this". A zone-501 collision set that is 100% `hit_wall` was confirmed standable in
game. Treat the flag as affecting camera/LoS behaviour and material response, and do not
assume floor-vs-wall from it alone — check in game.

## 3. Rebuilding collision from scratch

The practical route for a prototype zone whose own collision is too sparse or too coarse
to be worth patching:

1. `xi zone export <dat>` — geometry to GLB/FBX.
2. Join the meshes and author a clean collision hull in Blender: floors and stairs where
   you want to walk, walls where you want blocking, nothing else. Keep triangles under
   ~2 units so the per-cell test is reliable, and keep the total under the ~117k
   authored-triangle budget.
3. `xi zone import --add-collision <obj>` (or `replace_zone_collision`) to bake it in.

Authoring fresh is usually cheaper than salvaging: the shipped collision is dense in
places you never walk (every wall face) and absent where you do.

## 4. Checklist

- [ ] stride converted on **both** `<dat>` and `<dat>.base`
- [ ] editor pointed at a stride-aware xi-tools build, or every publish re-corrupts
- [ ] spawn point sits over real collision — `!zone` drops you at the `zoneList` coords,
      and a spawn in a collision hole reads as "the zone is broken"
- [ ] `0x1C` section size checked against 8,388,592 after any collision work
