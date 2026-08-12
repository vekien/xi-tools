# Client DLLs — `xi dll` (POL1 pack family)

## Overview

Several Square Enix client modules ship as **POL1-packed PE DLLs**: the real
`.text` is LZSS-compressed into a `POL1` section, and the on-disk `.text` has
`SizeOfRawData = 0`. At load time an OEP stub in `POL1` decompresses into the
virtual `.text` mapping.

`xi-tools` exposes one CLI category for all of them:

```text
xi dll list
xi dll <target> unpack
xi dll <target> pack
```

| Target | Module | Role |
|--------|--------|------|
| `ffximain` | `FFXiMain.dll` | Main FFXI game client (world, inventory, net, render) |
| `polcore` | `polcore.dll` | PlayOnline COM host (`IPOLCoreCom`) |
| `app` | `app.dll` | PlayOnline Viewer UI / apps shell |

Per-module deep dives:

- [ffximain.md](ffximain.md) — packer algorithm, gear groups, model formulas
- [polcore.md](polcore.md) — COM surface, registry, relation to FFXiMain base
- [app.md](app.md) — Viewer app module, PML/media, launch adjacency

---

## Why one category

Avoid top-level sprawl (`xi ffximain`, `xi polcore`, `xi app`, …). Shared
codec + PE rewrite lives under `src/xi/dll/`; target-specific helpers (gear
patch, crashdump, …) hang only under the relevant target group.

```text
xi dll
├── list
├── ffximain  unpack | pack | text-dump | gear-groups | gear-patch | crashdump
├── polcore   unpack | pack
└── app       unpack | pack
```

---

## POL1 packing (shared facts)

Same family as documented in detail for FFXiMain:

| Fact | Value |
|------|--------|
| Packer | Custom SE LZSS in `POL1` section |
| `.text` on disk | Empty (`SizeOfRawData = 0`) until runtime unpack |
| OEP | Points **into** `POL1` (stub), not into game/COM code |
| Preferred image base | **`0x10000000`** for all three (they cannot all occupy it at once) |
| Research unpack | Restores `.text` raw bytes → valid PE for Ghidra/IDA |
| Game-loadable pack | Re-compresses `.text` into `POL1`, leaves OEP on stub |

LZSS details and the full algorithm: [ffximain.md](ffximain.md).

### Load-order / base collision

In a live Ashita/`pol.exe` session, crash dumps typically show:

1. **`polcore.dll`** mapped at preferred base **`0x10000000`**
2. **`FFXiMain.dll`** **relocated** (e.g. `0x04660000`)

So any **absolute addresses** patched into FFXiMain (external BSS tables,
etc.) **must** have corresponding `.reloc` HIGHLOW entries, or they stay at
the preferred VA and point at the wrong process memory after relocate.

`app.dll` also prefers `0x10000000`; which module wins depends on load order
for that process. Always confirm with a module list from a dump or debugger.

---

## CLI

### List targets and resolved paths

```text
xi dll list
```

Prints each target, display name, and the first existing packed path found
(or `(not found)`). Resolution order is defined in `src/xi/dll/targets.py`:

1. Paths under `FFXI_DIR` (game install)
2. Paths under `FFXI_DIR` parent / grandparent (PlayOnlineViewer layouts)
3. `$XI_TOOLS_DIR/misc/<filename>`

### Unpack (Ghidra / IDA)

```text
xi dll ffximain unpack
xi dll polcore unpack
xi dll app unpack

xi dll polcore unpack --dll PATH --output PATH
```

- Decompresses POL1 → restored `.text` on disk
- Handles packed PEs where `.text` had `PointerToRawData = 0` (inserts raw
  blob and shifts later section file offsets)
- Default output: `$XI_TOOLS_DIR/misc/<Stem>_unpacked.dll`
- **Research only** — do not drop an unpacked PE over the live game file
  (OEP stub would decompress again into already-filled `.text`)

Ghidra: import as PE, image base **`0x10000000`**, run auto-analysis.

### Pack (game-loadable)

```text
xi dll ffximain pack --unpacked misc/FFXiMain_unpacked.dll
xi dll polcore pack  --unpacked misc/polcore_unpacked.dll --template PATH
xi dll app pack      --unpacked misc/app_unpacked.dll
```

- Takes unpacked PE (or `--text-bin`) + original packed **template**
- Re-LZSS-compresses `.text` into the template’s POL1 payload region
- Sets `.text` `SizeOfRawData = 0` again
- Identity fast-path: if `.text` matches template’s decompressed blob, reuses
  the original POL1 bytes bit-for-bit
- `--verify` (default): round-trip decompress and compare

Always keep a backup before overwriting a live DLL.

### FFXiMain-only helpers

| Command | Purpose |
|---------|---------|
| `xi dll ffximain text-dump` | Flat `.bin` + full disasm `.txt` |
| `xi dll ffximain gear-groups` | Dump gear model group tables |
| `xi dll ffximain gear-patch` | Raise custom `model_id` ceilings |
| `xi dll ffximain crashdump` | Parse `%LOCALAPPDATA%\CrashDumps\pol.exe.*.dmp` |

See [ffximain.md](ffximain.md).

---

## Typical paths (CatsEye / retail-style)

| DLL | Common location |
|-----|-----------------|
| `FFXiMain.dll` | `<FFXI_DIR>\FFXiMain.dll` |
| `polcore.dll` | `...\PlayOnlineViewer\viewer\com\polcore.dll` |
| `app.dll` | `...\PlayOnlineViewer\viewer\com\app.dll` |

Ashita bootloader often uses `pol.exe` from the Ashita tree, which still loads
these from the game/PlayOnline install.

---

## Code layout

```text
src/xi/dll/
  cli.py          # click groups: dll / ffximain / polcore / app
  targets.py      # DllTarget registry + path resolution
  pol1_io.py      # unpack_dll / pack_dll / write_unpacked_pe
  xi_unpack.py    # per-target unpack commands
  xi_pack.py      # per-target pack commands

src/xi/ffximain/
  xi_core.py      # shared LZSS + detect_pol1_layout (used by dll/)
  xi_text_dump.py
  xi_geargroups.py
  xi_gear_patch.py
  xi_crashdump.py
  xi_unpack.py    # thin re-export → xi.dll
  xi_pack.py      # thin re-export → xi.dll
```

---

## Workflow cheat sheet

```text
# 1. See what the tool finds
xi dll list

# 2. Unpack for static RE
xi dll polcore unpack
# → misc/polcore_unpacked.dll  → Ghidra @ 0x10000000

# 3. Edit unpacked PE (or patch bytes offline)

# 4. Repack against original template
xi dll polcore pack --unpacked misc/polcore_unpacked.dll

# 5. Backup live DLL, deploy repacked, full client restart

# 6. On crash
xi dll ffximain crashdump
# → preferred VA for Ghidra on the faulting module
```

---

## Related

- [ffximain.md](ffximain.md) — LZSS algorithm, gear/model RE
- [polcore.md](polcore.md) — COM API, base collision
- [app.md](app.md) — Viewer shell
- [common_crashes.md](../common_crashes.md) — client crash patterns
