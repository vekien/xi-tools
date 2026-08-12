# app.dll — PlayOnline Viewer App Module

## Overview

`app.dll` is the **PlayOnline Viewer application shell**: UI, login/network
settings presentation, in-viewer “apps” (mail, friend tools, media players,
GM tools, PML browser surfaces, etc.). It sits next to [polcore.dll](polcore.md)
under `PlayOnlineViewer\viewer\com\` and is loaded as a COM in-proc module.

It is **not** FFXiMain. FFXI gameplay still runs in [FFXiMain.dll](ffximain.md)
after the viewer / boot path hands off. app.dll is the right place to RE
viewer UX, PlayOnline content types, and launch adjacency — not bag slots or
zone meshes.

| | |
|--|--|
| Typical path | `PlayOnlineViewer\viewer\com\app.dll` |
| File description | PlayOnline Viewer App Module |
| Product | PlayOnline Viewer |
| Company | SQUARE ENIX CO., LTD. |
| Sample version | **1.18.13** (FileVersion / ProductVersion) |
| Copyright stamp | 2001–2010 (version resource) |
| Exports | Standard COM: `DllCanUnloadNow`, `DllGetClassObject`, `DllRegisterServer`, `DllUnregisterServer` |
| Packing | **POL1** LZSS (same family as FFXiMain / polcore) |
| Preferred image base | **`0x10000000`** |

---

## CLI

```text
xi dll app unpack
xi dll app unpack --dll PATH --output PATH
xi dll app pack --unpacked misc/app_unpacked.dll
xi dll list
```

Path resolution: PlayOnlineViewer layouts relative to `FFXI_DIR`, then
`$XI_TOOLS_DIR/misc/app.dll`. Shared behaviour: [dll.md](dll.md).

Unpacked PE is research-only. Ghidra image base **`0x10000000`**.

---

## PE layout (packed sample)

| Section | VA | Notes |
|---------|-----|--------|
| `.text` | `0x1000` | VSZ ≈ `0x31BF18` (~3.2 MB code); **raw 0** on disk |
| `.rdata` | `0x31D000` | Large (~1.4 MB) — strings, RTTI, tables |
| `.data` | `0x48C000` | |
| `.rsrc` | `0x91C000` | Version, other resources |
| `POL1` | `0x920000` | Compressed `.text` + OEP stub (~2 MB) |
| `.reloc` | `0xB05000` | Large reloc table |

OEP sample: RVA `0xB04B50` (inside `POL1`).  
Packed file size sample: ~4.3 MB. Unpacked `.text` ≈ 3.2 MB.

Much larger than polcore (~400 KB `.text`) — most Viewer UI/logic lives here.

---

## Role in the stack

```text
pol.exe / Ashita bootloader
        │
        ├─ polcore.dll   COM host, registry, input glue, PolViewerExec
        ├─ app.dll       Viewer UI + in-POL applications  ← this module
        │
        └─ (game path) FFXi.dll / FFXiMain.dll   actual FFXI client
```

RTTI / class names recovered from the packed binary (namespace `pol::`) include
patterns such as:

| Symbol fragment | Likely area |
|-----------------|-------------|
| `CPolApp` / `CPolWinApp` / `CApplication` | App object / WinApp shell |
| `CPolAppCom` | COM-facing app object |
| `CStartup_SignUpPlayOnline_Win` | Signup / startup UI |
| `CEmailApp` / mail viewer windows | In-viewer mail |
| `CAMess_ApprovalFriend_*` | Friend approval messaging |
| `CMssPlayMan` / `CMpsPlayMan` / play panels | Media (MSS/MPS) playback |
| `CPmlJumpApplication` / `CPmlJumpViewer` | PML content jump / browser |
| `CGmtoolApp` | GM tool surface |
| `CAMailPicViewerWindow` | Mail picture viewer |

Treat names as **hints** until confirmed in the unpacked decompilation.

---

## Network / content surface (strings)

app.dll carries PlayOnline **content-type and hello** vocabulary, for example:

```text
PlayOnline NetWork Setting File Ver 1.00
PlayOnline Login Setting File Ver 1.2
X-PlayOnline-Want-Hello:
X-PlayOnline-Hello:
Accept: text/x-playonline-pml, image/x-playonline-ang, ...
text/x-playonline-mbs
text/x-playonline-mps
text/x-playonline-mss
image/x-playonline-ang
application/x-playonline-pml
text/x-playonline-pml
```

Registry roots overlap polcore’s PlayOnline trees:

```text
SOFTWARE\PlayOnline
SOFTWARE\PlayOnlineUS
SOFTWARE\PlayOnlineEU
```

Imports include shell/OLE UI helpers (`oledlg`, `oleacc`), `msvfw32` (Video for
Windows), `sensapi`, IMM32, etc. — consistent with a rich desktop viewer, not
the D3D game client.

---

## Relation to other DLLs

| Module | Relationship |
|--------|----------------|
| **polcore** | Lower-level COM/services; app is the large UI consumer. Same folder. |
| **FFXiMain** | Separate process/module path for the game. Inventory/zone RE stays there. |
| **Base `0x10000000`** | Same preferred base as polcore/FFXiMain — confirm actual load address in dumps. |

When debugging “viewer won’t start” vs “game crashes on zone-in”, check
**which module** owns the fault VA (`xi dll ffximain crashdump` reports the
fault module and preferred VA).

---

## RE checklist

1. `xi dll app unpack` → Ghidra @ `0x10000000` (large analysis — expect longer auto-analyze)
2. Map COM entry (`DllGetClassObject`) → app class factory / `CPolAppCom`
3. Cross-ref PML / media MIME strings → protocol handlers
4. Find calls into polcore (`IPOLCoreCom` / `PolViewerExec` / common function table)
5. Trace game launch: CreateProcess / LoadLibrary paths toward FFXi / FFXiMain
6. Optional: `xi dll app pack` after experimental patches (backup first)

---

## Related

- [dll.md](dll.md) — `xi dll` category, POL1 unpack/pack
- [polcore.md](polcore.md) — COM host beside app.dll
- [ffximain.md](ffximain.md) — game client
