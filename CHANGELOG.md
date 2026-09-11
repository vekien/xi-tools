# Changelog

A plain-language summary of what changed in each xi-tools release. Every entry
covers the commits between that release and the one before it. Release links
point at the GitHub compare view for anyone who wants the technical detail.

---

## Unreleased

[Compare v1.7.0...main](https://github.com/vekien/xi-tools/compare/v1.7.0...main)

_Nothing yet._

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
