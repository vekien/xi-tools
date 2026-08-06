# xi entity inject (legacy)

`xi entity inject` is now a hidden compatibility command. New distributable
content should be authored through `xi dats prepare` + `xi dats build`, so
new IDs and ROM paths are recorded in `projects/locks.json` and can be rebuilt from Git.

Registers a custom entity model DAT (monster, NPC, or object) file into ROM10's FTABLE/VTABLE and
generates the SQL needed to wire it up in a LandSandBoat (LSB) server database.

**Prerequisite:** `xi ftable expand` must be run first. The command will
refuse to continue if `FTABLE10.DAT` is missing or if the base `FTABLE.DAT`
is still at retail size.

---

## What it does

1. Scans all FTABLE/VTABLE pairs (ROM1–ROM10) to find which model slots are already occupied.
2. Picks a free `modelid` in the safe custom range (15,000–30,000), or validates one you specify with `--modelid`.
3. Either copies your DAT into `ROM10/1/<n>.DAT` (default), or registers an already-placed file with `--register-existing`.
4. Patches `FTABLE10.DAT` and `VTABLE10.DAT` with the correct entry for that file.
5. Writes a SQL patch file to `patches/xi_monster_inject_<modelid>_<file_id>.sql`.

---

## The maths

```
file_id    = 98239 + modelid           ← confirmed formula in FFXiMain.dll (3500+ range)

ftable_val = (subdir << 7) | file_idx  ← encodes dir + file into one uint16
                                         e.g. subdir=1, file=0 → (1<<7)|0 = 0x0080

vtable_val = 10                        ← tells the client "look in ROM10/"

client resolves: ROM10/{subdir}/{file_idx}.DAT
```

The two table patches:

| File | Offset | Value written |
|---|---|---|
| `FTABLE10.DAT` | `file_id * 2` (uint16 LE) | `(subdir << 7) \| file_idx` |
| `VTABLE10.DAT` | `file_id` (byte) | `10` |

### Example — modelid 15000, ROM10/1/0.DAT

```
file_id    = 98239 + 15000 = 113239
ftable_val = (1 << 7) | 0  = 0x0080
vtable_val = 10

FTABLE10 patched at byte offset 226478  → 0x80 0x00
VTABLE10 patched at byte offset 113239  → 0x0A

mob_pools blob: 0x0000983A00000000000000000000000000000000
```

---

## Default file placement — ROM10/1/

When you pass a DAT that is not already in ROM10, the script copies it into
`ROM10/1/` and auto-numbers it:

```
ROM10/1/0.DAT   ← first injection
ROM10/1/1.DAT   ← second
...
ROM10/1/127.DAT ← subdir full, wraps to ROM10/2/0.DAT
```

FFXI subdirectories hold a maximum of 128 files (the lower 7 bits of the FTABLE
uint16 encode `0–127`). Use `--subdir N` to target a different subdir.

---

## The modelid blob (mob_pools)

LSB stores the model in `mob_pools.modelid` as a `binary(20)` column matching
the `look_t` struct:

```
bytes  0– 1 : uint16 LE  size    = 0      (0 = monster/NPC type, not humanoid)
bytes  2– 3 : uint16 LE  modelid = <your modelid>
bytes  4–19 : zeros                        (equipment slots — unused for monsters)
```

Example for modelid 15000 (`0x3A98`):

```
0x0000983A00000000000000000000000000000000
```

Use `xi model json --free` to get the blob for the next free slot.

---

## SQL output

The generated SQL file contains four sections:

```sql
-- Step 1: INSERT INTO mob_pools  — cloned from TigerFamiliar (pool 4604) as a baseline
-- Step 2: INSERT INTO pet_list   — links pet name/level/type to the pool
-- Step 3: Lua enum comment       — xi.pet.id.MYMONSTER = <pet_id>  (add to pet_id.lua)
-- Step 4: spawn / runtime test   — xi.pet.spawnPet() and setModelId() examples
```

Default ID assignments (all overridable with flags):

| Value | Default formula |
|---|---|
| `pool_id` | `modelid + 10000` |
| `pet_id` | `modelid + 200` |

---

## Usage

```
uv run xi entity inject <dat_file> [options]
```

| Flag | Default | Description |
|---|---|---|
| `--modelid N` | auto | Force a specific modelid (must be free, 15000–30000) |
| `--subdir N` | `1` | ROM10 subdir to place the DAT in |
| `--register-existing` | off | DAT is already inside ROM10 — skip copy, compute dir/file from its path |
| `--pool-id N` | modelid + 10000 | `mob_pools.poolid` in generated SQL |
| `--pet-id N` | modelid + 200 | `pet_list.petid` in generated SQL |
| `--pet-name STR` | `Custom_<modelid>` | Pet name in SQL (max 15 chars) |
| `--species N` | `114` | `mob_pools.speciesid` (114 = tiger family) |
| `--dry-run` | off | Print everything without writing any files |

### New DAT — copy into ROM10/1/ and register

```
uv run xi entity inject my_model.DAT --pet-name "MyMonster"
```

### Already-placed DAT — register existing ROM10 file

Use this when the DAT is already sitting in ROM10 at a specific path:

```
uv run xi entity inject "ROM10\1\0.DAT" --modelid 15000 --pet-name "TestTiger" --register-existing
```

The script parses `subdir=1, file_idx=0` from the path and patches the tables — no copy is made.

### Dry run

```
uv run xi entity inject my_model.DAT --dry-run
```

---

## After running

1. **Import the SQL** into your LSB database:
   ```
   patches/xi_monster_inject_<modelid>_<file_id>.sql
   ```

2. **Add the Lua enum** to `server/scripts/enum/pet_id.lua`:
   ```lua
   xi.pet.id.TESTTIGER = 15200
   ```

3. **Restart the map server** to reload `mob_pools` and `pet_list`.

4. **Spawn and test** in a BST script:
   ```lua
   xi.pet.spawnPet(player, xi.pet.id.TESTTIGER)
   ```

   Or test the model render directly without a DB entry:
   ```lua
   local pet = player:getPet()
   if pet then pet:setModelId(15000) end
   ```

---

## Config

Paths are read from environment variables (set in `src/xi/config.py`):

```
FFXI_DIR        default: <FFXI_DIR>
XI_TOOLS_DIR  default: D:\xi-tools  (patches/ and backups/ live here)
```

---

## Retail modelid boundary

| Boundary | Modelid | file_id |
|---|---|---|
| Last retail monster | 11,241 | 109,480 |
| Buffer zone | 11,242 – 14,999 | 109,481 – 113,238 |
| First safe custom slot | **15,000** | **113,239** |
| Top of expanded range (default) | 30,000 | 128,239 |
| Gear floor (derived) | — | **128,240** (`CUSTOM_GEAR_BASE`) |
