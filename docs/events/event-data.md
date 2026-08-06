# Extracted event data (knowone134)

We already have a large, **pre-extracted** dump of FFXI event dialogue:
`thirdparty/knowone134 event data/` — one JSON per zone, with each actor's events and
the dialogue lines they print. This is the fastest way to look up *"what does NPC X say
in event N"* without parsing the [Event DAT](format.md) yourself.

---

## Coverage

| | |
|---|---|
| Zones | **277** (one `*.json` per zone, e.g. `bastok_markets.json`) |
| Actors | ~**19,866** |
| Events | ~**55,771** |
| Dialogue lines | ~**326,202** |

---

## Structure

```jsonc
{
  "id": 48,                       // zone id
  "name": "Al Zahbi",             // zone name
  "actors": [
    {
      "id": 17375807,             // server entity (actor) id
      "name": "",                 // actor name (often blank in this dump)
      "events": [
        {
          "id": 3,                // event id (the N in "run event N on this actor")
          "dialogue": [
            { "id": 6355, "string": "1\u0000\u0007The shared memories of generations of mooglekind\u0001\u00053..." },
            { "id": 6356, "string": "1\u0000\u0007Review which trial?\u0007\u000bTrial 1.\u0007Return." }
          ]
        }
      ]
    }
  ]
}
```

- **`actors[].id`** is the same `actorId` the [Event DAT](format.md#actor-block) blocks
  are keyed by.
- **`events[].id`** is the event id the bytecode looks up
  ([`eventIds`](format.md#actor-block)).
- **`dialogue[].id`** is the message id the print opcodes
  ([`0x1D`/`0x2B`/`0x48`](dialogue.md#how-the-bytecode-reaches-a-line)) reference;
  **`string`** is the decoded text.
- Those `\u0000`, `\u0007`, `\u0001\u0005…` escapes inside `string` are **event-string
  control bytes** (prompt/line markers, color toggles, runtime substitution slots) —
  not literal text. See [dialogue.md](dialogue.md#the-event-string-codec). The `?` /
  `????` runs you'll see in some lines are unresolved substitution slots (a player name,
  a number, a chosen weather, …) that the engine fills at runtime.

---

## Looking things up

```bash
# All dialogue an actor speaks in a given event (jq)
jq '.actors[] | select(.id==17375807) | .events[] | select(.id==3) | .dialogue[].string' \
   "thirdparty/knowone134 event data/al_zahbi.json"

# Find which zone/actor a line of text appears in (ripgrep across the dump)
rg -l "Review which trial" "thirdparty/knowone134 event data/"
```

```python
import json, glob

# Search every zone for dialogue containing a phrase
needle = "moogle"
for path in glob.glob("thirdparty/knowone134 event data/*.json"):
    z = json.load(open(path))
    for actor in z["actors"]:
        for ev in actor["events"]:
            for d in ev["dialogue"]:
                if needle.lower() in d["string"].lower():
                    print(f'{z["name"]} actor {actor["id"]} event {ev["id"]} '
                          f'msg {d["id"]}: {d["string"][:80]!r}')
```

---

## Caveats

- This is a **snapshot** of one extraction (by *knowone134*) — it may lag the current
  CatsEyeXI client and doesn't include every zone or the **event bytecode** itself (only
  the resulting dialogue). For the scene logic (camera, branching, menus) you still need
  to read the Event DAT — see [format.md](format.md).
- Actor `name` is frequently blank here; join `actor.id` against the zone's **Entity
  (NPC) DAT** to recover names (see [format.md](format.md#entity-npc-definitions)).
- Strings are raw event-encoding — decode/clean control bytes before display.

---

## Related

- [format.md](format.md) — the binary Event DAT this data was extracted from.
- [dialogue.md](dialogue.md) — the string formats + the control-byte codec.
- [cutscenes.md](cutscenes.md) — how these lines fit into a scripted scene.
- [opcodes.md](opcodes.md) — the full event-VM opcode reference.
