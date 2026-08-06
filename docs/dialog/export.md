# `xi event dialogue` — decode dialog DATs to JSON

Decode a per-zone **dialog** (event-message) DAT — NPC speech / cutscene text —
into readable text plus a faithful list of the embedded control codes. See
[format.md](format.md) for the binary format and opcode reference.

```
xi event dialogue info <DAT>            # entry count + opcode histogram, no export
xi event dialogue search <DAT> "text"   # find the index + entry for some text
xi event dialogue export <DAT>          # writes 3 sibling JSON files under exports/event/dialogue/<rom>/
```

## Finding an entry (`search`)

To find the index of a line (e.g. to pass to `xi event dialogue edit --index`), search
by text. Matching is case-insensitive substring by default and flattens line
breaks, so a query can span them:

```bash
xi event dialogue search ROM/25/39.DAT "Mog House"
xi event dialogue search "Lower Jeuno" "when I was but a lass"  # by zone name
xi event dialogue search 245 "fountain"                        # by zone id
xi event dialogue search ROM/1/41 "fountain"                   # a zone model DAT also routes
xi event dialogue search ROM/25/39.DAT "lies to the north"     # matches across a line break
xi event dialogue search ROM/25/39.DAT "San d.Oria lies" --regex
```

```
58 match(es) for 'Mog House' in 39.DAT:
  #0      The San d'Oria residential area is ahead. You'll find your Mog House within.
  #1      I've not told you about Mog Houses yet, have I? Would you care to learn about them now?
  …
```

Options: `--regex`, `-s/--case-sensitive`, `--limit N` (0 = all).

## Specifying the DAT (paths, zone ids, zone names)

Every `dialog` command (`export`, `search`, `info`, `edit`, `reset`) accepts
`<DAT>` as any of:

- a **dialog DAT path** — absolute, relative, or ROM-relative (e.g. `ROM/25/39.DAT`);
  the `.DAT` suffix is optional (`ROM/25/39` works).
- a **zone id** (e.g. `245`) or **zone name** (e.g. `"Lower Jeuno"`) — routed to
  that zone's dialog DAT via FTABLE.
- a **zone's model DAT** (e.g. `ROM/1/41`) — also routed to the zone's dialog DAT.

When it routes, it prints which dialog DAT it picked — to **stderr**, so `--json`
pipes stay clean:

```
zone 245 (Lower Jeuno) → dialog DAT ROM/25/54.DAT
ROM/1/41.DAT is zone 245 (Lower Jeuno) → dialog DAT ROM/25/54.DAT
```

An ambiguous zone name lists the candidates with their ids. Edits live in place,
so the exported text reflects any prior edits.

## Output (three files, easy to share)

By default `export` writes **three sibling files** under `exports/event/dialogue/<rom>/`,
mirroring the ROM path like the other `xi` export commands. They split the data
by concern so you can share just the part you need — all joinable by `index`:

| File | Contents |
|---|---|
| `<stem>.json` | clean **text** — `{index, offset, length, text}` (the small, shareable one) |
| `<stem>.opcodes.json` | per-entry **opcodes** + `text` for context, plus the file `opcode_histogram` |
| `<stem>.hex.json` | per-entry **raw_hex** (de-obfuscated bytes) — `{index, offset, length, raw_hex}` |

e.g. `ROM/25/39.DAT` →
`exports/event/dialogue/rom/25/39/39.json`, `…/39.opcodes.json`, `…/39.hex.json`
(roughly 3 MB / 17 MB / 7 MB for San d'Oria's 16,859 entries).

## `export` options

| Option | Effect |
|---|---|
| `-o, --output FILE` | Output base path (default: `exports/event/dialogue/<rom>/<stem>.json`). The `.opcodes.json` / `.hex.json` companions derive from it. |
| `--no-opcodes` | Skip the `.opcodes.json` companion. |
| `--no-raw` | Skip the `.hex.json` companion. |
| `--json` | Emit a single **combined** doc (text + opcodes + raw) to stdout instead of writing files — for piping to `jq`. |
| `--preview` | Print a human-readable listing instead of writing files. |
| `--grep TEXT` | Only entries whose decoded text contains TEXT (case-insensitive). |
| `--index N` | Only entry index N (repeatable). |
| `--prompts-only` | Only entries containing a continue-prompt code. |
| `--limit N` | Cap entries emitted (0 = no cap; `--preview` defaults to 30). |

## Examples

```bash
xi event dialogue info ROM/25/39.DAT                       # Southern San d'Oria summary
xi event dialogue export ROM/25/39.DAT                       # → 39.json + 39.opcodes.json + 39.hex.json
xi event dialogue export ROM/25/39.DAT --no-raw              # skip the big hex file
xi event dialogue export ROM/25/39.DAT --grep "Mog House" --preview
xi event dialogue export ROM/25/39.DAT -o sandoria.json      # explicit base path
xi event dialogue export ROM/25/39.DAT --index 7663 --json | jq   # one combined doc to stdout
```

## Entry fields

- **text** — the readable rendering. Real newlines; content substitutions woven
  inline (`{player}`, `{npc}`, `{0}`). Layout / prompt / unmapped control codes
  are excluded here and live in the `.opcodes.json` file.
- **opcodes** — every control code, faithfully: byte `pos` within the string,
  `raw` hex (opcode + its parameter bytes), the upstream `code` name, a friendly
  `name` (`prompt`, `prompt_auto`, `newline`, `set_x`, `ctrl_<hex>`, …), decoded
  `params`, and a `note`.
- **raw_hex** — the full de-obfuscated string bytes, for ground truth.
- **opcode_histogram** (in `.opcodes.json`) — counts across the **whole** file
  (not just what was filtered/shown), most-frequent first.

## Notes

- Targets dialog DATs only. Pointed at the event-bytecode or NPC DAT, it fails
  with a clear message rather than emitting garbage.
- Decoding is read-only. To author custom dialog, see `xi event dialogue edit`
  ([edit.md](edit.md)). Bulk re-import of an edited export (`import`) and adding
  brand-new entries (`inject`) are planned subcommands.
