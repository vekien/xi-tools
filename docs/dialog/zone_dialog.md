# Zone dialog lines — the `zone_dialog` action

A zone's dialog lines — what its NPCs, menus and cutscenes print — edited or added by line
id in a `xi dats` project, built into the zone's English and Japanese dialog tables and
undone exactly. The table format is [format.md](format.md); the text form is the one
[edit.md](edit.md) documents for `xi event dialogue edit`.

Schema: [`schema/zone_dialog.json`](../../schema/zone_dialog.json). Library:
`xi.dialog.xi_zone_dialog`.

---

## An example

```json
{
  "id": "zone_dialog.lower_jeuno",
  "type": "zone_dialog",
  "zone": 245,
  "lines": [
    {"id": 6746, "en": {"replace": {"Right away.": "Yes, take me there."}}},
    {"id": 11344, "new": true, "en": "Welcome to Lower Jeuno, {player}!\\v"}
  ]
}
```

```
xi dats build jeuno --dry-run --pivot
xi dats build jeuno --pivot
```

Line 6746 of Lower Jeuno's travel prompt now offers "Yes, take me there."; line 11344 is added
at the end of both tables (the Japanese one takes the English text), for an event to print.

Three ways in: write the action (or `{"zone", "lines"}`) and `xi dats prepare lines.json
--project P` — a bare list of lines needs `--type zone_dialog --zone N`, and `--merge` adds
lines to the action already there; or `xi dats new` → *Zone dialog*: the zone, then line by
line, see the line and type its new text (`old=>new` changes part of it, `new` adds one).

---

## Lines

| Key | What |
|---|---|
| `id` | The line's index in the zone's table — `xi event dialogue search <zone> "words"` finds it. |
| `en` / `jp` | The new text, `{"replace": {old: new}}`, `{"hex": "…"}` for the displayed part's exact bytes (the variants after it kept), or `{"entry_hex": "…"}` for the whole entry exactly — its closing `00` and anything after it included. |
| `new` | Add the line past the end of both tables (lines in between stay empty). Needs `en` (text, `hex` or `entry_hex`); `jp` defaults to it. |
| `note` | Why. |

Text is authored as for `xi event dialogue edit`: `\n` a new line in the box, `\v` the
prompt that waits for a key, `{player}`, `{npc}`, `{auto:N}`, `{N}`, `{item:N}`,
`{keyitem:N}`, `{raw:hex}` …. A line's variants after its displayed text (gender / plural /
menu alternatives) are kept.

**Prefer `replace` for edits.** Line ids belong to the install: the 10 September 2026 retail
update inserted lines into every nation city, so Lower Jeuno has 11,344 lines on CatsEyeXI and
11,350 on retail, and line 6746 is a different line on each. A `replace` checks the text it
changes is there and refuses otherwise — the example above builds on CatsEyeXI and is refused
on retail — where a whole-text edit would overwrite whatever line now has that id. `replace`
also keeps control codes the text form can't show.

---

## Which files

The zone's tables come from its file ids: English `6420 + zone` and Japanese `6120 + zone`
below zone 256, the zone model's file id `+ 1700` / `+ 1400` above (`xi.zone.xi_inject`;
checked on the retail install, Mog Garden 280 → `ROM/303/32` / `ROM/303/30`). They resolve
through the target's ROM10 tables (custom zones) and every table pair of the install
(expansion zones live in `ROM2`–`ROM9`). The build reads each from the target — the base
install, or the pivot folder with `--pivot` (its own copy, else the install's) — and warns
when a base-install build edits a table the pivot folder has its own copy of.

Every nation-city and Mog Garden dialog table, English and Japanese, on both the CatsEyeXI
and the retail install rebuilds byte for byte from its lines, so only the lines an action
names change.

## Builds, rebuilds and undo

Each line written is recorded per target with its bytes before and after (and, for a new
line, the table's line count before and after). A build first puts back what the previous
build of the action changed, then applies the lines: a rebuild converges, a line taken out of
the action goes back, and a `replace` always applies to the original text. `xi dats undo`
restores each line and trims the lines the action added — only while the table still holds
what the build wrote; anything else is left and named.

## Not yet

- Events that print a new line: that's `zone_events` (next), which compiles cutscene JSON
  into the zone's event table and allocates its own lines.
- The zone editor's Publish still writes these tables through the bridge; moving it onto
  `zone_dialog` / `zone_events` actions comes after.
