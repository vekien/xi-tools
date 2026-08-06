# FFXI Dialog (Event-Message) DAT Format

The per-zone **dialog** DATs hold NPC speech and cutscene text — the strings the
client renders in the event message box (the bordered box at the bottom of the
screen during conversations and cutscenes). They are **not** the chat log, and
**not** the event *bytecode*; they are the string table that the event/message
system (`messageSpecial` / `text =` / `startEvent`) indexes into.

Example: Southern San d'Oria's dialog lives in `ROM/25/39.DAT`. The sibling
per-zone DATs are separate: `ROM/21/39.DAT` (event bytecode), `ROM/27/39.DAT`
(NPCs), `ROM/1/31.DAT` (zone model).

`xi event dialogue` decodes these — see [export.md](export.md).

---

## Container format

Matches Shining Fantasia's `EventMessage` resource.

| Bytes | Meaning |
|---|---|
| 0–2 | 24-bit resource length. The file is valid iff `lsb24(0) + 4 == filesize`. |
| 3 | `0x10` flags the body as obfuscated. |
| 4… | u32 offset table, then the string data. |

**Obfuscation.** If byte 3 is `0x10`, every byte from offset 4 to EOF is **XOR
`0x80`**. This is light obfuscation, not encryption — there is no key. It is why
a raw `grep`/`bytes.find()` for dialog text finds nothing (a raw scan of
`ROM/25/39.DAT` for the prompt code finds 3 coincidental hits; XOR-`0x80` first
and you find 16,215). It is a different scheme from the ROR-1 used by `menu`
text — see [../dat_ror1.md](../dat_ror1.md).

**Offset table.** Starting at offset 4, each entry is a `u32` little-endian
value; the real position is `value + 4`. The **first** entry doubles as the
end-of-table marker (`startOffset`), so the entry count is `startOffset / 4`.
String *N* runs from `offset[N]` to `offset[N+1]`, and is NUL-terminated (decode
stops at the first `0x00`; any bytes after it are alignment padding).

---

## String encoding

Each string is a near-Shift_JIS byte stream (decode normal characters with
`cp932`). Interleaved are FFXI-specific **control codes** ("opcodes"). A lead
byte's length (1 or 2 bytes) comes from a 256-entry table; some control codes
then consume additional **parameter** bytes.

### The continue-prompt codes (the ▼ "press to continue")

`0x7F` is a **2-byte lead**, so the prompt is a single `0x7F xx` lookup:

| Bytes | Meaning |
|---|---|
| `7F 31` / `7F 32` / `7F 33` / `7F 37` | **Manual** prompt — show ▼, wait for a keypress. (Retail standardizes on `7F 31`.) |
| `7F 34 NN` / `7F 35 NN` / `7F 36 NN` | **Auto** prompt — `NN` = seconds before it self-advances (else waits for a key). |
| `7F 38 NN NN` | Prompt variant taking two parameter bytes. |

These render only in the event message box, which runs the page/wait/timer state
machine. The chat-log channels (`printToPlayer` / `fmt` → `CHAT_STD`) do **not**
interpret them — they truncate at the `0x7F`. That is the whole reason you can't
produce the arrow from a chat message.

Verified example (`ROM/25/39.DAT` entry #7663, the zone intro crawl):

```
02 50 00  03 54 01  The fortress city ... [07] ... legends past. 7F 34 09  7F 31  00
└set_x=80┘ └set_y=340┘                    └nl┘                  └auto 9s┘ └ ▼ ┘ └end┘
```

### Common opcodes

| Byte(s) | Name | Params | Meaning |
|---|---|---|---|
| `00` | end | — | string terminator |
| `02 xx xx` | set_x | 2 | text X position (`p1<<8 | p0`) |
| `03 xx xx` | set_y | 2 | text Y position |
| `07` | newline | — | line break |
| `08` | player_name | — | inserts the player's name |
| `09` | npc_name | — | inserts the NPC's name |
| `0A nn` | value | 1 | numeric parameter substitution |
| `0B` | (choice sep) | — | menu / choice separator |
| `0C nn` | index | 1 | indexed substitution |
| `7F 31` | prompt | — | wait-for-key ▼ |
| `7F 34 nn` | prompt_auto | 1 | auto-advance after `nn` seconds |

Other codes appear as gauge-bar glyphs (`EF1F…EF26`), menu/list controls
(`01 05 …`), and various unmapped control codes — all surfaced in the dump's
`opcodes` list under a stable `ctrl_<hex>` id even when their meaning is unknown.

### What the decoder renders

`xi event dialogue`'s `text` field is the **readable** rendering: normal characters,
real newlines (`07`), and content substitutions woven inline (`{player}`,
`{npc}`, `{0}`). Layout, prompt, and unmapped control codes are kept **out** of
`text` but listed in full (offset, raw bytes, name, params) in `opcodes`, so
nothing is silently lost — `text` is the human view, `opcodes` is ground truth.

---

## Authoring (the reverse)

`xi event dialogue edit` re-encodes custom text back into this format — see
[edit.md](edit.md). Parsing a DAT into its per-entry byte-gaps and rebuilding them
unchanged is **byte-for-byte identical** to the original (the offset table and
24-bit length are recomputed and the body re-obfuscated), so editing one entry
leaves the rest intact. Because an entry's gap may hold several NUL-separated
sub-strings, an edit swaps only the displayed first part and keeps the rest.

## Decode tables (regeneration)

The byte-length table, the 795 special/opcode entries, and the 85 character
overrides where FFXI deviates from `cp932` are baked into
`src/xi/dialog/_sjis_data.py`, **generated** from Shining Fantasia's
`thirdparty/shining fantasia/src/common/string/Shift_JIS.ts`. To regenerate (if
the upstream table is updated), re-run the extraction that parses
`ShiftJISEventTable` / `ShiftJISEventBytes`, resolves each `MakeSpecial(code,
bytes)` / named const to its value, and emits the negatives as `SPECIAL`, the
`cp932` deviations as `CHAR_OVERRIDE`, and the 256-entry lead table as
`BYTE_LEN`. The format/decoder mirrors `EventMessage` + `decodeEventString`
there.
