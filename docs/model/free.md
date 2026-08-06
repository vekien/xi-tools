# xi model json --free

Scans the custom entity model range (monsters, NPCs, objects) (modelid 15,000–30,000) across all
FTABLE/VTABLE pairs and emits JSON with:

- the next free modelid and its file_id
- the `mob_pools` binary blob ready to paste (`next_model_id_text`)
- the configured range and currently occupied custom slots

Run this before assigning custom model IDs manually, or after `xi ftable
delete` to confirm a slot was freed. New reproducible packages should prefer
`xi dats build`, which stores allocations in `projects/locks.json`.

---

## Usage

```
uv run xi model json --free
```

No options.

---

## Example output

```json
{
  "range": {
    "start": 15000,
    "end": 30000
  },
  "next_model_id": 15000,
  "next_file_id": 113239,
  "next_model_id_text": "0x0000983A00000000000000000000000000000000",
  "occupied": []
}
```

With some slots in use:

```json
{
  "range": { "start": 15000, "end": 30000 },
  "next_model_id": 15002,
  "next_file_id": 113241,
  "next_model_id_text": "0x00009A3A00000000000000000000000000000000",
  "occupied": [
    { "model_id": 15000, "file_id": 113239, "rom": "ROM10", "dat": "ROM10/1/0.DAT" },
    { "model_id": 15001, "file_id": 113240, "rom": "ROM10", "dat": "ROM10/1/1.DAT" }
  ]
}
```

(For a human-readable “Next free model ID” text report, use `xi entity recommend`.)

---

## The mob_pools blob

The `next_model_id_text` value is the 20-byte `look_t` binary literal for the
`mob_pools.modelid` column. Paste it directly into SQL:

```sql
UPDATE mob_pools SET modelid = 0x0000983A00000000000000000000000000000000 WHERE poolid = 25000;
```

The format is:
```
bytes 0–1  : uint16 LE  size    = 0  (monster type, not humanoid)
bytes 2–3  : uint16 LE  modelid = <modelid>
bytes 4–19 : zeros       (equipment slots, unused for monsters)
```

→ See [../dats/README.md](../dats/README.md) for the reproducible package flow.

---

## Why the range is 15,000 – 30,000

The last retail monster modelid is **11,241** (file_id 109,480). The custom
range starts at **15,000** — giving a ~3,758-slot buffer above the retail cap
to absorb any future retail expansion without collision. The upper bound of
**30,000** is the default entity ceiling (`MAX_ENTITY_MODELID` in
`src/xi/xi_config.py`, overridable via env `XI_MAX_ENTITY_MODELID`):

```
entity file_ids  = 113239 – 128239   (modelid 15000–30000, +98239)
gear floor       = CUSTOM_GEAR_BASE = 128240
                 = 98239 + MAX_ENTITY_MODELID + 1
```

It is not a hard limit — raise the ceiling and re-expand:

```
uv run xi ftable expand              # both entity + gear at config defaults
uv run xi ftable expand entity 30000 # entity buffer only, explicit ceiling
# or: set XI_MAX_ENTITY_MODELID=40000 then xi ftable expand
```

`MODEL_SAFE_END` in `src/xi/entity/xi_core.py` is derived from
`MAX_ENTITY_MODELID` — do not hardcode a separate bump.

→ See [reference/model-file-ids.md](../reference/model-file-ids.md) for the
full custom range derivation and the file_id space map.

→ If the range is full, expand (or raise `XI_MAX_ENTITY_MODELID` first). See
[ftable/expand.md](../ftable/expand.md).

---

## Related commands

- **`xi dats build`** — package/register new content from a manifest
- **`xi ftable delete`** — free a slot if you need to replace a model
- **`xi model json`** — full dump of all registered models (retail + custom)
