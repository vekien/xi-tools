# FFXI Zone Binary Format

Reverse-engineered while building `xi zone export`, verified by ripping Lower
Jeuno (`ROM/1/41`). Parsing reference: xim's `ZoneMeshSection.kt`,
`ZoneDefParser.kt`, `ZoneDecrypt.kt`, `MainDll.kt`.

A zone DAT is large (8 MB+, ~1900 sections): mesh chunks (`0x2E`), the placement
table (`0x1C` ZoneDef), textures (`0x20`), environment (`0x2F`), particles
(`0x05`), sound pointers (`0x3D`), trigger volumes (`0x36` ZoneInteraction —
sub-areas / zone lines / doors), directories (`0x01`). No skeleton/skinned mesh.

> This page describes the **shipped** layout. The unreleased dev maps in `ROM/0/`
> predate it — chained mesh groups and `0x54` placement records — and a parser
> written against this page reads them as half-empty or garbage without erroring.
> See [prototype-zones.md](prototype-zones.md).

## Key insight: meshes are local + instanced

`0x2E` mesh vertices are in **local** space. The world is assembled from the
**`0x1C` ZoneDef** placement table: each placed object references a mesh by name
and supplies a `position`/`rotation`/`scale`. One mesh is instanced many times.

## Decryption (both schemes use tables from FFXiMain.dll)

Two 256-byte key tables live in `FFXiMain.dll`, located by scanning from `0x30000`
for the table's own first 4 bytes: **table1** starts `E2 E5 06 A9`, **table2**
`B8 C5 F7 84`. (Version-robust — this is xim's "offset hint" mechanism.)

**Zone mesh (`0x2E`)** — two passes (`data_start = section.start + 0x10`):
- *pass 1* keyed-XOR stream (only if `mode = (meta>>24)&0xFF >= 5`): `keyIndex =
  byte5`; `key = table1[keyIndex ^ 0xF0]`; per body byte from `ds+8`:
  `keyMod = (key&0xFF)*0x101`; `key += ++counter`; `xor (keyMod >> (key&7)) & 0xFF`;
  `key += ++counter`.
- *pass 2* if `u16 @ ds+6 == 0xFFFF`: swap 8-byte blocks between the two halves
  (`decodeCount = (decodeLen & ~0xF)/2`; `key1 = byte5 ^ 0xF0`; `key2 =
  table2[key1]`; step 8: if `key2 & 1` swap; `key1 += 9`; `key2 += key1`).
- decrypt order = pass1 then pass2 (each self-inverse).

**ZoneDef (`0x1C`)** — different: variable-length XOR-`0xFF` stream over the body
from `ds+8`. Key seed: `key = table1[(keyNodeData>>24) ^ 0xFF]` (mesh path uses
`^ 0xF0` instead). Then `xorLength = ((key>>4)&7)+16`; apply when `key&1` and room
remains; `key += ++counter`; `mode <= 0x1A` = unencrypted. After the stream, each
object's 16-char name is unmasked with `0x55` at `ds+0x20 + i*0x64`.

## ZoneMesh section (`0x2E`)

After decrypt (`ds`): `meta@0`, `keyConfig@4` (`config = &0xFF`: bit0 = tri-strip
else tri-mesh, bit1 = vertex-blend), author `str@8(8)`, name `str@0x10(16)`.
`defStart = ds+0x20`: `meshCount0 u32`, bbox0 (6×f32), `section1Off u32@0x3C`
(`meshCount0 == 0` ⇒ collision-only, skip), `meshCount1 u32@0x40`; meshes begin at
`defStart + section1Off`.

Per mesh: `textureName str(16)`, `numVerts u16`, `flags u16` (`0x8000` = alpha
blend, `0x2000` clear = back-face cull). Vertices, stride **36** (non-blend:
`p0`3f, `n0`3f, BGRA 4, uv 2f) or **48** (blend: `p0,p1,n0` + color + uv). Then
`numIndices u16` + `unk u16`, then `numIndices` × `u16` indices into the vertex
list; align to 4. Draw order = indexed vertices (tri-mesh = groups of 3,
tri-strip = strip).

**Alpha-cutout (foliage)** is keyed on the **mesh name's first byte**, *not* a flag: the
retail client enables alpha-test (transparent leaf texels) for a mesh only when its 16-char
name starts with `_` (`#` selects the opaque-clamp variant) — verified in the client
decompile (`ZoneRenderer`: `if (*data == '_') SetRenderState(D3DRS_ALPHATESTENABLE)`), the
xim reference (`name.startsWith("_") → discardThreshold 0.375`), and xi's own editor
preview. The `0x2000` flag above is **back-face-cull-disable** (double-sided), which tooling
sometimes mislabels "alpha". Consequence: any mesh renamer must preserve a leading `_`/`#`,
or copied foliage renders opaque/black — see [import-json.md](import-json.md) (`_xi_` rename).

## Lighting & shading — how a `0x2E` mesh is lit

The per-vertex **BGRA colour** and **`n0` normal** drive runtime lighting. The retail
client's terrain shader (xim `XimShader.kt` / `ShaderConstants.kt`, verified against the
live client) is:

```
litColor = vColor·ambient + Σ vColor·max(0, dot(N, lightDir))·lightColor   (sun + moon + point lights)
out.rgb  = 2 · litColor · texture.rgb      (framebuffer-clamped; the ×2 cancels FFXI's half-scale vColor)
out.a    = 4 · vColor.a · texture.a
```

Consequences when importing a custom mesh:

- **vColor MODULATES the live light** — it is *not* the final colour. Neutral is `0x80`
  (128) per channel: in the shader `2·(128/255) ≈ 1.0`, i.e. "fully lit, no baked shadow".
  Lowering vColor darkens the surface linearly — this is the brightness knob `--shade`
  writes (`byte = vColor·128·shade`; clamps at 255, and the engine clamps `litColor` to
  1.0, so `shade` > ~2 just washes to white).
- **Normals MUST be unit length.** The client does **not** renormalize, so `dot(N, light)`
  scales with `|N|`. A normal of magnitude ~100 (e.g. a GLB authored at 100× and placed at
  0.01 — the node scale leaks into the normal if it's transformed by the model matrix
  instead of its inverse-transpose) makes the diffuse term **saturate to full on every lit
  face**, so the mesh renders **fullbright regardless of vColor / `--shade`**. The xim
  *port* renormalizes in its vertex shader, so a bad mesh still looks correct **in the
  editor** — only the real client exposes it. xi's importer and encoder now force unit
  normals (`xi_object._read_glb_primitives`, `xi_mesh.encode_zone_mesh_section`). To
  diagnose a legacy/hand-built mesh, decrypt the `0x2E` section and check `|n0|` (≈1.0 =
  fine; anything else = this bug — renormalize in place).
- Retail meshes keep vColor near neutral and let the engine light them; a few bake mild
  ambient occlusion into vColor for crease/contact shadow. xi can bake AO too, but it's
  **opt-in** (`--ao`) — see [object/import.md](../object/import.md#shading--brightness).

## ZoneDef placement (`0x1C`)

Header (`ds`): `meta@0`, `keyNodeData@4` (`nodeCount = &0xFFFFFF`,
`keyIndex = >>24`), `collisionMeshOff@8`, grid `blocks@0xC` (4×u8),
`spaceTreeOff@0x10`, `cullTablesOff@0x14`, `pointLightOff@0x18`, `unk@0x1C`;
objects start at `0x20`.

Each object record = **0x64 bytes** (full layout per xim `ZoneDefParser.parseZoneObjs`):

| Off | Field |
|-----|-------|
| 0x00 | mesh id (16 chars; matches the `0x2E` name field) |
| 0x10 | position (3×f32) |
| 0x1C | rotation (3×f32, radians) |
| 0x28 | scale (3×f32) |
| 0x34 | **BlockID** (FourCC; 0 = ordinary static object). Non-zero + first byte `_`/`@` = one part of an animated multi-part object (a mog-house double door's halves both carry `_720`). The client takes such records OUT of the normal quad-tree pass (`RenderType 0`) and draws the group via an `UnderscoreAtStruct` of at most **four** parts — a fifth record with the same FourCC is never drawn. Cloned records must zero it (`xi_zonedef.clear_block_id`). |
| 0x38 | high-def LOD threshold (f32) |
| 0x3C | mid-def LOD threshold (f32) |
| 0x40 | **draw distance** (f32) — engine stops drawing past this range (~1=interior, 1000=buildings) |
| 0x44 | flags (4×u8; flags1 bit1 = skip-during-decal) |
| 0x48 | culling-table link (ds-relative offset to a culling table, or 0) |
| 0x4C | environment link (4-byte DatId) |
| 0x50 | **file-id link** (u32) — for a "closed building" placeholder, the **sub-area id** whose interior replaces it (0 = none); see [subareas.md](subareas.md) |
| 0x54 | point-light indices (4×u32, 1-based into the light table below; 0 = none) — the ONLY lights that shine on this object |

### Light table (`pointLightOff@0x18`)

A fixed **256 × 0x4C** array (the client's `LightPool[256]`); each entry holds a point-light
generator's FourCC @+0 (the rest is runtime pointer/params, zero in the file). On zone load the
client pre-allocates one pool slot per listed id (`ZoneRenderer::SetupLightBindings`); a point-light
0x05 generator (`StandardSetup` linked type `0x47` + `PointLightParams 0x58`) then finds its slot by
its **own FourCC** (`CMoPointLightProgElem::InitLight`) — an id missing from the table returns -1 and
the light is silently never created. Zone geometry is lit per placement through the four
`LightReferences` @0x54 (max four lights per object, a D3D fixed-function limit). Copying a point
light therefore needs (a) the new id in this table and (b) references from the placements it should
light — `xi_apply_changes._register_point_lights` does both on VFX add.

World transform = `translate(position) · rotateZYX(rotation) · scale(scale)`.
LOD: an id ending `_l`/`_m`/`_h` selects a detail variant. Skybox/celestial
meshes are absent from this table (drawn as environment around the camera).

## Trigger volumes & sub-areas (`0x36`)

A plaintext `"RID"` section of `0x40`-byte OBB entries — zone lines, doors, and the
**sub-area** (shop / building interior) links. Interiors are separate DATs swapped in
without a zone change; the placeholder above (`0x50`) is what they replace. Full
layout, the `subAreaId + 0x64` interior-DAT formula, and the runtime visibility rule
are documented in **[subareas.md](subareas.md)**.

## Visibility — space tree, culling tables, collision transforms

Three further sub-sections inside `0x1C` decide whether an object actually draws. To
**add** an object you must register its index in all of them (see
[object/import.md](../object/import.md)); editing existing objects in place needs none.

- **Space-partitioning tree** (`spaceTreeOff@0x10`): a quad-tree of nodes, each with an
  8-corner AABB. The renderer walks it front-to-back, skipping any node whose box is
  outside the frustum; leaf nodes list the object indices in that region. *Broad-phase.*
- **Culling tables / PVS** (`cullTablesOff@0x14`): `indexTableCount(u32)` then that many
  tables, each `count(u32)` + `count` object-index `u32`s. The renderer selects **one
  table from the collision floor under the *camera*** (a transform's culling-group →
  table index) and draws a leaf's objects **only if they appear in that table** — unless
  the floor's table is null, where everything draws. This is the real per-camera-position
  visibility gate: an object in no table is invisible from most camera spots.
  `object.cullingTableLink@0x48` points back to its table (or 0).
- **Collision transforms**: a contiguous `0xC0`-byte array indexed **1:1 by object
  index** (`[transformsOff, pairsOff)` in the collision section). Each = world matrix
  `@0x00` (16 f32), inverse `@0x40`, then the `@0x80…` tail: a **3×3 float matrix**
  (36 bytes @`0x80` — xi writes identity there and gets correct hit normals in-game,
  so a normal/rotation matrix is the leading interpretation, but the field is otherwise
  unconfirmed; an external corpus survey reads it as "3 × vec3, purpose unresolved"),
  the culling-group offset `@0xA8`, and the world-space Y cull AABB `@0xB4`/`@0xB8`
  (see [collision.md](collision.md) — same record, same tail). The collision header's
  `indexCount@+0x18` mirrors `nodeCount`, so growing the object array means growing
  this array too.

## Re-import (`xi zone import`)

Reverse of export (`src/xi/zone/xi_import.py`): placement write-back **and**
automatic mesh-merge into existing `0x2E` sections when GLB geometry grows. See
[import.md](import.md).

- **Re-encryption** (`xi_decrypt.py`): both ciphers are built from self-inverse ops,
  so re-encrypt = the same passes in reverse order. `reencrypt_zone_mesh` =
  pass2→pass1; `reencrypt_zone_objects` = mask-names→XOR-stream. The key streams
  are position-driven (key tables + counters, never the data) and the 8-byte
  header is never decoded, so they invert byte-for-byte regardless of edits.
  Round-trip is verified byte-exact across all `0x2E`/`0x1C` sections.
- **Placement write-back**: recover each node's raw FFXI TRS, decompose to
  position/rotation/scale, write into the matched `0x64` record, re-encrypt, save.
  Nodes are matched to records by replaying export's naming (`id`, `id.002`, …);
  matching is tolerant of the FBX round-trip rewriting `.NNN` → `_NNN`.
  TRS decomposition is ambiguous for negative-scale (mirrored) records — it picks
  an equivalent factorization, so verify by world matrix, not raw bytes.
- **Coordinate recovery** is *not* a fixed flip. Export parents each placement
  (raw TRS as its local matrix) under an `ffxi_root_correction` node (180°-X
  composed with scale `[-1,1,-1]`, net `(x,y,z) → (-x,-y,z)` = `diag(-1,-1,1)`).
  Blender preserves each node's local TRS through FBX but re-expresses the
  correction node itself, so we recover each node's transform **relative to the
  actual correction node** (`inv(C)·W`), exact for its descendants. With no
  correction node (flattened model) we strip the hardcoded `diag(-1,-1,1)`.
- **Mesh-merge**: when a GLB mesh's triangle count differs from the pristine `0x2E`
  by more than the grow threshold, geometry is re-serialized into that section
  (grows the DAT). Details in [import.md](import.md).
- **Fidelity**: the GLB export round-trips every object loss-lessly. Blender's
  FBX exporter corrupts some mirrored (negative-determinant) objects by
  re-parenting them to the scene root — those are detected and skipped (their DAT
  record keeps its correct base value). Edit mirrored objects via GLB.
- Object count/size unchanged for placement-only edits ⇒ no section resize,
  internal offsets (`collisionMeshOff`/`spaceTreeOff`/`cullTablesOff`/
  `pointLightOff`) stay valid.
- Adding brand-new objects (`xi object import`) grows the object array and
  rewrites the space-tree, culling, and collision-transform tables — see
  [object/import.md](../object/import.md).
