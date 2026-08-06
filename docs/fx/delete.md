# xi fx delete

Remove one or more `0x05` effects from a DAT — by **exact FourCC or name-prefix** —
and rebuild the file. Splices the sections out from the end so remaining offsets stay
valid; the engine tolerates the resize (same as mesh-merge growth).

---

## Usage

```
uv run xi fx delete <dat> <name>... [--dry-run]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | DAT path or ROM spec (e.g. `ROM/1/41`) |
| `<name>...` | One or more effect names; each matches by exact FourCC **or prefix** |
| `--dry-run` | Print what *would* be removed without writing |

---

## Examples

```bash
# one effect (the fountain puddle)
uv run xi fx delete ROM/1/41 grid

# prefixes: removes tki1-5 + awa1-6 + grid (the whole fountain effect set)
uv run xi fx delete ROM/1/41 tki awa grid

# preview only, write nothing
uv run xi fx delete ROM/1/41 tki --dry-run
```

---

## Example output

```
Removed 12 effect(s): tki1, tki2, tki3, tki4, tki5, awa1, awa2, awa3, awa4, awa5, awa6, grid
Wrote: <output DAT path>
```

With `--dry-run`:

```
Would remove 12: tki1, tki2, tki3, tki4, tki5, awa1, awa2, awa3, awa4, awa5, awa6, grid
```

---

## Notes

- Each `<name>` matches by **exact FourCC or prefix** — so `tki` removes `tki1`–`tki5`.
  Multiple names are allowed in one call.
- A pristine **`<dat>.base`** backup is created (shared with `xi zone import`).
  Restore the whole DAT from it — or run `xi zone reset` — to undo.
- **Verified**: removing the fountain set (`tki`/`awa`/`grid`) loads clean in-game —
  fountains present, no water, no crash (1934 → 1922 sections).

### Caveat — ordering with `zone import`

`fx delete` edits the DAT **in place** (it does not reset to `.base`), so it stacks on
top of mesh-merge/placement edits. But `xi zone import` **rebuilds from `.base`**,
which still contains the effects — so re-running `zone import` **re-adds** deleted
effects. Run `fx delete` *after* import. (Effect editing isn't wired into the import
pipeline yet.) See the worked recipe in
[../pipelines/rom_1_41_fountain_removal.md](../pipelines/rom_1_41_fountain_removal.md).

---

## Related commands

- **[`xi fx json`](json.md)** — find the effect names to delete
- **[`xi fx set`](set.md)** — edit effects instead of removing them
- **[`xi fx copy`](copy.md)** — duplicate / transplant effects

---

# xi fx delete-group

Delete an entire fixture group by **any member name** — the inverse of
[`xi fx copy-group`](copy.md#xi-fx-copy-group).

Matches all effects with the same **2-char stem and index** as the seed.
e.g. `xfd0` → stem `xf`, index `0` → removes `xfm0 xfi0 xfd0 xfl0 xfr0 xfs0`
while leaving `xfd1`, `xfd2`, … untouched.

---

## Usage

```
uv run xi fx delete-group <dat> <seed> [--dry-run]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | DAT path or ROM spec |
| `<seed>` | Any member of the group (e.g. `xfd0`) |
| `--dry-run` | Print what *would* be removed without writing |

---

## Examples

```bash
# preview — Norg torch group copied into Lower Jeuno
uv run xi fx delete-group "ROM\1\41.DAT" xfd0 --dry-run

# delete the first torch group
uv run xi fx delete-group "ROM\1\41.DAT" xfd0

# delete the second torch group (if a second copy-group was run)
uv run xi fx delete-group "ROM\1\41.DAT" xfd1
```

---

## Example output

```
Removed 6 effect(s): xfs0, xfr0, xfl0, xfi0, xfd0, xfm0
Wrote: <output DAT path>
```

---

## Notes

- The seed can be **any member** of the group — `xfd0`, `xfr0`, `xfs0` all target the same set.
- `xi fx copy-group` prints the ready-to-paste delete command at the end of its output.
- Keeps a `<dat>.base` backup (shared with `xi zone import` / `xi fx delete`).

---

## Related commands

- **[`xi fx copy-group`](copy.md#xi-fx-copy-group)** — create the group in the first place
- **[`xi fx delete`](delete.md)** — remove effects by name or prefix
