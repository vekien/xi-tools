# `xi dll` — client DLL commands

Unpack, patch, re-pack, and reverse-engineer the **POL1-packed** Square Enix
client DLLs (`FFXiMain.dll`, `polcore.dll`, `app.dll`). This folder documents
every command in the `xi dll` tree; for the *packer internals* (LZSS algorithm,
PE layout, base-collision/relocation) see the reference
[ffximain/dll.md](../ffximain/dll.md) and [ffximain/ffximain.md](../ffximain/ffximain.md).

```text
xi dll
├── list                          # resolve packed DLL paths
├── ffximain   unpack | pack | patch | text-dump | gear-groups | gear-patch | crashdump
├── polcore    unpack | pack
└── app        unpack | pack
```

## Targets

| Target | Module | Role |
|--------|--------|------|
| `ffximain` | `FFXiMain.dll` | Main game client — world, inventory, net, render. Also carries the RE helper commands. |
| `polcore` | `polcore.dll` | PlayOnline COM host (`IPOLCoreCom`). |
| `app` | `app.dll` | PlayOnline Viewer UI / apps shell. |

All three are the same POL1 family, so `unpack`/`pack` are **shared** commands
generated per target — they behave identically; only the default paths differ.
The extra RE helpers (`text-dump`, `gear-*`, `crashdump`, `patch`) live only
under `ffximain`.

## Commands

| Command | Doc | What it does |
|---------|-----|--------------|
| `xi dll list` | [list.md](list.md) | List targets and the first packed DLL found on disk |
| `xi dll <t> unpack` | [unpack.md](unpack.md) | Decompress POL1 → a Ghidra/IDA-loadable PE (research) |
| `xi dll <t> pack` | [pack.md](pack.md) | Re-compress an unpacked PE → a game-loadable packed DLL |
| `xi dll ffximain patch` | [patch.md](patch.md) | Apply a replayable `va/expect/replace` `.patch` to an unpacked DLL |
| `xi dll ffximain text-dump` | [text-dump.md](text-dump.md) | Write flat `.text` bytes + a full disassembly `.txt` |
| `xi dll ffximain gear-groups` | [gear-groups.md](gear-groups.md) | Dump per-race/per-slot gear model-group tables |
| `xi dll ffximain gear-patch` | [gear-patch.md](gear-patch.md) | Raise the custom `model_id` ceiling per (race, slot) |
| `xi dll ffximain crashdump` | [crashdump.md](crashdump.md) | Parse a Windows minidump → fault overview + disasm |

## The core workflow

`unpack` → edit → `pack` is the loop for any binary change. `patch` replaces
the manual "edit" step with a replayable file; the 80→120 inventory expansion
([ffximain/inventory.md](../ffximain/inventory.md)) is the worked example.

```bash
# 1. see what the tool finds on disk
uv run xi dll list

# 2. decompress for static RE (Ghidra @ image base 0x10000000)
uv run xi dll ffximain unpack --output FFXiMain_unpacked.dll

# 3a. edit the unpacked PE in Ghidra/IDA, OR
# 3b. apply a checked-in patch file
uv run xi dll ffximain patch --unpacked FFXiMain_unpacked.dll \
     --patch ../ffximain/ffximain_inventory.patch --output FFXiMain_patched.dll

# 4. re-compress against the original packed DLL as template
uv run xi dll ffximain pack --template "FFXiMain.dll" \
     --unpacked FFXiMain_patched.dll --output "FFXiMain.dll"

# 5. back up the live DLL, deploy, full client restart. On crash:
uv run xi dll ffximain crashdump
```

> ⚠️ An **unpacked** PE is research-only — never drop it over the live game
> file. Its OEP stub would try to decompress POL1 into an already-filled
> `.text`. The game only loads the **packed** output of `pack`.

## Default path resolution

`unpack`/`text-dump`/`gear-*` locate the packed DLL automatically when `--dll`
is omitted (`src/xi/dll/targets.py`):

1. Under `FFXI_DIR` (the game install root)
2. Under `FFXI_DIR`'s parent / grandparent — the `PlayOnlineViewer\viewer\com\`
   layouts for `polcore`/`app`
3. `$XI_TOOLS_DIR/misc/<filename>`

`xi dll list` shows exactly which one resolves.

## Code layout

```text
src/xi/dll/
  cli.py        # click groups: dll / ffximain / polcore / app
  targets.py    # DllTarget registry + path resolution
  pol1_io.py    # unpack_dll / pack_dll / write_unpacked_pe (shared codec+PE rewrite)
  xi_unpack.py  # make_unpack_cmd(target) → per-target unpack
  xi_pack.py    # make_pack_cmd(target)   → per-target pack
  xi_patch.py   # make_patch_cmd()        → patch (image base from the DLL)
src/xi/ffximain/
  xi_core.py    # shared LZSS + detect_pol1_layout
  xi_text_dump.py  xi_geargroups.py  xi_gear_patch.py  xi_crashdump.py
```

## See also

- [../ffximain/dll.md](../ffximain/dll.md) — POL1 family reference (why one category, base collision)
- [../ffximain/ffximain.md](../ffximain/ffximain.md) — FFXiMain deep dive (LZSS algorithm, gear/model formulas)
- [../ffximain/inventory.md](../ffximain/inventory.md) — the 80→120 patch, the flagship `patch` use
- [../common_crashes.md](../common_crashes.md) — client crash patterns
