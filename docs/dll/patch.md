# xi dll ffximain patch

Apply a **`.patch` file** — a list of `virtual-address / expect / replace` byte
edits — to an **unpacked** client DLL. This is the replayable form of a
reverse-engineering change: instead of re-running a bespoke Python build script,
you check the `.patch` into the repo and anyone can reproduce the exact same
binary from a clean unpack.

The canonical patch shipped today is the 80→120 inventory expansion,
[`ffximain_inventory.patch`](../ffximain/ffximain_inventory.patch) (183 edits) —
see [ffximain/inventory.md](../ffximain/inventory.md) for the full write-up of
what it changes and why.

> Works on the **unpacked** PE (the output of [`xi dll ffximain unpack`](../ffximain/dll.md)),
> not the packed game DLL. Unpack → patch → pack. Addresses in the file are
> relative to the DLL's PE image base (`0x10000000` for FFXiMain), and the CLI
> maps each one to a file offset through the section table of whatever DLL you
> pass — so the same command works for any POL1 target (`ffximain`, `polcore`,
> `app`).

---

## Usage

```
uv run xi dll ffximain patch --unpacked DLL --patch FILE [--output DLL] [--dry-run]
```

| Option | Description |
|---|---|
| `--unpacked PATH` | Unpacked DLL to patch (output of `unpack`). **Required.** |
| `--patch PATH` | Patch file to apply. **Required.** |
| `--output PATH` | Where to write the result. Default: **overwrite `--unpacked` in place**. |
| `--dry-run` | Verify every edit and print the report, but write **nothing**. |

The command is **all-or-nothing**: if any edit's `expect` bytes don't match, it
reports the mismatches and writes **no file**. It is also **idempotent** —
re-running against an already-patched DLL skips every edit whose bytes already
equal `replace` (reported as `already-patched`), so applying twice is safe.

---

## Patch file format

One edit per line. `#` starts a comment line; text after `;` on an edit line is
an inline note.

```
<va> <expect_hex> <replace_hex>  ; <note>
```

| Field | Meaning |
|---|---|
| `va` | Virtual address (image base `0x10000000`). Hex, `0x`-prefixed. |
| `expect_hex` | Bytes that **must** be present before patching. A mismatch aborts the whole run. |
| `replace_hex` | Bytes written in their place. **Must be the same length** as `expect_hex`. |

Because every edit is the same length as what it replaces, offsets never move —
the file can be applied in any order and re-applied freely.

```
# excerpt from ffximain_inventory.patch
0x10094101 5c08 0010            ; Mog Safe window: alloc size 0x85c->0x1000
0x100e934f 8d04c08d04c0 6bc079909090  ; storage: 121-slot table core (object-relative)
0x101646b6 bc13 0020            ; container windows: alloc size 0x13bc->0x2000 (per container)
```

---

## Full workflow

```bash
# 1. unpack the retail client DLL to a valid PE
uv run xi dll ffximain unpack --output FFXiMain_unpacked.dll

# 2. dry-run first — confirm all edits match a clean unpack
uv run xi dll ffximain patch \
  --unpacked FFXiMain_unpacked.dll \
  --patch docs/ffximain/ffximain_inventory.patch \
  --dry-run

# 3. apply for real, writing a separate output
uv run xi dll ffximain patch \
  --unpacked FFXiMain_unpacked.dll \
  --patch docs/ffximain/ffximain_inventory.patch \
  --output FFXiMain_patched.dll

# 4. re-pack against the original packed DLL as the template
uv run xi dll ffximain pack \
  --template "FFXiMain.dll" \
  --unpacked FFXiMain_patched.dll \
  --output "FFXiMain.dll"
```

Example output of step 3:

```
Patch file      : docs/ffximain/ffximain_inventory.patch
Target          : FFXiMain_unpacked.dll  (ImageBase 0x10000000)
Edits           : 183
  applied       : 183
  already-patched: 0
  failed        : 0
Wrote           : FFXiMain_patched.dll
Next: `xi dll ffximain pack --template <packed> --unpacked <this> --output <packed>`
```

---

## When an edit doesn't match

If the `--unpacked` DLL isn't a clean unpack of the DLL the patch was authored
against (wrong client version, already partly edited, or the wrong module), the
`expect` bytes won't be found. The command lists up to 25 mismatches and writes
nothing:

```
  applied       : 170
  already-patched: 0
  failed        : 13
    line 42  0x100EB098: expect 8d04c08d04c0 but found 6bc07981e6ff
  ...
Error: 13 edit(s) did not match — nothing written. Is --unpacked a clean unpack
of the matching FFXiMain.dll?
```

`applied` in that report counts edits that *would* apply — nothing is written
while `failed > 0`. Fix the input (re-unpack, or use the matching client build)
and re-run.

---

## Authoring a new patch file

A patch file is produced by diffing a clean unpack against a fully-built one
(byte-for-byte contiguous diffs grouped into edits). The 80→120 patch was
generated this way from the three build scripts in
`ghidra/FFXiMain/patch120/`, and verified by round-tripping: applying the
generated `.patch` to a fresh clean unpack reproduces the deployed DLL exactly.
Keep edits **same-length** (pad with `90` NOPs rather than shrinking) so offsets
stay stable and the file remains order-independent.

---

## See also

- [ffximain/inventory.md](../ffximain/inventory.md) — the 80→120 change this patch encodes, with root-cause analysis
- [ffximain/ffximain_inventory.patch](../ffximain/ffximain_inventory.patch) — the patch file itself (183 edits)
- [ffximain/dll.md](../ffximain/dll.md) — the shared `xi dll` unpack/pack workflow (POL1)
- [ffximain/ffximain.md](../ffximain/ffximain.md) — FFXiMain.dll reference (POL1 algorithm, PE layout)
