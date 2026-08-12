# xi dll ffximain crashdump

Parse a Windows **minidump** (`pol.exe.*.dmp`) and write a readable crash
overview next to it — exception record, register state, loaded-module list, and
(optionally) a disassembly around the faulting instruction. Turns a raw `.dmp`
into "the fault is `FFXiMain+0x1553ca`, here's the code."

This is the tool that drove the [80→120 inventory](../ffximain/inventory.md)
work: each crash pointed at the next unpatched site.

---

## Usage

```
uv run xi dll ffximain crashdump [DUMP] [OPTIONS]
```

`DUMP` is an optional path to a specific `.dmp`. With no path, it uses the
newest pol/ffxi dump in `--dir`.

| Option | Description |
|---|---|
| `--dir PATH` | CrashDumps folder. Default: `%LOCALAPPDATA%\CrashDumps`. |
| `--latest / --no-latest` | With no `DUMP`, use the newest pol/ffxi dump in `--dir`. **Default: latest.** |
| `--list` | List dumps in `--dir` and exit. |
| `--ffximain PATH` | Local FFXiMain.dll (an **unpacked** PE is best) to disassemble near the fault. |
| `--disasm-radius N` | Bytes of context each side of the fault to disassemble. Default 64. |
| `--json-only` | Write only the `.json` (skip `.txt`). |
| `--txt-only` | Write only the `.txt` (skip `.json`). |
| `--stdout` | Also print the text overview to the terminal. |

```
uv run xi dll ffximain crashdump                     # newest dump
uv run xi dll ffximain crashdump --list              # what's available
uv run xi dll ffximain crashdump path/to/pol.exe.1234.dmp
uv run xi dll ffximain crashdump --ffximain D:/xi-tools/ghidra/FFXiMain/FFXiMain_unpacked.dll --stdout
```

---

## What it extracts

From the minidump streams:

- **ExceptionStream (6)** — the exception code (`c0000005` = access violation),
  the faulting address, and the x86 `CONTEXT` (`Eip`, `Esp`, `Ebp`, GPRs).
- **ModuleList (4)** — every loaded module's base + size, used to translate the
  faulting `Eip` into a **`FFXiMain+offset`** (or whichever module faulted).
- **Memory64List (9)** — memory around the fault, so a bad read/write operand
  can be inspected.

With `--ffximain`, it disassembles `±--disasm-radius` bytes around the fault so
you see the exact instruction and its neighbours. Because FFXiMain **relocates**
at runtime, the module-relative offset is what you carry back to Ghidra (loaded
at image base `0x10000000`) — see
[../ffximain/inventory.md](../ffximain/inventory.md#3-the-root-cause).

Outputs are written next to the dump as `<dump>.overview.json` and
`<dump>.overview.txt` (subject to `--json-only` / `--txt-only`).

---

## Cross-check with the WER event log

The minidump and the Windows Error Reporting log corroborate each other. The WER
entry is faster to reach and gives the same fault via `P4` (faulting module),
`P7` (exception code), and `P8` (fault offset within the module):

```powershell
Get-WinEvent -ProviderName 'Windows Error Reporting' -MaxEvents 5 |
  Where-Object { $_.Message -match 'pol.exe' } | Select-Object -Expand Message
```

---

## See also

- [../ffximain/inventory.md](../ffximain/inventory.md) — crash-driven iteration in practice
- [../common_crashes.md](../common_crashes.md) — recurring client crash patterns
- [unpack.md](unpack.md) — produce the `--ffximain` PE for disassembly near the fault
