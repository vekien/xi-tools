# FFXiMain.dll — POL1 Packer, Unpacking Tools & Monster Model ID Formula

## Overview

`FFXiMain.dll` is packed with a **custom Square Enix packer** (not SecuROM directly — SecuROM is only the disc authentication layer). The packer compresses the `.text` section using a simple LZSS variant and stores the result in a new section called `POL1`. The `.text` section is empty on disk and is reconstructed in memory at load time by the unpacker stub.

> **CLI category:** all client DLL tools live under **`xi dll …`** (not a top-level
> `xi ffximain`). Shared unpack/pack for FFXiMain, polcore, and app is documented in
> [dll.md](dll.md). Sibling modules: [polcore.md](polcore.md), [app.md](app.md).

---

## PE Section Layout

| Section | VA | Raw offset | Raw size | Virtual size | Notes |
|---|---|---|---|---|---|
| `.text` | `0x00001000` | `0x00000400` | **0** | `0x0032716E` | Empty on disk — filled at runtime |
| `.rdata` | `0x00329000` | `0x00000400` | `0x00026000` | `0x00025595` | |
| `.data` | `0x0034F000` | `0x00026400` | `0x00085000` | `0x00677B49` | |
| `POL1` | `0x009CC000` | `0x000AF600` | `0x001E5A00` | `0x001E5898` | Compressed `.text` + unpacker stub |
| `.reloc` | `0x00BB2000` | `0x00295000` | `0x0002F400` | `0x0002F268` | Relocations |

The OEP (`AddressOfEntryPoint`) points **into POL1** at RVA `0x00BB17B0` — the unpacker stub executes before any game code. *(The post-unpack entry point — where execution lands in the decompressed `.text`, i.e. the address you want after dumping — is RVA `0x3162AF` per an external decompile (2026-08 crosscheck); not independently verified here.)*

---

## LZSS Decompression Algorithm

The unpacker stub (at VA `0x10BB17B0`, file offset `0x00294DB0`) implements a straightforward **LZSS decompressor**. Decompiled from the stub:

```
src       = POL1 base (VA 0x109CC000)
src_size  = 0x1E57B0  (1,988,528 bytes — everything before the stub itself)
dst       = .text base (VA 0x10001000)
dst_size  = 0x32716E  (3,305,838 bytes)
```

### Algorithm

```python
def lzss_decompress(src: bytes, dst_size: int) -> bytes:
    dst = bytearray(dst_size)
    si = di = 0
    while si < len(src) and di < dst_size:
        ctrl = src[si]; si += 1          # control byte
        for _ in range(8):               # process 8 bits, MSB first
            carry = (ctrl >> 7) & 1
            ctrl  = (ctrl << 1) & 0xFF
            if carry:                    # bit=1 → literal
                dst[di] = src[si]; si += 1; di += 1
            else:                        # bit=0 → back-reference
                b0 = src[si]; si += 1
                b1 = src[si]; si += 1
                offset = ((b0 << 8) | b1) & 0xFFF   # 12-bit lookback distance
                if offset == 0: return bytes(dst[:di])  # end-of-stream
                length = (b0 >> 4) + 3               # 4-bit length (min 3, max 18)
                for _ in range(length):
                    dst[di] = dst[di - offset]; di += 1
    return bytes(dst[:di])
```

- Control byte is processed **MSB first** (bit 7 → bit 0)
- `bit = 1` → copy one literal byte from source to output
- `bit = 0` → copy `length` bytes from `output[current - offset]` to output
- `offset = 0` is the end-of-stream sentinel

Verified: `xi dll ffximain unpack` produces exactly 3,305,838 bytes of `.text` that
disassemble as valid x86 code.

---

## Unpacking Tools

Lead with the **CLI** (`xi dll ffximain …`). All outputs are **research only** — the
game loads the original packed DLL, not any of these.

### `xi dll ffximain unpack`

Decompresses POL1 and writes a fully patched **`FFXiMain_unpacked.dll`**.

```
uv run xi dll ffximain unpack
uv run xi dll ffximain unpack --dll PATH --output PATH
```

- Restores the decompressed `.text` bytes back into the PE file structure
- Fixes `SizeOfRawData` in the `.text` section header so the PE is valid
- Output is a proper Windows DLL — load in Ghidra or IDA Pro for full analysis
  (set image base `0x10000000`, auto-analysis will run correctly)

**Why the game won't run it:** The OEP stub would try to decompress POL1 into
`.text` again, corrupting the already-filled section. Research only.

### `xi dll ffximain text-dump`

Decompresses POL1 and writes two flat files:

```
uv run xi dll ffximain text-dump
uv run xi dll ffximain text-dump --dll PATH --output-dir DIR
```

- **`pol_decompressed.bin`** — raw `.text` bytes, no PE wrapper (3.2 MB)
  Load in Ghidra as a raw binary (x86 32-bit, image base `0x10000000`).
- **`pol_decompressed.txt`** — full linear disassembly (~45 MB, 1,053,190 instructions)
  Plain text, one instruction per line — grep-friendly for hunting constants and patterns.

Takes ~2–3 minutes to run.

### `xi dll ffximain gear-groups` / `gear-patch`

```
uv run xi dll ffximain gear-groups [--race RACE] [--slot SLOT] [--json]
uv run xi dll ffximain gear-patch [--max-model N] [--dry-run]
```

List per-race per-slot gear model groups from the DLL, or patch those groups so
custom `model_id`s resolve (pairs with `xi ftable expand gear`).

### When to use which

| Goal | Tool |
|---|---|
| Full decompiler (Ghidra/IDA) with PE metadata | `xi dll ffximain unpack` |
| Grep / binary search scripts | `xi dll ffximain text-dump` |
| Gear group table / custom model_id patch | `xi dll ffximain gear-groups` / `gear-patch` |
| Search for a specific constant or instruction | research scripts against `pol_decompressed.bin` (see below) |
| Disassemble a region of the packed DLL (non-.text sections) | research `disasm_lookup.py` |

### Workflow

```
FFXiMain.dll  (packed — game uses this)
      │
      ├─ xi dll ffximain unpack ──────────→ misc/FFXiMain_unpacked.dll  (Ghidra/IDA)
      │
      └─ xi dll ffximain text-dump ───────→ pol_decompressed.bin        (search scripts)
                                        └→ pol_decompressed.txt        (grep)
```

### Research scripts (not CLI)

One-off helpers under `docs/dats/research/` / `research/` — there is **no**
`ffximain_unpacker.py` in the tree; use the CLI above.

| Script | Purpose |
|---|---|
| `pol1_inspect.py` | Show PE section layout and dump POL1 stub bytes |
| `pol1_unpack.py` | Exploratory: OEP finder + brute-force rotation |
| `search_model_formula.py` | Search `pol_decompressed.bin` for monster modelid formula constants |
| `search_gear_formula.py` | Search `pol_decompressed.bin` for gear race-offset constants |
| `lib/disasm_lookup.py` | Disassemble a window around a known raw file offset in the packed DLL |

---

## xiclient Symbol Cross-Reference

`thirdparty/xiclient/src` is a useful companion reverse-engineering source tree. It contains real
client-style class/resource names, packed resource structs, language-dependent DAT tables, and VFS
logic that match what we see in `FFXiMain.dll` and ProcMon traces. Treat it as an independent naming
cross-reference, not as proof that every field name is final.

Notable files:

| xiclient file | Useful contents |
|---|---|
| `XIClient/include/Common/RTTI.h` | Runtime class metadata layout: `{ ClassName, ClassSize, ClassParent }` |
| `XIClient/include/Constants/Strings.h` | Exact resource IDs such as `menu    lobbywin`, `menu    playermo`, `menu    logwindo` |
| `XIClient/source/Constants/DatIndices.cpp` | Language-dependent DAT/file_id tables for JP/EN/FR/DE |
| `XIClient/include/Resource/ResourceType.h` | Resource type enum (`D3m`, `Mot`, `Skl`, `Rid`, `Weather`, `Damvalueprog`, etc.) |
| `XIClient/include/UI/MenuDefinitionFormat.h` | Packed UI menu/frame/button definition structs |
| `XIClient/include/UI/MenuShapeFormat.h` | Packed UI quad/shape texture coordinate structs |
| `XIClient/source/System/FileIO/FileIOVirtualFileSystem.cpp` | FTABLE/VTABLE overlay and `ROM2..ROM13` merge logic |

### Runtime class names

xiclient models the client's RTTI as:

```cpp
struct RTTI {
    const char* ClassName;
    size_t ClassSize;
    const RTTI* ClassParent;
};
```

Many class names match the original client naming style and are valuable search terms when scanning
`pol_decompressed.bin` / Ghidra, for example `CApp`, `CDx`, `CMoResource`, `CYySepRes`, `CYyIcon`,
`CXiOpening`, `CXiMovie`, `XiZone`, `ZoneRenderer`, `TkManager`, `CTkQueryWindow`, `YkWndPartyList`,
and `Ka*` menu/helper classes.

### Language-dependent DAT labels

xiclient names several language-dependent DAT slots. The important current mappings for the English
client are:

| xiclient enum/name | English file_id | Resolved DAT | xi interpretation |
|---|---:|---|---|
| `TEX_General` | `0x9A76` / 39542 | `ROM/119/51.DAT` | Main UI `menu` texture/resource DAT |
| `MENU_Unk1` | `0x51` / 81 | `ROM/118/114.DAT` | Boot-loaded numeric `mnc2`/`mgc_`/`comm` tables |
| `MENU_MissionQuest` | `0x52` / 82 | `ROM/118/115.DAT` | Live English mission/quest text DB |
| `TEX_Icons1` | `0x9A7F` / 39551 | `ROM/280/15.DAT` | Main menu/status/job icon DAT (`mgc_`) |
| `TEX_Icons2` | `0x9A88` / 39560 | `ROM/324/95.DAT` | Secondary icon DAT (`mgc_`) |

This validates our ProcMon-based conclusion that `ROM/118/115.DAT` is the live mission/quest text
file and gives `ROM/118/114.DAT` a client-side label (`MENU_Unk1`) without implying it is 2D title
layout.

### Menu resource IDs

The client uses 16-byte resource IDs split as 8-byte category + 8-byte identifier. Useful boot/lobby
and UI identifiers from `Constants/Strings.h` include:

| Resource ID | Meaning / likely use |
|---|---|
| `menu    lobbywin` | Lobby window |
| `menu    loby1win` | Lobby sub-window 1 |
| `menu    loby2win` | Lobby sub-window 2 |
| `menu    lobycwin` | Lobby character/window variant |
| `menu    ptcbgwin` | Patch/background window |
| `menu    ptc8lice` | License/TOS style window |
| `menu    netwait ` | Network wait window |
| `menu    netbar  ` | Network bar |
| `menu    playermo` | Player/game selection menu |
| `menu    logwindo` | Log window |
| `menu    chmkrace` / `chmkface` / `chmkhair` / `chmksize` / `chmkjobs` / `chmkname` | Character creation menus |
| `menu    race1   ` through `menu    race8   ` | Character creation race assets |

These names are better search targets than guessed labels when hunting title/lobby layout in DATs or
FFXiMain disassembly.

### Menu definition structs

xiclient's packed menu definition structs line up with `xi ui layout menu-pos`:

```cpp
struct FrameDefinitionHeader {
    uint16_t TotalSize;
    int16_t PosX;
    int16_t PosY;
    int16_t CursorOffsetX;
    int16_t CursorOffsetY;
    int16_t Width;
    int16_t Height;
    int16_t DrawOffsetX;
    int16_t DrawOffsetY;
    uint8_t Unknown_Byte18;
    uint8_t AnchorType;
    uint8_t ShapeReferenceCount;
    uint8_t HelpTextIdLength;
    uint8_t TitleTextIdLength;
};

struct ButtonDefinitionHeader {
    uint16_t TotalSize;
    int16_t PosX;
    int16_t PosY;
    int16_t CursorOffsetX;
    int16_t CursorOffsetY;
    uint16_t SizeX;
    uint16_t SizeY;
    int16_t SelectRectOffsetX;
    int16_t SelectRectOffsetY;
    int16_t ButtonID;
    uint8_t Padding_Bytes20_22[3];
    int8_t NavUpButtonID;
    int8_t NavDownButtonID;
    int8_t NavLeftButtonID;
    int8_t NavRightButtonID;
    uint8_t ShapeReferenceCount;
    uint8_t Unknown_Byte28;
    uint8_t HelpTextIdLength;
    uint8_t TitleTextIDLength;
    uint8_t Unknown_Byte31;
};
```

This supports the current interpretation that normal menu position records are `TotalSize, PosX,
PosY, CursorOffsetX, CursorOffsetY, Width/SizeX, Height/SizeY, ...`. It also reinforces why editing
`ROM/0/1.DAT` or the parsed positions in `ROM/119/51.DAT` can be valid for normal menus while still
not affecting the modern boot/title flow if those resources are not the ones used at runtime.

### FTABLE/VTABLE overlay behavior

xiclient's VFS loads base `FTABLE.DAT` / `VTABLE.DAT`, then scans `ROM2` through `ROM13` for
`FTABLEn.DAT` / `VTABLEn.DAT`. For each expansion table, entries are merged only when the expansion
VTABLE byte equals that ROM index. This matches xi's table model and explains why each file_id is a
logical index resolved through the active merged FTABLE/VTABLE rather than a direct path.

---

## Monster Model ID → File ID Formula

Found at VA `0x100C513D` in the decompressed `.text`. This is a small function that converts a monster `modelid` to a `file_id` used to index FTABLE/VTABLE:

```asm
100C513D:  mov ecx, 0x514           ; default offset = 1300 (range [0, 1500))
100C5142:  cmp eax, 0xDAC           ; modelid >= 3500?
100C5147:  jl  → check_3000
100C5149:  mov ecx, 0x18D6B         ; offset = 101739
100C514E:  sub eax, 0xDAC           ; modelid -= 3500
100C5153:  add eax, ecx             ; file_id = (modelid-3500) + 101739
100C5155:  ret

100C5156:  cmp eax, 0xBB8           ; modelid >= 3000?
100C515B:  jl  → check_1500
100C515D:  mov ecx, 0x18643         ; offset = 99907
100C5162:  sub eax, 0xBB8           ; modelid -= 3000
100C5167:  add eax, ecx             ; file_id = (modelid-3000) + 99907
100C5169:  ret

100C516A:  cmp eax, 0x5DC           ; modelid >= 1500?
100C516F:  jl  → fallthrough
100C5171:  mov ecx, 0xCA53          ; offset = 51795
100C5176:  sub eax, 0x5DC           ; modelid -= 1500
100C517B:  add eax, ecx             ; file_id = (modelid-1500) + 51795
100C517D:  ret
; fallthrough (modelid < 1500): eax += 1300 (ECX still = 1300)
```

### Simplified formula table

| modelid range | file_id formula | flat offset |
|---|---|---|
| 0 – 1499 | `modelid + 1300` | +1300 |
| 1500 – 2999 | `(modelid - 1500) + 51795` | +50295 |
| 3000 – 3499 | `(modelid - 3000) + 99907` | +96907 |
| **3500+** | `(modelid - 3500) + 101739` | **+98239** |

Retail monsters use **all four ranges** — the server's `mob_pools` table has ~7,176 modelids below 3000 and 122 in 3000–3193 (Behemoth is 404, Tiamat 608, Cerberus 1793). An earlier revision claimed "all retail monsters use the 3500+ range", which is wrong; what's true is that **custom injected monsters** land in the open-ended 3500+ range. Tiger Familiar (modelid 308) uses the **0–1499 range** (offset +1300) for its skeleton (`tige`) file at `ROM/5/3.DAT`.

**Registration extent (byte-checked against the retail FTABLE, 2026-08):** range 3 is only
*registered* for modelids **3000–3193** (fids 99907–100100; every fid for 3194–3499 has
VTABLE=0, and no retail `mob_pools` row uses a modelid in 3194–3499 — the tail of the range
is simply unused). Range 4's first registered fid is exactly `101739 = 3500 + 98239`, and a
later dense run starts at `102239 = 4000 + 98239` — clean family alignment that also refutes
an external candidate base of `+98546` for this range (under which those runs would start at
modelids 3193/3693).

The 4 ranges exist for historical reasons — each represents a batch of monsters added to the FTABLE at different points in the game's development. The 3500+ range is open-ended and covers all content from late retail through to the final expansion.

### Verification

```
Tiger skeleton:  modelid=308,  file_id = 308 + 1300  = 1608  → ROM/5/3.DAT      ✓
Retail cap:      last non-zero FTABLE entry at file_id 109480
                 109480 - 98239 = 11241  → last retail modelid in 3500+ range
```

### Incorrect formula (previously assumed)

`file_id = 102429 + modelid` — **this constant does not exist in FFXiMain.dll**. The value `0x1901D` (102429) has zero occurrences as a uint32 in both the packed and decompressed binary. It was a coincidence that `308 + 102429 = 102737` mapped to Tiger's primary mesh (`moun`) file — that file is accessed via a different lookup mechanism, not this formula.

---

## Safe Custom Model ID Range

| Boundary | Value | Derivation |
|---|---|---|
| Last retail file_id | 109480 | Last non-zero VTABLE entry in retail FTABLE |
| Last retail modelid (3500+ range) | **11241** | `109480 - 98239` |
| First safe custom modelid | **11242** | One above retail cap |
| Recommended custom start | **15000** | 3758 slots of buffer above retail |
| FTABLE expanded to | 128240 entries | `xi ftable expand` default (`MAX_ENTITY_MODELID=30000` → gear floor 128240) |
| Max modelid at expanded size | **30000** | `(128240 - 1) - 98239` |

**Recommended custom range: 15000 – 30000** (raise via `XI_MAX_ENTITY_MODELID` + `xi ftable expand entity N`)

For modelid 15000: `file_id = 15000 + 98239 = 113239` (within expanded FTABLE, zero in retail).

---

## Notes

- The primary mesh (`moun`) file for monsters is looked up via a **different mechanism** — not directly derivable from this modelid formula. Only the skeleton/animation (`tige`) file uses the formula above.
- ROM10 loads correctly (confirmed via Process Monitor). FTABLE10/VTABLE10 are read at Ashita startup.
- The `102429` "BASE_MODEL_OFFSET" in older tooling versions was wrong and has been corrected to `MODEL_FILE_OFFSET = 98239` in `src/xi/entity/xi_core.py`.
