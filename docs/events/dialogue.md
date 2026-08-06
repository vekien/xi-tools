# NPC dialogue & messages

How the **text** an NPC speaks is stored, encoded, and shown. The event
[bytecode](format.md#the-scene-bytecode-event-vm) decides *when* to speak and *which*
line; this doc covers *where the lines live* and *how they're decoded*.

> Sources: `thirdparty/shining fantasia/src/common/resources/event-message.ts` &
> `dmsg.ts` (parsers), `src/common/string/string.ts` (the codec),
> `src/xi/zone/xi_list.py` `parse_dmsg` (our working `d_msg` reader), and
> `thirdparty/xiclient/.../UI/Windows/InGame/CTkEventMsg*` (the display side).

---

## Two string formats

FFXI stores message text in two related table formats. Which one a given DAT uses
depends on its role:

| Format | Magic / marker | Used for | Parser |
|---|---|---|---|
| **EventMessage** | length-prefixed, optional `0x80` XOR | per-zone **event/cutscene dialogue** | `event-message.ts` |
| **d_msg** | `d_msg` magic, optional bytewise XOR | structured/localized tables — item text, system messages, **zone names**, NPC text | `dmsg.ts`, `parse_dmsg` |

Both are **offset-table + string-blob** designs: a header, a table of offsets, then the
string data. Both are lightly obfuscated with an XOR the parser reverses.

---

## EventMessage (event/cutscene dialogue)

The dialogue a cutscene prints (`0x1D`/`0x2B`/`0x48` opcodes) comes from an
EventMessage table — the zone's JP and NA string DATs (see
[README.md](README.md#per-zone-file-ids)).

Layout (`event-message.ts`):

```
+0x00  u24  resourceLength − 4          # total size check
+0x03  u8   flag                        # if == 0x10, every byte from +4 is XOR 0x80 (deobfuscate)
+0x04  u32  offsets[]                    # each = (byte offset of a string) − 4
       ...                               # the first offset doubles as the table's end marker
       ...  null-terminated string data  # decoded with the event-string codec (below)
```

- The **offset table** runs until the first entry's target (`startOffset`); each entry
  points at a string; the next entry (or end-of-buffer) bounds its length.
- *Convention byte-verified 2026-08 (S. San d'Oria, `ROM/25/39.DAT`): demasked
  `offsets[0] = 0x1076C`, and the first string ("The San d'Oria resid…") begins at exactly
  `offsets[0] + 4`; the 4 bytes at `offsets[0]` itself are the table's final entry. A
  competing "offsets are file-absolute" reading (2026-08 external crosscheck) would start
  record 0 with those 4 binary bytes and lose the last string — stored = absolute − 4 is
  what the bytes say.*
- **Deobfuscation**: when `b[3] == 0x10`, XOR every payload byte with `0x80` before
  reading — a trivial scramble, not encryption.
- Strings are **indexed by position** — the bytecode's "print message id" is an index
  into this table.

### The event-string codec

Event strings are **"kinda Shift-JIS with many non-standard bytes"** (the
`decodeEventString` comment). They are **not** plain ASCII/UTF-8: they mix readable
text with **inline control bytes** for formatting and substitution. In the extracted
[dataset](event-data.md) you'll see these as low-codepoint escapes, e.g.:

```
"1\u0000\u0007The shared memories of … decipher the cryptic glyphs of your \u0001\u00053…"
```

Those `\u0000`/`\u0007`/`\u0001\u0005…` bytes are control codes — line/prompt markers,
text-color toggles, and **placeholders** the engine fills at runtime (a player name, an
item, a number, a chosen weather, …). That's why raw strings contain `????` / `?` runs:
they're substitution slots, not literal text. Decode with the codec
(`decodeEventString` / shining fantasia's `ShiftJISEventTable`) rather than a stock
Shift-JIS decoder.

---

## d_msg (structured / localized tables)

`d_msg` tables are the more structured cousin — used for item descriptions, system
messages, zone names, and NPC text. We already parse them (`parse_dmsg` in
`src/xi/zone/xi_list.py`; it's how `xi zone` reads zone names from
`ROM/165/84.DAT`).

Header (at `+0x10`, little-endian):

```
char[5] "d_msg"                          # magic at +0x00
...
+0x10  u32 unk0
+0x14  u32 fileSize
+0x18  u32 tableOffset
+0x1C  u32 tableSize
+0x20  u32 stringBlockSize
+0x24  u32 stringSectionSize
+0x28  u32 numStrings
+0x2C  u32 unk1
```

- **Obfuscation**: bytes from `tableOffset` to `fileSize` are XOR'd (often `0xFF`;
  `parse_dmsg(..., bitmask=0xFF)`). Some tables use a different/zero mask.
- **Two table modes**: if `tableSize == 0`, strings are fixed-stride
  (`tableOffset + stringBlockSize * i`); otherwise an offset table at `tableOffset`
  (`u32 offset, u32 ...` per entry) points into the string section.
- **Entry block**: `u32 count`, then `count` × `(u32 offset, u32 type)`; a `type == 1`
  element is a string (a `0x1C`-byte sub-header then a null-terminated **cp932 / Shift-JIS**
  string), other types are inline integers. Localized tables carry both English and
  Japanese variants. (Matches `dmsg.ts` `decodeDmsgEntry`.)

Round-trip helpers exist in shining fantasia: `dmsg2json` / `json2dmsg`.

---

## How the bytecode reaches a line

From the [scene VM](format.md#the-scene-bytecode-event-vm), the dialogue-related
opcodes are:

| opcode | does |
|---|---|
| `0x1D` | print an event message, speaker = `EntityTargetIndex[1]` |
| `0x2B` | print an event message with a given entity as the speaker (like `0x1D`) |
| `0x48` / `0x49` | print an event message **with no speaker** (narration) |
| `0x23` | **wait** for the player to dismiss the message box |
| `0x24` | open a **selection menu** (player picks an option) |
| `0x25` | **wait** for the menu selection |
| `0x40` / `0x41` | set / test the **menu-option enabled** bit flags (which choices are available) |
| `0x1E` | tell the speaker to face the listener and play the "talking" (mouth) animation |

So a typical dialogue beat is: `0x1E` (face + talk anim) → `0x1D` (print line) →
`0x23` (wait for dismiss). A branching prompt is: `0x40`/`0x41` (enable choices) →
`0x24` (open menu) → `0x25` (wait) → `0x02`/`0x3E` (branch on the chosen value).

**The "message id" is a `references[]` work-selector, not a literal.** A print opcode's
operand is a 2-byte selector `0x8000 | refIndex` (little-endian) — the `0x8000` flag means "this
is a reference", and `references[refIndex]` (the actor block's
[ImidData table](format.md#operand-references--the-references--work-selector-model)) holds the
real message id, which is the **index into the table above**. Selector byte offsets per opcode:
`0x1D`/`0x48`/`0x49` at +1, `0x2B` at +5, `0x24` (menu text) at +1, `0xB0` at +10. (This was
**verified** against 13.6k retail print ops — 17% reference an index > 127, which only the
2-byte selector can express; a single-byte read mis-resolves them.) To **author** a new dialogue
event from a JSON list of lines, see [authoring.md](authoring.md) — it appends strings here and
emits this bytecode.

---

## How dialogue is displayed (client side)

The in-game box is the **event-message window**. In **xiclient** (a *fan*
reimplementation — names invented, see [README.md](README.md#source-trust--three-tiers))
it's `CTkEventMsgBase` + `CTkEventMsgType1` / `Type2`: two presentations — a plain
message box vs. a box with selectable menu options — paired with opcodes `0x1D`/`0x23`
vs. `0x24`/`0x25`. Whatever the retail class names, the observable behaviour (a message
box and a menu box, resolving control codes for color and name/number substitution as it
renders) matches what the print/menu opcodes do.

---

## Related

- [format.md](format.md) — the Event DAT + which opcodes print/branch text.
- [cutscenes.md](cutscenes.md) — dialogue in the context of a full scripted scene.
- [event-data.md](event-data.md) — 326k already-decoded dialogue lines to read/search.
- [../dat_ror1.md](../dat_ror1.md) — ROR-1 encoding used by some `menu` string DATs
  (a different scheme from the event-string codec — don't mix them up).
