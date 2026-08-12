# polcore.dll — PlayOnline COM host

## Overview

`polcore.dll` is the **in-process COM server** for PlayOnline Viewer. It is
**not** the FFXI game client. Inventory, zone logic, and rendering live in
[FFXiMain.dll](ffximain.md). polcore provides:

- COM class `CPOLCoreCom` / ProgID `POLCore.POLCoreCom`
- Dual interface **`IPOLCoreCom`** (+ connection-point events)
- Registry path helpers (PlayOnline / Square Enix keys)
- Input/pad hooks, mask windows, friend-list paint helpers
- **`PolViewerExec`** — create/init the viewer core object
- **`GetCommonFunctionTable`** — hand a function-pointer table to callers

Packed with the same **POL1** LZSS scheme as FFXiMain. Preferred image base
**`0x10000000`**.

| | |
|--|--|
| Typical path | `PlayOnlineViewer\viewer\com\polcore.dll` |
| Sibling | `app.dll` (Viewer UI) in the same folder |
| Exports | `DllCanUnloadNow`, `DllGetClassObject`, `DllRegisterServer`, `DllUnregisterServer` |
| Product | POLCore / PlayOnline Viewer POLCore Module |
| File version (sample) | 1.18.12.0 (from live process module list) |
| Typelib | Embedded `TYPELIB` resource; MIDL 6.00.0366 (2011-08-19 stamp on one build) |

---

## CLI

```text
xi dll polcore unpack
xi dll polcore unpack --dll PATH --output PATH
xi dll polcore pack --unpacked misc/polcore_unpacked.dll
xi dll list
```

Defaults resolve via `FFXI_DIR` parent/grandparent PlayOnlineViewer layouts,
then `$XI_TOOLS_DIR/misc/polcore.dll`.

See [dll.md](dll.md) for shared unpack/pack behaviour.

Ghidra: load `polcore_unpacked.dll`, image base **`0x10000000`**.

---

## PE layout (packed sample)

| Section | VA | Notes |
|---------|-----|--------|
| `.text` | `0x1000` | VSZ ≈ `0x63BC1` (~400 KB); **raw size 0** on disk |
| `.rdata` | `0x65000` | Imports, vtables, strings |
| `.data` | `0x6F000` | Large virtual BSS (~3.7 MB) |
| `.rsrc` | `0x408000` | REGISTRY scripts, TYPELIB, version |
| `POL1` | `0x40C000` | Compressed `.text` + OEP stub |
| `.reloc` | `0x44B000` | |

OEP points into `POL1` (e.g. RVA `0x44A360` on the sampled build).

Unpacked `.text` size matches virtual size (~408,513 bytes).

---

## Why it matters for FFXI modding

### Image base collision

In a normal boot with Ashita/`pol.exe`:

- **polcore** loads first at **`0x10000000`**
- **FFXiMain** is forced to **relocate**

Absolute constants patched into FFXiMain (e.g. external inventory BSS at a
fixed preferred VA) are wrong at runtime **unless** the PE `.reloc` table
contains HIGHLOW entries for those dwords. Missing relocs were the root cause
of null item-name `strlen` crashes during 120-slot client work.

### Not the inventory DLL

Do not look here for bag stride, slot records (`0x2C`), or `0x1F`/`0x20`
packet handlers — those are FFXiMain.

---

## COM surface (`IPOLCoreCom`)

Embedded type library method names (order is approximate; confirm against the
vtable in Ghidra before patching):

| Method | Role |
|--------|------|
| `GethInstance` | Module `HINSTANCE` |
| `GetlpCmdLine` | Command line |
| `SetParamInit` | Init parameters |
| `GetWindowsType` | OS class |
| **`GetCommonFunctionTable`** | Export FP table to caller |
| **`PolViewerExec`** | Construct/init viewer core (`DAT_*` singleton) |
| `GetWindowsVersion` | Version string fill |
| `PressAnyKey` | Input wait helper |
| `PolconSetEnableWakeupFuncFlag` | Wakeup control |
| `UpdateInputState` / `GetPadRepeat` / `GetPadOn` | Pad/input |
| `FinalCleanup` | Teardown |
| `PaintFriendList` / `CreateFriendList` / `DestroyFriendList` | Friend list UI |
| `SetMaskWindowHandle` / `HideMaskWindow` / `ShowMaskWindow` / `IsVisibleMaskWindow` / `MaskWindow` | Overlay mask HWND |
| `GetPlayOnlineRegKeyName*` | `SOFTWARE\PlayOnline…` path strings |
| `GetSquareEnixRegKeyName*` | SE registry roots |
| `SetAreaCode` / `GetAreaCode` | Region code |

Vtable (sample unpacked): **`CPOLCoreCom_vtbl` @ `0x10065874`** (duplicate /
aggregate object vtable nearby). After `IUnknown` + `IDispatch` slots:

- `PolViewerExec` ≈ `0x100069B6` — alloc + init if singleton unset
- `GetCommonFunctionTable` ≈ `0x1000698D` — dispatches into internal table builder

CLSID variants appear in embedded REGISTRY resources (region builds), e.g.:

- `{07974581-0DF6-4EF0-BD05-604B3ADA9BE9}`
- `{E5966FB3-C97B-42EB-84BF-37F95EE54A9F}`
- `{3501F5DD-7894-42DF-866A-A2B6527D8049}`

---

## Data / network surface

### Registry roots

```text
SOFTWARE\PlayOnline
SOFTWARE\PlayOnlineUS
SOFTWARE\PlayOnlineEU
SOFTWARE\PlayOnline\SQUARE\...
SOFTWARE\PlayOnlineUS\SquareEnix\...
SOFTWARE\PlayOnlineEU\SquareEnix\...
```

(and `...\PlayOnlineViewer` suffixes)

### Hosts / IDs

- `gm000.pol.com`, `gd000.pol.com`, `pp%03d.pol.com`, `*.pol.com`
- POLID debug format strings / SQL-ish fragments for id compare

### Local data files (path builder)

Resolved relative to install layout, including:

| File | Notes |
|------|--------|
| `sqpoliop.bin` | |
| `sqpolkey.bin` | |
| `sqpolexe.bin` | |
| `sqpolcts.bin` | |
| `polerr.bin` | |
| `sqsound.irx` | |
| `entry*.dic`, `vulgar*.dic` | Dictionary / filter data |
| `fnt_%03d.bin` | Fonts |

Path assembly helper (named in Ghidra notes): `POL_BuildDataFilePath`.

Network settings marker string:

```text
PlayOnline NetWork Setting File Ver 1.00
```

---

## Imports (high level)

Kernel/User/GDI, ADVAPI32 (registry), OLE/OLEAUT (COM), DINPUT8, IMM32,
WS2_32, WINMM (`timeGetTime`). No Direct3D — rendering is not polcore’s job.

---

## RE checklist

1. `xi dll polcore unpack` → Ghidra @ `0x10000000`
2. Label `CPOLCoreCom` vtable; rename `IPOLCoreCom_*` methods from typelib order
3. Trace `PolViewerExec` → who calls it from `app.dll` / viewer
4. Dump `GetCommonFunctionTable` payload (what FPs the viewer receives)
5. Cross-check live base with `xi dll ffximain crashdump` module list

---

## Related

- [dll.md](dll.md) — shared CLI / POL1
- [app.md](app.md) — Viewer UI module that coexists with polcore
- [ffximain.md](ffximain.md) — game client (relocates when polcore owns `0x10000000`)
