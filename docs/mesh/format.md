# FFXI Entity Mesh & Texture Binary Format

Reverse-engineered while building `xi mesh export`/`import`, verified by
loading rebuilt DATs in the retail client. Source of truth for parsing: the xim
Kotlin `SkeletonMeshSection.kt` / `TextureSection.kt`; the **writer** details
(header size, field values) were derived by diffing against retail DATs because
xim's reader seeks by offset and ignores fields the game actually requires.

> ⚠️ A viewer like AltanaView is lenient (it seeks by offsets and loops the
> instruction stream to `0xFFFF`), so it loads files the **game rejects**.
> "Loads in AltanaView but crashes in-game" almost always means one of the
> engine constraints below is violated.

All section offsets in headers are stored **divided by 2** (the reader multiplies
by 2), so every block must start at an even byte offset.

## Section container

DAT = a sequence of 16-byte-aligned sections. Each section header is 16 bytes:

```
0x00  4   section id (4 chars, e.g. 'hf_b')
0x04  4   packed meta:
            bits 0–6  = type
            bits 7–25 = size in 16-byte units (19-bit, mask 0x7FFFF)
            bit  26   = is_shadow
0x08  8   zero
```

Entity model section types: `0x20` texture, `0x29` skeleton, `0x2A` skinned mesh,
`0x2B` animation (animation binary layout: [../anim/format.md](../anim/format.md)).

## Skeleton mesh section (`0x2A`)

### Header — **64 bytes** for non-cloth meshes (instructions start at `0x40`)

| Off | Type | Field | Notes |
|-----|------|-------|-------|
| 0x00 | u8 | flags1 | **= 1** in every retail non-cloth mesh (required) |
| 0x01 | u8 | flags2 | 0 |
| 0x02 | u8 | flags3 | bit0 = cloth-effect, bit7 = useJointArray |
| 0x03 | u8 | flags4 | occludeType |
| 0x04 | u8 | flags5 | symmetric if `== 1` |
| 0x05 | u8 | flags6 | 0 |
| 0x06 | u32 | instructionOffset / 2 | → `0x40` |
| 0x0A | u8 | maybeMeshCount | hint; uncorrelated with size in retail |
| 0x0B | u8 | maybeInstructionCount | hint |
| 0x0C | u32 | jointArrayOffset / 2 | |
| 0x10 | u16 | numJoints | **≥ 1** (retail never uses 0) |
| 0x12 | u32 | vertexCountsOffset / 2 | |
| 0x16 | u16 | numVertexCounts | **= 2** |
| 0x18 | u32 | vertexJointMappingOffset / 2 | |
| 0x1C | u16 | vertexJointMappingCount | **= 2 × vertexCount** (u16 entries) |
| 0x1E | u32 | vertexDataOffset / 2 | |
| 0x22 | u16 | vertexDataSize / 2 | size of vertex data in **2-byte words** (u16 → caps a section) |
| 0x24 | u32 | endOffset / 2 | end of vertex data |
| 0x28 | u16 | endOffsetDataSize | 0 |
| 0x2E | u32 | (= endOffset / 2) | trailing field xim never reads but the engine does; rest of 0x2A–0x3F is zero |

Writing a 42-byte header (stopping at `endOffsetDataSize`) makes the engine read
instruction bytes as header offsets → **crash**. The full 64 bytes are required.

### Blocks (order as written by the importer)

1. **Instructions** (at `0x40`) — see opcodes below.
2. **Joint array** — `numJoints` × u16. The importer writes an identity array
   `[0..n-1]`; a vertex's joint-ref index then equals the skeleton joint index.
3. **Vertex counts** — u16 `singleJointed`, u16 `doubleJointed`.
4. **Vertex joint refs** — 2 × u16 per vertex (`jointRef0`, `jointRef1`). Packed:
   bits 0–6 = index, bits 7–13 = flipped (mirror) index, bits 14–15 = flip axis.
5. **Vertex data** (last; `endOffset` marks its end):
   - single-jointed vertex: `p0`(3×f32) + `n0`(3×f32) — 24 bytes
   - double-jointed vertex: `p0.x p1.x p0.y p1.y p0.z p1.z w0 w1` then
     `n0.x n1.x n0.y n1.y n0.z n1.z` — 14×f32 = 56 bytes

Retail meshes always have single-jointed vertices first, then double-jointed; an
all-double-jointed section is never seen and the importer avoids it.

### Vertex → world-space skinning (THE assembly math)

> ⚠️ Getting this wrong produces a mesh that looks plausible in bounds but is
> visibly broken. The authoritative reference is the model viewer C++
> `CModel::GetPrimitiveVertex` (`thirdparty/ffxi model viewer source code/.../CModel.cpp`),
> cross-checked against xim and Noesis. Three separate bugs here each shipped a
> "looks broken" mesh before this was nailed down (2026-06-07).

Each bone's **global** transform `g_i` is its local TRS accumulated down the parent
chain (rotation quaternion + translation; FFXI bind bones are rigid, no scale).

**Single-joint vertex** (implicit weight 1):

```
world  = g0.translation + rotate(g0.rotation, p0)
normal = normalize(rotate(g0.rotation, n0))
```

**Two-joint vertex** — the stored `p0`/`p1` are **already multiplied by their
weight** (`p_i = w_i · v_i`, where `v_i` is the unweighted joint-local position).
The weight scales **only the bone translation**, and the two contributions are
**summed** — NOT a `w0·posA + w1·posB` blend:

```
world  = (rotate(g0.rotation, p0) + w0 · g0.translation)
       + (rotate(g1.rotation, p1) + w1 · g1.translation)
normal = normalize(w0 · rotate(g0.rotation, n0) + w1 · rotate(g1.rotation, n1))
```

Note the asymmetry: **positions** are pre-weighted in the data (weight only on the
translation term); **normals** are stored unweighted and weight-blended at assembly.
Verified against retail `ROM/5/3`: stored `p0` equals `w0 · (g0⁻¹ · worldBind)` to
0.00000. Doing `w0·posA + w1·posB` instead squares the weight on the rotated
position and collapses every 2-joint vertex (the torso) toward the bone origins.

**Mirror (symmetric meshes, `flags5 == 1`)** — each primitive is emitted twice. The
mirror copy reuses every vertex with its `flipAxis` applied (negate the matching
local axis of `p_i` and `n_i`) and the **flipped** joint index from the joint-ref.

### Winding & coordinate frame (for export to glTF/FBX)

- **Winding:** DAT triangles are **clockwise-front** (Direct3D; xim sets
  `frontFace(CW)`). glTF/Blender/C4D treat **counter-clockwise** as front, so the
  exporter reverses winding on the non-mirror half. The mirror half is a reflection
  that already flips winding, so it keeps DAT order. Symptom of getting this wrong:
  dark / inside-out / faceted surface (positions fine, normals fine).
- **Coordinate frame:** FFXI is **Y-up**. The exporter parents the skeleton + mesh
  under a 180°-about-X `ffxi_root_correction` node so DCC tools show it upright;
  raw-FFXI → glTF-display maps as `(x, -y, -z)` (Noesis uses `-rotate 180 0 0`).
- **Inverse-bind matrices** (skinned glTF): `ibm_i = g_i⁻¹ = [Rᵀ | -Rᵀ·t]`, so the
  skin is exactly identity at bind (`g_i · ibm_i == I`) and reproduces the baked
  positions. A wrong rigid inverse here tears the mesh the moment any renderer
  applies the skin (worst on the multi-joint torso).

The import path is the exact inverse: it un-skins each world vertex to joint-local
space (`v_i = g_i⁻¹ · world`) and stores `p_i = w_i · v_i` (pre-weighted), with
unweighted normals.

### Instruction opcodes

```
0x8000  set texture     : nextString(0x10) — 16-char texture name
0x8010  render props    : fixed-size block (lighting/specular/tFactor)
0x0054  tri mesh        : u16 nTris, then per tri: 3×u16 vertexIndex + 6×f32 UV (u,v per corner)
0x5453  tri strip       : u16 nTris, 3 start verts + UVs, then per extra vert u16+UV
0x0043  untextured mesh : u16 nTris, per tri 3×u16 index + BGRA(4×u8)
0x4353  untextured strip: u16 nTris, strip verts + one BGRA
0xFFFF  end
```

### Engine constraints (enforced by the importer)

| Constraint | Value | Why |
|---|---|---|
| Triangles per draw call | **128** (384 corners) | fixed per-draw buffer; overflow crashes |
| Vertex-data per section | u16 words → 131070 bytes (~5,400 single-jointed verts) | `vertexDataSize` is u16 |
| Vertex indices per section | u16 (65,535) | index width |
| Header size | **64 bytes**, `flags1=1`, joint array, `numJoints≥1` | engine reads them |

Large meshes are split across multiple `0x2A` sections; the renderer collects all
of them. Recommended budget and retail norms: see [import.md](import.md).

## Texture section (`0x20`)

Inner format byte at `dataStart`: `0xA1` = DXT, `0x91`/`0xB1` = palette/uncompressed.
Retail entity textures are **DXT3** (`0xA1` / `3TXD`); DXT5 (`5TXD`) is in the
format spec but unobserved on entities and not emitted.

### `0xA1` DXT header

| Off | Type | Field |
|-----|------|-------|
| 0x00 | u8 | 0xA1 |
| 0x01 | 16 | texture name (space-padded; matched against the mesh's `0x8000`) |
| 0x11 | u32 | 0x28 |
| 0x15 | u32 | width |
| 0x19 | u32 | height |
| 0x1D | u16 | 1 |
| 0x1F | u16 | bitCount (ignored on the DXT path) |
| 0x21 | 5×u32 | 0 |
| 0x35 | u32 | 0x20 |
| 0x39 | 4 | dxt type: `1TXD` (DXT1) / `3TXD` (DXT3) |
| 0x3D | u32 | DXT data size (`w·h` DXT3, `w·h/2` DXT1) |
| 0x41 | u32 | bytes per 4-px block-row (`w·4` DXT3, `w·2` DXT1) |
| 0x45 | … | raw DXT blocks |

The DXT block data is **standard** (verified: a retail texture decodes
identically with a standard DDS decoder), so `texconv` output is byte-usable. The
engine alpha-tests entity meshes (~69/255 cutout), so DXT3 alpha gives clean
cut-out transparency — author a real alpha channel in the source PNG.

DXT decode/encode details and DDS handling: [../../utils/texture.md](../../utils/texture.md).

## What the importer does NOT write

- **Custom/new skeleton (`0x29`)** — geometry re-skins onto the existing bones;
  adding bones needs a `0x29` writer (joints + attach-point references + bounding
  boxes + root handling). Not built.
- **Cloth-effect simulation** — cloth meshes are re-imported as static skinned
  meshes.
- **Vertex colours** from the DCC — untextured primitives get a neutral colour.
