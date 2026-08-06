# xi object delete

Blank one or more zone placement records so the engine stops rendering them.

The mesh_id field of each matched record is zeroed — the record itself stays so the
section size and all internal offsets remain valid. Re-encrypts and writes the DAT
back in place.

---

## Usage

```
uv run xi object delete <dat> <name> [<name> ...]
uv run xi object delete <dat> <name> --dry-run
```

| Argument / Option | Description |
|---|---|
| `<dat>` | Zone DAT path or ROM-relative spec (e.g. `ROM/1/41`) |
| `<name>` | Mesh name(s) as shown in `xi object json` or the Object panel |
| `--dry-run` | Print what would be deleted without writing anything |

Multiple names can be passed in a single call.

---

## Examples

```bash
# delete a single object
uv run xi object delete ROM/1/41 hasi

# delete several at once
uv run xi object delete ROM/1/41 hasi block03 my_npc

# preview first
uv run xi object delete ROM/1/41 hasi --dry-run
```

---

## What it does

1. Loads the key tables from `FFXiMain.dll`.
2. Decrypts the `0x1C` ZoneDef section in place.
3. Scans every placement record for a name match.
4. Zeroes the 16-byte mesh_id field → the engine resolves to no mesh and renders nothing.
5. Re-encrypts and writes the DAT back in place.

The object count is **unchanged** — no offsets shift. The record is invisible to the
renderer but still present in the binary.

---

## Notes

- Names are case-sensitive and must match the exact mesh_id string in the DAT.
  Use `xi object json ROM/1/41` or the web editor's Objects panel to see the exact names.
- The web level editor's **Export JSON → `xi zone import-json`** workflow also handles
  deletions from the Changes tracker.
- To truly remove a record (splice it out), see `xi zone import-json`; simple deletion
  (zeroing) is safer and sufficient for rendering purposes.

---

## Related commands

- **`xi zone json`** — list all zone names and DAT paths
- **`xi zone import-json`** — batch apply moves + deletions from the web editor
- **`xi object export`** — export a single mesh for editing
- **`xi object replace`** — replace a mesh's geometry from an edited GLB
