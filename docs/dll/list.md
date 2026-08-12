# xi dll list

List the known client DLL targets and, for each, the first **packed** DLL found
on disk. Use it to confirm `xi-tools` can locate a module before running
`unpack`/`pack` — and to see which concrete path it will act on when you omit
`--dll`.

---

## Usage

```
uv run xi dll list
```

No options. Output is one block per target: key, display name, resolved path
(or a `(not found — pass --dll)` note), and the target's one-line description.

```
ffximain     FFXiMain.dll     <FFXI_DIR>\FFXiMain.dll
             Main FFXI client (inventory, rendering, net). POL1-packed.
polcore      polcore.dll      ...\PlayOnlineViewer\viewer\com\polcore.dll
             PlayOnline COM host (IPOLCoreCom). POL1-packed; preferred base 0x10000000.
app          app.dll          (not found — pass --dll)
             PlayOnline Viewer UI module. POL1-packed (same family).
```

---

## How resolution works

For each target, `resolve_packed()` walks a candidate list and returns the first
that exists (`src/xi/dll/targets.py`):

1. **`FFXI_DIR`** — the game install root (e.g. `FFXiMain.dll` lives directly here).
2. **Parent / grandparent of `FFXI_DIR`** — the `PlayOnlineViewer\viewer\com\`
   layouts where `polcore.dll` and `app.dll` sit.
3. **`$XI_TOOLS_DIR/misc/<filename>`** — a local drop-in fallback.

`FFXI_DIR` and `XI_TOOLS_DIR` come from your `xi` config (`src/xi/xi_config.py`).
If a target shows `(not found)`, either the install path isn't configured or the
module isn't there — pass `--dll PATH` explicitly to the command you're running.

---

## See also

- [unpack.md](unpack.md) / [pack.md](pack.md) — the commands that consume the resolved path
- [README.md](README.md) — the full `xi dll` tree and default-path rules
