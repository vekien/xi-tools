# `0x1F` ParticleMesh & `0x21` SpriteSheetMesh — the geometry an effect draws

The stored triangles a `0x05` generator puts on screen. Zone effects place a
`0x2E` ZoneMesh (the fountain's splash quad); **everything else — every spell,
ability, weapon skill and effect-only NPC — draws a `0x1F`**, with `0x21` for
sprite-sheet billboards.

> **Source & trust.** Layout taken from the client's own loader in the
> `xiclient` decompile (`research/external/xiclient/.../Resource/Derived/CMoD3m.cpp`,
> `CMoD3m::Open` / `GetShortPointer` / `GetAnotherShortPointer` / `GetFloatPointer`)
> and verified byte-for-byte against retail DATs — 51 sections across `ROM/0/0`,
> `ROM/0/60`, `ROM/1/41` and `ROM/3/25` decode with every vertex run landing
> inside its section. Decoder: [`src/xi/fx/xi_particle_mesh.py`](../../src/xi/fx/xi_particle_mesh.py).
> Tests: [`tests/test_particle_mesh.py`](../../tests/test_particle_mesh.py).
> Companions: [effects.md](effects.md) (`0x05` generators), [effect_system.md](effect_system.md)
> (how it all wires together), [../dats/ROM_3_25.md](../dats/ROM_3_25.md) (a worked example).

---

## `0x1F` ParticleMesh

`data_start = section + 0x10`, as everywhere. All offsets below are **payload**
offsets (i.e. from `data_start`).

```
+0x00  u16  flags        marker = flags & 0xF
+0x02  u16  runtime      0x8080 = "already opened"; always 0 on disk
+0x04  u8   matCount     materials in the table below
+0x05  u8   extraCount   further count entries with no material
+0x06  u16  triCount     triangles; corners = triCount * 3
+0x08  u16[matCount+extraCount]   per-entry triangle counts
       materials  @ 8 + 2*align(matCount+extraCount)   — 16 bytes each, matCount of them
       vertices   @ materials + 16*matCount            — 36 bytes each, triCount*3 of them
```

**`marker`** is the low nibble of the header word. `5` and `6` are the
vertex-array layouts this document describes; `7` is what the client *rewrites
the marker to* when its own validation fails (`CMoD3m::Open` sanity-checks the
first three vertices — normal length in 0.1‥2.0, finite position — and stamps 7
on the resource if they don't hold); below 5 is a different resource shape
entirely, which `GetFloatPointer` parks at payload `+0x50`. Every retail `0x1F`
measured so far is marker 6.

### The alignment rule (read this before writing a decoder)

The material table is aligned by rounding the **entry count** down to a multiple
of four and adding three — not by rounding the byte offset up to 16:

```python
n = matCount + extraCount
shorts = n if n % 4 == 0 else n - n % 4 + 3
materials = 8 + 2 * shorts
```

| `n` | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|---|
| materials at | 8 | **14** | **14** | **14** | 16 | 22 | 22 | 22 | 24 |

The two agree at 14 for one to three entries, which is every retail mesh
sampled, and diverge after. A generic 16-byte alignment therefore *looks* right
until it isn't, and when it is wrong it reads **two bytes into every vertex** —
positions come out as plausible-looking garbage rather than as an obvious
failure.

The second trap is `matCount = 0`. `ROM/0/0`'s `coll` and `hi14` have
`matCount=0, extraCount=1`, so their vertices start at **14**, where a
single-material mesh keeps its material. Assuming "vertices at 30" reads their
material-less geometry as nonsense.

### Vertex — 36 bytes

`CMoD3m::Open` uploads these straight to a vertex buffer declared
`D3DFVF_XYZ | D3DFVF_NORMAL | D3DFVF_DIFFUSE | D3DFVF_TEX1`, which is exactly 36
bytes, and steps the diffuse fixup by 9 floats:

```
+0x00  3x f32   position
+0x0C  3x f32   normal
+0x18  u32      diffuse — D3DCOLOR, so the bytes read B, G, R, A
+0x1C  2x f32   uv
```

The diffuse is authored at **half range**: `0x80` is white, and on zone load the
client doubles every byte and clamps (`v = min(2*v, 255)`). That is the same
modulate2x convention the `0x2E` zone meshes use, so `xi` stores the raw value
and lets `build_glb` fold the ×2 in with everything else.

### Material tag — 16 bytes

Each entry is the referenced `0x20` texture's **own 16-character name, verbatim**
— not its section FourCC. So the lookup is a plain string match against the
texture map the zone/entity parsers already build:

| mesh | material tag | the `0x20` it names |
|---|---|---|
| `ROM/3/25` `bind` | `"bind␣␣␣␣kori␣␣␣␣"` | section DatId `kori`, name `"bind    kori    "` |
| `ROM/3/25` `wa␣␣` | `"bind␣␣␣␣tayu␣␣␣␣"` | section DatId `tayu` |
| `ROM/0/0` `hit5` | `"hit5␣␣␣␣hit50␣␣␣"` | section DatId `hi15` |

It is two 8-char space-padded fields — a group name then the texture name — and
the **section DatId is not either of them** (`hit50` lives in a section called
`hi15`). Match on the name.

### What is NOT in here

**The motion.** A `0x1F` never moves on its own: its placement rotation, its spin
rate and its UV scroll are all opcodes on the generator that draws it
([effects.md → What MOVES an effect](effects.md#what-moves-an-effect)). Nor is it
in the DAT's `0x2B` clip — an effect-only entity's animation is a placeholder that
poses the invisible proxy triangle.

**The blend mode.** A `0x1F` carries geometry and a texture name; how it is
composited is on the **generator** that draws it — `0x05` sec2 opcode `0x1E`. A
generator that declares no `0x1E` falls through to `Src_One_Add`, i.e. **additive**,
which is why glow shells are authored with a black-to-white vertex ramp: under
additive, black is transparent. Render one of these opaque and you get a solid
dark shell instead of an aura.

---

## `0x21` SpriteSheetMesh

The quad a sprite-sheet particle billboards. One texture, N cards:

```
+0x00  u16  0
+0x02  u16  cardCount
+0x04  u8   0            +0x05 u8 cardCount   +0x06 u8 cardCount   +0x07 u8 0
+0x08  16B  material tag (same rule as above)
+0x18  card[cardCount], stride 148:
         +0x00  u32       card header
         +0x04  vertex[6], stride 24:  3x f32 position, u32 diffuse, 2x f32 uv
```

Six vertices = two triangles as a list, with the shared edge duplicated. There is
**no normal**: the cards face −Z in their own space and the generator orients them
at runtime.

`ROM/3/25` has two: `tama` (1 card, a ±2 quad) and `tubu` (2 cards, ±1) — six
triangles between them.

Because each card is the billboard for **one emitted particle**, a static copy at
the origin is a flat square through the middle of the model rather than a layer of
it. `xi fx export --assemble` and the model viewer both leave `0x21` out by
default for that reason; `--all-layers` includes them.

---

## Using it

```bash
uv run xi fx export ROM/3/25 bnd0            # one generator's mesh -> GLB + PNG + JSON
uv run xi fx export ROM/3/25 --assemble      # the whole object, one GLB
uv run xi fx export ROM/3/25 --assemble --all-layers
```

`xi fx json` reports the placed mesh for these too — before `0x1F` was recognised
it printed `mesh: null` for every spell, ability and NPC effect in the game,
because [`xi_core.py`](../../src/xi/fx/xi_core.py) only counted `0x2E` as a mesh.

In code:

```python
from xi.entity.anim.xi_export import parse_sections
from xi.fx.xi_particle_mesh import particle_meshes

data = bytearray(path.read_bytes())
meshes = particle_meshes(data, parse_sections(data))   # FourCC -> ParticleMesh
meshes["bind"].prims                                   # List[ZonePrimitive], ready for build_glb
```

`particle_meshes` is a **name map**, matching how the client resolves a
generator's DatId (local directory, then parents). When a DAT reuses a FourCC
across scopes the first wins — `ROM/0/0` does this for 14 of its 61 meshes.

---

## Unresolved

- **The `+0x08` count array.** Its values equal `triCount` in every single-entry
  mesh measured, and the client only ever uses them as an upper-bound sanity
  check (`Σ counts > 3*triCount` → mark the resource bad). Whether they are
  per-material triangle counts or corner counts is therefore undetermined; the
  decoder treats them as triangle counts and falls back to one primitive when
  they don't sum to `triCount`.
- **`+0x0A` and `+0x0C`.** Non-zero and varying (`0x2E` and `0` for `coll`, `0`
  and `0x12` for `wa`), read by nothing in the loader. Not a vertex stride —
  `hit5` has 36-byte vertices and `0` here.
- **The multi-material path.** The alignment formula above is transcribed from
  the client, but no sampled DAT has `matCount+extraCount > 1`, so only the
  `n ≤ 3` branch is exercised against real bytes. `tests/test_particle_mesh.py`
  covers the rest synthetically.
