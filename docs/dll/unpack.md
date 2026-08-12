# xi dll &lt;target&gt; unpack

Decompress a POL1-packed client DLL's `.text` back onto disk, producing a
**valid PE that Ghidra/IDA can load**. This is the entry point for all static
reverse-engineering of `FFXiMain.dll`, `polcore.dll`, and `app.dll`.

Available for every target — the command is generated per target by
`make_unpack_cmd()`, so behavior is identical; only the default paths differ:

```
uv run xi dll ffximain unpack
uv run xi dll polcore  unpack
uv run xi dll app      unpack
```

> **Research only.** The output is *not* game-loadable. In a packed DLL the
> on-disk `.text` has `SizeOfRawData = 0` and an OEP stub decompresses POL1 into
> it at load; an unpacked PE already has `.text` filled, so the stub would
> double-unpack. To ship a change, edit the unpacked PE and re-[`pack`](pack.md) it.

---

## Usage

```
uv run xi dll <target> unpack [--dll PATH] [--output PATH]
```

| Option | Description |
|---|---|
| `--dll PATH` | Packed DLL to read. Default: the first existing candidate (see [list](list.md)), else `misc/<filename>`. |
| `--output PATH` | Where to write the unpacked PE. Default: `misc/<stem>_unpacked.dll`. |

---

## What it does

`unpack_dll()` (`src/xi/dll/pol1_io.py`) runs the shared codec + PE rewrite:

1. **Locate the POL1 payload** and the packed `.text` section header
   (`detect_pol1_layout` in `xi_core.py`).
2. **LZSS-decompress** the payload to the original `.text` bytes.
3. **Rewrite the PE**: give `.text` a real `PointerToRawData`, insert the raw
   blob, and shift every later section's file offset to keep the PE consistent.
4. Leave the image base at the preferred **`0x10000000`**.

The full LZSS algorithm and PE details are in [../ffximain/ffximain.md](../ffximain/ffximain.md).

---

## Example

```
uv run xi dll ffximain unpack --output FFXiMain_unpacked.dll
```

```
Packed          : <FFXI_DIR>\FFXiMain.dll
POL1 raw offset : 0x000B2C00
Decompressed    : 3,283,456 bytes
First 16 bytes  : 558bec6aff68...
Image base      : 0x10000000
.text VA        : 0x10001000

First 10 instructions (VA 0x10001000):
  10001000: 558bec...            push ebp
  ...

Wrote unpacked  : FFXiMain_unpacked.dll
Load in Ghidra with image base 0x10000000 (research only — not game-loadable).
```

The disassembly preview needs `capstone` + `pefile`; without them the command
still writes the DLL and just skips the instruction dump.

---

## Loading in Ghidra / IDA

Import the output as a PE, set the image base to **`0x10000000`**, and run
auto-analysis. Note that at runtime FFXiMain often **relocates** (polcore wins
the preferred base) — see [../ffximain/dll.md](../ffximain/dll.md#load-order--base-collision)
and [../ffximain/inventory.md](../ffximain/inventory.md) for why that matters
when injecting absolute addresses.

---

## See also

- [pack.md](pack.md) — the inverse: re-compress an unpacked PE into a game-loadable DLL
- [patch.md](patch.md) — apply a `.patch` file to the unpacked PE
- [text-dump.md](text-dump.md) — flat `.text` bytes + full disassembly instead of a PE
- [../ffximain/ffximain.md](../ffximain/ffximain.md) — POL1 / LZSS internals
