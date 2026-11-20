# xi dll ffximain sig-audit

**Check a C++ project's byte patterns against two builds of `FFXiMain.dll`, offline.**
A project that hooks the client by byte pattern (a string such as
`"8B 0D ?? ?? ?? ?? 85 C9"` in its source, searched for in the loaded module at run
time) breaks when the client is rebuilt, in two ways:

- a pattern no longer matches, and its feature loses its hook;
- a pattern still matches, but the code around it changed (a structure field moved, a
  function was rewritten), so an offset the source adds to the match, or reads from it,
  is now wrong.

`sig-audit` finds both before the new client ships. Give it the build the project was
written against (OLD), the new one (NEW) and the project's source folder:

```
uv run xi dll ffximain sig-audit old/FFXiMain.dll new/FFXiMain.dll --src ../myproject/src
```

It prints a Markdown report and writes it to `exports/dll/sig-audit/report.md`. Nothing
is loaded or run, and the game install isn't written. Any C++ project with hex patterns
in its source works; VanaCore's hooks are the case it was written for.

| Option | Description |
|---|---|
| `OLD` | The `FFXiMain.dll` the project's patterns were written against: the game's packed file, or one already [unpacked](unpack.md). |
| `NEW` | The build to check them against (packed or unpacked). |
| `--src DIR` | The project's source folder. Every `.cpp`, `.hpp`, `.h`, `.c` and `.inc` under it is read. Required. |
| `--output-dir DIR` | Where `report.md` and the unpacked DLLs go. Default: `exports/dll/sig-audit/`. |
| `--verbose` | Also list the patterns that match more than once, and show every changed instruction of a function instead of the first few. |

---

## What it does

1. **Unpacks both DLLs.** The game's `FFXiMain.dll` keeps its code LZSS-compressed in
   the POL1 section (see [unpack.md](unpack.md)). A packed file is unpacked into
   `--output-dir` as `old_FFXiMain_unpacked.dll` / `new_FFXiMain_unpacked.dll` with the
   same codec `xi dll ffximain unpack` uses; an unpacked one is read as it is.
2. **Finds the patterns in the source.** Every C++ string literal (adjacent literals
   joined, as the compiler joins them; comments skipped) that is a hex byte pattern
   counts: space-separated (or packed) hex bytes with `??` or `?` wildcards, at least
   four bytes and three of them literal. `"A | B"` is two alternatives for the same
   place (as two builds compile it); the first that matches is used, as a scanner tries
   them. A pattern written in several places is listed once, with every `file:line`.
3. **Searches both builds' `.text`** for each pattern, with the same scanner as
   [`sig-apply`](sig-patch.md).
4. **Follows each match to its function and compares it** with the function NEW has
   at the match: up to 700 instructions from the match to the function's `ret`, with
   call and jump targets and image addresses masked (they move with every rebuild and
   mean nothing), and a switch's table of addresses inside `.text` ignored.
5. **Aligns every function between the builds** (call targets and pointers in
   relocated data give the function starts; each function is compared by its masked
   instructions) and collects the memory-operand constants that changed between
   same-shaped instructions: the structure offsets that moved.

Addresses in the report are against FFXiMain's preferred image base, `0x10000000`, as
Ghidra and IDA show them for an unpacked DLL.

---

## Reading the report

Read it top down. The first lines name both DLLs (PE header timestamp, image size,
`.text` size), then:

### Patterns

One line of counts: the distinct patterns in the source, how many are found in both
builds, **only in old (would miss in new)**, only in new, in neither, and with several
matches.

- **In neither**: the pattern isn't in either build's `FFXiMain.dll` code. It's for
  another module, matches data, or matches code the project itself patches in at run
  time. Nothing to do unless it should be in `FFXiMain.dll`.
- **Several matches**: the first match is the one compared. `--verbose` lists them.

### Found in old, not in new

**These patterns break on the new client.** Each line gives where the pattern is in the
source and the pattern, then:

- `its function is 0x… in old, 0x… in new; the pattern's bytes that differ there: [8] (old 10 new 14)`:
  the function was found in the new build, and the listed byte positions of the pattern
  (0-based) hold different bytes there. Usually a displacement or a constant changed:
  above, the 9th byte, a field offset, went from `0x10` to `0x14`.
- `its function was rewritten in new: look for the code by hand`: the function couldn't
  be paired with one in the new build.

A pattern is made to survive this kind of change by putting `??` over the bytes that
moved (a displacement, a limit, a register's encoding) and reading the value back from
the matched code at run time, rather than writing it into the source.

### Matches, but in a different function than the old one's counterpart

The pattern matches in both builds, but in NEW it lands in a different function than
the one paired with its OLD function. Here every pattern is searched over the whole
`.text`: if the source only searches a window of code (from a known address, for a
limited size), a hit elsewhere can be just that. Otherwise the pattern now finds
different code, and the hook would go in the wrong place.

### Functions under the matching patterns

For every pattern found in both builds whose code differs, the pattern's place, both
addresses, the instruction counts and a similarity ratio (1.000 is identical), then the
changed instructions, `-` from OLD and `+` from NEW (the first few per change, all of
them with `--verbose`). A change here is a field that moved or code that was rewritten
under a pattern that still matches: check every offset the source adds to that match.
The count line under the list says how many of the matched functions differ; the rest
are the same instruction for instruction.

### Offsets that moved (whole binary)

How many functions aligned as the same, changed, added and removed, and a table of the
most common shifts in memory-operand constants across all changed functions (up to
eight, each seen on at least eight instructions):

| Column | Meaning |
|---|---|
| shift | how much the constants moved, new minus old (`+0x4`) |
| instructions | how many instructions carry a constant that moved by it |
| where (old offsets) | the old constants it applies to, as ranges |

A shift of `+0x4` over old offsets `0x1a0-0x2c4` means a structure grew by four bytes
before `0x1a0`: a constant in the source inside that range, an offset into the same
structure, moved by that much.

---

## Notes

- Both inputs must be 32-bit PE files with a `.text` section; anything else stops the
  command with a message.
- The unpacked copies are written fresh on every run; they are research-only PEs, never
  game-loadable (see [unpack.md](unpack.md)).
- The comparison is static. It doesn't prove a hook is right; it points at the patterns
  and functions to look at first.

## See also

- [sig-patch.md](sig-patch.md): `sig-gen` / `sig-apply`, signatures for byte edits that follow their function across builds
- [unpack.md](unpack.md): the POL1 unpack used here
- [../ffximain/ffximain.md](../ffximain/ffximain.md): the POL1 packer and FFXiMain.dll
