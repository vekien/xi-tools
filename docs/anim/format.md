# FFXI DAT Animation Format — Knowledge Dump
> Reverse-engineered from `42.DAT` (Chocobo 3D model) and `SkeletonAnimationSection.kt` (xim project).  
> Source of truth: `D:\xidata\xim\src\jsMain\kotlin\xim\resource\SkeletonAnimationSection.kt`

---

## File Structure

A DAT file is a sequence of named blocks. Each block starts with a 4-char magic ID. The top-level container is `cbk_`.

**Known blocks in `42.DAT`:**
```
0x000000  cbk_   (container header)
0x000020  init
0x000090  pop0
0x00011C  init
0x000140  @tl0   (geometry/texture data)
0x01CD10  idl0   (idle animation)    ← 21,680 bytes, 31 frames
0x0221C0  wlk0   (walk animation)
0x025F90  run0   (run animation)
0x029330  vil0   (custom anim, appended at original file end)
```

---

## Block Header (16 bytes)

```
offset  size  description
0x00    4     magic ("idl0", "wlk0", etc.)
0x04    4     encoded size value (NOT raw block size — see notes)
0x08    4     0x00000000 (always zero in observed data)
0x0C    4     0x00000000 (always zero)
```

**Size field (bytes 4–7) — packed section meta**, same layout as mesh/zone sections:
```
type = meta & 0x7F                         # low 7 bits; animation = 0x2B
size = ((meta >> 7) & 0x7FFFF) * 0x10      # bits 7–25: 19-bit size in 16-byte units
# bit 26 = is_shadow (not part of size)
```
Example:
```
idl0:  meta 0x0002A5AB  →  type 0x2B (0xAB & 0x7F), size ((0x2A5AB >> 7) & 0x7FFFF)*0x10 = 21,680
wlk0:  meta 0x0001EEAB
run0:  meta 0x00019BAB
```
`0xAB` is not a mystery constant: type `0x2B` plus the low size bits often produce that
low byte. Writers must use `encode_section_meta` in `src/xi/common/xi_section.py`
(19-bit size mask `0x7FFFF` — a 20-bit write would spill into `is_shadow`).

---

## Animation Header (10 bytes)

Starts at `block_start + 16` (immediately after the 16-byte block header).

```
offset  size  field
0x00    2     unk0 (unknown — preserve as-is)
0x02    2     numJoints
0x04    2     numFrames
0x06    4     keyFrameDuration (float32)
```

`keyFrameDuration = 0.4` in idl0/vil0 → at 30fps game rate = 12 animation keyframes/sec.  
31 frames ÷ 12 ≈ 2.58 seconds per loop.

```
keyFrameDataOffset = block_start + 16 + 10 = block_start + 26
```

---

## Bone Entry Layout (84 bytes, STRIDE = 0x54)

Entries start **immediately** at `keyFrameDataOffset`, packed sequentially.

```
local offset  size  field
0x00          4     jointIndex (int32)
0x04          16    rot_offsets[4]      (int32[4])   — X, Y, Z, W
0x14          16    rot_constValues[4]  (float32[4]) — X, Y, Z, W
0x24          12    trans_offsets[3]    (int32[3])   — X, Y, Z
0x30          12    trans_constValues[3](float32[3])
0x3C          12    scale_offsets[3]   (int32[3])
0x48          12    scale_constValues[3](float32[3])
```

Total: `4 + 16 + 16 + 12 + 12 + 12 + 12 = 84 bytes` ✓

---

## Channel Data (float arrays)

**Offset semantics:**

| Value | Meaning |
|-------|---------|
| `== 0` | Constant — use `constValue` for all frames |
| `> 0` | Variable — read `numFrames` float32 values from computed position |
| `< 0` (INT_MIN = −2147483648) | Absent — no animation data |

**Reading variable channel data:**
```python
byte_pos = keyFrameDataOffset + offset * 4 + frame * 4
value = struct.unpack_from('<f', data, byte_pos)[0]
```

**Skip rule:** If **any** offset in a channel group (rotation **or** translation **or**
scale) is negative, that whole group is dropped; if any of the three groups is absent,
the **entire bone track is skipped**. The root bone (jointIdx=0) has all rot_offsets =
INT_MIN and is therefore dropped as a track.

The float data region and entries region overlap in address space (entries ARE inside the addressable float space). In practice, channel float data is placed after all entries.

---

## Quaternion Format

Stored as `(X, Y, Z, W)` — channel order:
- rotationSequences[0] → X
- rotationSequences[1] → Y
- rotationSequences[2] → Z
- rotationSequences[3] → W

**Interpolation between keyframes uses NLERP** (not SLERP):
```python
def nlerp(q1, q2, t):
    if dot(q1, q2) < 0:
        q2 = negate(q2)   # shortest path
    q = lerp(q1, q2, t)
    return normalize(q)
```

---

## Special Bones

**Root bone (jointIdx = 0):**
- All rot_offsets = INT_MIN → skip, never modify
- Scale ignored by engine
- Translation rotated 270° by engine before applying
- **DO NOT TOUCH**

**Scale channels (all bones):**
- All offsets = 0, constValues = (1, 1, 1)
- Scale is effectively unused — leave alone

---

## Bone Index Ranges (Chocobo, 48 joints total)

From experimentation on `42.DAT`:

| Range | Area |
|-------|------|
| 0 | Root — never modify |
| 3 | Neck/head bone (head bob applied here) |
| 4 | Sub-head bone (near-identity rest pose) |
| 3–15 | Upper body / neck / head |
| 18–21 | Wing area |
| 40–43 | Leg group A |
| 44–47 | Leg group B |

---

## Quaternion Rotation Scaling (N× amplification)

Extract axis, scale half-angle by N, reconstruct:

```python
def scale_angle(x, y, z, w, n):
    w = max(-1.0, min(1.0, w))
    h = math.acos(w)            # half-angle
    s = math.sin(h)
    if abs(s) < 1e-7:
        return 0., 0., 0., 1.   # identity — no axis to scale
    nh = h * n                  # scaled half-angle
    ns = math.sin(nh)
    return x/s*ns, y/s*ns, z/s*ns, math.cos(nh)
```

> ⚠️ Do NOT use `W'=2W²−1, X'=2WX` (double-angle shortcut) — only valid for exactly 2×.

---

## Head Bob Implementation (Bone 3 — Final)

**Axis:** Z (negated direction)  
**Amplitude:** 50°  
**Pattern:** 3 bobs per animation loop  
**Easing:** Sine arc (smooth, non-linear)

```python
deg = 50.0
h = math.radians(deg / 2)
rot_z = (0.0, 0.0, -math.sin(h), math.cos(h))   # Z-axis, negative direction
Q_end = quat_mul(rot_z, Q_start)

for frame in range(num_frames):
    t = abs(math.sin(frame / (num_frames - 1) * 3 * math.pi))
    Q = nlerp(Q_start, Q_end, t)
    for ci, (o, nv) in enumerate(zip(ro3, Q)):
        if o > 0:
            wf(kfd + o*4 + frame*4, nv)
```

**Axis testing results:**
| Axis | Result in engine |
|------|-----------------|
| Y | Head turns left/right (Yaw) |
| X | Head tilts ear-to-shoulder |
| Z | Head nods up/down ✓ |

Z is also the dominant natural-motion axis in bone 3's idle animation data (Z varies 0.0806 → 0.1025 → 0.0669 across frames).

---

## Quaternion Composition

```python
def quat_mul(p, q):
    px, py, pz, pw = p
    qx, qy, qz, qw = q
    return (
        pw*qx + px*qw + py*qz - pz*qy,
        pw*qy - px*qz + py*qw + pz*qx,
        pw*qz + px*qy - py*qx + pz*qw,
        pw*qw - px*qx - py*qy - pz*qz,
    )
```

To apply a delta rotation to an existing rest pose:
```python
Q_end = quat_mul(delta_rotation, Q_start)
```

---

## Appending a New Animation Block

1. Copy existing animation block bytes (e.g. idl0) verbatim
2. Change bytes 0–3 to new magic name (e.g. `b'vil0'`)
3. Modify float channel data in place
4. Append bytes to end of DAT file

```python
idl0_start = 0x1CD10
idl0_end   = 0x221C0
block = bytearray(data[idl0_start:idl0_end])
block[0:4] = b'vil0'
data.extend(block)
vil0_start = len(data) - len(block)
```

No other DAT structural changes needed — the engine scans for named blocks.

---

## Emote Playback: the `0x07` EffectRoutine `dur`

For **emotes**, the in-game playback window is **not** set by the `0x2B` frame
count — it's set by an `0x07` EffectRoutine in the same DAT that references the clip
and carries a duration. The client time-scales the clip into `dur / (2·rate)`
frames, so a clip plays at natural speed only when:

```
dur = 2 × clip_length_in_gameframes        # clip_length = (numFrames - 1) / keyFrameDuration
```

A longer edited clip left with the original `dur` is cut off mid-play. `anim import`
grows `dur` automatically (grow-only) unless `--keep-routine-duration`.

**`0x07` section, Section-2 op `0x05` (SkeletonAnimationRoutine)** — fields relative
to the entry position `p` within the section body (body = section bytes after the
16-byte header; the Section-2 list pointer is `u32` at body `0x10`):

| Offset | Size | Field |
|--------|------|-------|
| `p+0`  | 1    | opcode (`0x05`) |
| `p+1`  | 2    | `numArgs` (low 5 bits → entry stride = `max(1,n)·4`) |
| `p+4`  | 2    | `delay` (u16) |
| `p+6`  | 2    | **`dur`** (u16) — the playback window |
| `p+8`  | 4    | clip ref — 4-char wildcard tag (`poi?`) |
| `p+30` | 2    | `maxLoops` (u16; emotes = 1 → play once then hold) |

Across `rom/37/13`'s emotes the relationship is exact: `sl1` 84→`dur` 168, `sl2`
78→156, `kne` 130→260. Measured against the **client reimpls**
(`SkeletonAnimator.kt` / UE5 `FFXIActorFacade.cpp`): `LengthFrames =
(numFrames-1)/keyFrameDuration`, `ScalingFactor = LengthFrames / LoopDurationFrames`,
`LoopDurationFrames = dur/(2·rate)`. See [emotes.md](emotes.md) for the overlay-slot
model (`poi0` lower body + `poi1` upper body played together).

---

## Coordinate System Notes

- FFXI internal space: non-standard axis orientation (root translation rotated 270° on apply)
- When importing into Unreal Engine, axes transform — Z rotation in raw data ≠ Z in UE
- Raw bone 3 rest pose: `Q = (0.00020, -0.00976, 0.08055, 0.99670)`
  - Axis ≈ (0.003, -0.120, 0.993) — nearly pure Z with tiny −Y component
  - Angle ≈ 9.3°

---

## Key Files

| Path | Purpose |
|------|---------|
| `<FFXI_DIR>\ROM\169\42.DAT` | Chocobo model (modified) |
| `<FFXI_DIR>\ROM\169\42 - Copy.DAT` | Clean backup — always copy from here |
| `D:\xidata\xim\src\jsMain\kotlin\xim\resource\SkeletonAnimationSection.kt` | Authoritative parser source |
| `D:\xidata\xim\src\jsMain\kotlin\xim\resource\SkeletonInstance.kt` | Root bone / transform logic |
