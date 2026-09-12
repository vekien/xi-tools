# Zone messages in the chat log — the TALKNUM packet family

How the server prints a line from a zone's **dialog DAT** into the **chat log** — as
opposed to the event message box. LandSandBoat's `messageSpecial(...)` and the fishing,
digging, and "you obtain" lines all go this way: the server sends *"print entry N of this
zone's dialog table, with these parameters"*, and the client looks the string up locally.
No event runs, so nothing in [format.md](format.md) is involved; only the string table
([../dialog/format.md](../dialog/format.md)) is.

> **Trust.** Layouts below are from LandSandBoat's server packet headers
> (`src/map/packets/s2c/0x02a_talknumwork.h`, `0x027_talknumwork2.h`, `0x036_talknum.h`,
> `0x043_talknumname.h`), which mirror atom0s's **XiPackets** field names, cross-checked
> against the 2003 PS2 client's handler table
> ([../reference/ps2_decomp_crosscheck.md](../reference/ps2_decomp_crosscheck.md) §9:
> `TalkNumWork2` / `TalkNumWork` / `TalkNum` / `TalkNumName`). We have **not** byte-verified
> them against a live capture ourselves.

---

## The family

Four server→client packets share one job and one lookup; they differ only in the
parameter payload:

| id | SE name | size | payload beyond the message id |
|---|---|---|---|
| `0x036` | `GP_SERV_COMMAND_TALKNUM` | 16 | none |
| `0x02A` | `GP_SERV_COMMAND_TALKNUMWORK` | 64 | 4 × `i32` + a 32-byte name |
| `0x027` | `GP_SERV_COMMAND_TALKNUMWORK2` | 112 | 4 × `i32` + 8 × `i32` + a 32-byte and a 16-byte string |
| `0x043` | `GP_SERV_COMMAND_TALKNUMNAME` | 32 | a 16-byte name |

LandSandBoat routes `messageSpecial` through `0x02A`; every fishing line goes through
`0x036` / `0x027` / `0x043` (`fishingutils.cpp` — `0x027` carries the caught item id in
`Num1[0]`, the stack count in `Num1[1]`, and the angler's name in `String1`; `0x043` is the
"*<Player>* caught a monster!" form).

### Fields every member carries

| field | meaning |
|---|---|
| `UniqueNo` u32 | server id of the entity the message is *about* (the speaker / actor) |
| `ActIndex` u16 | that entity's target index |
| `MesNum` u16 | **bit `0x8000` = hide the name**; the **low 15 bits are the dialog-DAT entry index** |
| `Type` u8 (u16 on `0x027`) | chat-mode selector, see below |

Unless the hide bit is set, the client prefixes the line with the speaker's name
(`Name : text`) — the entity resolved from `UniqueNo`, or the packet's own string on the
members that carry one. Servers set the hide bit whenever the dialog string already embeds
the name (LandSandBoat's fishing constructor always does, and still fills `String1`).

**`Type` → chat mode.** All four index the same 8-entry table with `Type`, falling back
to entry 0 for anything `≥ 8` (XiPackets, `0x0036` README; identical tables under
`0x0027` / `0x002A` / `0x0043`):

```
Type:  0     1     2     3     4     5     6     7
mode:  0x8E  0xA1  0x90  0x91  0x92  0xA1  0x94  0x95
```

The mode picks the chat channel/colour the line is filed under; the values are the
client's internal chat-mode ids and are not decoded further here.

---

## Wire layouts

Offsets are from the start of the packet (the usual 4-byte `id/size` + `sync` header
comes first, as in the `0x032` table in
[../external_source/New-Player-Cutscene-Pipeline.md](../external_source/New-Player-Cutscene-Pipeline.md)).
All little-endian; strings are NUL-terminated inside their fixed field.

### `0x036` TALKNUM — 16 bytes

| off | type | field |
|---|---|---|
| 0x04 | u32 | `UniqueNo` |
| 0x08 | u16 | `ActIndex` |
| 0x0A | u16 | `MesNum` |
| 0x0C | u8 | `Type` |
| 0x0D | u8[3] | padding |

### `0x02A` TALKNUMWORK — 64 bytes

| off | type | field |
|---|---|---|
| 0x04 | u32 | `UniqueNo` |
| 0x08 | i32[4] | `Num[0..3]` — the numeric parameters (`messageSpecial`'s `p0..p3`) |
| 0x18 | u16 | `ActIndex` |
| 0x1A | u16 | `MesNum` |
| 0x1C | u8 | `Type` |
| 0x1D | u8 | `Flag` |
| 0x1E | char[32] | `String` — speaker name (only shown when the hide bit is clear) |
| 0x3E | u8[2] | padding |

### `0x027` TALKNUMWORK2 — 112 bytes

| off | type | field |
|---|---|---|
| 0x04 | u32 | `UniqueNo` |
| 0x08 | u16 | `ActIndex` |
| 0x0A | u16 | `MesNum` |
| 0x0C | u16 | `Type` |
| 0x0E | u8 | `Flag` — bit 0: take the name from the entity at `UniqueNo` rather than `String1`; bit 1: `String2` overrides the name when the hide bit is set (XiPackets `0x0027`) |
| 0x0F | u8 | padding |
| 0x10 | i32[4] | `Num1[0..3]` |
| 0x20 | char[32] | `String1` |
| 0x40 | char[16] | `String2` |
| 0x50 | i32[8] | `Num2[0..7]` |

### `0x043` TALKNUMNAME — 32 bytes

| off | type | field |
|---|---|---|
| 0x04 | u32 | `UniqueNo` |
| 0x08 | u16 | `ActIndex` |
| 0x0A | u16 | `MesNum` |
| 0x0C | u8 | `Type` |
| 0x0D | u8[3] | padding |
| 0x10 | char[16] | `String` |

---

## Resolving the text

1. `index = MesNum & 0x7FFF`.
2. Open the **current zone's** dialog DAT (`6420 + zone_id` for the base ROM — see
   [README.md](README.md#per-zone-file-ids); e.g. Southern San d'Oria → `ROM/25/39.DAT`)
   and take entry `index`. It is the **same table** the event VM's print opcodes index
   ([dialogue.md](dialogue.md#how-the-bytecode-reaches-a-line)); `xi event dialogue <zone>`
   lists it by index ([../dialog/export.md](../dialog/export.md)).
3. Substitute the packet's parameters into the string's control codes
   ([../dialog/format.md](../dialog/format.md#string-encoding)): `0A nn` prints `Num[nn]`,
   and the inline tags `01 <len> <kind> 82 (80|n) …` read `Num[n]` as an item id, key-item
   id, count, or zone id depending on `kind` (the same tags the event authoring tokens
   `{item:n}` / `{keyitem:n}` / `{qtyitem:c:i}` emit — [authoring.md](authoring.md#text-tokens-what-a-client-must-resolve-for-executors-of-the-json-2026-09-04)).
   On `0x027` the first four slots are `Num1`.
4. Render to the **chat log**, not the message box: the `7F 3x` prompt codes are not
   interpreted there (the line is cut at the first `0x7F` —
   [../dialog/format.md](../dialog/format.md#the-continue-prompt-codes-the--press-to-continue)).

### Caveat — server text ids vs. the installed DAT

The index is an absolute position in the zone's table, and Square Enix inserts entries
over time. A server's `zone.text.*` ids are therefore tied to the client version it was
written against: point a LandSandBoat build at a dialog DAT from a different era and every
`messageSpecial` in the affected zone renders the *wrong line* — or lands on a **menu**
entry (one containing the `0B` choice separator), which is a sure sign of skew, since
menus are only ever driven by the event VM and never printed as chat. The fishing block,
for instance, sits about 8–10 entries apart between the 2023 client and a current one.
Compare the entry text against what the server expects before trusting an index.

---

## Related

- [../dialog/format.md](../dialog/format.md) — the dialog DAT container + control codes.
- [dialogue.md](dialogue.md) — the same strings reached from event bytecode.
- [../external_source/New-Player-Cutscene-Pipeline.md](../external_source/New-Player-Cutscene-Pipeline.md)
  — the *event* packets (`0x032`/`0x033`/`0x034` start, `0x05B` end, `0x052` release).
- [../reference/ps2_decomp_crosscheck.md](../reference/ps2_decomp_crosscheck.md) §9 — the
  2003 client's full packet handler tables.
