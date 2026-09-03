# Pre-production / prototype zones

A handful of DATs in `ROM/0/` are leftover **development maps** that never shipped as
playable zones. They have no entry in the zone-name table and no zone id, so they are
invisible to both the named-zone scan and the FTABLE room scan — `xi zone list --dev`
lists them from a curated table (`DEV_ZONES` in `src/xi/zone/xi_list.py`).

They are not just unfinished retail zones. They use an **older on-disk layout**, and a
reader written against the shipped format silently produces a partial zone (or nothing
at all) rather than an error. This page documents each difference, how to detect it, and
what still doesn't work.

> Names for these maps are inferred from the Japanese mesh/texture strings inside each
> DAT (`sabaku` = 砂漠 desert, `setugen` = 雪原 snowfield, `gratest` = graphics test),
> not from any official source.

---

## 1. Chained mesh groups (`0x2E`)

The biggest difference, and the one that makes a zone load "half empty".

A shipped zone's `0x2E` section stores the submesh stream offset at `+0x3C` and its count
at `+0x40`. Pre-production sections routinely leave **both zero** and park the offset at
`+0x4C` or `+0x5C` instead. Worse, a single section can hold **several groups**: after the
first submesh stream comes another header — `count (u32) + bbox (6×f32) + pad (u32)`,
`0x20` bytes total — and then more submeshes. Large floor and wall meshes chain
several of these.

A reader that follows only `+0x3C`/`+0x40` gets the first group and stops. Measured share
of each zone's geometry that lives in the *chained* groups:

| DAT | primary stream | full | in chained groups |
|---|---:|---:|---:|
| ROM/0/46 | 16,578 tris | 38,949 | **57.4%** |
| ROM/0/41 | 31,061 tris | 44,507 | **30.2%** |
| ROM/0/42 | 34,892 tris | 48,591 | **28.2%** |
| ROM/0/28 | 79,494 tris | 79,494 | 0% |
| ROM/1/41 (retail) | 54,008 tris | 54,008 | 0% |
| ROM/0/64 (retail) | 34,532 tris | 34,532 | 0% |

No retail zone tested uses chained groups — the feature appears only in the
pre-production set, which is consistent with it being an early format that was dropped
before release.

**Detection.** You cannot trust the counts, so validate candidate headers structurally: a
submesh header is 16 bytes of texture name followed by `numVerts (u16)`, and after
`numVerts × stride` bytes there must be a plausible `numIndices (u16)` that also fits
inside the section. Walk groups only while the bytes at the cursor pass that test, and
keep a high-water mark so a group reached twice (once by the cursor, once by a `+0x4C` /
`+0x5C` pointer) isn't added twice.

### Note on rendering

An earlier version of this page blamed chained groups for these zones rendering
incompletely in the retail client. That was wrong — the real cause is the placement
record stride (see §4), verified against `FFXiMain.dll` and fixed by converting the
zone. Chained groups are still a genuine format difference that a *parser* must handle,
but they are not what made the game drop objects.

---

## 2. Blank submesh texture names

Prototype submeshes often leave the 16-byte texture name all-NUL or all-space. Any
detection that gates on "the name looks like text" rejects these and truncates the mesh.
Accept a blank name, but then require the vertex and index counts to check out — that is
the only thing separating a real header from arbitrary bytes.

Several of these zones also ship **no textures at all** (ROM/0/46 and ROM/0/48 have zero
`0x20` sections; ROM/0/41 has two). They are meant to be read untextured.

---

## 3. Strip flag not set on strip data

Bit 0 of the config word at `+0x04` marks a triangle strip. Some prototype meshes leave it
clear even though the index buffer is plainly a strip. Fall back to the data: if the flag
is clear but `numIndices % 3 != 0`, treat it as a strip.

Degenerate triangles (a repeated index) are strip restarts in both cases — skip them and
reset the winding parity, or every triangle after a restart is wound backwards.

---

## 4. Placement records are `0x54`, not `0x64`

The `0x1C` ZoneDef stores object placements as fixed-size records after a `0x20` header.
Retail uses **`0x64`** bytes per record. Pre-production zones use **`0x54`** — the same
name/pos/rot/scale fields, packed tighter, with no room for the `fileIdLink` field that
sits at `+0x50` in the retail record.

Reading a `0x54` zone at `0x64` misaligns every record after the first:

```
ROM/0/41.DAT  n=393  stride=0x54 -> printable names 393/393
ROM/0/41.DAT  n=393  stride=0x64 ->  printable names 57/393
ROM/1/41.DAT  n=704  stride=0x64 -> printable names 704/704
ROM/1/41.DAT  n=704  stride=0x54 -> printable names  34/704
```

**Detection.** The mode byte — the top byte of the u32 at the section data start — is
`<= 5` on these zones (the same modes that skip `0x1C` decryption entirely). As a
fallback, score both strides by how many records yield a printable mesh id and take the
clear winner.

Note that a wrong stride does not always look wrong. A modify of record *N* at `0x64`
can land on a valid-looking field of a *different* record when `0x64 × a == 0x54 × b`
for small `a`, `b` — e.g. `100 × 63 == 84 × 75 == 6300`, which made one edit appear to
apply correctly. Do not treat "it worked once" as evidence the stride is right.

### The retail client always reads `0x64` — verified

`FUN_10177ef0` in `FFXiMain.dll` is the ZoneDef reader (it carries the `mode > 0x1A`
decryption gate, the `^ 0x55` name unmask, and the TRS matrix build). Its per-record
loop ends with:

```
10178cff   ADD ECX, 0x64
10178d0a   ADD EDX, 0x64
```

100 bytes per record, unconditionally. Every other `0x54` in that function is a stack
offset. **There is no branch on the mode byte for stride**, so the client misreads every
pre-production zone: on ROM/0/41 only 57 of 393 rows land on a printable name plus usable
floats, and the rest are silently dropped. That is what makes these zones look half-built
in game — objects missing, others in the wrong place, and edits appearing to do nothing
because the tools write at one stride while the client reads at another.

### Converting a zone so the client can read it

`convert_zonedef_to_retail_stride()` (`xi.zone.xi_zonedef`) widens every record to `0x64`
with the extra 16 bytes zeroed, and re-serialises the collision block at its new base so
all of its internal offsets are recomputed rather than patched. The array grows by
`node_count * 0x10`.

```
ROM/0/41 before  records=393  printable-at-0x64= 57/393  collision-tris=44507
ROM/0/41 AFTER   records=393  printable-at-0x64=393/393  collision-tris=44507
```

Confirmed in game: the zone renders properly after conversion.

It refuses to run when the collision block is not immediately after the object array, or
when the zone has a space tree — neither holds for the known `0x54` zones, and relocating
tree nodes is not implemented.

**A converted zone still carries the pre-production mode byte**, so stride detection must
not trust mode alone. `zonedef_record_size()` scores both strides by printable-name count
and only falls back to mode when neither wins; the editor's JS parser does the same.

### Publishing to a `0x54` zone

Supported. `zonedef_record_size()` resolves the stride once and `ZoneDef.record_size`
carries it through `xi_zonedef`, `xi_apply_changes`, `xi_export` and `xi_add_object`, so
reads and writes both land on real record boundaries.

This was not always true. Until it was threaded through, the writer used a hardcoded
`data_start + 0x20 + index * 0x64`, which on a `0x54` zone drifts 16 bytes per record
and writes pos/rot/scale across record boundaries. One publish to ROM/0/41 damaged 164
of 393 records — 63 mesh names overwritten with float bytes (`tu1` → `tu1         R#`,
`ta1` → `ì4`), 45 positions and 47 scales altered — while reporting success. If you see
that signature in a zone, restore from its `.base` (`xi zone reset <dat>`).

Regression checks worth repeating after touching this code:

- every zone in the editor's list resolves `0x64` except the 13 prototype DATs;
- a placement modify on a retail zone changes exactly the targeted record;
- a placement modify on ROM/0/41 changes exactly the targeted record, with all 393
  mesh names intact.

**Collision changes were always safe.** Collision baking works on the `0x1C` collision
block and never touches the placement record table.

---

## 5. Extra texture types

`0x20` texture sections in these zones use type bytes `0x01` and `0x05` (and `0x81`)
alongside the usual `0x91`/`0xB1`. All share the `0x91` header and payload layout and
decode through the same paletted path. Without them the zone parses zero textures and
renders untextured.

---

## 6. 16-bit palettes (`A1R5G5B5`)

The texture header is a standard 40-byte `BITMAPINFOHEADER`. Its final dword — where
BMP puts `biClrImportant` — is repurposed as **bits per palette entry**:

| Value  | Palette                | Size        |
| ------ | ---------------------- | ----------- |
| `0x20` | 32-bit BGRA            | 1024 bytes  |
| `0x10` | 16-bit A1R5G5B5        | 512 bytes   |

Retail is always `0x20` (864 DATs scanned, 565 paletted textures, zero exceptions), so
decoders that hardcode a 256 x u32 palette work everywhere until they hit prototype
content. A `0x10` palette read as `0x20` goes wrong twice: the colours are scrambled,
*and* every pixel is offset by 512 bytes. The result is a recognisable image buried in
bright-green speckle — Noesis decodes these correctly, which is the usual tell.

Affected files: `ROM/0/29` (3 textures), `ROM/0/33` (2 — `gratest_sizenn`,
`gratest_s00_jew`), `ROM/0/42` (1).

Unpack as `a = bit 15 ? 255 : 0`, `r = bits 14-10`, `g = bits 9-5`, `b = bits 4-0`, each
5-bit channel scaled `* 255 / 31`.

### Every copy of the decoder has to be fixed

This bit cost the most time. Paletted-texture decoding is duplicated in **four** places
across the three repos, and they do not share a code path — patching one and reloading
looks like the fix simply did not work:

| Repo             | File                                | Used for                          |
| ---------------- | ----------------------------------- | --------------------------------- |
| `xi-tools`       | `src/xi/entity/mesh/xi_export.py`   | exports (OBJ/FBX/glTF materials)  |
| `xi-model-viewer`| `ui/js/zone.js`                     | **zone rendering** (the visible one) |
| `xi-model-viewer`| `ui/js/dat.js`                      | entity/model textures             |
| `xi-zone-editor` | `ui/ffxi/zone.js`                   | zone rendering                    |
| `xi-zone-editor` | `ui/ffxi/sections.js`               | section inspector / texture list  |

`ui/ffxi/sections.js` is written differently from the others — it locates the palette via
`headerSize` at `ds + 17` and hardcodes `palOff + 1024` for the pixel offset, so it needs
both the entry unpack *and* the `+512` stride fixed.

Both UIs are Vite apps whose `ui/dist/` is gitignored, so a source edit changes nothing
until `npm run build` is re-run in `ui/` and the app is restarted.

### How this was tracked down

Worth repeating because the symptom points away from the cause:

1. The green speckle looked like a renderer problem. It was not — the collision debug
   overlay is grey for terrain 0, and the "solid Unreal-green" navmesh overlay needs a
   `.nav` file that did not exist.
2. Decoding the zone's textures straight out of the DAT to PNG and *looking at them*
   settled it in one step: `gratest_ido` / `johheki` / `yuka_s` were perfect, only
   `gratest_sizenn` and `gratest_s00_jew` were noise. A whole-pipeline bug would have
   broken all of them.
3. Diffing a good header against a bad one byte-for-byte left exactly one differing
   field — `0x20` vs `0x10` in the last dword.

Counting bright-green pixels (`g > 200, r < 80, b < 80`) makes it measurable rather than
a judgement call: 5.4% / 5.0% before, 0.0% after.

---

## Zone list

`xi zone list --dev` / `xi zone json --dev` emit these under a `Dev / Prototype` group.
The table lives in `DEV_ZONES` (`src/xi/zone/xi_list.py`) — edit the names there.

| DAT | Name | Layout |
|---|---|---|
| ROM/1/5 | Character Creation | `0x64` |
| ROM/0/28 | Dev Town — windmill + bridge | `0x64`, no chained groups |
| ROM/0/29–30 | Dev Snowfield 1–2 | `0x54` |
| ROM/0/31 | Dev Cave + Waterfall | `0x54` |
| ROM/0/32 | Dev Boss Test | `0x54` |
| ROM/0/33 | Dev Castle Town | `0x54` |
| ROM/0/34 | Dev Desert | `0x54` |
| ROM/0/35 | Dev Forest — moss test | `0x54` |
| ROM/0/36 | Dev Cliffs + Forest | `0x54` |
| ROM/0/37 | Dev World Map / diorama? | `0x54` |
| ROM/0/38 | Dev Mountain Terrain | `0x54` |
| ROM/0/39 | Fort Ghelsba prototype? | `0x54` |
| ROM/0/40 | Dev Test Plane — 100 flat tiles, 400×400 | `0x64` |
| ROM/0/41 | Castle prototype — very early? | `0x54`, chained groups |
| ROM/0/42 | Tower prototype? | `0x54`, chained groups |
| ROM/0/43 | Northern San d'Oria prototype? | `0x64` |
| ROM/0/44 | Bastok interior prototype? | `0x64` |
| ROM/0/46 | Ru'Lude Gardens prototype (untextured) | `0x64`, chained groups |
| ROM/0/47 | Chateau d'Oraguille prototype? | `0x64` |
| ROM/0/48 | Selbina prototype (untextured) | `0x64` |
| ROM/0/49 | Ship / airship room prototype? | `0x64` |

`ROM/0/45` parses to zero meshes and zero placements — genuinely empty, so it is not
listed. `ROM/0/50` was believed to be a Bastok mog-house room but doesn't load
anything in-editor (2026-09) — it isn't one; the real Bastok mog houses are
`ROM/1/22.DAT` (rental) and `ROM/1/46.DAT` (home nation), see `MOG_HOUSE_NAMES` in
`src/xi/zone/xi_list.py`.

The two layout traits are independent: ROM/0/46 uses retail-size placement records but
chained mesh groups, while ROM/0/29 uses `0x54` records with a single mesh group. Detect
each separately.

---

## See also

- [format.md](format.md) — the shipped `0x2E` / `0x1C` layouts these deviate from
- [zones.md](zones.md) — zone id → DAT resolution and the full zone table
- [collision.md](collision.md) — collision authoring, which works on these zones
