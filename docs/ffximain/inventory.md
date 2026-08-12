# FFXiMain.dll — 80 → 120 Inventory Expansion

How the CatsEyeXI client was patched to give every storage container **120 real
slots** instead of the retail 80, why the naive approaches failed, and how to
reproduce the whole thing from a clean unpack with one command.

- **Patch file:** [`ffximain_inventory.patch`](ffximain_inventory.patch) — 183 byte edits, replayable.
- **Apply:** `xi dll ffximain patch --unpacked <clean> --patch ffximain_inventory.patch --output <patched>`
- **Base reference:** [`ffximain.md`](ffximain.md) — POL1 packer, PE layout, unpack/pack workflow.

> Scope note: this is a **client-side** change to how many slots the client will
> track, render, and accept from the server. The server (`xi_map`) and DB were
> already set to 120 (`MAX_CONTAINER_SIZE`, `char_storage`); the client was the
> last piece that still believed in 80.

---

## 1. The goal and the shape of the problem

FFXI stores every bag (Inventory, Mog Safe, Satchel, Sack, Case, Locker,
Wardrobes…) as a flat array of fixed-size item records. Retail caps each bag at
**80 usable slots**. Internally the client over-allocates by one (a sentinel
row), so the *stride* you see everywhere in the code is **81 (0x51)**, and the
record size is **0x2C bytes**.

To get to 120 usable slots we need **121 (0x79)** everywhere 81 appears, and the
per-bag stride grows from `0x51 × 0x2C = 0xDEC` to `0x79 × 0x2C = 0x14CC`. That
sounds like a find-and-replace. It is not, for two reasons that took most of the
effort to discover:

1. The number 81 is **encoded**, not stored. `index = bag*81 + slot` shows up as
   shift/lea chains (`bag*81` via `×9 ×9` lea pairs, or `<<5 + <<8 + …`), not as
   a literal `0x51` you can grep for.
2. **FFXiMain.dll relocates at runtime**, which silently breaks the "obvious"
   fix of pointing the enlarged table at a new fixed address. This is the whole
   ballgame — see §3.

---

## 2. How we traversed to get here (crash-driven iteration)

There is no source. The method was: patch a candidate set of sites, pack, drop
into the game, boot to the crash, read the crash to find the *next* unpatched
site, repeat. The client crashes early and hard, which is actually a gift — each
crash points at exactly one more place that still assumes 80/81.

**Reading the crashes.** Two independent sources, cross-checked:

- **WER event log** (fast, always available):
  ```powershell
  Get-WinEvent -ProviderName 'Windows Error Reporting' -MaxEvents 5 |
    Where-Object { $_.Message -match 'pol.exe' } | Select-Object -Expand Message
  ```
  `P4` = faulting module, `P7` = exception code (`c0000005` = access violation),
  `P8` = fault offset **within that module**. Because FFXiMain relocates, P8 is
  the module-relative offset — add it to the runtime base to get the VA, or just
  treat it as `imagebase + offset` in Ghidra.
- **Full minidumps** at `C:\Users\Josh\AppData\Local\CrashDumps\pol.exe.*.dmp`.
  Parse: header → stream directory → `ExceptionStream (6)` for the record and
  x86 `CONTEXT` (`Eip=0xb8, Esp=0xc4, Ebp=0xb4`), `ModuleList (4)` to map the
  faulting address back to `FFXiMain+offset`, `Memory64List (9)` to inspect the
  faulting operand. `xi dll ffximain crashdump` wraps this.

**The staircase of crashes we climbed:**

| Symptom | Where it died | What it taught us |
|---|---|---|
| Instant crash on entering game after char-select | storage index math | 81-stride still live in getItem/handlers |
| AV writing to `0x10900000` at `…eb05a` | init/clear loop | the new table address was **never committed / never relocated** → led to §3 |
| `strlen` AV at `…1553ca` | deep in unrelated code | red herring caused by the table being at the wrong address at runtime → confirmed §3 |
| Loads into game, crashes "downloading inventory" | server→client item sync | client-side receive path still bounded at 80 |
| In game, stable, **but UI says 80** and can't hold >80 | UI window objects | storage was 120 but the *windows* were still 81 → §5, §6 |

Each row is one more subsystem in the patch file.

---

## 3. The root cause: FFXiMain relocates, so absolute addresses are poison

This is the single most important fact in this document.

FFXiMain.dll's preferred `ImageBase` is `0x10000000`. But **polcore.dll already
occupies `0x10000000`** at load time, so the loader relocates FFXiMain to
roughly **`0x04660000`** (varies). Normally the PE `.reloc` table fixes up every
absolute address for the new base. **It doesn't here**, because FFXiMain is
POL1-packed: `.text` is empty on disk and is decompressed at load by the
unpacker stub, and the stub applies its **own embedded relocation data** to the
freshly-decompressed code. The PE `.reloc` directory has **zero entries for
`.text`.**

Consequences:

- Any **absolute address we inject into code is never fixed up.** If we assemble
  `mov dword [0x10900000], …` and the module loads at `0x04660000`, that
  instruction still writes to `0x10900000` — which isn't ours — and we get an
  access violation. That is exactly the `eb05a` / `1553ca` crashes.
- We tried three dead ends before understanding this:
  - **External BSS table + new `.ext` PE section** — never committed at the
    right runtime address; AV.
  - **Editing `.reloc` to add fixups for our writes** — the stub ignores PE
    `.reloc` for `.text`; also we first parsed relocs at the section start
    (`0xbaf000`) instead of the real reloc directory RVA (`0xbd2268`). Dead end.
  - **Clamping `getSize` to 81** to "stabilize" — masked the real bug; removed.

### The fix: object-relative addressing (what retail already does)

Retail never uses an absolute table address. It keeps a pointer to the storage
**object** in a register (loaded via the original, already-relocated
`mov reg, [0x104ddc28]`) and addresses every slot as
`[obj_reg + index*4 + displacement]`. **Displacements are not relocation
targets** — they're constants baked into the instruction — so they survive any
load base for free.

So instead of moving the enlarged 121-slot table to a fresh address, we keep it
**object-relative** and just shift the displacement. The retail table sits at
`obj + 0x9860`. We place the enlarged table at image `0x10900000`, and because
`obj = 0x1097dde4`, the constant shift is:

```
SHIFT = 0x10900000 - (obj + 0x9860)
      = 0x10900000 - 0x10987644
      = -0x87644
```

At runtime, for **any** load base, `obj + (0x9860 + SHIFT)` resolves to
`image_base + 0x900000` — the enlarged table — with no fixups required. Every
storage edit in the patch is either "shift this displacement by −0x87644" or
"replace this 81-multiply with a 121-multiply." Relocation-safe by construction.

Key constants (from `build_objrel_120.py`):

```
OBJ      = 0x1097dde4      obj pointer origin
NEW_TBL  = 0x10900000      enlarged table (image-relative)
SHIFT    = -0x87644        added to every table displacement
STRIDE   = 0x14CC          per-bag byte stride  (0x79 * 0x2C)
STRIDE4  = 0x0533          per-bag dword stride (121 * 0x11 form used by index math)
CLEAR    = 0x5D96          init/clear dword count = 18 bags * STRIDE / 4
```

---

## 4. The three subsystems (and 183 edits)

The patch groups into three independent client subsystems. All were needed;
fixing one without the others produces the "storage is 120 but UI says 80" or
"UI says 120 but can't hold >80" half-states we saw.

| Subsystem | Edits | What it is |
|---|---:|---|
| **Storage core** (object-relative) | 28 | The 121-slot table itself: getItem, init/clear, packet handlers, iterator, free-slot search. §4.1 |
| **Storage bag-walks / getSize callers** | 13 | Loops that stride across all 18 bags; multiplies `bag*891` → `bag*STRIDE4`. §4.1 |
| **Outbound action slot-bounds** | 3 | Slot-range checks on outgoing packets, `0x50/0x51 → 0x78/0x79`. §4.1 |
| **Mog Safe window UI** | 38 + 1 alloc | The `0x101d` window class: 3 per-slot arrays widened 81→121. §5 |
| **Container window UI** | 82 + 18 alloc | The shared `0x1021a/b` class behind the main Inventory window *and every other bag window* (Satchel/Sack/Case/…): widen arrays + slot→widget map, pane-multiply 81→121. §6 |

**183 total** = 28 + 13 + 3 + (38 + 1) + (82 + 18). Verified: applying the patch
to a clean unpack reproduces the deployed build **byte-for-byte**.

### 4.1 Storage (relocation-safe core)

Built by `build_objrel_120.py`. Highlights, by function:

- **getItem `0x100EB080`** — `bag*81 + slot` index. The `×9 ×9` lea pair
  (`8d04c0 8d04c0`) becomes `imul eax,eax,0x79` (`6bc079`) + nop; the load
  displacement `0x9860` is shifted to `0x9860 + SHIFT`.
- **init/clear `0x100EB042`** — zeroes and tags the whole table. Clear count
  `0x407b → 0x5D96`, base lea displacement shifted, slot sentinel bound
  `0x51 → 0x79`.
- **iterator `0x100EB138`** — `bag*891` computed as `<<5 + …`; replaced with
  `imul ecx,ecx,0x533`. Bag-pointer step `add ecx,0xDEC → 0x14CC`.
- **free-slot search `0x100EB4E2`** — `imul eax,esi,0x533 ; push esi`.
- **secondary clear `0x100EBACF`**, **split-brain reader `0x100E935D`**
  (`cmp word [edx+eax*4+0x9860]` → shifted), and the bag-0 no-SIB walks
  `0x100EB3FF / 0x100EB454` (`lea esi,[eax+0x988C]` → shifted).
- **bag-walk multiplies** outside the core, in the packet/UI-feeder functions:
  `0x101A5E7A`, `0x101A5F84`, `0x101EE4AB`, `0x1021B330` — each an
  `imul r,r,0x533`.
- **outbound slot-bounds:** `0x100F783E`, `0x100F7A7C` (`cmp …,0x50→0x78`),
  `0x100DF83D` (`cmp …,0x51→0x79`).
- **getSize is left RAW** — deliberately *not* clamped, so the client reports
  the true container size (up to 120) to the rest of the code and to the UI.

A generic displacement scanner handles the remaining single-slot
`[base+idx*4+{0x9860,0x988c}]` reads across a fixed list of address ranges,
shifting each by `SHIFT` — this catches the long tail without hand-listing every
site.

### 5. Mog Safe window UI (`0x101d` class)

Built by `build_ui120.py`. The Safe window is a singleton
(`operator_new(0x85c)` at `0x10094100`) with **three parallel per-slot arrays**,
each `2 panes × 0x51 × 4 = 0x288` bytes:

```
A @ +0x4c   widget pointers   (disp8  — kept in place, widened into vacated space)
B @ +0x2d4  floats (-1.0f)     (disp32 — moved to +0x818)
C @ +0x55c  item id/type       (disp32 — moved to +0xBE0)
```

Slot index within the window is `slot + (bag==9)*0x51`, emitted as
`sete al ; lea eax,[eax+eax*8] ; lea eax,[eax+eax*8]` (= `×81`). Changes:

- **alloc** `0x85c → 0x1000` at `0x10094100`.
- Widen to `2 × 0x79`: A stays at `+0x4c`, B → `+0x818`, C → `+0xBE0`; all edits
  same-size, no encoding growth.
- **pane-multiply** `sete al; lea×9; lea×9` → `imul eax,eax,0x79` + nop.
- immediates `0x51 → 0x79`, `0x144 → 0x1E4`, ctor count `0xa2 → 0xf2`, and the
  combined-loop A-delta `-0x288 → -0x7CC`.

### 6. Container window UI (shared `0x1021a/b` class)

Built by `build_ui120_inv.py`. This is the big one (100 edits) because a single
window class backs the **main Inventory window and every other bag window**
(Satchel, Sack, Case, Locker, Wardrobes). Object is `operator_new(0x13bc)`,
singletons in `0x1063218c…`. Layout:

```
source  @ +0x6c    (0x51 × 0x18)     item source records
map     @ +0x804   (8-byte entries, pane-stride 0x288)   slot → widget map
widgets @ +0x1224  (0x51 × 4)        widget pointers
tail    @ +0x1368
```

New layout (widen to `0x79`): keep `source` at `+0x6c` (grows into vacated map
space), **move** `map → +0xC00` (pane-stride `0x288 → 0x3C8`),
`widgets → +0x1B20`, `tail → +0x1D04`; **alloc `0x13bc → 0x2000`** at **18 call
sites** (each container instantiates the class separately — that's the 18
alloc edits).

The hard part was the **pane-multiply**, `pane*0x51`, which the compiler emitted
in *four* different encodings across this class:

- chained `×9 ×9` leas (adjacent) → `imul r,r,0x79`;
- **disp8-encoded** `×9` lea (`8d6ced00`, mod=01 disp8=0) — missed by a naive
  byte-match, caught by a **Capstone-based detector**;
- non-adjacent `×9` chains (gap up to 4 instructions);
- **split-form** `lea eax,[eax+eax*8]; lea rB,[rC+eax*8]; add eax,rB` →
  `imul eax,eax,0x79; add eax,rC`.

The Capstone pass found 16 and the split-form pass found 2, for 18 conversions —
matching the number of panes/sites. (An earlier byte-only detector missed the
disp8 and split forms, which is what broke bag-0's map and produced an empty
inventory on one iteration.) Plus immediates `0x51→0x79`, `0x288→0x3c8`,
`0xa2→0xf2`, `0x1e6→0x2d6`, and the map disp moves `0x804/808/80a/80c/810 →
0xC00/C04/C06/C08/C0C`.

---

## 7. Server + DB (already at 120, for completeness)

The client work above assumes the server actually sends 120-slot containers.
That side was done first:

- **`item_container.h`** — `MAX_CONTAINER_SIZE = 120`.
- **DB** `tpzdb` @ `127.0.0.1:3306` — `char_storage` sizes all set to 120;
  `char_inventory.location`: `0=inv, 5=satchel, 6=sack, 7=case`, etc.
- **`lua_baseentity.cpp` `CLuaBaseEntity::addItem`** — table form now accepts a
  `location` field, so items can be added to any container, not just inventory.
- **`scripts/commands/fillinv.lua`** — `!fillinv [itemId] [location]` fills any
  container's free slots (used to test each bag holds 120).

Rebuild `xi_map` with the VS 18 `vcvars64` + CMake/Ninja flow after touching the
C++.

---

## 8. Reproduce from scratch

```bash
# 1. unpack the retail client DLL
xi dll ffximain unpack --output FFXiMain_unpacked.dll

# 2. apply all 183 edits (aborts if any 'expect' byte doesn't match)
xi dll ffximain patch \
  --unpacked FFXiMain_unpacked.dll \
  --patch docs/ffximain/ffximain_inventory.patch \
  --output FFXiMain_patched.dll

# 3. re-pack against the original packed DLL as template
xi dll ffximain pack \
  --template "FFXiMain.dll" \
  --unpacked FFXiMain_patched.dll \
  --output "FFXiMain.dll"
```

The `patch` command is **safe to re-run** (already-patched edits are skipped)
and **fails loudly** on any mismatch without writing a partial file. `--dry-run`
reports what would change and writes nothing. Every edit is same-length, so
offsets never move.

**Patch file format** (`<va> <expect_hex> <replace_hex>  ; <note>`, `#`
comments):

```
0x10094101 5c08 0010  ; Mog Safe window: alloc size 0x85c->0x1000
0x100e934f 8d04c08d04c0 6bc079909090  ; storage: 121-slot table core, object-relative
0x101646b6 bc13 0020  ; container windows: alloc size 0x13bc->0x2000 (per container)
```

`va` is relative to image base `0x10000000`; the CLI maps it to a file offset
via the PE section table of whatever DLL you point `--unpacked` at.

---

## 9. What is left to check

Everything below either isn't verified to 120 yet, or is a known loose end. This
is the to-do list, not a claim that any of it is broken.

- **HD-edition duplicate code.** There appears to be a second copy of some of
  these routines up around `0x10ae…` (HD client path). The patch does **not**
  touch it. If the HD renderer path is ever exercised, those sites may still
  assume 81. Confirm whether this build reaches that code.
- **Mog Safe 2 (`location 9`) / Storage (`location 2`) container sizes.** The
  storage core is size-driven (getSize is raw), so these *should* follow, but
  they haven't each been individually filled to 120 and eyeballed. Run
  `!fillinv 4096 9`, `!fillinv 4096 2`, etc., per bag.
- **Pane compaction paths (containers with >1 pane).** The container class has
  compaction logic for panes 1–2 when items are removed/sorted. Inventory is
  pane 0 (no compaction) and is verified; the multi-pane sort/compact path is
  the least-tested code and the most likely place a stray 81 hides. Sort a full
  120-slot Satchel/Case and watch for misplaced or vanishing icons.
- **Other windows that read storage.** Auction house, bazaar, trade, delivery
  box, equipment/inventory-in-menus — anywhere a *different* window enumerates a
  container. These reuse the storage core (good) but may have their own
  per-window slot arrays like §5/§6 that weren't audited.
- **Split-form pane-mult correctness across all panes.** The 2 split-form
  conversions were verified by disassembly, but only pane 0 was exercised
  in-game. Confirm panes 1–2 render correctly in a container that uses the
  split form.
- **Overflow headroom.** Allocs were bumped to round numbers (`0x1000`,
  `0x2000`) with slack, not sized exactly. Fine for 120; if the target ever went
  higher, re-check each alloc against `arrays_end`.

---

*Generated as part of the 80→120 inventory project. Build scripts:
`xi-tools/ghidra/FFXiMain/patch120/{build_objrel_120,build_ui120,build_ui120_inv}.py`.
The `.patch` file is the canonical, tool-replayable form of all three.*
