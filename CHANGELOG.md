# Changelog

A plain-language summary of what changed in each xi-tools release. Every entry
covers the commits between that release and the one before it. Release links
point at the GitHub compare view for anyone who wants the technical detail.

---

## Unreleased

[Compare v1.6.4...main](https://github.com/vekien/xi-tools/compare/v1.6.4...main)

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
