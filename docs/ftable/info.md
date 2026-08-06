# xi ftable info

A one-glance summary of your custom id ranges: which **entity** and **gear**
model ids you can use, where the entity↔gear boundary sits, what's already
registered, and the recommended starting ids for new content.

Use this whenever you're about to add custom content and want to know *"what
number do I use, and is there room?"*

---

## Usage

```
uv run xi ftable info
```

No options. It reads the live FTABLE/VTABLE on disk plus the gear state file.

---

## Example

```
================================================================
  xi custom id ranges
================================================================
    Tables provisioned  : 423,152 entries

  ENTITY  (monsters / NPCs / objects)      file_id = modelid + 98,239
    usable modelid      : 15,000 - 30,000   (15,001 slots)
    file_id range       : 113,239 - 128,239
    recommended start   : 15,000+
    registered now      : 0  (none yet)

  GEAR  (per race + slot)                  file_id = 128,240 + (race*9+slot)*4,096 + modelid
    usable modelid      : per (race, slot), up to 4,095
    per-slot minimum    : face 32 · ranged 256 · head/body/hands/legs/feet 672 · main/sub 1,196
    recommended start   : 3,000+
    gear file_id range  : 128,240 - 423,151
    window size         : 4,096 file_ids x 8 races x 9 slots
    registered now      : 2,520 table entries  (armor pointers only)

  Layout (file_id):
            0 -   109,700   retail content
      113,239 -   128,239   custom entity band   (15,001 file_ids)
      128,240 -   423,151   custom gear bands    (294,912 file_ids, 72 windows)
================================================================
```

---

## How to read it

**Tables provisioned** — current table size. If it says *"NOT fully expanded"*,
run `uv run xi ftable expand` first.

**ENTITY**
- `usable modelid` — the range you may inject into (recommended start: **15,000+**).
- `registered now` — how many custom entities you've actually injected (`0` until you start).

**GEAR**
- `usable modelid` — per (race, slot), up to 4,095. Recommended start: **3,000+**.
- `per-slot minimum` — the hard floor for each slot (below this is retail gear).
- `registered now` — table entries in the gear region. Right after `expand` this
  is the **armor pointers** (retail gear kept working), *not* free slots; it ticks
  up by one for each gear you inject.

**Layout** — the three file_id bands: retail content, your entity band, your gear
bands. Everything above retail is custom.

---

## See also

- [expand.md](expand.md) — how to create/grow these ranges, and the recommended ids explained
- [tables.md](tables.md) — the raw per-ROM table view (sizes, registration counts)
