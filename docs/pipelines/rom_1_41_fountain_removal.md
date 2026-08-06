# Pipeline — Lower Jeuno (`ROM/1/41`) Fountain Removal

**Goal:** remove the central fountain entirely — the `funsui` basin **structure**
and its **water** (spray + bubbles + puddle) — and produce a redistributable
`41.DAT`.

**Zone:** Lower Jeuno — `ROM/1/41.DAT`

> A **recorded recipe**: run the steps top-to-bottom on a clean install to
> recreate this custom DAT.

---

## Prerequisites

- A clean `ROM/1/41.DAT` (the first `xi zone import` makes a pristine
  `ROM/1/41.DAT.base`; if `.base` already exists it is treated as the source of
  truth — make sure it's clean).
- `xi` available (`uv run xi …` or the installed entrypoint).

---

## Steps

```sh
# 1. export the zone to GLB
uv run xi zone export ROM/1/41
#    -> exports/zone/rom/1/41/41.glb
```

```text
# 2. edit in your DCC  (C4D -> FBX -> Blender -> GLB, per docs/zone/import.md)
#    - delete the `funsui` object (or move it far off-map)
#    - re-export over exports/zone/rom/1/41/41.glb
```

```sh
# 3. re-import with --prune so the deleted placement is actually removed
uv run xi zone import ROM/1/41 --prune
#    (this also (re)creates ROM/1/41.DAT.base from the clean source)

# 4. strip the now-orphaned water effects
uv run xi fx delete ROM/1/41 tki awa grid
```

**Order matters:** run `fx delete` **after** `zone import`. `zone import` rebuilds
the DAT from `.base` (which still contains the effects); `fx delete` then edits in
place. Re-running `zone import` later will **re-add** the effects — re-run step 4.

---

## What gets removed (reference)

| Item | Kind | How removed |
|------|------|-------------|
| `funsui` basin | `0x2E` mesh + `0x1C` placement (idx 45) | delete in DCC + `--prune` (step 2-3) |
| splash jets `tki1`–`tki5` | `0x05` → places `sibj` mesh | `fx delete … tki` |
| bubbles `awa1`–`awa6` | `0x05` → places `awan` mesh | `fx delete … awa` |
| puddle `grid` | `0x05` → places `suim`/suimen mesh | `fx delete … grid` |

The splash/bubble/puddle **meshes** (`sibj`/`awan`/`suim`) are orphan `0x2E`
sections (positioned only by their effect, no `0x1C` placement), so removing the
effects is enough — they stop rendering. See `docs/dats/ROM_1_41.md`.

---

## Verify

```sh
uv run xi fx json ROM/1/41        # tki*/awa*/grid should be gone
```

Load Lower Jeuno in-game: no fountain, zone loads without crash.

## Undo / restore

```sh
# restore the pristine DAT from the backup
copy /Y "ROM\1\41.DAT.base" "ROM\1\41.DAT"     # Windows
# cp ROM/1/41.DAT.base ROM/1/41.DAT            # *nix
```
