# xi dll ffximain text-dump

Decompress FFXiMain's POL1 `.text` and write it as **flat bytes plus a full
disassembly** — for grepping opcodes, byte-pattern hunting, or diffing across
client builds without loading a PE into Ghidra.

Where [`unpack`](unpack.md) produces a loadable PE, `text-dump` produces raw
research artifacts.

---

## Usage

```
uv run xi dll ffximain text-dump [--dll PATH] [--output-dir PATH]
```

| Option | Description |
|---|---|
| `--dll PATH` | FFXiMain.dll to read. Default: `$XI_TOOLS_DIR/misc/FFXiMain.dll` (or the resolved packed path). |
| `--output-dir PATH` | Directory for the outputs. Default: `$XI_TOOLS_DIR/research`. |

---

## Outputs

| File | Contents |
|---|---|
| `pol_decompressed.bin` | The raw decompressed `.text` bytes (no PE wrapper). |
| `pol_decompressed.txt` | Full linear disassembly of `.text`, one instruction per line. |

The `.txt` is large (**~45 MB**) and the disassembly takes **~2–3 minutes** —
this is a full linear sweep of the client's code section, not a targeted dump.

The `.bin` doubles as a `--text-bin` source for [`pack`](pack.md) if you edit
`.text` bytes offline instead of through a PE.

---

## When to use it

- **Byte-pattern search** across the whole code section (`grep`, `rg`, scripts).
- **Cross-build diffing**: dump two client versions, diff the `.txt` to spot
  moved/changed routines.
- **Quick opcode lookups** without a Ghidra project open.

For interactive analysis, symbol recovery, and xrefs, load the
[`unpack`](unpack.md) output in Ghidra instead.

---

## See also

- [unpack.md](unpack.md) — loadable PE for Ghidra/IDA
- [../ffximain/ffximain.md](../ffximain/ffximain.md) — POL1 / LZSS internals, what lives in `.text`
