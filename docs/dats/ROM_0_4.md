# ROM/0/4.DAT - Fixed-Page Graphics Data

**Path:** `ROM/0/4.DAT`  
**Size:** 12,582,912 bytes (`0x00C00000`)  
**Magic:** none  

This file is not a normal DAT container and does not contain standard UI texture magic such as
`menu`, `lobb`, `titl`, `TIM2`, `DDS `, `1TXD`, `3TXD`, or `5TXD`.

Direct inspection shows a fixed-size page table: `4096` records of exactly `0xC00` bytes each.
The first word of every record follows a deterministic address-like pattern also seen in the
neighboring files `ROM/0/5.DAT` through `ROM/0/8.DAT`.

Current best interpretation: this is precompiled low-level graphics/page data, likely related to
boot or system UI rendering, not a texture sheet that can be edited with the regular `ui tex export`
workflow.

---

## Top-Level Layout

```text
+0x000000  Record[0]     0xC00 bytes
+0x000C00  Record[1]     0xC00 bytes
+0x001800  Record[2]     0xC00 bytes
...
+0xBFF400  Record[4095]  0xC00 bytes
```

The file size divides exactly by `0xC00`:

```text
0xC00000 / 0xC00 = 4096 records
```

---

## Record Header

The first `0x30` bytes are structured little-endian `uint32` fields. Example record 0:

```text
+0x00  00000000
+0x04  00201E48
+0x08  BF1D0020
+0x0C  FFFF0000
+0x10  00000020
+0x14  00000000
+0x18  00000040
+0x1C  00000082
+0x20  00000000
+0x24  00000086
+0x28  00000000
+0x2C  00000020
```

Confirmed fixed fields across all 4096 records in `ROM/0/4.DAT`:

| Offset | Value |
|---|---:|
| `+0x18` | `0x00000040` |
| `+0x1C` | `0x00000082` |
| `+0x20` | `0x00000000` |
| `+0x28` | `0x00000000` |
| `+0x2C` | `0x00000020` |

The fields at `+0x04`, `+0x08`, `+0x0C`, `+0x10`, `+0x14`, and `+0x24` vary by record.

---

## Record Identifier Pattern

The first `uint32` at `+0x00` is not random. For every record in `ROM/0/4.DAT`, the value is:

```text
group = index // 512
within = index % 512

id = ((group % 4) * 0x4000)
   + ((group // 4) * 0x100)
   + ((within // 256) * 0x2000)
   + ((within % 8) * 0x20)
   + ((within % 256) // 8)
```

Selected examples:

| Record | `+0x00` |
|---:|---:|
| 0 | `0x00000000` |
| 1 | `0x00000020` |
| 7 | `0x000000E0` |
| 8 | `0x00000001` |
| 255 | `0x000000FF` |
| 256 | `0x00002000` |
| 511 | `0x000020FF` |
| 512 | `0x00004000` |
| 1024 | `0x00008000` |
| 1536 | `0x0000C000` |
| 2048 | `0x00000100` |
| 3584 | `0x0000C100` |

This looks like a swizzled page/tile address rather than an ordinary sequential index.

---

## Neighboring Files

`ROM/0/5.DAT` through `ROM/0/8.DAT` use the same `0xC00` record size and the same identifier
formula with a different starting value.

| DAT | Size | Records | First `+0x00` | Identifier formula |
|---|---:|---:|---:|---|
| `ROM/0/4.DAT` | `0x0C00000` | 4096 | `0x00000000` | matches |
| `ROM/0/5.DAT` | `0x0C00000` | 4096 | `0x00000200` | matches with start offset |
| `ROM/0/6.DAT` | `0x1380000` | 6656 | `0x00000800` | matches with start offset |
| `ROM/0/7.DAT` | `0x1200000` | 6144 | `0x00000500` | matches with start offset |
| `ROM/0/8.DAT` | `0x0180000` | 512 | `0x00000400` | matches with start offset |

The start offsets are significant because the CatsEyeXI override set also includes these same
low-numbered DATs and their `ROM/118/106-109.DAT` counterparts.

---

## Negative Findings

- No standard image/container magic was found.
- The dense region beginning around `+0x280` is exactly `0x800` bytes before `+0xA80`, which is the
  same size as a `64x64` 4bpp bitmap or a `64x64` DXT1 texture payload.
- Wrapping that `0x800` byte region in a DDS/DXT1 header decodes, but the result is visual noise.
  This makes a standalone DXT1 texture interpretation unlikely.
- Treating the same region as a simple linear 4bpp bitmap also does not produce a readable glyph or
  UI image without additional decoding such as palette lookup, tiling, swizzling, or command parsing.

---

## Open Questions

- Whether the records are PS2-era graphics upload packets, swizzled texture pages, font pages, or
  another low-level renderer format.
- Which fields define page dimensions, format, palette, or destination address.
- Whether `ROM/118/106-109.DAT` are direct alternate versions of `ROM/0/4-7.DAT` for another client
  mode, region, or override layer.
