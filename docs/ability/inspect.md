# xi ability inspect

Flatten an ability DAT's `0x07` routine graph into one absolute-frame timeline of
**motion, VFX, sound** and everything else the client does while the ability plays.

```bash
uv run xi ability inspect ja:33                 # job-ability animation 33 (Mighty Strikes)
uv run xi ability inspect spell:144             # Fire
uv run xi ability inspect ws:1                  # Fast Blade, all eight races
uv run xi ability inspect ws:259:HumeMale       # one race, extended bank
uv run xi ability inspect fid:2801              # any file id
uv run xi ability inspect ROM/76/30             # any DAT
uv run xi ability inspect ja:33 --json          # every command, raw bytes included
uv run xi ability inspect <spec> --routine tgt0 # flatten a routine other than main
uv run xi ability inspect <spec> --all          # include flow/branch commands
```

## Why

Every ability presentation is one DAT whose `main` routine is a timeline of commands.
Each command names a sibling section by 4-char id — a `0x2B` clip, a `0x05` generator, a
`0x3D` sound, a `0x54` weapon trace, or another `0x07` routine. Reading that graph by hand
means chasing `delay` fields that are *relative to the previous command* through several
linked routines. `inspect` does the walk once and prints absolute frames, so the recipe of a
retail ability — which clip, which effects, which sound, at what frame — can be read off and
later recombined (`xi ability compose`, planned).

## Spec forms

| Spec | Resolves through | Name comes from |
|---|---|---|
| `ja:N` | `file_id = 4412 + N` → FTABLE | `abilities.sql` `animation` column (`XI_SERVER_DIR`) |
| `spell:N` | `0xAF0 + SpellAnimationTable[N]` (`xi.spell`) | `ROM/181/73.DAT` |
| `ws:N[:RACE]` | the per-race weapon-skill banks in `FFXiMain.dll` (`xi anim ws`) | `weapon_skills.sql` + humanoid rows of `mob_skills.sql` |
| `fid:N` | FTABLE/VTABLE | — |
| `ROM/x/y`, a path | as given | — |

Job-ability animation `0` is the generic no-visual fallback shared by 100+ abilities; the
name list is capped at six.

## Output

```
ROM/76/30.DAT  (ws:1 HumeMale)  120,560 B  file_id 33228
  Fast Blade (ws 32); Fast Blade (mob_skill 32)

hm_0/
  hm_0/  6 clip: b001 b000 b010 b011 b020 b021 | 1 trace: b0a0
  0011/  9 gen: g004 g000 … | 3 routine: hit1 hit2 main | 5 curve … | 2 sound: 8051 8050

timeline: main  totalDelay 152  last event ends f152
  frame  dur  kind     op   command                ref   detail
      0  100  lock     20   LockActorStatus        -
     10   35  motion   05   PlayClip               b00?  b000 10f, b001 10f; blend 20/0 x1
     35    -  link     03   LinkRoutine(source)    eis1  external routine (shared / caster DAT)
     35   70  motion   05   PlayClip               b02?  b020 45f, b021 45f; blend 10/20 x1
     55   70  trace    2C   WeaponTrace            b0a?  b0a0
     60    -  vfx      02   FireGenerator          g002  TargetActor, tex 0113, mesh 0113 [hit1]
     60    -  sound    0A   Sound(source pos)      8050  se018050 Various Effects
     60    -  link     03   LinkRoutine(source)    hit1  expanded below
     65   10  vfx      02   FireGenerator          g003  TargetActor, tex 0112, mesh 0112 [hit1]
    100    -  link     03   LinkRoutine(source)    mdam  external routine (shared / caster DAT)

clips in DAT: b001 10f, b000 10f, b010 1f, b011 1f, b020 45f, b021 45f
sounds: 8051→se018051, 8050→se018050
generators: TargetActor: g004 g000 g001 g002 g003 g010 g011 g012 g013
```

- **frame** is absolute: the sum of every `delay` *before* the command (a delay is the
  wait after its command, so the first command runs at 0), carried into linked
  routines (`[hit1]` marks a command that lives in the linked routine, fired at the
  parent's clock).
- **motion** refs carry a client-side `?` wildcard (`b00?`): the client picks a variant.
  The clips that match in this DAT are listed with their length in game frames; a ref with
  no match (`cm0?` in a job ability) plays from the **actor's own motion pool**, which is
  how job abilities and spells animate every race from one DAT.
- **sound**: routine sound commands (`0x0A/0x0B/0x53/…`) point at a `0x3D` section and are
  resolved to `seNNNNNN` + the pol-utils title/category. Spells and job abilities usually
  play their sound from an **audio generator** instead — a `0x05` whose StandardSetup names
  the `0x3D` — shown as `sound se003032 Spell Sounds` on the generator row and `♪` in the
  generator summary.
- **external routine**: a link whose target is not in this DAT (`mdam` damage numbers,
  `proc`, `eis1`, the shared cast routines `shbk`/`shwh`). They live in the caster's own
  or a shared DAT and are resolved by the client at runtime.
- **locks, hits and target loops**: `0x2B` *ShowResult* is the hit (the damage or heal line
  and its number; the shared `mdam` is one), `0x1F` / `0x20` / `0x56` and `0x2E` / `0x2F` /
  `0x59` are locks, `0x31` *EachTarget* … `0x32` *NextTarget* repeat what lies between them
  for each target of the result, and `0x15` / `0x16` / `0x22` / `0x23` put a stand-in doll of
  the caster or target in its place. A `0x30` (a link run once per target) and a `0x5F`
  (*StopRoutine*) show the routine they name; `0x19`'s argument is a spell animation index.
  What they do in game: [mixer.md](mixer.md#locks-hits-and-links).
- Unknown opcodes print as `opNN` with kind `other`; `--json` includes each command's raw
  bytes (`raw`) so nothing is lost.

## What it tells you for composing

Motion clips are **skeleton-bound** (a weapon-skill DAT holds one clip set per race; job
abilities and spells reference the actor's pool), while generators, curves, textures and
sounds are not. The timeline gives the frame each piece fires at, which is what a compose
recipe needs to re-align an impact effect to a different clip's strike frame.

Format references: [../fx/effect_system.md](../fx/effect_system.md) §3 (routine layout and
opcodes), [../reference/ps2_decomp_crosscheck.md](../reference/ps2_decomp_crosscheck.md) §5
(SE task names), [../anim/weapon-skills.md](../anim/weapon-skills.md) (the ws banks),
[../audio/refs.md](../audio/refs.md) (sound pointers).
