# `xi event dialogue edit` — author custom dialog

Replace a dialog entry's displayed text with your own, writing the rebuilt DAT
back in place (the pristine bytes are kept in a `<dat>.base` backup).
See [format.md](format.md) for the binary format and [export.md](export.md) to
find the entry index you want to change.

```
xi event dialogue edit <DAT> --index <N> --text "<text>"   [--dry-run]
```

- Find the `--index` with `xi event dialogue search <DAT> "some words"`.
- Edits **layer** — run `edit` several times to change multiple entries; each
  builds on the previous edit in the mirror.
- `--dry-run` shows the before/after and size delta without writing.

## Authoring syntax (`--text`)

| Write | Produces | Meaning |
|---|---|---|
| `\n` (or a real newline) | `07` | line break |
| `\v` (or `\p`) | `7F 31` | press-enter continue prompt ▼ |
| `\\` | `5C` | literal backslash |
| `{player}` | `08` | inserts the player's name |
| `{npc}` | `09` | inserts the NPC's name |
| `{auto:N}` | `7F 34 NN` | auto-advance after N seconds (then waits for a key) |

Plain text is encoded as Shift-JIS (`cp932`); a character outside Shift-JIS is
rejected with a clear error.

## Examples

```bash
xi event dialogue edit ROM/25/39.DAT --index 4 --text "ho ho ho"
xi event dialogue edit ROM/25/39.DAT --index 0 --text "Welcome home!\nYour Mog House awaits.\v"
xi event dialogue edit ROM/25/39.DAT --index 5 --text "Hello {player}!\nThe shop opens shortly.\n{auto:5}"
xi event dialogue edit ROM/25/39.DAT --index 0 --text "New line here." --dry-run
```

```
entry #0:
  old: "The San d'Oria residential area is ahead. You'll find your Mog House within."
  new: 'Welcome home!\nYour Mog House awaits.'
  size: 1788784 -> 1788744 bytes
Wrote: …\ROM\25\39.DAT
```

## How it stays safe

- **Faithful rebuild.** Parsing a DAT into its per-entry byte-gaps and rebuilding
  them unchanged is **byte-for-byte identical** to the original (verified on
  `ROM/25/39.DAT`). Only the entry you edit changes.
- **Variants preserved.** An entry's byte-gap can hold several NUL-separated
  sub-strings (gender/plural/menu variants); `edit` swaps only the displayed first
  part and keeps the rest. If an entry carried variants, the command says so.
- The offset table and 24-bit length header are recomputed and the body
  re-obfuscated (XOR-`0x80`) automatically, so the result is a valid container of
  whatever new size.

## Notes

- The prompt code `\v` is flow control, so it shows up in the `--text` round-trip
  as a `prompt` opcode rather than an inline glyph — in game it's the ▼ that waits
  for a keypress.
- **Undo:** `xi event dialogue reset <DAT>` puts the DAT back to pristine — it
  restores `<dat>` from its `<dat>.base` backup. `--dry-run` shows what it would do. Add `--full`
  to also reset the zone's **event** DAT, fully undoing a `dialogue new` (which writes
  both the dialog string table *and* the event DAT); without it only the strings reset.
- Reads the current mirror when one exists (so layered edits accumulate),
  otherwise the pristine DAT. `--dry-run` has no side effects.
- Replaces existing entries by index. Re-applying an edited export JSON wholesale
  (`import`) and adding brand-new entries (`inject`) are planned subcommands.
