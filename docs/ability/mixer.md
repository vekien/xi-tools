# Ability Mixer — recipe, compose, publish

Build a new ability presentation from pieces of retail ones: the motion of one, the
effects of another, the sound of a third. The xi-model-viewer's **Ability Mixer** mode
is the UI; these are the commands it drives, usable on their own.

```bash
uv run xi ability recipe ws:1:HumeMale --out fb.mix.json # starter recipe from one source
uv run xi ability compose LOVE.mix.json [--out DIR] [--race Mithra] [--json]
uv run xi ability publish LOVE.mix.json [--project NAME] [--kind ja|spell|ws] [--animation N | --animation-from N] [--subdir 20] [--dry-run]
                          [--apply-db [--db-row ID]] [--clone-from X] [--server-id ID] [--menu-record [--menu-name T]] [--lua-stub]
uv run xi ability slots [--pivot] [--free] [--json]     # the weapon-skill numbers and what holds each
uv run xi mv update --only abilities                      # the viewer's pick list (mv/lists/abilities.json)
```

A recipe is validated against [`schema/ability_recipe.json`](../../schema/ability_recipe.json)
(`"schema": "xi.ability.v1"`); `compose`, `publish` and `dats prepare` refuse one that
does not match, naming the field. Its file is a **mix file**, `<Name>.mix.json` (formerly
`<Name>.recipe.json`; every command, and the `dats new` wizard, still reads that name).
What names the ability is the recipe's `name`, never the file name.

> **A published action plays but looks wrong in game?** See
> [troubleshooting.md](troubleshooting.md): the layers, the recurring gotchas
> (custom-band race is 1-based, per-race joints, waist → companion, emote weapon
> stow), and why "looks right in the mixer preview" is not "plays right in game".

## Recipe

```jsonc
{
  "name": "tiger_fury",
  "sources": {
    "motion": { "spec": "ws:1",      "routine": "main" },   // race-bound: one DAT per race
    "vfx":    { "spec": "spell:144", "routine": "main" },
    "sound":  { "spec": "ja:33",     "routine": "main" }
  },
  "events": [
    { "from": "motion", "op": 5,  "ref": "b00?", "start": 10, "dur": 35, "blend": [20, 0], "loops": 1 },
    { "from": "motion", "op": 44, "ref": "b0a?", "start": 55, "dur": 70 },
    { "from": "vfx",    "op": 2,  "ref": "g004", "start": 60, "dur": 100 },
    { "from": "sound",  "op": 2,  "ref": "g0s0", "start": 60 },
    { "op": 3, "ref": "mdam", "start": 100, "raw": "03040000000000006d64616d00000000" }   // no lane: the hit, added by hand
  ]
}
```

- **`sources`** — one lane per name; `spec` is anything `xi ability inspect` accepts.
  A `ws:N` spec without a race is *race-bound*: the recipe composes once per race.
- **`events`** — one command each in the new `main` routine. `start` is the absolute
  frame (the writer turns it back into the format's relative delays). `dur`, `blend`
  (`[in, out]`) and `loops` patch the command's fields; anything else in the command is
  the source's own bytes. Give `raw` (hex from `inspect --json`) or `routine` +
  `offset` to pin an exact source command; otherwise the first command in the lane's
  routine with the same `op` and `ref` is used, and a handful of ops have templates.
- Commands with no ref (locks, flinch) are copied verbatim.
- An event with **no `from`** is a command of no lane — a lock, a hit, a link to a shared
  or actor routine added by hand — and must carry `raw`: see
  [Locks, hits and links](#locks-hits-and-links).
- **`name`** and **`row`** are the mixer's own and compose ignores both: `name` is the
  pill's label (the inspector's command name, or a base motion's stage — `Start`,
  `Middle`, `End`), `row` (an integer from 0) is the row of its track the pill sits in.
  Neither changes a byte of the DAT; timing comes from `start` alone.
- **`generators`** and **`curves`** (both optional, top level) change the particle sections
  the recipe carries: see [Generator edits](#generator-edits).
- **`textures`** (optional, top level) replaces a lane's textures with PNGs, each by its id
  and 16-character name: see [Textures](#textures).

## Compose

`compose` gathers, for every event, the sections its command names from that lane's
DAT: generators plus their textures, sprite sheets, meshes, curves and sound pointers
(the `xi fx copy --from` walk); sound pointers; clips and weapon traces matching the
ref (wildcards included); locally linked routines, recursively. Two sources naming the
same section differently get the later one **renamed** (`g003` → `g004`…) and every
reference in that source's copied generators and routines patched; the report lists
the renames. Then it writes the sections in retail's folders ([Folders](#folders)) and a
fresh `main` routine laid out like retail (sec1 end marker, sec2 start + commands + end,
sec3 end marker, `totalDelay` = the routine's end: the source's own total when the recipe
came from `xi ability recipe`, else where the last window closes — an event's start +
duration; each command's delay is the gap to the next, and the first start rides on the
start marker).

A clip's `dur` is the window of **one** cycle (the client time-scales the clip into it)
and `loops` repeats it, so a clip played N times holds the routine open for
`dur × loops`. That is retail's own arithmetic: the race base's `ssbk` plays `mb2?` x2 in
a 61-tick window, waits 122 after it, and its `totalDelay` is 58 + 122 = 180. The loop
count is the event's `loops`, else the source command's own. `loops: 0` (forever) counts
one cycle — there is no end to wait for — so follow a forever clip with another event or
give the recipe a `total`.

Output: `exports/ability/<name>/<name>[.<Race>].DAT` and a `<name>.report.json` with
sections, renames, the absolute-frame timeline, warnings and textures (the same entries
`--json` prints). Round trip is proven in
`tests/test_ability_compose.py`: a recipe made by `xi ability recipe` from Fast Blade
composes back to the same timeline the inspector reads from the retail DAT.

A referenced generator or sound pointer that the lane's DAT does not have is an
error, not a silent drop. A routine a link names that the game will not find where that
link looks is written all the same, with a warning ([Locks, hits and links](#locks-hits-and-links)).

A renamed section takes a name that neither the output nor its own lane's DAT uses, and the
rename covers every section of that lane under the old name (an audio generator and the
`0x3D` sound pointer it is named after, a texture and its sprite sheet), including one the
lane had shared with another lane because the bytes matched. A generator whose curve or
texture had to be renamed for its lane is that lane's own copy even when another lane
brought identical bytes, and a routine two lanes link is carried once per lane when their
copies differ, so each lane plays its own resources. The patch finds an id the way the DAT
spells it: Cure III pads its short ids with spaces (`ho␣␣`), others with NULs. A link takes
its lane's rename only for a routine carried from that lane: a link to the shared `mdam`
keeps its name even where the lane renamed a section called `mdam`, in the recipe's own
`main` and in the routines it carries.

**Textures go by name.** A `0x1F` mesh, a `0x21` sprite sheet or a `0x2E` zone mesh names
its texture by the texture's 16-character name (an 8-character namespace and a localName,
`"carel3  ho"`), and the client takes the first `0x20` in the file with that name. Compose
carries the texture every carried mesh names, compared with trailing spaces and NULs
dropped, as well as any whose 4-character id appears in the mesh. The id is not the
name's: Fire's `fai0` sheet draws `"fai01   fai01"`, which is section `fai2`, and its `fa04`
mesh draws `"fai01   fai04"` = `fai3`, so a mix of Fire's `g004` used to arrive without the
texture it draws. Nor is an id always one texture: `ROM/11/21` gives five textures the id
`faid` (`"faida01 faida01"` to `"faida01 faida05"`), `ROM/11/48` two `fire` and two `fir1`,
`ROM/11/122` four `enm1`, and names them apart. Every one a carried mesh names comes along;
the output needs one id per section, so the second (third…) of an id goes out under a new
id that no lane's DAT, the output or a rename uses (`faid` → `fai4`). Nothing has to follow
it, since meshes bind by name, and a generator, ring link or specular op that names a
texture by id still means the first of that id. Two lanes carrying the same one unchanged
share one copy. For the same reason one name has to mean one texture: when two lanes
would put different textures of the same name into the output (a replaced texture and the
other lane's original, or two sources that happen to share a name), the first keeps it and
the later lane's is renamed — a fresh namespace such as `carel3_1`, the localName and
padding kept — and that lane's own copies of the meshes and sprite sheets naming it are
patched to the new name, under ids of their own where another lane uses theirs. The report
lists every such rename (below).

### Folders

A job ability or spell is one DAT: one folder holding every section, then `main`, as
retail's are (Cure III, `ROM/10/11`, is all in `care`). The folder is the recipe's `dir`,
by default the first four letters and digits of `name`, padded with `_`.

A weapon skill is one DAT per race, each laid out like a retail weapon-skill body. Tachi:
Enpi's for Hume female (`ROM/101/76`) is `hf_1 { 1111 {…, main} hf_1 {clips, traces} }`,
and a mix named `LOVE` comes out as:

```text
hf_l/      race root: the race's tag, _, the effect folder's first character (lowercased)
  LOVE/    effect folder (`dir`): generators, linked routines, curves, meshes, textures,
           sprite sheets, sound pointers, then `main`
  (end)
  hf_l/    clip folder, named like the root: the clips, then the weapon traces (0x54);
  (end)    left out when the mix carries neither
(end)
```

- The tags are retail's: `hm` `hf` `em` `ef` `tr` `mt` `gl`. Both Tarutaru get `tr`, since
  retail loads one file for the two. A `dir` that starts with neither a letter nor a digit
  gives a root ending `_0`.
- An emote's waist part (`bow2`, part 2) goes to the skill's **two companion DATs**, not the
  body, exactly as retail lays a weapon skill out — the client reads a weapon skill's waist
  only from the companion, so a waist left in the body froze the mid-body and tore the model.
  Parts 0/1 stay in the body's clip folder; a wildcard ref such as `bow?` resolves each part
  across the body and the companion the client loads alongside it. A motion with no waist
  sibling falls back to a placeholder companion; a weapon-skill motion copies the source
  slot's own companions.
- An emote (or dance) never moves the **weapon hand** — the grip re-parents onto the
  skeleton's right-hand joint reference when the weapon is drawn, and an emote is animated
  for an empty hand — so a drawn weapon would hang frozen while the arms move. A weapon skill
  built from an emote or dance therefore gets two `0x75` ShowHideWeapon tags at the front of
  `main` (verbatim from `ROM/0/0.DAT`'s `hwmg`, the pair a spell cast runs) that stow main and
  sub while it plays; the idle that resumes when the skill ends shows them again. The viewer's
  mixer preview already hides the weapon for an emote motion, so the two now match.
- The folder names are cosmetic. The client finds `main`, clips and weapon traces by type
  and name across every file loaded for the skill, and a routine's own lookups widen from
  its folder to the root (PS2 decompile: `ReverseFindRes`, `YmResourceFile::FindResource`),
  so any `dir` works. Retail's numbered names (`1111`, `11e1`) are explained in
  [weapon-skills.md §3](../anim/weapon-skills.md#the-effect-directory-naming-pattern-a-generator-not-an-oracle).
- A folder's payload (the 16 bytes after its header) is retail's too: `0x20` at byte 3 on an
  effect folder (the byte the client reads), all zero on a race root and a clip folder.
- Only the folders differ from one flat folder: the sections and `main` are the same bytes,
  and `xi ability inspect` reads the same timeline.

## Locks, hits and links

Beside its motion, effects and sound, every retail ability's `main` carries commands that
play nothing you can see but decide how the action lands in game. The mixer shows them on
its last track, *Locks · hits · links*. What each does is read from the PS2 client's
decompile (`ymschdecript.cpp`, `ExecuteTag`, in `research/external/FFXI-PS2`); the PC client
is assumed to do the same.

- **A hit** shows the result: the damage or heal line in the chat log and its number, for
  the next target whose result has not been shown yet. Retail does not write the command
  (`0x2B`, *ShowResult*) itself: it links the shared routine `mdam` of `ROM/0/0.DAT`, which
  is that one command. The server has applied the damage before the animation starts; the
  hit only decides when the player sees it. With no hit the line still comes, late: every
  result not shown yet is shown when the routine ends (`XiSchStatus.cpp`).
- **A lock** is a timer that holds part of an actor for `dur` ticks and lets go by itself
  (or when its routine ends):
  - `0x20` the target's status, `0x56` every target's, `0x1F` the caster's: until it ends,
    the actor ignores the server's death, stance and gear changes, so a target cannot fall
    or disengage before its number shows.
  - `0x2E` control: the player cannot act, and when it ends the body goes back to idle.
    `0x2F` rotation: the caster cannot turn. `0x59` magic: locks the caster's magic and holds
    its status.
- **A link** runs another routine: one of this DAT's own, a shared one of `ROM/0/0.DAT`, or
  one of the actor's own (its race's files). It runs alongside, or (`0x3B`, `0x3C`) the
  routine waits for it to end — its locks wait too. Where the client looks depends on the op:

  | op | runs | looks in |
  |---|---|---|
  | `0x03` | alongside | this DAT, then `ROM/0/0` |
  | `0x3B` | and waits | this DAT, then `ROM/0/0` |
  | `0x09` | alongside, caster and target swapped | the target's own routines, then `ROM/0/0` |
  | `0x57` | alongside | the caster's own routines and the files attached to it (a race-bound weapon skill's body), then `ROM/0/0` |
  | `0x3C` | and waits | the caster's own routines only |

  Retail's shared routines: `mdam` (the hit), `proc` (the added effect or skillchain
  animation, when the result has one: the client turns this empty routine into op `0x80` at
  login, `ymsystem.cpp`), `eis1` / `ei11` (the activation flash on the caster), `hwmg` /
  `hwso` (weapons away to cast, the instrument out), `stnm` (stop the charge circle). A
  spell's cast release `sh<xx>` (`shbk`, `shwh`, `shbl` …) is the caster's own.
- **Flinch** (`0x25` the target, `0x21` the caster) and **knockback** (`0x5E`) are reactions
  the server's result drives: a flinch plays only as hard as the server says the hit was
  (LandSandBoat sends no strength for magic), a knockback in mode 0 only when the server
  sends one.

### What retail puts in `main`

Frames as `xi ability inspect` gives them, which does not count the time a blocking link
waits.

- **Weapon skill** — Fast Blade (`ws:1`): from frame 0 a target-status lock to the hit
  (`0x20`, 100) and a magic and a control lock (`0x59`, `0x2E`, 90); the flash `eis1` at 10;
  the swings, each hit's flinch and sparks in a routine of its own (`hit1` at 60, `hit2` at
  65); the hit `mdam` at 80, after the last flinch; the added effect `proc` at 100. The
  routine runs on to 152.
- **Job ability** — the same shape: locks from frame 0 that end at the hit (`0x2E` and
  `0x59` 90 most often), `eis1` or `ei11`, `mdam` about 60% of the way through, and often
  `0x28` (*ReturnToIdle*) to blend the body back.
- **Spell** — `main` opens with `0x3C sh<xx>` at frame 0, the caster's cast release: it stops
  the casting circle, plays the release burst and motion, and waits on the shared `wash` /
  `waso`, 60 ticks of magic, target and control locks. `main` waits for it, so everything
  after it lands about a second (61 ticks) later in game than its frame says, and the spell
  DAT itself seldom carries a control lock. An area spell locks every target (`0x56`) from
  frame 0 and plays its per-target part between `0x31` (*EachTarget*) and `0x32`
  (*NextTarget*): Curaga's `tgt0`, with its `mdam`, plays once for each target.

Retail ends `0x20` and `0x2E` at the hit (the median is within two ticks of it), and nearly
every retail ability links `mdam` (298 of 300 sampled).

### A command of no lane

A lock, a hit or a link to a shared or actor routine comes from no source, so the mixer
writes it as an event **without `from`**, carrying its own bytes:

```jsonc
{ "op": 32, "start": 0,  "dur": 100, "raw": "200300000000640000000000", "name": "LockActorStatus" }
{ "op": 3,  "ref": "mdam", "start": 80, "raw": "03040000000000006d64616d00000000" }
{ "op": 60, "ref": "shbk", "start": 0,  "raw": "3c040000000000007368626b00000000" }
```

- Compose writes it as given, with the delay (from `start`), `dur` (at +6) and a named
  routine's `ref` (at +8) stamped in. Nothing is gathered for it and no lane's rename reaches
  it. Every race's DAT has it — one built without some lane too — and clearing or
  re-picking a track leaves it alone. Its row in the report's `timeline` has `"from": null`.
- It needs `raw`, which `validate_recipe` reads as the client walks a routine: whole dwords,
  8 bytes at least, as many as the size in byte +1 says (its low 5 bits, in dwords), and
  byte 0 the event's `op` — one wrong size runs every later command together. A command that
  names a routine, sound or generator is 16 bytes at least with 0 at +0xC, where the client
  keeps its pointer once it has looked the name up (anything else would be taken for one),
  and without a `ref` its name at +8 is 1–4 printable characters, padded with NULs. These
  checks hold for every event that carries `raw`, and a `ref` is 1–4 printable characters.
- It cannot be a generator, sound, clip or weapon trace (`0x02` `0x1E` `0x2D` `0x3F`, `0x0A`
  `0x0B` `0x4A` `0x53` `0x60`, `0x05`, `0x2C`): those name a section of their lane's DAT.
- A link to a routine of a track's own source keeps `from` (that track), so compose carries
  the routine, renamed with the lane when a collision needs it. The other way round, a link
  takes a lane's rename only for a routine carried from that lane (see [Compose](#compose)).
- A routine the game will not find where its op looks is written anyway, and the result's
  `warnings` say so: a name in neither this DAT nor `ROM/0/0.DAT` that is not a race schedule
  (`sh??` `ca??` `ss??` `st??` `sp??` `lc??` `ls??`); a race schedule linked with an op
  that never looks on the actor; a `ROM/0/0` routine linked with `0x3C`, which looks only on
  the caster; a routine of this DAT linked with `0x09` or `0x3C`, or with `0x57` / `0x85`
  outside a race-bound weapon skill, which look on the actor and never here. Without an
  install to read `ROM/0/0.DAT` from, only that last one is said.

```text
⚠ link shbk (0x03) at frame 0: 0x03 looks in this DAT and ROM/0/0, never on the caster, so the race schedule shbk is not found; retail links it with 0x3C
```

The schema's fourth example is Fast Blade built this way: its swings, trace, sparks and
sounds from `ws:1`, its locks, flash, hit and added effect by hand.

### Dangers

- **A blocking link to a routine that does not end** — a casting circle `ne??` (1800 ticks),
  a routine started as a loop (`0x73`) or whose section 3 holds op `0x01` — freezes the
  routine and its locks with it, so the player stays control-locked. The client's 1800-tick
  safety release covers status locks only.
- **Lock timing.** A target lock that ends before the hit lets the target die or disengage
  before its number shows; a control lock far past the hit keeps the player stuck. A lock
  with `dur` 0 does nothing. A long lock also lengthens the routine when the recipe sets no
  `total`, since the routine ends at its last window.
- **The hit.** With none, the number shows when the routine ends; before the swings, it
  shows before contact; a second hit on an action with one result shows nothing. A
  multi-hit weapon skill still shows one number: the server sends one total per target.
- **An area action without `0x31` / `0x32`**: only the first target gets its per-target
  part and the others' numbers come together at the end. A `0x32` with no `0x31` before it
  replays the whole routine, locks and clips included, once for each other target.
- **A stand-in** (`0x15` / `0x16` / `0x22` / `0x23`, a copy of the caster or target that the
  rest of the routine plays on) at `main`'s own level, or without its `0x17` / `0x18`: the
  rest of `main` plays on the copy. Retail keeps them inside the routines it links.
- **The op has to suit where the routine is.** `0x03 shbk` finds nothing (retail uses
  `0x3C`); `0x3C` to a name the race does not have does nothing; `0x09` swaps caster and
  target. A `0x03` or `0x3B` link to a name the mix also carries runs the carried routine,
  since this DAT is searched before `ROM/0/0` (`hit1`, `hit2` and `tgt0` are names in both).
  `0x09`, `0x3C` and `0x57` never look in a job ability's or spell's DAT, so the carried
  routine does not play (`0x09` and `0x57` run `ROM/0/0`'s of that name, if any); a race-bound
  weapon skill's `0x57` does find it.
- **Reactions follow the server**: a flinch on a spell, or a knockback in mode 0, can show
  nothing in game.
- **Not by hand**: the random and conditional blocks (`0x3D` / `0x3E`, `0x64`–`0x6B`),
  `0x24`, a `0x73` loop without its `0x85`, and the world ops (`0x52`, `0x58`, `0x7C`–`0x7E`).

## Generator edits

A recipe can change the particle generators and keyframe curves it carries: tint, scale,
spawn rate, lifetime, a texture swapped for another in the same DAT, the shape of a fade.
The edits live in the recipe, and compose writes them into **its copy** of the section
(`xi.ability.xi_genedit`); the source DAT is only read, and a recipe without the two keys
composes exactly as before.

```jsonc
"generators": [
  { "lane": "vfx", "ref": "g000",                 // the generator's name in the SOURCE DAT
    "edits": [
      { "sec": 2, "op": "0x16", "at": 4, "type": "u8",  "value": [51, 128, 198, 38] },  // tint R,G,B,A
      { "sec": 2, "op": "0x0F", "nth": 0, "at": 8, "type": "f32", "value": [0.75, 1.5] }, // scale Y, Z
      { "sec": 0, "at": 118, "type": "u16", "value": 1 },                                // spawn interval (0x76)
      { "sec": 0, "at": 121, "type": "u8",  "value": 0, "mask": 16 }                     // clear autoRun, keep the rest
    ] }
],
"curves": [
  { "lane": "vfx", "ref": "k001", "keys": [[0, 0], [0.5, 0.8], [1, 0]] }   // [time, value], same count as the source
]
```

**Addressing.** An edit names bytes by where they sit in the format, never by file offset,
so it lands on every race's copy of a weapon skill (`ws:N` composes once per race from
that race's DAT).

- `sec: 0` is the generator **header**. `at` is the byte offset from the section start
  (the spawn interval is at `0x76` = 118), and `0x10 <= at`, `at + size <= 0x80`. No `op`.
- `sec: 1`–`4` are the four **op streams** (1 generator updaters, 2 initializers,
  3 updaters, 4 expiration handlers). The op is found by walking the stream from its
  offset at section `+0x80` (config dword: `op = & 0xFF`, size in dwords `= (>> 8) & 0x1F`
  including the config dword; op 0 ends the stream), and `nth` (default 0) picks among
  repeats of one opcode. `at` is the byte offset from the op's config dword, `4 <= at` and
  `at + size <=` the op's size. `op` is an integer or a hex string, as on an event.
- `type` is `u8`, `u16`, `u32`, `i16`, `i32`, `f32` or `datid`; `value` is one value or a
  list of consecutive ones. Writes are little-endian, the size of what they replace, and
  **absolute** — composing twice gives the same bytes. `mask` (integer types) writes only
  those bits, for a flag inside a bitfield.
- `datid` writes a 4-character section id, padded the way the DAT pads it. The section has
  to be a texture, mesh, sprite sheet, curve or sound pointer **in that lane's DAT**;
  compose walks the edited generator, so the newly named resource and what it names (a
  mesh's textures) are carried, and what is no longer named is not.
- Never writable: the 16-byte section header, the stream-offset table (`0x80`–`0x8F`), an
  op's config dword. Nothing can grow: adding or removing an op, or a curve key, is a
  different section size and is out of scope.

`uv run xi fx json <dat> --opcodes` lists each stream's ops in order with their payload as
hex; payload byte *k* of an op is `at = 4 + k`. Field layouts: [`../fx/effects.md`](../fx/effects.md),
[`../fx/effect_system.md`](../fx/effect_system.md) §4–5.

**Curves.** `keys` replaces the `[time, value]` pairs of a `0x19` ParticleKeyFrameData
section; there must be as many as the source has. `time` is a fraction of the particle's
life (0..1). The last key keeps the source's time (1, which ends the curve) whatever the
recipe says, and no earlier key may have time 1. A curve is **shared**: every generator of
that lane that names it plays the new keys (in Cure III nine of thirteen curves serve more
than one generator). A progress updater replaces the first key's *value* with the
particle's own starting value, so that key often has no visible effect.

**Lanes keep their own edits.** Edits are addressed by lane + source name and applied
before the section joins the output, so two lanes on the same spec each carry their own
copy: the second is renamed (`g000` → `g002`, reported in `renames`) and what neither lane
changed is carried once. That is how one generator plays twice with different edits:
declare a second lane on the same spec. A name collision's rename does not disturb the
addressing, and a generator fired only inside a carried linked routine is edited the same
way.

**Errors.** `validate_recipe` (Check, compose, publish, `dats prepare`) refuses unknown
keys, a lane that is not in `sources`, a `ref` that is not 1–4 printable ASCII characters,
two entries for one lane + ref, an empty `edits`, and any `sec` / `op` / `nth` / `at` /
`type` / `value` / `mask` that is mistyped or out of range, naming
`generators[i].edits[j]` or `curves[i]`. With the DAT open, compose refuses a generator or
curve the lane's DAT does not have, an op or `nth` that is not in the stream, a write past
the op, a key count that differs, and a `datid` the DAT has no section for. An entry whose
generator exists but is never fired is simply unused.

**What is safe to edit.** Layouts confirmed by at least two independent sources (xim and
the viewer's port of it, the PS2 decompile, xi-tools' own writers, in-game checks):

| Where | Field |
|---|---|
| header `0x76` u16, `0x74` u16 | spawn interval (the engine adds 1) and its variance |
| header `0x78` u8 | particles per spawn, 0–255 |
| header `0x79` u8, mask `0x10` / `0x04` | autoRun, continuous singleton; leave bit 0 alone |
| header `0x10` u16 | attach type (low 4 bits) and joints (bits 4–9, 10–15): use a mask |
| sec2 `0x01` StandardSetup | position `+0x14` 3×f32, life `+0x22` u16 (ticks), life variance `+0x24` u16, billboard / render flags `+4` / `+6` u16; link id `+0x0C` datid with its type `+0x21` u8 |
| sec2 `0x02` `0x03` `0x08` `0x0B` `0x0C` `0x12` `0x13` `0x31` `0x41` | velocities and their variances, `+4` f32 (×3 where the op is 4 dwords) |
| sec2 `0x06` `0x07` `0x1F` | spawn shell: variance `+4`, base radius `+8` f32 |
| sec2 `0x09` `0x0A` (radians), `0x0F` `0x10` `0x11` | rotation, scale and their variances, `+4` f32 |
| sec2 `0x16` | colour `+4` 4×u8 **R,G,B,A**; `0x80` is neutral, `0xFF` doubles a channel |
| sec2 `0x17` `0x18`, `0x19` `0x1A` (4×i16), `0x1E` | colour variance, colour-change rate, blend byte `+4` and alpha `+5` |
| sec2 keyframe ops (`0x21`–`0x2F`, …) | curve id `+8` datid; cycle count in bits 5–13 of `+0x0C` (mask `0x3FE0`) |
| sec3 `0x03` `0x06` `0x09`, `0x26`, `0x27` `0x28`, `0x29`–`0x2B`, `0x2C`, `0x2E` | acceleration, velocity rotation, UV scroll, oscillation, drag, draw distance |
| sec1 `0x0A` | draw distance `+4` f32 |
| `0x19` curve | key values, key count unchanged |

Leave alone: header `0x30`, `0x60`, `0x68`–`0x73`, `0x7C` and the eight floats at `0x40`
(runtime or undecoded); the zero pointer slots at `+4` / `+8` inside keyframe and child
ops; op `0x01` `+0x20` (the per-particle work size) and `+0x26`–`0x2F`; the alloc slot in a
config dword; anything only one source describes. Generators of the shared `ROM/0/0.DAT`
(the routines a spell links by name, such as `mdam`) are never carried and cannot be edited.

Covered by `tests/test_ability_genedit.py` on synthetic bytes, plus the schema's second
example composed against retail Fire.

## Textures

A recipe can replace a lane's textures with PNGs. Compose encodes each one into **its copy**
of the `0x20` section before it joins the output; the source DAT is only read, and a recipe
without the key composes as before.

```jsonc
"textures": [
  { "lane": "vfx",            // a key of `sources`
    "ref": "ho",              // the 0x20 section's 4-character id in the SOURCE DAT
    "name": "carel3  ho",     // optional: its 16-character name there (trailing spaces dropped)
    "png": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg…" }   // or "gold.png", beside the recipe
]
```

- **An entry is one `0x20` section**: the one with that `ref` and `name`, or, without
  `name`, the first with that `ref` (every recipe written before names were). A DAT can give
  one id to several textures but never repeats a name — `ROM/11/21`'s five `faid` are
  `"faida01 faida01"` to `"faida01 faida05"` — so a `name` is how to reach the second and
  later ones, and the model viewer writes it. `name` is 1–16 printable ASCII
  characters, compared with trailing spaces and NULs dropped, as a mesh's binding is.
- **One entry per lane + ref + name** (no name is a value of its own), and it applies to
  **every generator of that lane that draws the texture**, on every race's copy for a
  race-bound lane: the section keeps its id, its 16-character name and its flag bits, so
  each mesh and sprite sheet that names it draws the PNG (Cure III's `"carel3  ho"` is
  drawn by the `ho` mesh of `ho00` and `ho01` and by the `hp` mesh of `sh01`). A second
  texture of an id keeps its name and flag bits too; the
  id it goes out under is a new one ([Compose](#compose)). A texture of the shared
  `ROM/0/0.DAT` is never carried and cannot be replaced.
- **`png`** is the file's bytes as a `data:image/png;base64,` URI (standard, padded base64;
  what the model viewer writes), or a path ending `.png` relative to the recipe file and
  inside its folder. `load_recipe` reads a path and puts the data URI in its place, so
  compose, `plan` and `dats build` only ever see data URIs, and `dats prepare` stores that
  self-contained recipe under `projects/resources/ability/` (a recipe already holding data
  URIs is copied byte for byte).
- **Alpha is full scale**: 255 is opaque, as the viewer shows a texture. FFXI stores alpha
  at half scale (`0x80` opaque; the engine doubles it at draw), so compose halves it.
- **Size.** Each side is resized to the nearest power of two from 4 to 256 (the smaller on a
  tie), independently: every retail effect texture is a power of two, 256 at most. A
  non-uniform resize is fine for a mesh — particle UVs are normalised, so a mesh draws any
  size the same way — but a **sprite sheet is an atlas** (Cure III's `shp1` draws 16 frames
  from `"carel3  shu"`): a replacement has to keep the cells where the sheet's UVs expect
  them, so give it the original's proportions and layout.
- **Encoding** is the importers' own: `texconv` (DirectXTex, `TEXCONV_PATH`, by default
  `misc/texconv.exe`) to DXT3, one level, no mips — the format of nearly every retail effect
  texture. Each distinct PNG is encoded once per compose. Without texconv compose stops and
  says where to put it; when texconv fails (it will not start, or exits with an error),
  compose stops with texconv's own reason.
- **Shared by name.** The client binds a texture by its 16-character name, first match in
  the file winning. When another lane already puts a different texture of that name into
  the output — the same spec on a second lane without the replacement, say — the later
  lane's is renamed and its meshes patched ([Compose](#compose)), so each lane draws its
  own. The report gives the name each one went out under.

**The report.** Every `--json` entry (and `<name>.report.json`) carries `textures`: one row
per replacement the recipe asks for, then one per texture renamed to keep its name its own.
A row is one section of the lane's DAT: `ref` is its id there and its name there is
`renamed_from` when it was renamed, else `name` — lane + ref + that name (trimmed) is what
an entry addresses, so a row matches its entry even among textures that share an id.

```jsonc
"textures": [
  { "lane": "b", "ref": "ho",
    "name": "carel3_1ho      ",               // the 16-character name in the output: what a mesh binds
    "from": { "w": 64, "h": 64, "format": "DXT3" },   // the source section
    "to":   { "w": 32, "h": 16, "format": "DXT3" },   // the output's
    "renamed_from": "carel3  ho      ",       // only when renamed
    "users": ["ho02", "sh01"] }               // the generators drawing it, as the output names them
]
```

A generator is a user only when it draws the texture: its linked mesh or sprite sheet binds
the name, or its ring link or a specular op names the section. It carries more than it
draws — Fire's `g004` brings the `0x20` `fai0` along with its `fai0` sheet, which draws
`fai2` — and those extras do not count. A replacement nothing the mix fires draws has
`"users": []` and `"unused": true`, carried or not; when nothing carries it, its `to` is the
size it would be encoded at. `name` is what the model viewer keys a texture by
on its stage (trimmed), so a live swap targets the source name unless the report says the
texture was renamed.

**Errors.** `validate_recipe` refuses unknown keys, a lane that is not in `sources`, a `ref`
that is not 1–4 printable ASCII characters, a `name` that is not 1–16 printable ASCII
characters (or is only spaces), two entries for one lane + ref + name, and a `png` that
is neither form: a data URI with another prefix, broken base64, more than 2 MiB, no PNG
signature, or an IHDR side outside 1–4096; an absolute path. `load_recipe` refuses a path
that leaves the recipe's folder or a file that is not such a PNG. With the DAT open, compose
refuses a `ref` the lane's DAT has no texture for, a `name` none of that id's textures has
(the message lists theirs), and two entries on one section (one naming the first texture of
an id beside one naming only the id), naming `textures[i]`.

Covered by `tests/test_ability_textures.py` on synthetic bytes (texconv stubbed except where
the test is about it), plus the schema's third example — Cure III's `ho` as a gold dot —
composed against retail, and `ROM/11/21`, `ROM/11/48` and `ROM/11/122` composed with every
generator fired: each texture a generator draws is in the output with its own pixels.

## Publish

> **Running out of numbers?** The counts above are what the *client* can reach:
> the animation number becomes a DAT file id through fixed arithmetic per kind, and
> the ids that arithmetic lands on are mostly taken by other content. A client-side
> plugin can patch it so numbers at or above a threshold resolve into a reserved
> region instead, and the publisher allocates there once the numbers any client loads
> are used up. The bands are **on by default with cexislots' values** (weapon skills
> 272–527, job abilities 500+, spells 1612+; `FX_*_BAND_*` in `xi_config.py`): set them
> in `.env` only for a different plugin, and set a band's `FIRST` to `0` to switch it off
> for a stock client. The plan prints a `⚠` line when the number it took needs the plugin.
> Band numbers are built with `--pivot`: their file ids sit past the expanded tables, the
> plugin merges them from the XIPivot overlay's ROM10 tables, and the build grows that pair
> (to 437,488 entries) the first time — the install's tables are never touched for it.
> `xi ability slots [--pivot]` lists every weapon-skill number and what holds it, and
> `--animation-from N` starts the automatic number at `N` (272 = straight to the band).

Publishing is an **`xi dats` action** (`type: "ability"`, schema
[`schema/ability.json`](../../schema/ability.json)), so a published ability sits in a
project manifest beside any gear, mount or entity actions, is rebuilt from Git by
`dats build`, listed by `dats changelog` and reverted by `dats undo`. Three ways in:

```bash
# 1. the wizard — pick "Ability" as the content type
uv run xi dats new

# 2. straight arguments — every parameter has a default the build fills in
uv run xi dats prepare exports/ability/mixer/tiger_fury.mix.json --project tiger_fury --replace \
    [--kind ja|spell|ws] [--animation N | --animation-from N] [--subdir 20]
uv run xi dats build tiger_fury --dry-run          # the plan: slot, file ids, server SQL
uv run xi dats build tiger_fury                    # add --pivot to build into FFXI_PIVOT_DIR

# 3. the shortcut — exactly 2., in one command
uv run xi ability publish tiger_fury.mix.json [--project NAME] [--dry-run] [--pivot]
```

`prepare` copies the mix to `projects/resources/ability/<slug>.mix.json` (one whose
`textures` name PNG files is stored with each PNG inlined as a data URI, so the copy
rebuilds on its own; a manifest that still names an older `<slug>.recipe.json` copy builds
from it, and a re-prepare leaves that file where it is) and writes:

```jsonc
{
  "id": "ability.tiger_fury", "type": "ability",
  "kind": "auto",                              // auto = from the recipe (table below)
  "target": {"animation": "auto", "subdir": 20},
  "resources": {"recipe": "ability/tiger_fury.mix.json"},
  "server": {"emit": true}                     // <slug>_<animation>.sql in the publish folder
}
```

`build` composes the recipe (one DAT, or body + two companion DATs per race for a
weapon skill), takes the animation number, places the DAT(s) under `ROM10/<subdir>/`
in the base install and registers the file id(s) — the same verbatim placement and
table patching every other action uses, `.base` backups included, then syncs the
custom table region into the pivot overlay. With `--pivot` it places and registers in
`FFXI_PIVOT_DIR` instead and skips that sync. A pivot folder that carries its own `ROM10`
tables (CatsEyeXI's does) hides a base-install publish from its client: the client reads
that folder's `ROM10` tables, and the sync copies only ids above retail, which ability
file ids never are — so publish with `--pivot` for such a client (see
[which table registers a file_id](../dats/README.md#which-table-registers-a-file_id)).
The allocation is recorded on the action (`result`: kind, animation, placements), so a
rebuild lands on the same slot and `dats undo` knows what to clear. A running client
holds the file tables in memory: publish with it closed, or restart it afterwards.

| Kind | When | Client lookup | Custom numbers | Server |
|---|---|---|---|---|
| `ja` | no lane is race-bound, or the recipe / action says `ja` (a race-bound motion is then baked from one race, below) | `file_id = 4412 + animation` | first free from **339** (retail band 4412–4750 is full) | `abilities.animation` |
| `spell` | the motion lane is a `spell:N` (or `target.kind` says so); one DAT for every race | `file_id = 0xAF0 + animation` — the client has no spell table, `spell_list.animation` rides in the magic-finish action packet | first **unregistered** id from **1012** (retail reaches 1011; the ids above are shared with other content, 92 free up to 1611) | `spell_list.animation` |
| `ws` | a lane is race-bound: `ws:N`, or a motion from a race's own animation files (below) | per-race extended bank, slots 256–271 | first slot whose body DAT is a retail dummy on every race (**264–271**) | `weapon_skills.animation`, or a `mob_skills` row with id < 256 |

### Motion from a race's own animation files

A lane whose source is a PC motion DAT — an emote, a battle pack, a dance, a weapon-skill
file picked from the character list — is mapped to every race through the client's own
per-race motion tables in FFXiMain.dll (`base[race] + index`, the lookup the client
uses; `xi.entity.anim.xi_motion_tables.motion_slot_for`). Hume Female's emote file
`ROM/37/13` is Galka's `ROM/61/8`, and so on. Such a recipe composes once per race, and
each race's DAT carries that race's own clips, an emote's waist part from its `+6`
sibling included. A race with no copy of the file, or no clip by that name, is built
without that motion, and compose, `dats build` and its dry run say so:

```text
⚠ Galka: motion2 (ROM/173/48.DAT) is HumeFemale only; built without its 3 events
```

The proven route for such motion is `ws`: one DAT per race, and with a client-side plugin's
custom band (cexislots: numbers 272–527, see *Running out of numbers?* above) there is room
for hundreds.

**Experimental — a job ability or spell with a baked motion.** Asked for `ja` or `spell`,
compose bakes the motion from one race's copy into the single DAT (`_lanes` resolves the lane
to that race — the `--race` given to `compose`, HumeMale by default — and carries its clips,
an emote's waist sibling included). This is **not verified in game** and there is no retail
precedent: no spell or job ability carries caster clips (Blue Magic's `wz*` clips animate the
monster it shows; Maelstrom's caster plays `ma2?` from its pool), the per-race copies of one
clip do not even share a joint count (`bow0` is 14 joints on Tarutaru, 16 on Hume and Elvaan,
24 on Mithra and Galka), and the PS2 client's PlayClip (`ymschdecript.cpp`, tag `0x05`)
resolves a wildcard ref such as `bow?` only from the actor's loaded motions — only an exact
ref (`bow0`) searches the routine's own DAT. Two cases differ:

- **The race base** (the first five files of the `movement` table: idle, walk, cast and
  job-ability motions such as `cm0?`, `mb0?`) is always loaded on a character, so its
  clips are named, not carried, and a job ability or spell can use them. A race whose
  base lacks the clip gets a warning.
- **A file the tables do not index** that the character list gives to some races only
  (a race's Variations files) is built for those races, with the warning above for the
  rest.

For `ws`, the three DATs per race (the body, laid out as in [Folders](#folders), and waist
packs A and B) are placed and registered; the companions are copied from the motion
source's own slot for that race. In the retail banks Tarutaru male and female share one
bank row and are registered once; in the custom band every race has its own three file ids
(24 per number).

A weapon-skill slot built from motion that is not itself a weapon skill has no source
companions to copy: its waist clips ride in the body DAT. On a retail slot (264–271) the
slot's own companion ids keep pointing at retail's placeholders. A custom-band number has
none, so each race's two companions are retail's placeholder instead: slot 0's, a 160-byte
`[dumm]` DAT that is byte for byte the same on every race, copied and registered like the
body. The client loads a companion only for body gear with a waist variant; what the PC
client does with a companion id that is not registered is unknown (the PS2 client waits
for every file it attaches before `main` runs), so the ids are filled the way retail fills
every empty slot.

`dats build --dry-run` prints the plan: kind, animation number, every file id with its
current occupant, the [publish folder](#the-publish-folder) it would write and the SQL to
add. The mixer shows this plan before it asks to confirm. **Restart the client** after publishing — it caches the file tables at
startup.

The client accepts custom numbers of all three kinds from the ROM10 overlay: job abilities
past retail's 338, weapon skills in the extended slots, and spell animations past 1011.
The quickest in-game check needs no database change — on a LandSandBoat server the
`!injectaction <category> <animation>` GM command sends the action packet directly
(category 6 job ability, 3 weapon skill, 4 spell). Target a mob or NPC first: the server
takes the client's *face target*, which is not sent for yourself, and ignore the message
line the command prints (it is hardcoded). Publish with the client closed, or restart it
afterwards: a running client holds the overlay's file tables in memory and can write them
back over a registration made while it runs.

### The publish folder

A publish (not a dry run) also leaves everything it made in one folder,
`projects/abilities/<slug>/` under the folder `xi` runs in — `<slug>` is the action
id's last part, the recipe name in lowercase with anything but a–z and 0–9 made `_`
(`ability.love` → `love`):

```text
projects/abilities/love/
  LOVE.mix.json          the mix the model viewer published (check.LOVE.mix.json: its last Check)
  love_300.sql           the server SQL the build printed
  love_300.applied.sql   what ran against the database (--apply-db only)
  placements.json        what each DAT copy is (schema/ability_publish.json)
  ROM10/28/2.DAT         a copy of every DAT the publish placed, at its ROM path:
  ROM10/28/0.DAT …         the bytes written to the target, companions included
```

The ROM paths let the folder be dropped into a DAT overlay or a release package as it is.
Copying the DATs does not register them: `placements.json` lists what to register —
`{schema, id, name, kind, animation, placements: [{race, role, file_id, dat}], server}`,
the same placements the action records in `result`, and `server` the SQL's file name
(null when the action's `server.emit` is false, which writes no SQL).

The folder holds the latest publish only. Before writing, a publish removes what the
previous publish of the action put there and this one does not: the DATs and SQL its
`placements.json` lists, any `<slug>_<number>.sql` and `<slug>_<number>.applied.sql` of
another animation, and the folders that leaves empty. Anything else in the folder (notes, a
zip, the mix files `*.mix.json` / `check.*.mix.json` / `*.recipe.json`) is left alone. A
copy that already holds the right bytes is not written again, so building into a second
target copies nothing twice. `dats undo` clears the target, not this folder. Earlier
versions wrote this folder as `projects/server/abilities/<slug>/` (and before that the SQL
straight into `projects/server/abilities/`); those are not moved or read — delete them once
the mix is republished.

The folder is written after the DATs are placed and registered, so one that cannot be
written (a read-only copy, a file in use) does not fail the build: it finishes, records its
result and warns `publish folder not updated`. An action id whose last part is not a plain
folder name (a–z, 0–9, `_`, `-`; a hand-typed `--id` can be anything) is refused before
anything is placed.

## Database Update, Client Menu Record, Lua Stub

Publishing places the DATs: **how the ability looks**. Three more steps of the same build do
the server side, each opt-in (the model viewer's Manage switches, **per mix** and off again
after an import, rename or duplicate):

```bash
uv run xi ability publish LOVE.mix.json --kind spell --apply-db --clone-from fire --menu-record --lua-stub [--dry-run]
uv run xi dats build LOVE --apply-db --clone-from fire --menu-record --lua-stub          # the same, on the action
```

| Switch | Option | What it does |
|---|---|---|
| Database Update | `--apply-db` (`--db-row ID` to confirm) | points the server row named after the mix at the new animation, or, when there is none, inserts one cloned from the kind's **default donor** (spell → `cure`, ja → `berserk`, ws → `fast_blade`) |
| Client Menu Record | `--menu-record` (`--menu-name TEXT`) | the client's menu entry — the spell / command record and its names — at the same id as that row, so players can use it |
| Lua Stub | `--lua-stub` | the server script for a row this mix created: **what the ability does**, handed to the donor's script |
| Server id | `--server-id ID` | the id for a new row and its menu record (default: the highest that fits, below) |

The donor a new row/record/stub clones is the kind's default (`DEFAULT_DONOR` in
`xi.server.xi_db_apply`): `cure`, `berserk`, `fast_blade`. It is a working row that carries
the animation so the new one plays and can be used; a developer edits its real stats
(MP, cast time, jobs, damage, …) in the database afterwards. `--clone-from X` (an id, or a
server name: `fire`, `Fast Blade`, `fast_blade`) overrides it; the model viewer has no field
for it. Once this mix has created a row, that row's donor is kept.

**Where the server is.** xi-tools' `.env`: `XI_DB_HOST`, `XI_DB_PORT`, `XI_DB_USER`,
`XI_DB_PASSWORD`, `XI_DB_NAME`, and `XI_SERVER_DIR` (the LandSandBoat checkout the stub is
written into). The model viewer's **Settings › Local Server** edits exactly those lines of
that `.env`; the server's `settings/network.lua` is never read. With none of `XI_DB_HOST`,
`XI_DB_USER` or `XI_DB_NAME` set there is no database: `db: skip — no database configured`.
Credentials never go on the command line, and no output, result or file holds the password
(a result names the server `database@host:port`). `xi server check` reports the setup.

**What it prints.** One line per step, which the viewer reads, then the warnings:

```text
     db: insert spell_list #1023 'love' like #144 'fire' animation 1100 (xidb@127.0.0.1:3306)
     menu: place spell 1023 'LOVE' like spell 144, menu index 973, in pivot (replaces the blank placeholder)
     lua: write scripts/actions/spells/black/love.lua (calls black/fire #144 at run time)
     ⚠ db: learn it in game with !addspell 1023
     ⚠ db: restart the map server (xi_map): it reads spell_list only at startup
     ⚠ menu: restart the game client to load it (114.DAT is read at start-up)
```

The ops are `db:` update, insert, unchanged, needs-confirm; `menu:` place, unchanged;
`lua:` write, rewrite, unchanged, kept — and, for any step, `skip`, `refused` or `error`,
whose text after ` — ` is the reason. A dry run (Check) puts `would` after the step
(`db: would insert …`) and only reads: SELECTs, `information_schema` and files. **None of
it fails the publish**: the exit code is 0 whenever the DATs were placed, and a real build
that wrote a row ends with `database: 1 row written (spell_list #1023) — restart the map
server (xi_map) to load it`.

### Database Update

- **The row.** `spell_list`, `abilities` or `weapon_skills` by kind, the row whose `name` is
  the mix name in lower case. Only `animation` is written — never `animationTime` or `name`
  — by one statement keyed by primary key and guarded by the value just read; a row that
  changed in between is `error — the row changed while publishing; publish again`.
- **Confirm once.** A row this mix created, or one it was confirmed for, is *ours*: later
  publishes update it without asking. Any other row with the mix's name is `needs-confirm`
  and nothing changes until you confirm it — `--db-row <id>` (Manage › **Confirm**) — which
  is recorded (`result.db.confirmed`), so it is asked once.
- **The donor.** A mix with no server row inserts one cloned from the kind's default donor
  (`cure` / `berserk` / `fast_blade`), or from `--clone-from` when given. The new row copies
  every column of the donor's — jobs, group, MP, cast time, `animationTime`, recast… — except
  the id, name and animation (`content_tag` is NULL), so it plays and works until a developer
  edits its stats. A created row keeps its donor for good: it is also the donor of the menu
  record and the stub, and a different `--clone-from` later only warns (undo first to clone it
  from something else). A created row that has since vanished (a dbtool re-import) is
  re-inserted at its id from the same donor, with a warning. If the default donor is missing
  from the database it is refused (name one with `--clone-from`); donors that can't be cloned
  are refused too: a spell of group 0 (no script folder), blue magic or a trust; a pet or
  non-player job ability.
- **The id** — one number for the row and its menu record: blank in the client (a retail
  placeholder, below), free on the server (no row, and for a spell ≥ 896 no trust pool at
  id + 5000) and used by no other project in `projects/`, the highest first. Server id
  (`--server-id`) picks one under the same guards. The server takes spells 1–1023, job
  abilities 16–511 (not 55 or the pet abilities 353–355) and weapon skills 1–255, and the
  client has blank rows at only **6 spells** (1023, 1022, 1021, 1020, 1001, 1000), **116 job
  abilities** (511 down to 396) and **12 weapon skills** (237, 236, 223, 207, 206, 205, 143,
  127, 111, 95, 79, 63) — the same in the install and in CatsEyeXI's pivot overlay.
- **Type.** While a mix still owns a row, a menu record or a stub made under another Type
  (spell / job ability / weapon skill), every step is refused: `refused — this mix created
  spell_list #1023 when its Type was spell; set the Type back, or undo it first (xi dats undo
  LOVE --apply-db)`.
- **Weapon skills above 255** need the server widened first (below); until then such a
  publish refuses the database step. 0–255 fits the stock column and is published like any
  other kind.
- **Afterwards.** Restart the map server: it reads these tables only at startup. A new spell
  is learned with `!addspell <id>`. A cloned job ability goes live for every job and level
  its donor is for, and shares the donor's recast timer on the server and in the client.
- **What ran** goes into the publish folder as `<slug>_<animation>.applied.sql`: the
  statements, safe to run again on another server (keyed by primary key; an insert is a
  `DELETE` of that id and name, then the `INSERT … SELECT`). A dbtool re-import of the stock
  SQL drops inserted rows; the next publish puts a created one back, or run this file.

### Client Menu Record

- **The record.** Spell N is `mgc_[N]`, job ability A is `comm[A + 512]`, weapon skill W is
  `comm[W]` in `ROM/118/114.DAT`, plus its EN and JP name (help is `.`). It is a copy of the
  donor's record (MP, cast time, levels, icons, targets) with the id changed. The name is
  **Menu name** (`--menu-name`), else the mix name with `_` as a space: a `'` is fine, a
  control character or a line break is refused, and it holds 99 bytes for a spell, 39 for a
  command (a Japanese character counts 2). A spell takes the next free menu index, which
  must be below 1024 (what the game lists without cexislots), and keeps it on rebuilds.
- **Only a blank row.** It writes over a retail *placeholder* — both names `.` and the band's
  canonical reserved shape — or over the record it wrote itself, never a named row, whatever
  `--force` says (that flag is for the animation slot), and never past the table's end.
  Every build re-checks the row: one a retail update has taken since is refused and left
  alone (an id no server row is bound to yet is simply re-picked).
- **Which client.** It writes into the root being built: the install, or the pivot overlay
  with `--pivot` (*Use Pivot Folder*). A client with PIVOT on reads the overlay's own
  `114.DAT`, so a build without `--pivot` warns when the overlay has one.
- **Without Database Update** it still finds the row named after the mix, with SELECTs only,
  when a database is configured, and puts the record at that row's id (warning when the row
  is not this mix's; nothing on the server changes). With no database the id is
  **provisional** (`· provisional — database not checked`): the next publish with Database
  Update keeps it if it is free. A weapon-skill record is placed only with Database Update
  (a `weapon_skills` row at the same id).
- **Afterwards.** Restart the game client (114.DAT is read at start-up). `dats package` and
  `dats release` ship `114.DAT` and the name tables with the ability.

### Lua Stub

- The server looks an action's behaviour up by the row's name —
  `scripts/actions/spells/<group folder>/<name>.lua`, `…/abilities/<name>.lua`,
  `…/weaponskills/<name>.lua` — so a new row has none and cannot be used. The stub hands
  every call to the donor's script at run time: it is **what the ability does**, copied from
  the donor (the default `cure` / `berserk` / `fast_blade`, or `--clone-from`); the published
  DAT is only **how it looks** (and the cast's wind-up belongs to the spell's type, not either).
- It is written only for a row this mix created (Database Update on), into
  `XI_SERVER_DIR`, in place (the server reloads a changed script; restart it for a new
  row), and never creates a folder. Its header carries the line `-- xi: mixer lua stub
  (xi dats build --lua-stub); delete this line to keep your own edits` and a hash of the
  body: a stub edited by hand is `kept`, never overwritten; delete that line to own it, or
  delete the file to get it regenerated. A file of that name xi didn't write, a script of
  the same name elsewhere in the kind, a name a server module uses, a loader folder name
  (`black`, `pets`…), a donor with no script of its own, and corsair rolls / dancer steps
  (which look their numbers up by ability id) are refused.
- A spell stub also lends the new spell id the donor's rows in the spell helpers' private
  per-spell tables (the damage tables and the like, keyed by spell id — without them most
  cloned black, white, song, ninjutsu and geomancy spells error when cast). **This has not
  been tried in game yet**, and every spell stub write says so: cast it once; "attempt to
  index a nil value" in the map server log means it failed — then clone a self-contained
  spell (cure, raise, meteor or a summon).

### Weapon skills above 255 (`xi server ws-widen`)

Custom weapon skills use animation numbers 264–527 (the cexislots plugin reads 272–527).
The server can't carry those yet: `weapon_skills.animation` only stores 0–255, and xi_map
reads it as an 8-bit number in four places in its C++. So a published weapon skill would
play its number minus 256 — 300 plays as 44, a retail motion. The patch makes those four
spots 16-bit and the SQL widens the column; retail weapon skills (all below 256) are
unaffected.

```bash
uv run xi server ws-widen [--out DIR] [--print] [--json]
```

writes `900-xitools-WsAnimation16.patch` (diffed from your own checkout), the idempotent
`ws_animation_16bit.sql` and a README into `projects/patches/ws_animation_16bit/`: apply
the patch to your server checkout (`git -C <server> apply …`), run the SQL, rebuild xi_map
and restart it. It only reads the server's source — never edits it, never touches the
database, never builds. The model viewer's **Settings › Local Server › Weapon skills › Get
the C++ patch** runs it and opens the folder. A weapon skill above 255 then publishes to the
database only when the column is wide and xi_map was rebuilt with 16-bit reads (read from
`xi_map.pdb`; with no PDB it goes ahead with a warning); `xi server check` shows each part.

### Undo, and what is recorded

Each step records what it did on the action — `result.db` (the row, whether this mix
created or confirmed it, its donor, the animation it had before), `result.menu` (the id,
and per root the record written and what it replaced) and `result.lua` (the path and a
hash) — see [`schema/ability.json`](../../schema/ability.json). Every later build carries
them over, with or without the switches. `xi dats undo <project>` puts back the placeholder
in each root (only while the row still holds the record written there);
`--apply-db` also deletes a created row, or puts the old animation back, and deletes the
stub while it is unedited (that needs only `XI_SERVER_DIR`). Without `--apply-db` the undo
prints the revert SQL and the stub path and leaves them, and keeps the manifest — its cleared
actions marked `undone` — until an `undo --apply-db` removes them. A row that is already
gone (a dbtool re-import) or already holds its old animation counts as done; only a row
that still holds other values is left. A menu record that can't be put back (the game has
`114.DAT` open) keeps the manifest too, and the next undo tries it again.

## Catalog (the viewer's pick list)

`xi mv update --only abilities` rebuilds `mv/lists/abilities.json`: every job ability
(band 0–338, named from `abilities.sql`), spell (`xi.spell`) and weapon skill (both
banks, per-race paths, named from `weapon_skills.sql` and humanoid `mob_skills.sql`),
each with its generators (audio ones separately), sound pointers, clips and total
frames. Dummies and DATs without `main` are skipped. ~1,500 entries, about a minute.
It ships with the viewer like every other list and reaches installs through the lists
manifest (see [../mv/README.md](../mv/README.md)); the mixer's *Build catalog* button
runs the same target into the viewer's own lists folder when the list is missing.

The file also carries a `base_motions` list (`xi.ability.xi_catalog.build_base_motions`):
the curated cast and job-ability motions the always-loaded race base offers — black/white/
blue magic, ninjutsu, summoning, item use, generic job ability, plus Bard songs, ranged
(bow/marksmanship) and Geomancy — each a clip group (`mb0?`, `mw0?`, `cm0?`…) read from the
real race base, with the base DAT per race for preview. The game does not name these clips;
the song/ranged/geomancy labels follow the ranged schedule that plays each (`lc<NN>`, NN the
weapon's RangeType: 00 singing, 01 wind, 02 string, 03 marksmanship, 06 archery, 11
geomancy), and the clip prefix, not the name, resolves
the motion (edit `BASE_MOTIONS` freely). On the `ja`/`spell` motion lane the mixer shows this short list in place of every
spell whose motion is really one of these few; a pick composes by-reference (one
race-agnostic DAT that names the clip). WS still shows every motion, baked per race. The
names are cosmetic — edit `BASE_MOTIONS` in `xi_catalog.py`; the clip prefix resolves the
motion. Motions that live in the weapon-skill bank (Tomahawk, Jump, Mug…) are **not** here:
they are race-bound and would force a `ws` publish, so they stay on the WS list.

### Base-motion stages

A base motion is more than one clip: each family is a run of clip groups (`mb0?`, `mb1?`,
`mb2?`) the game plays in order. Every row lists them as **`stages`**, with the numbers
retail plays each with — read from the race base's own schedules, not typed in
(`BASE_MOTIONS` only says which schedules make up a family):

```jsonc
{ "name": "Black Magic Cast", "kind": "spell", "spec": "ROM/27/82.DAT", "paths": { … },
  "clip": { "ref": "mb0?", "frames": 14 },          // the first group, as before
  "stages": [
    { "label": "Start",  "ref": "mb0?", "frames": 14, "dur": 33, "blend": [16, 10], "loops": 63, "schedule": "cabk" },
    { "label": "Middle", "ref": "mb1?", "frames": 30, "dur": 58, "blend": [10, 0],  "loops": 1,  "schedule": "ssbk" },
    { "label": "End",    "ref": "mb2?", "frames": 30, "dur": 61, "blend": [0, 15],  "loops": 2,  "schedule": "ssbk" } ] }
```

- `dur` (one cycle's window, ticks), `blend` (`[in, out]`) and `loops` (0 = forever) are the
  schedule's PlayClip fields and are the same on every race. `frames` is HumeMale's clip
  length and is not — `mb0` is 14 frames on a Hume, 28 on an Elvaan male — so an event made
  from a stage carries the stage's `dur`, and the client fits each race's clip into it.
  Laid end to end, the next stage starts at `start + dur × loops`.
- `label` goes by position: the first is `Start`, the last `End`, anything between
  `Middle` (`Middle 1`, `Middle 2` when there are two); a two-stage set is `Start`, `End`.
- `schedule` is the routine the numbers were read from: a routine of the race base, or
  `ja:0 main` for the job-ability pair. `clip` is unchanged (always the first group), and a
  row whose schedules are missing from the base has no `stages`.

What the stages are in game:

| Family | Stages | Played by |
|---|---|---|
| Black / White / Blue Magic, Ninjutsu, Summoning (`mb` `mw` `ma` `mn` `ms`) | **Start** `m?0` the chant, looping (x63 black … x8 summoning); **Middle** `m?1` the release; **End** `m?2` the follow-through | chant: the caster's `ca<xx>` schedule at cast **start**; release + follow-through: `ss<xx>` at cast **finish**, linked by the spell DAT's `sh<xx>` |
| Item Use (`mi`), four stages | **Start** `mi0?` the intro, once; **Middle 1** `mi1?` the looping hold (x17); **Middle 2** `mi2?` and **End** `mi3?` the two finish clips | `cait` (first two) when the use starts, `ssit` (last two) when it finishes |
| Bard: Flute / String, Ranged, Geomancy (`sf` `sh` `yu` `gu` `gc`) | **Start** the intro, once; **Middle** the looping hold (x8–x15); **End** the finish | `lc<NN>` (RangedStart) then `ls<NN>` (RangedFinish) |
| Bard: Singing (`sk`), two stages | **Start** `sk1?` the looping song — there is no intro (`sk0` exists on Tarutaru only and no schedule plays it); **End** `sk2?` | `lc00`, `ls00` |
| Job Ability (`cm`), two stages | **Start** `cm0?`, **End** `cm1?` | the ability's own `main` (read from `ja:0`) — a job ability has no cast start |

**A spell's chant is the client's, not the DAT's.** When a cast starts the client runs the
caster's own `ca<xx>` schedule — the looping chant clip plus the casting circle — for the
whole cast time; the server names which one, from the spell's `spell_list.group`
([effect_system.md](../fx/effect_system.md#cast-motion-fourccs)). The spell DAT only plays
when the cast **finishes** (Fire, Cure III,
Katon and Knight's Minne name no caster clip at all: their `main` links `sh<xx>`, which
stops the circle and plays the finish schedule — `ss<xx>`, or `ls<NN>` for a song). A
published spell that also carries the **Start** stage therefore plays the chant a second
time, after the real one, and its effects land that much later. Leave Start out of a
spell (Middle + End are what `ss<xx>` plays); on a job ability, which is instant, every
stage belongs in the DAT.

## In the viewer

*Assets → Ability Mixer.* Left: the picker, one lane at a time — hover or arrow through
rows and the stage plays them on the current character (open Actors to pick race or an
NPC). Click or Enter takes a row for the lane. Right: the timeline (drag a block to
move it, drag a lane label to shift the lane, *snap to strike* aligns a lane's first
generator with the motion's hit frame), the selected block's numbers, and the **Parts**
of the previewed source with *solo* (play one generator alone) and *take*. **Play mix**
composes for the actor's race under `exports/ability/mixer/` and plays it; **Publish**
prepares the `xi dats` action for the recipe, shows `dats build --dry-run`'s plan, then
builds it. The Manage panel's *Use Pivot Folder* switch adds `--pivot` to that build and
to its Check, so the mix goes into `FFXI_PIVOT_DIR` instead of the game folder. Its
*Folder* button opens the mix's [publish folder](#the-publish-folder), or
`projects\abilities` when the mix has not been published yet. A Publish writes the mix it
prepares there as `<Name>.mix.json`, a Check as `check.<Name>.mix.json`; saved mixes are
`exports\ability\mixer\<Name>.mix.json`. Manage's *Database Update*, *Client Menu
Record* and *Lua Stub* switches, with *Server id* and *Menu name*, are the
[server steps](#database-update-client-menu-record-lua-stub); *Settings › Local Server*
edits the `.env` they read.

Weapon-skill motion carries its own clips; the viewer resolves a routine's clip refs
against the loaded character, so picking a `ws:` motion source also sets the
character's Action to that skill (a short reload). Job-ability and spell motion
(`cm0?`, `ma2?`) plays from the character's own pool.

**Stance.** The Category / Action combos above the Actors panel are the Characters
view's own: *Battle → Battle: Sword* (etc.) or equipping a main weapon in Actors loads
that stance's `btl` clip, and in the mixer the character rests in it between the
routine's clips, so the mix is seen from the pose it plays from in game. *Basic* puts
the plain idle back.
