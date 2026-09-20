# Changelog

A plain-language summary of what changed in each xi-tools release. Every entry
covers the commits between that release and the one before it. Release links
point at the GitHub compare view for anyone who wants the technical detail.

---

## Unreleased

## v1.10.0 — 2026-09-21

[Compare v1.9.0...v1.10.0](https://github.com/vekien/xi-tools/compare/v1.9.0...v1.10.0)

- **Zone docs for elevators and doors** (`docs/zone/elevators.md`, `docs/zone/doors.md`), plus updates to the zone README and subareas notes.

- **Database credentials come from xi-tools' `.env` only.** `XI_DB_HOST`, `XI_DB_PORT`, `XI_DB_USER`, `XI_DB_PASSWORD` and `XI_DB_NAME` (the model viewer's Settings › Local Server and the zone editor's setup both write them) are the one source; a field no key sets uses the default (`127.0.0.1`, `3306`, `root`, an empty password, `xidb`). The server's `settings/network.lua` is no longer read for credentials — the zone editor's setup can still offer it as a pre-fill. `xi server db`, `zone new`'s auto-apply and everything else now log in the same way: `zone new`'s password used to default to `xi` when `XI_DB_PASSWORD` was unset, and now defaults to empty like the rest. The setup report's `source` is now `env` or `default` (it was `override`, `network.lua` or `default`).
- **`xi server check`** reports the local server setup, read-only: the server folder, where each database field comes from (`env` or `default`; never the password), whether it connects (version, `sql_mode`), the animation column of `spell_list`, `abilities` and `weapon_skills`, and whether weapon skills can carry animations above 255 (xi_map's source, the column, and whether xi_map was rebuilt, read from `xi_map.pdb`). `--no-db` makes no connection attempt, `--no-binary` skips the PDB probe; `--json` prints `xi.server-check.v1` (`schema/server_check.json`) and always exits 0.
- **`xi server ws-widen`** writes the C++ patch and SQL that let weapon skills use animation numbers above 255 — `900-xitools-WsAnimation16.patch` (4 lines in `battleutils.cpp`, `weapon_skill.h` and `weapon_skill.cpp`, diffed from your own checkout), `ws_animation_16bit.sql` (the idempotent `ALTER … smallint(5) unsigned`) and a README on applying them — into `projects/patches/ws_animation_16bit/` (or `--out`). It only reads the server's source: it never edits the checkout, never touches the database, and never commits, builds or restarts anything. `--print` writes nothing and returns the texts; `--json` prints `xi.server-ws-widen.v1` (`schema/server_ws_widen.json`).
- **An ability publish can update the local server, place the client menu record and write the Lua script.** Three opt-in options of `dats build` and `xi ability publish` run after the DATs are placed — the model viewer's Manage switches *Database Update*, *Client Menu Record* and *Lua Stub*. `--apply-db` points the `spell_list` / `abilities` / `weapon_skills` row named after the mix at the new animation (only `animation`; `animationTime` and the name are never touched), or, when there is none, inserts one cloned from the kind's default donor — `cure` (spell), `berserk` (ja), `fast_blade` (ws) — a working row that carries the animation so the new one plays and works until a developer edits its stats; `--clone-from <id or name>` overrides the donor (the model viewer has no field for it). A row the mix did not create changes only after it is confirmed once (`--db-row <id>`). `--menu-record` writes the client's spell / command record and its EN/JP names (`--menu-name`) at the same id as that row — only over a blank retail placeholder or its own earlier record, never a named row, whatever `--force` says — into the root being built (`--pivot` for a client running PIVOT). `--lua-stub` writes `scripts/actions/…/<name>.lua` into `XI_SERVER_DIR` for a row the mix created, handing every call to the donor's script (spells also borrow the donor's per-spell helper rows, untested in game, and say so). One id serves both sides: blank in the client, free on the server and in every other project, highest first, or `--server-id`; the client has blank rows for 6 spells, 116 job abilities and 12 weapon skills. A weapon skill above 255 is refused until `xi server ws-widen`'s patch is applied, the column widened and xi_map rebuilt. Each step prints one `db:` / `menu:` / `lua:` line (with `would` on a dry run, which only reads) and never fails the build: it exits 0 whenever the DATs were placed. What each did is recorded on the action (`result.db`, `result.menu`, `result.lua` in [schema/ability.json](schema/ability.json)) and kept by every later build; `<slug>_<animation>.applied.sql` in the publish folder holds the statements that ran. A mix whose Type changed is refused while it still owns a row, menu record or stub of the old kind. `docs/ability/mixer.md` → Database Update, Client Menu Record, Lua Stub
- **`dats undo` puts an ability's server side back too.** It restores the blank placeholder under each client menu record (only while the row still holds what the build wrote; one a retail update has taken since is left, with a ⚠). `--apply-db` also deletes a row the ability inserted, or puts back the animation it changed, and deletes its Lua stub while it is unedited (that needs only `XI_SERVER_DIR`). Without `--apply-db` the undo prints the revert SQL and the stub path and **keeps the manifest** — the cleared actions marked `undone`, so they are not undone twice or packaged — until an `undo --apply-db` removes what is left. A row already gone (a dbtool re-import) or already back at its old animation counts as done, so that undo can always finish; only a row that still holds other values is left. A menu record that can't be put back (the game has `114.DAT` open) keeps the manifest too, and the next undo tries it again. `dats package` and `dats release` now ship an ability's menu record (`ROM/118/114.DAT` and its name tables) with it.
- **Mix files are `*.mix.json`.** `dats prepare` copies an ability recipe to `projects/resources/ability/<slug>.mix.json` (it was `<slug>.recipe.json`; a manifest that names the old copy still builds, and a re-prepare leaves that file in place), and the `dats new` wizard lists `*.mix.json` and the old `*.recipe.json` under `exports/ability/` (the `.mix.json` when both exist). Every command still reads either name, and the action id comes from the recipe's `name`, never the file name — the model viewer's `check.LOVE.mix.json` makes `ability.love`.
- **The publish folder's SQL keeps `animationTime`.** The spell and job-ability `UPDATE` no longer sets `animationTime = 2000`, and the positional `INSERT … VALUES` templates are gone (the server's columns drift between versions; `--apply-db --clone-from` inserts safely). The weapon-skill note now points at `xi server ws-widen`. A command record's server snippet for an id below 512 (a weapon skill) no longer names a negative `abilities` id.
- **Each published ability gets a folder of its own.** A publish (`xi ability publish`, or `dats build` of an ability action) now writes `projects/abilities/<slug>/` — `<slug>` is the action id's last part, `love` for `ability.love` — holding the server SQL (`<slug>_<animation>.sql`, where `result.server` now points), a copy of every DAT it placed at its ROM path (`love/ROM10/28/2.DAT`, companions included, the same bytes the target got) and `placements.json` ([schema](schema/ability_publish.json), `xi.ability-publish.v1`) naming each copy's race, role and file id, so an ability can be handed on or packaged as one folder. The folder holds the latest publish only: a republish removes the SQL and DATs the previous one listed that it does not write again (and another animation's `<slug>_<animation>.applied.sql`), and leaves anything else in the folder alone — the model viewer's mix files (`<Name>.mix.json`, `check.<Name>.mix.json`) among them. A build into a second target copies nothing twice, and a dry run writes nothing (its plan names the folder). A folder that cannot be written only warns (the DATs are placed and the build is recorded), and an action id whose last part is not a plain folder name is refused before anything is placed. SQL files earlier versions wrote into `projects/server/abilities/` stay where they are (delete them once the ability is republished). `docs/ability/mixer.md` → The publish folder
- **Locks, hits and links can be added by hand.** A recipe event may leave out `from` when it carries `raw`: a command of no lane — a target or control lock, the `mdam` hit, a link to a shared or actor routine — which compose writes as given (the delay, `dur` and a named routine's `ref` stamped in) into every race's DAT, gathering and renaming nothing for it; its timeline row has `"from": null`. It cannot be a generator, sound, clip or weapon trace, which need their lane's DAT. `validate_recipe` now reads every event's `raw` as the client walks a routine — whole dwords, 8 bytes at least, as many as its size byte says, byte 0 the event's `op` — and, for a command that names a routine, sound or generator, 16 bytes at least with 0 at +0xC (where the client keeps a pointer) and a printable name at +8; a `ref` must be 1–4 printable characters. The schema's fourth example is Fast Blade with its locks, flash, hit and added effect added this way. `docs/ability/mixer.md` → Locks, hits and links also says what each lock, hit and link does in game, what retail puts in a weapon skill's, job ability's and spell's `main`, and what is dangerous to add
- **Fix: a link to a shared routine keeps its name.** A lane's rename reached every command naming that name, so a lane that renamed a section named like a shared routine (a sound pointer `mdam`, say) pointed its links to the shared `mdam` hit at the new name too — in `main` and inside the routines it carried — and the game found nothing to run. A link now takes a lane's rename only for a routine carried from that lane. A mix with no such collision composes byte for byte as before
- **Compose warns about a link the game will not find**: a routine that is neither in the DAT nor in `ROM/0/0.DAT` and is not a race schedule (`sh??`, `ca??`, `ss??`, `st??`, `sp??`, `lc??`, `ls??`); a race schedule linked with `0x03` or `0x3B`, which never look on the actor (retail uses `0x3C`); a `ROM/0/0` routine linked with `0x3C`, which looks only on the caster; a routine of the DAT linked with `0x09` or `0x3C`, or with `0x57` / `0x85` outside a race-bound weapon skill, since those look on the actor. The command is still written, and the warning comes in `warnings` like the others
- **`xi ability inspect` names more of what it shows**: `0x2B` is *ShowResult* (it was StatusMessage: it is the hit), `0x15` / `0x16` / `0x22` / `0x23` the caster's and target's stand-in dolls (still or tracking), `0x28` *ReturnToIdle*, `0x30` *LinkRoutine(each target)*, `0x31` / `0x32` *EachTarget* / *NextTarget* (a loop over the result's targets), `0x5F` *StopRoutine*. `0x30` and `0x5F` now show the routine they name, and compose stamps a `ref` into them as into any link
- **A weapon skill composes in retail's folder layout.** Each race's DAT is now laid out like a retail weapon-skill body — Tachi: Enpi's is `hf_1 { 1111 {…, main} hf_1 {clips, traces} }`: a race root `<tag>_<c>` (retail's tags `hm` `hf` `em` `ef` `tr` `mt` `gl`, then the effect folder's first character, so a mix named `LOVE` gets `hf_l` on Hume female) holding the effect folder (the recipe's `dir`: every section, then `main`) and a clip folder of the root's name (the clips, then the weapon traces; left out when there are none). The sections and the routine are the same bytes as before and `xi ability inspect` reads the same timeline — only the folders around them differ, and the client finds `main`, clips and traces by type and name, never by folder. A job ability or spell stays one folder. `docs/ability/mixer.md` → Folders
- **Fix: a composed DAT's folder payload is retail's.** The effect folder carried its `0x20` at payload byte 7, but the client reads byte 3 (PS2 decompile; retail's effect folders are mostly `00 00 00 20 …`, as in `ROM/15/89`, the file the constant was said to be checked against), so it read 0 where retail has 2. Two bytes move in every composed DAT and nothing else changes. What the value does on PC is not known; on PS2 it is a texture-memory mode for which 0 and 2 behave the same
- **A weapon skill built from an emote plays its whole body.** FFXI splits a motion into three joint regions — lower, upper and the waist (part 2, joints ~4–25) — and a weapon skill keeps parts 0/1 in the body DAT and part 2 in its `+256`/`+512` companion DATs; the client reads the waist from the companion. Composing an emote (`/bow`, `/clap`) into a weapon skill used to leave every race's two companion slots as a 160-byte `[dumm]` placeholder and cram the waist clips into the body, where the client never reads them — so the mid-body froze in bind pose and the model tore apart in game. Compose now splits the emote's part-2 waist clips (from its `+6` sibling) into `Composed.companion`, and Publish writes that to both companion slots (a real ~15 KB DAT per race, the same layout as retail Fast Blade's `b*2` companions). A motion with no waist sibling still falls back to the placeholder; a weapon-skill motion still copies the source slot's own companions. The number registers 24 DATs (three per race), and `dats build --dry-run` lists them
- **A weapon skill built from an emote stows its weapon.** An emote (or dance) never moves the weapon hand — the joint the drawn weapon re-parents onto (skeleton reference 127; Hume male's is joint 68) — because you gesture with an empty hand, while a real weapon skill animates that hand to carry the blade through the swing. Played as a weapon skill with the weapon drawn, an emote therefore left the weapon hanging frozen at its battle-rest position while the arms moved around it — a detached, floating weapon in game, though the mixer preview looked right because it hides the weapon for an emote motion. Compose now emits, at the front of the `main` routine, the two `0x75` ShowHideWeapon tags a spell cast runs to stow the weapon while it plays (verbatim from `ROM/0/0.DAT`'s `hwmg`: hide main and sub, if engaged); the idle motion that resumes when the skill ends shows them again, so the pose reads in game as it does in the preview. Only a weapon skill whose motion is an emote or dance is touched — a weapon-skill motion, which does move the hand, keeps its weapon shown
- **Fix: a custom-band weapon skill loaded the wrong race's DAT.** The custom band's file id put the race term in one-based, but placed it zero-based: the client (and `cexislots`, which inherits the client's register at the bank stub) indexes the band by the runtime **RaceGenderConfig number, 1..8** — Hume male is 1, not 0 — while compose numbered the races 0..7. So every race resolved one slot low: a Hume male weapon skill loaded the **Hume female** body and companions (file id `+1`), a Hume female loaded Elvaan male, and so on — the wrong race's clips on your skeleton, which stretches and tears the model in game (a Process Monitor capture of the client opening `ROM10/28/3.DAT`, Hume female's, for a Hume male character is what pinned it). Retail hides the one-based index behind a bank pointer that is pre-offset so `table[1]` is the first race; the flat custom arithmetic added the index straight, so it was off by one. The band now places each race at `base + number*24 + block*8 + (race + 1)`, matching what the client asks for (Hume male's body id for animation 316 is `432401`, the id the client opened). Every custom weapon skill must be **republished** to move to the corrected ids. `cexislots` is unchanged — it was already using the client's own number
- Docs: `docs/anim/weapon-skills.md` §3 reads retail's effect-folder names by animation number — the 15-slot family block `(anim − 1) div 15`, the position in it (`1`–`9`, `a`–`f`, then `g`–`i` for later additions) and a final `1`: `1111` is Tachi: Enpi (166), `11d1` Tachi: Fudo (178), `11e1` Tachi: Shoha (179). The names are not hex and the client never reads them. It also describes a body DAT's folders and the payload retail gives each (about a third of weapon-skill effect folders carry 0xA rather than 2; compose writes the commonest values). `docs/fx/effect_system.md` no longer says the client picks a spell's chant from its MagicType: the server sends it, chosen by `spell_list.group` alone (`caso` songs, `cabk` black magic, `cawh` white …)
- **Base motions list their stages.** Every `base_motions` row in `abilities.json` gains `stages`: the clip groups the game plays for that motion, in order, each with the window, blend and loop count retail plays it with — read from the race base's own schedules (`cabk` + `ssbk` for black magic, `lc01` + `ls01` for a flute song, `ja:0`'s `main` for a job ability), not typed in. A magic cast is the looping chant (`mb0?` x63), the release (`mb1?`) and the follow-through (`mb2?` x2); Item Use has four stages, Job Ability two, and singing has no intro. Labels go by position (`Start`, `Middle`, `End`). `clip` is unchanged, so an older viewer picks what it always did. The baked lists carry the new rows. `docs/ability/mixer.md` says what each stage is in game — and that a spell's chant is played by the client at cast start, so a published spell that also carries the Start stage plays it twice
- **A composed routine waits out a looped clip.** A PlayClip's `dur` is one cycle and `loops` repeats it, but the routine's end counted one cycle, so a clip with `loops: 2` and no `total` on the recipe was cut off halfway. The end is now `start + dur × loops`, which is retail's own arithmetic (the race base's `ssbk` waits 122 ticks on its x2 clip of 61). `loops: 0` (forever) still counts one cycle, and a recipe's own `total` still wins
- **Recipe events may carry `row`** — which row of its track the model viewer's mixer drew the pill in. It is layout only: the schema and the validator hold it to a whole number from 0, and compose ignores it
- **An ability recipe can edit the particle effects it carries.** Two optional top-level keys: `generators` lists in-place edits to a lane's `0x05` generators — tint, scale, spawn rate, lifetime, a texture or mesh id swapped for another in the same DAT — and `curves` replaces the keys of a lane's `0x19` keyframe curves (same key count). An edit names its bytes by stream, opcode and offset (`{"sec": 2, "op": "0x16", "at": 4, "type": "u8", "value": [51, 128, 198, 38]}`; `sec: 0` is the header), never by file offset, so it lands on every race's copy of a weapon skill; the op is found by walking the stream, not by searching for a tag. Compose writes the edits into its own copy of the section before it joins the output, so the source DAT is untouched, two lanes on one spec keep their own edits (the second copy is renamed and reported), and a re-pointed id brings its new resource along. The section header, the stream table and an op's config dword are never writable, and nothing can grow. `validate_recipe`, Check and Publish name a bad entry (`generators[0].edits[1]: …`); a recipe without the keys composes as before. `docs/ability/mixer.md` has the shape and the list of fields that are safe to edit
- **`xi fx set --color` writes the colour the right way round.** The tint bytes of a generator are R,G,B,A (`0x80` neutral), but `--color` wrote B,G,R and `fx json` / `fx dump` read them back the same way, so red and blue were swapped both ways and the mistake hid itself (the in-game check was pure green, which cannot tell). Both now use R,G,B; alpha is still left alone. A DAT already recoloured with `--color` keeps its swapped bytes: set the colour again
- **Compose keeps colliding lanes apart properly.** A section renamed to settle a name collision could take a name its own lane still needed (Fire II's `g003` became `g000`, and firing Fire II's real `g000` afterwards played `g003` again); a rename now avoids the lane's own names. It also covers every section of that lane under the old name, so an audio generator keeps the `0x3D` sound pointer it is named after and a texture keeps its sprite sheet, a renamed dependency shared by several generators is carried once instead of once per generator (every generator of Cure III and Cure IV in one mix came out 25 KB lighter, and its `kir1` generator now goes by the name the routine fires), and a generator or linked routine whose resources were renamed for its lane is that lane's own copy rather than the first lane's. A mix with no name collision composes byte for byte as it did
- **An ability recipe can replace a texture with a PNG.** The optional top-level `textures` key takes `{"lane", "ref", "name", "png"}`: `ref` is the `0x20` section's id in the lane's source DAT; the optional `name` is its 16-character name there, which tells apart textures that share an id (`ROM/11/21` gives five the id `faid`, and never repeats a name), while an entry without it is the first texture of its id; and `png` is the image as a `data:image/png;base64,` URI (what the model viewer writes) or a `.png` path beside the recipe, which `load_recipe` inlines — so `dats prepare` stores a self-contained recipe. Compose encodes each distinct PNG once with texconv as DXT3, each side the nearest power of two from 4 to 256, alpha halved (the PNG's 255 is FFXI's `0x80` opaque), and keeps the section's id, 16-character name and flags, so every mesh and sprite sheet of that lane that names the texture draws it, on every race's copy. `validate_recipe` checks the PNG and the name without the DAT (prefix, base64, 2 MiB, PNG signature, sides 1–4096; 1–16 printable characters), compose refuses a texture the lane's DAT does not have (by id, or id and name) and two entries on one texture, and `compose --json` lists every replacement with its old and new size, the generators that draw it (by their linked mesh, sprite sheet or ring, or a specular op — not every texture a generator carries) and whether it is unused. When texconv fails, compose passes on texconv's own reason. A sprite sheet is an atlas: keep its layout. `docs/ability/mixer.md` → Textures
- **Compose carries the texture a mesh actually draws.** A particle mesh or sprite sheet names its texture by the texture's 16-character name, and that name need not spell the section's id: Fire's `fai0` sheet draws `"fai01   fai01"`, which is section `fai2`, and its `fa04` mesh draws `fai3`. Compose and `xi fx copy --from` found a mesh's texture only by an id inside the mesh, so a mix of Fire's `g004` or `g003` (and Fire II's flames) came out without the texture it draws. They now also carry the texture each carried mesh names. Where a DAT gives one id to several textures (`ROM/11/21`'s five `faid`, `ROM/11/48`'s two `fire` and two `fir1`, `ROM/11/122`'s four `enm1`), compose carried only the first, so most of those effects drew textures that were not there; it now carries every one a carried mesh names, the second and later under new ids of their own (a mesh binds by name, and an id still means the first). Sources whose names spell their ids (Cure III) compose byte for byte as before
- **Same-named textures from two lanes no longer draw the wrong one.** The client takes the first texture of a name in the file, so when two lanes put different textures of one name into a mix (a replaced texture beside the other lane's original, or two sources that share a name) both drew the first. The later lane's texture is now renamed with a fresh namespace (`carel3  ho` → `carel3_1ho`) and that lane's own meshes and sprite sheets are patched to match, under ids of their own; the report lists the rename. Two lanes colliding on a short id are fixed with it: a DAT that pads an id with spaces (Cure III's `ho␣␣`) kept the later lane's generators pointing at the first lane's mesh and texture, and a mesh the later lane had shared was left behind when its texture's id was renamed, so its generator pointed at a mesh that did not exist (`kir0`)

---

## v1.9.0 — 2026-09-18

[Compare v1.8.1...v1.9.0](https://github.com/vekien/xi-tools/compare/v1.8.1...v1.9.0)

The Ability Mixer release: build a new job ability, spell or weapon skill out of retail ones, and zone exports that come out right in a game engine.

- **`xi ability`** (with Carver) — mix retail ability presentations into a new one. `inspect` flattens a job ability, spell or weapon skill (`ja:N`, `spell:N`, `ws:N[:RACE]`, or any DAT) into one absolute-frame timeline; `recipe` writes a starter recipe that reproduces a source; `compose` builds a new DAT from lanes of retail sources — the motion of one, the effects of another, the sound of a third; `publish` places it in ROM10 under a new animation number. Recipes are `xi.ability.v1` JSON (`schema/ability_recipe.json`), checked field by field before anything is built; an event may carry a display `name`, and a lane may have no routine at all — a bare clip pack (Basic, the emotes) composes from the template
- **Publishing an ability is an `xi dats` action** (`schema/ability.json`), not a path of its own: `dats new` offers Ability as a content type, `dats prepare recipe.json --type ability` writes the action with every parameter defaulted, and `dats build` composes the recipe, takes the animation number against the live tables, places the DAT(s) and registers the file ids — recorded on the action, so a rebuild keeps its slot and `dats undo` clears it. `xi ability publish` is that prepare + build in one command. `AGENTS.md` sets the rule for the library: new content is an `xi dats` action, every JSON format has a schema, viewer lists come from `xi mv update`
- **The mixer's pick list is an mv list**: `xi mv update --only abilities` writes `mv/lists/abilities.json` (1,522 entries), shipped and refreshed like the others, keeping real names from a previous run on a machine with no server SQL. A weapon-skill bank slot takes the job ability's name before a mob skill's — Tomahawk 244, Angon 245, Steal 181, Jump 204
- **Ability recipes map a race's own motion to every race.** A motion lane whose source is a PC motion file (an emote, a battle pack, a dance, a weapon-skill file) resolves through FFXiMain.dll's per-race motion tables, so `xi ability compose` builds one DAT per race with that race's own clips, emote waist parts included, and `dats build` publishes it as a weapon skill. A race without a copy or without the clip is built without that motion and compose, `dats build` and the dry run print a `⚠` line for it. Clips from the race base (idle, cast, job-ability motions) are named instead of carried, so a job ability or spell can use them; a file only some races have (a race's Variations) is built for those races. Choosing job ability or spell for per-race motion now says why it can't be: one DAT for every race, and the game loads those motions only while they play
- **The ability catalog carries a short base-motion list for job abilities and spells.** `abilities.json` now has a `base_motions` section (`xi.ability.xi_catalog.build_base_motions`): the curated cast and job-ability motions the always-loaded race base offers — Black/White/Blue Magic, Ninjutsu, Summoning, Item Use, generic Job Ability, plus Bard songs, ranged (bow/marksmanship) and Geomancy — each read from the real race base as a clip group (`mb0?`, `mw0?`, `cm0?`…) with the base DAT per race. On a `ja`/`spell` mix the model viewer offers this handful in place of every spell whose motion is really one of them; a pick composes by-reference (one race-agnostic DAT that names the clip). WS still lists every motion, baked per race. Names are cosmetic (`BASE_MOTIONS` in `xi_catalog.py`); the clip prefix resolves the motion
- **Experimental: a job ability or spell can be composed with a baked motion.** Publishing a recipe whose motion is race-bound (an emote, a weapon skill, a race's own file) as `ja` or `spell` no longer errors: compose bakes the motion from one race's copy (the `--race`, HumeMale by default) into the single DAT and carries its clips; `xi ability compose` gains `--kind`. **Not verified in game** — no retail spell or job ability carries caster clips (Blue Magic's `wz*` clips animate the monster it shows), and the PS2 client's PlayClip resolves a wildcard ref only from the actor's loaded motions. The proven route for a unique motion remains a weapon skill
- **Custom animation bands** (Carver): the publisher allocates past the retail ceilings into the bands a client-side plugin reaches — spells, job abilities, and a third weapon-skill motion bank (272–527 with cexislots, 24 file ids per number) once 264–271 are taken. **On by default with cexislots' values**: `FX_*_BAND_*` only need setting for a different plugin, and `FX_<KIND>_BAND_FIRST=0` switches a band off for a stock client (the model viewer sends that when its *Custom animation bands* setting is off — a real environment variable beats `.env`, which is what made a configured band look unconfigured). The numbers any client loads are still handed out first, and the plan prints a `⚠` line when the number it took needs the plugin. The "no free number" error names the range it tried, says when the band is switched off and how to turn it on, and no longer suggests `--pivot` to a build that is already in the pivot folder
- **A build into a custom band grows the pivot folder's ROM10 tables for it.** Band file ids (423,152 and up) sit past the end of the expanded tables, so the build stopped at "table is too small … run `xi ftable expand`" — which sizes every table to 423,152 and could never help. The client plugin grows the client's table in memory and merges each XIPivot overlay's ROM pair into it, so only the overlay's pair has to hold the id: `dats build --pivot` now grows it to the band ceiling (437,488 with cexislots; `.base` backup first, the dry run says it would), the install's tables are left alone, a pivot sync (`ftable expand`, a non-pivot build, `gear inject`) keeps that tail instead of cutting the table back to the install's size, and the uniform-size check no longer counts it. A band build into the install says to use `--pivot`, before any DAT is copied
- **`--animation-from N`** on `xi dats prepare` / `xi ability publish` (`target.animation_from`, asked by the wizard): an automatic animation number starts there — the first free one at or above it — so a mix can go straight to the custom band (`--animation-from 272`) or keep a server's skills in a range of their own. A rebuild keeps its number unless that is now below the start
- **`xi ability slots`** lists the weapon-skill numbers a mix can publish to — 264–271, then the custom band with its bank index 0–255 — and what holds each: free, or the DATs, the races that have one, and the dats project that put them there. `--pivot`, `--from/--to`, `--free`, `--json` (the model viewer's Manage › Slots table)
- **`xi zone export` welds by default**, like `gear export`: coincident triangle corners become one shared, indexed vertex per mesh (position + UV + baked colour; the first corner wins the normal) instead of loose triangle soup — about half the vertices on a standard zone with the triangles unchanged, and `zone import` reads it the same. `--no-weld` keeps the old output, `--mesh-merge-dp N` sets the position tolerance
- **`xi zone export --weld-seams`** (with `--fbx`) also joins the splits a glTF forces at every UV seam, which is what leaves tiled terrain in pieces. The weld runs in Blender on the way to FBX, where UVs are per face-corner, so the ground connects and the texture stays as it was. All opaque materials weld as one group and each alpha overlay on its own, so the blended pass FFXI draws over the ground (`sar_kk2` under `sar_kk2_alpha`, the same triangle twice) is kept rather than folded away; the DAT's normals ride through the weld, and a corner whose weld would delete a face is left alone
- **`xi zone export --right-handed` now comes out right in Unreal, Unity and Godot.** Those engines make imported materials one-sided, and FFXI terrain is clockwise-front and drawn two-sided, so the ground was black from above and lit from below; on top of that FFXI places about 40% of its ground tiles mirrored (negative scale), which flips the winding for that one copy and left a checkerboard of black tiles. The flag now flips the winding to counter-clockwise and bakes every mirrored placement into a mirrored copy of its mesh (`<mesh>~mir`, ignored by `zone import`) with a positive scale, so no mirrored transform reaches the engine — checked through the final FBX: no negative-determinant objects, winding and normals agree on every triangle, geometry where it was
- **`xi anim export --split-anim`** writes every animation track in a DAT as its own file named after the track (`exports/anim/rom/27/82/idl0.gltf`, `wlk0.gltf`, …) instead of one `--anim` clip; with `--fbx` the lot is baked through a single Blender run
- **`xi anim export --categories`** lays the output out as `<race>/<category>/<action>/` — `exports/anim/hume_male/sword/fast_blade/`, `galka/emote/emote/` — named from the viewer's character list with the FFXiMain.dll motion tables as the fallback; DATs neither knows (monsters, NPCs) go under `other/`. Works for a single DAT and for the no-DAT bulk export. The same lookup now also detects the race of animation-only DATs the ROM-id heuristic didn't know (weapon skills, job emotes, battle packs), so those export without `--race`
- **`xi dats` writes spell and command menu records** (Carver), and warns when an id is past what the game reads without a client plugin such as cexislots. Names and help text are checked against their block sizes before anything is written, and a row that a `--force` build replaced, or an id a rebuild moved away from, is put back
- **`--pivot`** on `dats build` / `dats new`, `ability publish` and `ui spells`: `FFXI_PIVOT_DIR` is written only when you ask for it. Actions record which tree they went to, and `undo`, `package` and `release` follow it. The main FTABLE/VTABLE pair is written only in the install, and only when it can hold the id; `--pivot` refuses a `ROM/` placement that would need a new main entry
- **File ids resolve the way the client reads them** (Carver): through the `ROM{n}` pair first, then the base install's main pair — a pivot folder's main pair is never read. The collision check and the ability publisher no longer hand out a slot that is already live through ROM10
- **A pivot build registers a file id; it does not raise the ceiling.** The client takes its file-id ceiling from the tables it loads at startup, and those come from the base install. Measured on an install still at the retail 109,701 entries with the pivot's ROM10 pair grown to 431,344: a registration at 100,500 loaded in game, the same one at 109,750 did not, and lengthening only the pivot pair changed neither; after `xi ftable expand` on the install both loaded. The docs and the permission hint say so now — it is the first thing to check when a registration that reads back correctly resolves to nothing in game
- **The `dats` dry run tells a free slot's retail placeholder from a real collision**: placeholders print as free and are not counted
- Fix: `xi fx copy` and `xi ability compose` follow particle meshes (`0x1F`) and sprite sheets (`0x21`) to the textures they name, as they already did for zone meshes. Cure III's `ob2` mesh came without `obi` and its `shp1` sheet without `shu`, so a composed ability drew white quads on a cold start and looked right only once the retail spell had been played
- Fix: a composed routine ends at its last window when the recipe gives no total — a lone clip at frame 0 used to end before it had played a frame
- Model-viewer lists refreshed from the 10 September 2026 retail client (append-only): 273 gear rows in `characters.json` (39 per race, model ids 951–968 under `ROM/385–387`), 224 Unsorted Models rows in `npcs.json` plus anims on 171, 116 `images.json` rows, Cutting Cards in `effects.json`, `music023/024`, and the first `abilities.json`. `xi mv` gear-sets writes `engagedDisplay` — the action groups whose motions play with the weapons drawn
- **`xi mv` validates every list before it is published**: a list is read back after it is written and the run fails if it does not parse, and the manifest parses every list beside it before hashing — a broken one reports the file by name and leaves the previous manifest in place, so a bad list never reaches an install at boot

---

## v1.8.1 — 2026-09-13

[Compare v1.8.0...v1.8.1](https://github.com/vekien/xi-tools/compare/v1.8.0...v1.8.1)

Hotfix for the 10 September 2026 retail update.

- **Item DATs from the 10 September 2026 retail update decode again.** That update grew every item record from `0xC00` to `0x1400` bytes and widened the header (`flags` became a u32; the equipment layouts gained a pad after `races`), so anything that assumed `0xC00` read nothing from a patched client. The record stride is now detected per file (`xi.ui.items.xi_layout`) and every field offset is looked up for that file's format, so `xi ui items search/export/json/import/inject/icon` and `xi mv database` work unchanged against a legacy install and a current retail one — even side by side. New `xi ui items info` lists each item DAT with the format it detected. Import and inject write records in the DAT's own format; the string block they write now matches the client's real layout (text objects with the `0x1C` header, a number object for the article slot) instead of a packed form only this tool could read
- New item table `ROM/387/13–14` (ids 30720–31743, `Items_7`) registered in the item CLI and the `xi mv database` registry (`items7`); skipped where it does not exist
- `xi mv database` item documents carry `stride`, `strides` and `format`
- Model-viewer lists: the Colibri Scythe (main-hand model 969, `ROM/387/15–21`, one DAT per race) added to `characters.json` for all seven races; `manifest.json` regenerated
- Tests: `tests/test_items_layout.py` (no game files needed — synthetic records in both formats round-trip and the detector tells them apart) and `tests/test_items_reference.py` (Scorpion Harness, Kraken Club, Byakko's Haidate, Excalibur, Chocobo Bedding decode identically from a legacy and a retail install; set `FFXI_RETAIL_DIR` / `FFXI_LEGACY_DIR`, either may be absent)
- Docs: `docs/retail/update_september_10_2026.md` — every DAT the update touched, what each one is, the record-format change field by field, the verified zone file-id formulas, and the item / dialog / event / Ambuscade content diff

---

## v1.8.0 — 2026-09-12

[Compare v1.7.0...v1.8.0](https://github.com/vekien/xi-tools/compare/v1.7.0...v1.8.0)

- **`xi zone export --opaque`** — solid materials the way the client draws them
- **Lists:** the Beasts > Rabbit row that pointed at an Elvaan Bishop (`ROM/3/108`, a typo for `ROM/4/108`) and the "Phanauet Channel - 1" map row that was an NPC model are gone, along with 170 effect rows that could never play — four-byte stubs, empty shells, zone files, and the four 2009 add-on categories whose effect index had been resolved as an absolute file id. `manifest.json` regenerated, so the viewer picks the lists up at boot

---

## v1.7.0 — 2026-09-11

[Compare v1.6.7...v1.7.0](https://github.com/vekien/xi-tools/compare/v1.6.7...v1.7.0)

- The `0x1F` ParticleMesh and `0x21` SpriteSheetMesh formats are decoded, so `xi fx` can finally see the geometry that spell, ability and NPC effects actually draw. `xi fx` only ever counted `0x2E` ZoneMesh as a mesh, which is the type *zone* effects use — the fountain splash quad. Everything else in the game stores its triangles in a `0x1F`, so `xi fx json` reported `mesh: null` for every one of them and `xi fx export` quietly wrote a JSON file and no GLB. Layout taken from the client's own loader (`CMoD3m::Open` in the `xiclient` decompile) and verified against 51 sections across four retail DATs. The trap it documents is the alignment: the material table is placed by rounding the *entry count* down to a multiple of four and adding three, not by rounding the byte offset up to 16 — the two agree for one to three materials and then quietly read two bytes into every vertex. Format: `docs/fx/particle_mesh.md`
- New `xi fx export <dat> --assemble` builds the whole object as one GLB instead of one generator at a time. An effect-only entity's appearance is spread over a dozen generators and none of them looks like anything alone; this places each mesh at its generator's own position and scale. By default it draws the object *at rest*: only generators with the `genFlags` autorun bit, which is what separates a Home Point's idle routine from its activation routine exactly, and no `0x21` sprite cards (those are the billboard for one emitted particle, so a static copy is a flat square through the model). `--all-layers` turns both filters off.
- `xi fx json` now reports what MOVES an effect: `rotation` (sec2 `0x09`), `rotation_velocity` (sec2 `0x0B`, radians per 60 Hz frame) and `uv_scroll` (sec3 `0x27`/`0x28`). None of this is in a `0x2B` animation clip — an effect-only entity's clip is a placeholder, and a Home Point's `idl0` is 99 frames of identity transforms that pose the invisible proxy triangle and nothing else. Two things about it are easy to get wrong and are now written down: the spin *rate* and the *updater* that integrates it are separate opcodes in separate streams, so a generator with `0x0B` and no sec3 `0x05` does not turn; and `0x09` is not decoration — a Home Point's `nak0`/`nak1` are one mesh at 30° and 150°, the crossed planes inside the crystal, differing in nothing else. `xi fx export --assemble` was keying its dedupe on mesh + position + scale and so had been dropping one of the two. It now bakes the rotation and keys on it, which is why the Home Point assembles as 7 layers rather than 6. Docs: `docs/fx/effects.md`
- New `xi.fx.xi_opcodes.op_floats` / `has_op` pull one opcode's payload out of one generator sub-stream by walking it. The existing param readers byte-search for a two-byte tag, which also matches inside float payloads — fine for the header-adjacent fields it was written for, not fine for the animation opcodes further into a stream.
- New `xi mv update --only effect-npcs` marks the NPC models whose visible form is an effect rather than a mesh — `effect: <layer count>` plus an `(Effect)` name suffix, 104 rows. These are the models that used to render as an empty stage: Home Points, portals, telepoints, lightbeams, vortexes and every elemental. Their `0x2A` geometry is a 160–208 byte proxy — one sub-millimetre triangle on a skeleton named `toum` (*toumei*, transparent) — that exists only so the client's actor system has something to place, click and pose. Detection is a cheap header seek-walk to reject anything with a real body, then a parse to confirm an autorun generator actually draws a `0x1F`. Pattern: `docs/entity/npc-look.md`, worked example: `docs/dats/ROM_3_25.md`
- A `0x3D` sound section's DatId is **not** its sound id, and `docs/audio/refs.md` now says so. They match often enough to look like a rule and then don't: the Home Point's activation sound sits in a section named `6023` carrying id `16023`, and `se006023.spw` also exists — so reading the name plays a real but wrong sound. `xi audio refs` was already reading the embedded `u32`; the docs were the thing implying otherwise.
- `docs/fx/effects.md` gains the element-class selection table (which `CMoD3mElem` subclass a generator gets, from its StandardSetup flag bits) and the `0x55` SpecularParams field layout — the entry that gives the Home Point crystal a dedicated specular path and points it at a second texture it never draws with.

---

## v1.6.7 — 2026-09-09

[Compare v1.6.6...v1.6.7](https://github.com/vekien/xi-tools/compare/v1.6.6...v1.6.7)

- `xi gear pose --pose-file` bakes joint world transforms a viewer has already evaluated, instead of resolving a clip name. `--anim` can only reach a pose that a clip and a frame number describe, and plenty of what a viewer shows is not that: a weapon-skill schedule lays several clips on a timeline, blends each back out to an underlaid base idle, merges the waist pack and re-parents the weapon grips, and the viewer's own "current animation" is empty for all of it. The locals that reproduce those worlds on the DAT hierarchy are solved for, so the node tree in the file agrees with the baked vertices; one frame freezes, many with `--all-frames` embed. Format and rationale: `docs/gear/pose.md`

---

## v1.6.6 — 2026-09-09

[Compare v1.6.5...v1.6.6](https://github.com/vekien/xi-tools/compare/v1.6.5...v1.6.6)

- New `xi gear pose` exports a whole dressed character — every armour slot plus the weapons — as one rigged GLB/FBX, with the geometry the game hides actually removed. FFXI gear is authored to overlap (the body keeps its bare wrists, shins and full head of hair) and the client drops whichever pieces the worn set covers, keyed on the `occludeType` byte in each mesh header against each piece's `displayType`; merging the DATs by hand leaves all of that sealed inside the armour, where it pokes through the moment anything is posed. Weapons are re-parented into the hands the way the client draws them, a stowed ranged weapon is left out as the game leaves it out, and every body-region layer of the pose clip is merged so the upper body is posed too, not just the legs. Takes DAT paths, `--race` + `--slots`, or an NPC `look` blob. Docs: `docs/gear/pose.md`
- `xi gear pose` freezes the frame you name (`--frame N`, counted on the 30 fps played timeline so N is the frame a viewer's own counter shows, not an index into the clip's stored keyframes) or embeds the whole clip with `--all-frames`, which with `--fbx` bakes the motion into the FBX too
- The mesh parser now keeps the two equipment-occlusion bytes it used to read past — a section's `occludeType` and each piece's `displayType` — and `compute_global_transforms` accepts joint parent overrides, which is what puts a drawn weapon in the hand. The model viewer's Full Pose export runs through all of this
- New `xi mv update --only ws-unreleased` adds the extended weapon-skill bank to `characters.json` as its own **WS (Unreleased)** category: 55 clips across the seven PC races (animations 256–271) that the client can play and retail has never named — `ROM/181/72` holds no name for a single one of those slots. Labelled by animation number, the number `!injectaction 3 N` takes, rather than by a guessed skill name. Placeholder (`dumm`) slots are skipped, so each race lists only what it actually has — thirteen for Hume Female, three for Elvaan Female.
- `xi mv update` now writes `mv/lists/manifest.json` at the end of every run — a sha256 and byte count for each list beside it. That file is what publishes a list: the model viewer fetches it from `main` at boot and pulls any list whose contents no longer match the copy it holds, so a list refreshed here and pushed reaches every install without a new .exe. It indexes whatever JSON is in the directory, so a list this tool does not generate (the viewer's `zone_npcs.json`) is covered as soon as it sits beside the rest. Content-addressed, with no version counter to bump or get out of step.
- `mv/lists` brought up to date with the copies the model viewer had been shipping: characters gains the 105 fishing-rod rows added there, floors goes from 34 to 73 rows with the viewer's clearer zone labels, and `zone_npcs.json` joins the set. These lists are now the published set, so shipping the older copies would have taken those rows back off everyone.

---

## v1.6.5 — 2026-09-07

[Compare v1.6.4...v1.6.5](https://github.com/vekien/xi-tools/compare/v1.6.4...v1.6.5)

- New `xi anim ws N` resolves a weapon-skill animation number to the motion DAT each race plays. The client uses two per-race banks in `FFXiMain.dll`: numbers 0–255 go through the primary bank and 256–271 through a separate extended bank, so adding 259 to the primary base (which gave a waist-clip DAT with no `main` routine) was wrong. `xi anim list` and bulk `xi anim export` now enumerate the extended bank as `weaponSkillExt`.
- `xi ui layout mnc2-pos --records` now undoes the per-record byte rotation of the `comm` (ability) and `mgc_` (magic) tables in `ROM/118/114.DAT`, names each ability row from `ROM/181/72.DAT`, and lists all 2,816 rows; `--raw` shows the on-disk bytes.
- Docs: `docs/anim/weapon-skills.md` — the three unrelated weapon-skill id spaces (name id, server animation number, per-race file id), the bank tables and their companion blocks, the decoded ability table, the effect-directory naming pattern with its counterexamples, and what remains unproven.
- Fixed the two "Dev / XI Modified" rows in the model viewer's zone list (403 Dev Castle Town, 404 Dev Town) pointing at the wrong ROM10 DATs; they now carry the right paths and file ids.
- Docs: new model-viewer reference (`xi mv update` targets including `npc-anims` and `zone-names`, `xi mv database`), a `xi zone package` guide, `xi zone import-collision` and `--compact-buckets` in the collision doc, the zone-export filter flags, and the command lists brought up to date with `xi run`, the title, event and zone commands added since v1.5.12.

---

## v1.6.4 — 2026-09-05

[Compare v1.6.3...v1.6.4](https://github.com/vekien/xi-tools/compare/v1.6.3...v1.6.4)

**Events and cutscenes**

- Any retail event can now be decompiled into a readable JSON file and recompiled back to exactly the same bytes. Branches, jumps, labels, shared subroutines, menus and dialog text all come out readable and editable.
- A check mode recompiles the result and compares it against the original so you can be sure nothing drifted.
- A sweep command runs the same round-trip over every event in a zone (or many zones) in parallel, and every event in all 293 retail zones with event data comes back clean.
- New helper commands explain an event as annotated disassembly, survey which opcodes appear across zones, and lint a script before compiling it.
- The cutscene compiler can now find the event and dialog files from the zone named in the JSON, so you no longer need to point it at ROM paths by hand.
- Fixed a bug where a replaced event could shift to a different slot in the actor's event table and cause the wrong sub-events to play in game.
- New guide documenting the full workflow: explain, decompile, sweep, edit, compile and verify.

**Scripting and title screen**

- New `xi run` command replays a text or markdown file full of `xi …` commands in one go. It skips blank lines, comments and code fences, supports variables and line continuations, and has a dry-run mode.
- New `xi title wardrobe` command lists or hides the wardrobe badge icons and digits on the title screen.
- The title menu command now prints a one-line summary of what changed instead of dumping before and after state.
- The custom title screen guide was rewritten as a runnable `xi run` script.

**Zones**

- Objects that are drawn by an animation generator (rather than a plain placement) now stay visible when you copy, move or delete them in the editor. Copies get their own generator, moves update it, and deletes park it cleanly.
- Plain copies no longer inherit a group link from the object they were cloned from, so they show up as normal static objects.
- VFX modify operations now accept a rotation.

**Documentation and housekeeping**

- New reference cross-checking the documented file formats against the 2003 PlayStation 2 client, with corrections to the sub-area record layout and a comparison of the 120-slot inventory patch against the original container code.
- Title-screen artwork sources tidied into a logos folder and updated Photoshop files.

---

## v1.6.3 — 2026-09-04

[Compare v1.6.2...v1.6.3](https://github.com/vekien/xi-tools/compare/v1.6.2...v1.6.3)

- New `xi audio scan` command walks every DAT in the game install and writes a JSON map of which files use each sound effect. Each DAT is identified by what it is (zone, entity model, spell, job ability, weapon skill, gear, mount, fishing rod) with human-readable names where possible.
- Options let you narrow the scan to specific ROM folders or sound ids, list sound files on disk that nothing references, and print to the console instead of a file.
- The main docs now list the title screen, packaging, UI texture and model-viewer commands.

---

## v1.6.2 — 2026-09-04

[Compare v1.6.0...v1.6.2](https://github.com/vekien/xi-tools/compare/v1.6.0...v1.6.2)

**Collision**

- Fixed baked collision boxes that players could walk straight through. The two faces of each triangle are now emitted the way the retail client expects, so boxes block from the outside.

**Zone editor and VFX**

- Pasted point lights now actually light the scene in game. They are registered in the zone's light table and bound to every nearby object, and they can be moved after pasting.
- Pasted effects keep a stable id across publishes, so moving a pasted effect is no longer silently lost on the next publish.
- Pasted weather effects (such as snow) are converted to ordinary ambient effects so they play regardless of the current weather.
- Fixed cross-zone copies that joined an animated door group by accident, which made every copy after the second one invisible in game ("barrel shows, fence doesn't").
- Editor console log lines no longer show a blank row between every real line on Windows.

**Model viewer lists**

- Mog houses now get proper hand-verified names instead of internal mesh names or raw file paths, and a new `zone-names` target pushes them into the viewer's zone list.
- Corrected the prototype-zone docs about which DAT is a Bastok mog house.

---

## v1.6.0 — 2026-09-03

[Compare v1.5.12...v1.6.0](https://github.com/vekien/xi-tools/compare/v1.5.12...v1.6.0)

**Model viewer support (new `xi mv` command group)**

- New `xi mv update` command refreshes the list files used by the xi-model-viewer project. It only appends missing entries, so curated names are never overwritten.
- Targets cover gear, music, sound effects, zone music, spell and ability effects, texture-only image DATs, NPCs (named from the server database), file ids for every row, and gear set labels (Artifact, Relic, Empyrean, Prime, Aeonic, Mythic and more).
- Gear labels were reworded to read as "BLM - Wizard's Coat" style, and gear section order is now data-driven rather than hard-coded in the viewer.
- Trust NPCs now pick up the animation packs they borrow from other DATs, so their combat animations can be previewed.
- Fishing rods appear as ranged rows, with the rule for when a ranged weapon is visible shipped alongside.
- Weapon type labels can be renamed through the data (for example "Hand" became "Hand-to-hand").
- The Limbus gear set was retired because those items do not exist on this server's item list.
- New `xi mv database` command bakes the viewer's item and message tables to JSON so it no longer needs to read large DATs at runtime.
- Progress is now streamed per target instead of the command going quiet for a minute.
- The generated list JSON files are now shipped in the repo.

**Zone export**

- Zone export now defaults to what the client actually draws. Collision-only proxies, sub-area interiors and far-distance stand-in copies are filtered out, with flags to add each class back. Ru'Aun Gardens drops from over 3,000 placements to about 1,600.

**AI assistant support**

- Added a tracked skill file describing FFXI quirks, common command recipes and where to look in the docs, so AI coding assistants give correct answers about the tool.

---

## v1.5.12 — 2026-08-27

[Compare v1.5.8...v1.5.12](https://github.com/vekien/xi-tools/compare/v1.5.8...v1.5.12)

**Title screen editing (new `xi title` command group)**

- The login screen's 3D background can now be edited: list the zones it uses, point a segment at a different zone, view fog colour and range per segment, and export or import camera flight paths as JSON.
- The opening zone on a fresh launch is now editable without touching the client.
- New timeline command prints the shot list for each segment, including which camera flies while each weather state shows.
- One-file export of the whole title screen: camera paths, UI textures for all four languages, and notes on what is known about the music and play order.
- Fixed camera export missing most of the camera data, fixed a mis-read keyframe field (it is a focal length, so zoom moves are now preserved), and fixed garbage characters in control-track names.
- Camera keyframes accept a field of view in degrees as well as a focal length.
- New menu and sprite layout tools for the title UI DATs, plus docs on wardrobe badges, UI chrome and the main menu layout.
- The zone editor bridge can now drive the title screen scene the same way it drives cutscene cameras.
- Shipped a heavily customised title screen example with source artwork.

**UI textures**

- Imported UI textures are resized to the size the game expects by default, with sprite mappings kept in sync. Use `--no-resize` to opt out or `--hd` to keep them above vanilla resolution.
- Fixed the alpha boost being undone on exports that had been edited.

**Zones and collision**

- New `xi zone package` command bundles a custom zone with everything it needs (DATs, file tables, override tree copies, spawn entry, server scripts) and writes a manifest plus a README explaining the common pitfalls.
- New `--compact-buckets` option for collision bakes cuts the cost per triangle by more than half, letting a whole large zone fit where the old method managed only a small disc.
- Fixed re-baked collision causing the client to relocate geometry twice.
- The editor now lists all custom zones up to the server's maximum id, with their real names instead of a generic label.
- Documented how prototype towns are rendered (windmills and dual meshes) and corrected a collision header field description.

---

## v1.5.8 — 2026-08-19

[Compare v1.5.7...v1.5.8](https://github.com/vekien/xi-tools/compare/v1.5.7...v1.5.8)

**Client DLL tools (`xi dll`)**

- New top-level `xi dll` group for PlayOnline client modules, with a nested `ffximain` group for unpacking, packing, gear tables, crash dumps and patching. Also covers the polcore and app modules.
- Inventory expansion from 80 to 120 slots ships as a replayable patch file with a full write-up, and a `patch` command applies it safely (aborts on mismatch, safe to re-run, has a dry-run).
- New signature-based patching locates each edit by the surrounding code rather than a fixed address, so patches survive client updates. Includes generate and apply commands and the inventory patch in signature form.
- Complete command reference for every `xi dll` command.

**Pre-production and prototype zones**

- End-to-end support for the pre-production zone layout that the retail client cannot read. The tools detect the layout automatically and can convert a zone so the client loads it.
- Zone list and JSON commands gain a curated "Dev / Prototype" group, and the custom zone ceiling was raised.
- New `xi zone inject` writes the spawn entry into the server's zone script when a server checkout is configured.
- Zone export now works on prototype zones with geometry and textures, where it previously reported no geometry at all.
- Fixed scrambled green-speckled textures in prototype zones caused by an unhandled 16-bit palette format.

**New zone commands and navmesh**

- New `xi zone patch-proto` converts a prototype zone's placement records for the retail client, safely and idempotently.
- New `xi zone import-collision` bakes an authored OBJ as zone collision, with options to replace or reset existing collision, set wall/floor/terrain defaults and block the camera. It prints a summary before writing and reports how much of the size ceiling is used.
- Navmesh bakes work again with the bundled library, and a floors-only mode voxelises just near-horizontal surfaces.

**Zone editor bridge**

- Large messages are reassembled correctly instead of being silently dropped, and console output inside handlers no longer kills the request.

**Documentation**

- Character creation DAT classification and progress notes.
- DLL reference consolidated in one place; regenerable DLL outputs are ignored by git.

---

## v1.5.7 — 2026-08-12

[Compare v1.5.6...v1.5.7](https://github.com/vekien/xi-tools/compare/v1.5.6...v1.5.7)

- Pack and unpack support for the main game client module, the foundation for the DLL tooling that followed.

---

## v1.5.6 — 2026-08-07

[Compare v1.5.5...v1.5.6](https://github.com/vekien/xi-tools/compare/v1.5.5...v1.5.6)

- Runtime caches are written to the configured workspaces directory (or the exports cache) instead of inside the level editor's web folder or the package tree.
- The workspace path chosen during setup is remembered.
- Sample environment file entries are commented out so they are not mistaken for real configuration, and the default database is now `xidb` with an empty password.

---

## v1.5.5 — 2026-08-07

[Compare v1.5.3...v1.5.5](https://github.com/vekien/xi-tools/compare/v1.5.3...v1.5.5)

**Zone editor setup and bridge**

- Workspace setup accepts any folder and no longer requires git.
- First-run setup can read and save environment settings from the editor, and changes take effect without restarting the bridge.
- The setup wizard now owns the server path and database credentials, fixing a fresh install silently falling back to a hard-coded login. It can test the database connection with clear error messages and pre-fill credentials from a server checkout.
- Game files and exported assets (icons, sprite sheets, decoded audio) are served over the bridge's HTTP port, so the desktop editor no longer needs local folder junctions.
- Cutscene NPCs render without a database: a bundled snapshot of the NPC list fills in name, look and position when no server is reachable, and `xi server npc-snapshot` rebuilds it from a server checkout.
- Fixed single-quoted values in the server's network settings being ignored.

---

## v1.5.3 — 2026-08-06

[Compare v1.5.2...v1.5.3](https://github.com/vekien/xi-tools/compare/v1.5.2...v1.5.3)

- Fixed the Python module entry point exiting immediately, which stopped the zone editor bridge from ever starting.

---

## v1.5.2 — 2026-08-06

[Compare v1.5.1...v1.5.2](https://github.com/vekien/xi-tools/compare/v1.5.1...v1.5.2)

- Fixed a missing import that broke bridge startup.

---

## v1.5.1 — 2026-08-06

[Compare v1.5.0...v1.5.1](https://github.com/vekien/xi-tools/compare/v1.5.0...v1.5.1)

- New `xi bridge` command runs a local WebSocket server for the zone editor. It exits on its own when no clients remain and can start before setup is complete.
- README rewritten with the setup flow, feature overview, navmesh notes and an AI-assistant section, plus a screenshot.

---

## v1.5.0 — 2026-08-06

First public release.

- The full command-line toolkit for FFXI DAT modding on private servers, covering models, animations, entities, gear, mounts, zones, objects, collision, navmesh, VFX, audio, UI, events and packaging.
- Format documentation, JSON schemas, sample environment file, bundled texture conversion and navmesh helpers, and the release workflow.
- README and quick command list aligned with the live CLI.
