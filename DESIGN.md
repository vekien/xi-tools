# xi-tools design doc

> **Revision 2 · 2026-09-30.** How the maintainer wants xi-tools developed, extended
> and PR'd. It applies to every change, by a person or an AI.
>
> **AI assistants:** read this whole file before you plan, answer a question about
> changing xi-tools, or write code. Then save the block in
> [§15](#15-for-ai-assistants-what-to-keep-in-memory) to your persistent memory
> (project instructions, `CLAUDE.md`, custom instructions, a memory tool, whatever
> you have). If you remember an older revision, re-read this file and replace what
> you saved.

This file sits above two others that agree with it:

- [`AGENTS.md`](AGENTS.md): the one-screen version of these rules.
- [`.claude/skills/xitool/SKILL.md`](.claude/skills/xitool/SKILL.md): how to run
  `xi`, the FFXI data quirks (Y-down, the correction node, FTABLE), and where each
  format is documented.

If you find them disagreeing, follow this file and fix the other one in the same PR.

---

## Contents

1. [Before you change anything](#1-before-you-change-anything)
2. [What xi-tools is](#2-what-xi-tools-is)
3. [The rules at a glance](#3-the-rules-at-a-glance)
4. [The established core: don't change it](#4-the-established-core-dont-change-it)
5. [New content goes through `xi dats`](#5-new-content-goes-through-xi-dats)
6. [A flat command tree](#6-a-flat-command-tree)
7. [Simple commands, options for complexity](#7-simple-commands-options-for-complexity)
8. [Adding a `dats` action type](#8-adding-a-dats-action-type)
9. [JSON formats and schemas](#9-json-formats-and-schemas)
10. [Code conventions](#10-code-conventions)
11. [Documentation: everything in `docs/`](#11-documentation-everything-in-docs)
12. [Tests](#12-tests)
13. [Commits and pull requests](#13-commits-and-pull-requests)
14. [Anti-patterns](#14-anti-patterns)
15. [For AI assistants: what to keep in memory](#15-for-ai-assistants-what-to-keep-in-memory)
16. [Revision history](#16-revision-history)

---

## 1. Before you change anything

Read, in this order:

1. **This file.**
2. [`QUICKY.md`](QUICKY.md): the public command surface. A command not listed there is
   hidden, renamed or gone.
3. `uv run xi <group> <cmd> --help`: the real flags (docs can lag the code).
4. `docs/<area>/`: behaviour, examples, formats, what has been verified in game.
5. `src/xi/<area>/`: the code you are about to touch, **and the closest existing
   feature to the one you are adding.** Copy its shape: its module layout, option
   names, output folders, docs page and tests.

The codebase is the style guide. When this file doesn't cover something, do what the
nearest existing code does. Don't introduce a second way of doing something the code
already does one way.

---

## 2. What xi-tools is

- **One CLI, `xi`** (Click, [`src/xi/xi_cli.py`](src/xi/xi_cli.py)), over a library in
  `src/xi/`. Each top-level group is a domain of the game: `zone`, `gear`, `mount`,
  `anim`, `ability`, `audio`, `event`, …
- **It edits a real game install in place** (`FFXI_DIR`), keeping a pristine
  `<file>.base` beside every file it touches, or writes an override tree instead
  (`FFXI_PIVOT_DIR`, "pivot").
- **Front-ends drive the same CLI.** [xi-model-viewer](https://github.com/vekien/xi-model-viewer)
  and xi-zone-editor call `xi` with arguments, or through `xi bridge`, and read some of
  its output. So every command must be drivable by arguments alone, and console
  output they parse is an interface.
- **New content is data kept in Git.** A `dats` project (`projects/<name>.json`) says
  what to add; `xi dats build` places it and records what it did, so the same JSON
  rebuilds the change on another install or after a client update, and `undo` takes it
  back out.

Two lanes, and every feature belongs to one of them:

```text
                     domain commands (xi zone, xi gear, xi tex …)
  game DATs ── export / json / search ───────────────▶ exports/<area>/…   (your art)
      ▲    ◀── import / set / edit / copy / reset ────  (change what exists, in place, .base kept)
      │
      │                      xi dats (the only way to ADD)
      └── build · undo ◀── projects/<name>.json ◀── prepare --type … | new (wizard)
          package · release     (schema'd actions + recorded `result`)
```

---

## 3. The rules at a glance

1. **Follow what is already written.** Extend the existing pattern; don't invent a
   parallel one.
2. **Don't change the established core**: config loading, path and file-id resolution,
   in-place writes with `.base`, the build redirect, pivot (§4).
3. **Anything that adds new content goes through `xi dats`** as an action type, built
   by `xi dats build`, with an interactive wizard in `xi dats new` and a scripted path in
   `xi dats prepare --type`. Domain commands only export, import, inspect and
   manipulate what exists. No `xi spell create` (§5).
4. **A flat command tree.** Domains sit at the root (`xi spell`, `xi ability`), never
   under an umbrella (`xi actions spells`). Two levels: `xi <domain> <verb>` (§6).
5. **Simple commands; options carry the complexity.** One verb per command, a default
   for every option, flags for variants, no prompts outside `xi dats new` (§7).
6. **Every JSON format has a schema** in `schema/` (§9).
7. **Everything is documented in `docs/`**, in the same PR as the code (§11).
8. **Every write can be previewed and undone**: `--dry-run`, `.base`, `xi dats undo`.
9. **Small PRs that look like the rest of the codebase**: one topic, code + docs +
   schema + tests together, no drive-by refactors (§13).

---

## 4. The established core: don't change it

These mechanisms are settled. Every area uses them, and the viewer, the zone editor
and existing projects rely on how they behave. **Call them. Don't re-implement,
bypass, "simplify" or "fix" them in passing.**

| Mechanism | Where | What that means for new code |
|---|---|---|
| Config / `.env` loading | `xi_config._load_dotenv`: `XI_ENV_FILE`, then next to the executable, the repo root, the cwd; the first file found wins, real environment variables always win, `_NOT_FROM_DOTENV` keys only from the environment | A new setting is one `os.environ.get('KEY', default)` in `xi_config.py` plus a commented line in `.env.sample` (and the README table if users set it). Never read `.env` anywhere else or add another loader. |
| The `FFXI_DIR` gate | `_require_ffxi_dir` in `xi_cli.py`, `xi_config.require_ffxi_dir()` | Every command aborts without an install, except `--help` and `xi bridge`. Don't add exemptions; keep `--help` working without one (no install access at import time). |
| DAT path arguments | ROM-relative arguments (`ROM/1/41`, `ROM10/2/0.DAT`, `.DAT` optional) resolved against `FFXI_DIR`; absolute paths also work | Take DAT arguments the same way and resolve them with the helpers in §4.1. Never join `FFXI_DIR` with a string yourself. |
| File-id resolution | `FTABLE.DAT` / `VTABLE.DAT` and the `ROM{n}` pairs, in `xi.ftable.xi_core` | Resolve a file_id → DAT through these helpers. `scan_file_ids` reads the install; `resolve_dat_in_root` reads a root (install or pivot) the way the client and XIPivot do: the root's `ROM{n}` pair first, then the install's main pair (a pivot folder's main pair is never read). |
| DAT sections | read: `parse_sections` (`xi.entity.anim.xi_export`, re-exported by the mesh, fx, tex and zone modules); write headers: `xi.common.xi_section` (`encode_section_meta`, `set_section_size`) | Use them; don't write another section walker or header packer. |
| In-place edits and `.base` | `xi_config.editable_dat`, `ensure_base`, `pristine`, `reset_to_base`, `read_path_for`, `output_path_for` | Open a DAT for writing only through `editable_dat` (or the area helper that calls it). The first write keeps `<file>.base`; reset commands restore from it. Don't break this contract. |
| The build redirect | `xi_config._REDIRECT_DIR`, set only by `xi dats build` | While a build runs, writes land in its target instead of the install. Internal: never set it elsewhere, never expose it as configuration. |
| Pivot | `FFXI_PIVOT_DIR`; `xi.ftable.xi_expand` (`pivot_root`, `table_roots`, `sync_pivot_from_base`); the `dats` build target (`_target_root('dir'\|'pivot'\|'hd')`) | A build writes the install, then `sync_pivot_from_base()` syncs the custom region of the pivot tables so every table the client loads is the same size (a mismatch crashes it). `--pivot` makes the pivot folder the target instead and skips the sync; a pivot build keeps no `.base` there, and `--reset --pivot` copies the install's pristine table over. New content gets `--pivot` for free by being a `dats` action; don't write into the pivot folder any other way. |
| `--ffxi DIR` | `_apply_ffxi` / `_apply_ffxi_dir` on the title and `ui tex` commands | Points `FFXI_DIR` at another tree (an override root) for one call. It is not pivot; don't mix the two. |
| HD tree | `FFXI_HD_DIR`, `hd_path_for`, `hd_editable_dat` | The HD DAT is its own pristine source; never overwrite it from the vanilla copy. |
| Coordinates | the `ffxi_root_correction` node; Y is down; see SKILL.md §2 | Model relative to the correction node. Never hand-write an axis flip. |
| Custom ranges and bands | `xi_config` constants (`CUSTOM_ROM`, `MAX_ENTITY_MODELID`, `MAX_GEAR_MODELID`, `FX_*_BAND_*`) | Allocate inside them; the animation bands follow the client plugin, never `.env`. |

If you believe one of these is wrong, **don't change it in the PR you're working on.**
Write down the evidence (the bytes, what the client did, the command that shows it)
and raise it with the maintainer in an issue or the PR description. Changing the core
is the maintainer's call.

### 4.1 Canonical helpers

Reach for these before writing any path, table or file logic:

| Need | Call |
|---|---|
| A user's DAT argument (`ROM/1/41`, `128/79`, absolute) → `Path` | `xi.entity.mesh.xi_export.resolve_dat_path` |
| Where to read / write an install DAT | `xi_config.read_path_for` / `output_path_for` |
| Edit a DAT in place, `.base` kept (`fresh=False` to layer on the current bytes) | `xi_config.editable_dat` |
| The install's untouched copy / put a file back | `xi_config.pristine` / `reset_to_base` |
| file_id → DAT in the install | `xi.ftable.xi_core.scan_file_ids` |
| file_id → DAT as the client sees a root (install or pivot) | `xi.ftable.xi_core.resolve_dat_in_root` |
| Read or patch a table pair | `xi.ftable.xi_core.load_tables`, `patch_table` (call `forget_tables()` after writing) |
| A table in a given root (install or pivot), dry-run aware | `xi.dats.xi_stage.read` / `write` |
| Zone id ↔ name ↔ DATs | `xi.zone.xi_list.get_zone_entries`, `zone_file_id`; `xi.zone.xi_inject.zone_model_file_id`, `zone_event_file_id`, `zone_dialog_file_id`, `zone_npc_file_id` |
| Entity modelid → file_id | `xi.entity.xi_core.modelid_to_file_id` |
| Gear race / slot / model id ↔ DAT | `xi.gear.xi_export.resolve_gear_target`, `resolve_gear_dat`; `xi.gear.xi_core.detect_gear`, `normalize_dat` |
| Pivot folder and table sizes | `xi.ftable.xi_expand.pivot_root`, `table_roots`, `sync_pivot_from_base` |
| Placing a DAT at a file_id in a build | `xi.dats.xi_dats._place_raw_dat_in_build` |

Some areas carry their own small resolver (the event and dialogue commands take a file,
a zone id or a zone name). When you work in that area, use its resolver. Don't add
another copy elsewhere.

In new code, read settings at call time (`import xi.xi_config as cfg` …
`cfg.FFXI_DIR`), as `resolve_dat_in_root` does. `from xi.xi_config import FFXI_DIR`
freezes the value when the module is imported, so the module misses `--ffxi` and the
settings the bridge changes while it runs.

---

## 5. New content goes through `xi dats`

**Adding** is anything that puts into the game something it didn't have before:

- a new id or slot: model id, gear window, mount id, animation number, spell or
  command record, item row, key item, NPC, dialog line, event or cutscene;
- a new file placed in the install or pivot tree, or a new file_id registered in the
  FTABLE/VTABLE.

All of that is an **action type of `xi dats`**, built by `xi dats build`. Not a new
command, and not a new flag on a domain command. (Growing a table once so it has room,
`xi ftable expand` or `xi database grow`, is set-up, not adding. What goes into the
room is adding.)

**Domain commands** (`xi zone`, `xi gear`, `xi tex`, `xi ui`, …) only do these:

| Kind | Verbs | Example |
|---|---|---|
| Export | `export`, `json`, `search`, `info`, `list` | `xi zone export ROM/1/41 --fbx` |
| Import (over what exists) | `import` | `xi mesh import ROM/351/102` rebuilds that DAT from its export |
| Manipulate what exists | `set`, `edit`, `copy`, `delete`, `reset`, `set-placement`, … | `xi fx set ROM/1/41 tki --color 00FF00` |
| One-time set-up of an install | `expand`, `grow` | `xi ftable expand`, `xi database grow` |

When you can't tell whether something adds or manipulates, treat it as adding.

**Why `dats`.** It is where new content lives because it:

- supports pipelines: one project can carry gear, an ability, a mount and record edits
  together, and `dats build` places them all in one pass, in order, with the same table
  patching, `.base` backups and pivot handling;
- gives each type its own logic (id allocation, per-race fan-out, companion files,
  server SQL/Lua) behind one interface: `prepare` / `new` / `build` / `changelog` /
  `undo` / `package` / `release`;
- is schema'd JSON, so a script, the model viewer or another AI can write it;
- records what it did on the action (`result`: ids, file ids, DAT paths), so the JSON
  kept in Git recreates the change exactly, and `undo` knows what to clear;
- previews: `--dry-run` plans without writing, and that plan is what the viewer shows
  before the user confirms.

**Both ways in, always:**

- **Interactive:** a wizard branch in `xi dats new`, with defaults preloaded from the
  project's previous action of that type.
- **Arguments only:** `xi dats prepare <source> --type <type> [--flags]` with a default
  for every parameter, then `xi dats build <project>`.

| You want to… | Don't build | Do |
|---|---|---|
| add a spell | `xi spell create`, `xi ui spells add` | a `spell` action: `xi dats prepare fire_vi.json --type spell --project my_spells`, then `xi dats build my_spells` (or the `xi dats new` wizard) |
| add a gear model | `xi gear add HumeMale body …` | a `gear` action (`xi dats new` → Gear) |
| add or rename a zone's NPCs | another `npc add` command | a `zone_npcs` action |
| ship a redrawn UI sheet or music track | `xi ui tex install` | a `copy` action |
| add a new kind of content | a new command group with its own placement code | a new `dats` action type (§8) |
| look up spells | — | fine on a domain group: `xi spell json`, `xi spell search` |
| change an existing texture | — | fine on a domain group: `xi tex import` |

**Convenience aliases.** A few domain commands already exist as a thin alias of
`prepare` + `build`: `xi ability publish`, `xi event cutscene compile`,
`xi event dialogue new`. They stay, and stay thin. **Don't add new ones**; the way to add
content is the `dats` action, and a user or front-end calls `dats prepare` + `dats build`.

**Commands that add on their own today** predate this rule: `xi zone new`,
`xi mount import` (and the hidden `mount inject`), the hidden `xi gear inject`,
`xi entity inject` and `xi zone inject`, `xi ui items <type> inject`, `xi event npc add`
and `xi audio install`. Most already have a `dats` equivalent (`mount`, `gear`,
`entity`, `database`, `zone_npcs`, `copy`). Don't extend them. When one needs work,
move its logic into a `dats` action type and make the old command a hidden alias of
`prepare` + `build`.

---

## 6. A flat command tree

- **`xi <domain> <verb>`.** Two levels. A domain is a thing in the game (`zone`, `gear`,
  `mount`, `spell`, `ability`, `anim`, `audio`), not a category of things.
- **No umbrella groups.** Not `xi actions spells` / `xi actions abilities`, not
  `xi content gear`, not `xi edit zone`. Spells and abilities are each at the root:
  `xi spell …`, `xi ability …`.
- **A new domain is a new top-level group** in its own package `src/xi/<domain>/`, with
  its own `cli.py` when it has several commands, registered in `xi_cli.py` under its own
  `# ── <domain>` section.
- **A variant is an option, not a sub-group.** The codebase has already flattened this
  way, and new work should look like the result:

  | Was | Now |
  |---|---|
  | `xi audio music list`, `xi audio sfx list` | `xi audio json --type music\|sfx` |
  | `xi zone object …` | `xi object …` |
  | `xi entity mesh …`, `xi entity anim …` | `xi mesh …`, `xi anim …` |

- **Existing deeper paths stay as they are**: `xi ui items <type> …`, `xi ui tex …`,
  `xi ui layout …`, `xi ui strings …`, `xi anim schedule …`, `xi dll ffximain …`,
  `xi title camera …`, `xi event cutscene|dialogue|npc …`, `xi ftable expand entity|gear`.
  Don't add a level beneath them, don't copy their shape for new work, and don't
  restructure them in an unrelated PR.
- **Command names are kebab-case** (`import-json`, `set-placement`). The snake_case
  names under `xi batch` are old; don't copy them.
- **Renames keep the old name working.** Register the new name and keep the old one as
  a hidden alias (`.hidden = True`; for a second name, `copy.copy` the command, as
  `xi_cli.py` does to keep `gear recolor` working after it became `gear edit`). Never remove a public command name
  in a PR. Note the rename in `QUICKY.md` and SKILL.md §3.

---

## 7. Simple commands, options for complexity

- **One command, one verb, one thing.** `xi zone export ROM/1/41` works with no flags;
  `--fbx`, `--objects`, `--sub-areas`, `--unreal` refine it. Not `export-fbx`,
  `export-objects`.
- **Positional arguments name the thing acted on**: a DAT path, a zone id or name, a
  race and slot, a project. Everything else is an option.
- **Every option has a default** that does the common case. Nothing prompts except the
  `xi dats new` wizard, so scripts (`xi run`) and the viewer can drive any command.
- **Presets are options too**: `--unreal` bundles several flags; it is not a command.
- **Find the obvious input yourself**: `xi mesh import ROM/351/102` finds its export
  (`--source-dir` points it at another folder).
- **Reuse the flag vocabulary before inventing a name:**

  | Flag | Meaning |
  |---|---|
  | `--dry-run` | plan and report, write nothing (required on anything that writes the install) |
  | `--pivot` | write `FFXI_PIVOT_DIR` instead of `FFXI_DIR` |
  | `--ffxi DIR` | read and write another game tree for this one call |
  | `--output PATH` / `--output-dir DIR` | where an export or JSON goes (default under `exports/<area>/…`, or stdout for `json`) |
  | `--source-dir DIR` | where an import reads from instead of the folder its export writes (`exports/<area>/…`); the folder is only read |
  | `--force` | overwrite something the command would otherwise refuse |
  | `--replace` / `--merge` | `dats prepare`: swap the action with the same id / add to it |
  | `--only A,B` | limit a multi-target command to some targets |
  | `--all` | include what is hidden by default |
  | `--lang en\|jp` | pick a language table |
  | `--json` (as `as_json`) | print machine-readable JSON instead of a table (`--as-json` in `xi ui` is older; don't copy it) |

- **Kebab-case option names**, `--x/--no-x` pairs for booleans that default on.
- **Help text**: the docstring's first line is the summary; `\b` blocks keep an
  `Examples:` list intact (see `ftable expand` in `xi_cli.py` or `check_cmd` in
  `xi.server.xi_check`).
- **Errors** a user can fix are a `click.ClickException` that says what is wrong and
  what to do, not a traceback. Library code raises its own exception (like `GrowError`
  in `xi.database.xi_grow`) and the command turns it into a `ClickException`.
- **Console style**: `click.echo`; `click.style` green for success (often a leading
  `✓`), cyan for dry-run notes (`Dry run — nothing written.`), yellow for warnings;
  progress lines go to stderr (`err=True`) so stdout stays clean for `--json`.
- **Outputs** go under `exports/<area>/<rom path>/<stem>/` (for example
  `exports/mesh/rom/351/102/`). `exports/` is the user's master art: build products only
  land in a folder named for the thing being built.
- **Console output that tools parse is an interface.** The model viewer reads lines such
  as the `db:` / `menu:` lines of an ability build. Don't reword them; add new lines
  instead.

---

## 8. Adding a `dats` action type

All of this lives in [`src/xi/dats/xi_dats.py`](src/xi/dats/xi_dats.py) unless it says
otherwise. Look at the most recent types (`database`, `zone_dialog`, `zone_npcs`,
`zone_events`, `copy`) and do what they do.

1. **Builder**: `_build_<type>(action, manifest_path, manifest, force, dry_run)` returns
   the placement result. `dry_run` plans without writing. Reuse
   `_place_raw_dat_in_build` for any verbatim DAT placement rather than patching tables
   yourself.
2. **Dispatch and result**: a branch in `build_cmd`'s `_dispatch`, and, when the type
   places DATs or registers file ids, in the `pack_actions` type list (it drives the
   FTABLE check and the pivot sync); the `result` recorded on the action after the build
   (`_plan_result` when the allocation follows from the definition, else the build's own
   result when it is decided against the live tables).
3. **Reporting and undo**: `_action_summary`, `_result_rows`, `_print_placements`,
   `_action_placements` and `_project_dat_rels`, so `changelog`, the build listing,
   `undo` and `package` all see the type.
4. **Both ways in**: a `_wizard_<type>` branch in `dats new` (defaults preloaded from a
   previous action of that type) **and** `dats prepare <source> --type <type> [--flags]`
   with every parameter defaulted. `prepare --replace` must keep a recorded `result`
   (and the target, unless a flag changed it) so a re-prepare never loses the slot a
   build took.
5. **Library in its own package** (`xi.ability.xi_publish`, `xi.mount.xi_core`,
   `xi.database.xi_build`, …). `xi_dats.py` wires it; it doesn't hold the domain logic.
6. **Schema**: `schema/<type>.json`, listed in `schema/package.json`'s action `oneOf`
   (§9).
7. **Docs**: `docs/dats/README.md` (a wizard section and a "Current builders" entry),
   the domain doc, `QUICKY.md`, SKILL.md (§11).
8. **Tests**: build, dry run, rebuild on top, `--reset`, `undo`, `prepare --replace`
   keeping the result, and the schema examples (`tests/test_dats_*.py`).

---

## 9. JSON formats and schemas

Any JSON the tools read or write as an interchange format (a `dats` action, a recipe, a
definition, a manifest, an export) gets a file in [`schema/`](schema/):

- JSON Schema 2020-12, `$id` `https://xi.tools/schema/<name>.json`,
  `additionalProperties: false`, and an `examples` entry that is a real working document.
- Documents carry a `schema` field (`"xi.<area>.v1"`) so a file says what it is.
- Shared pieces (`romPath`, `actionId`, `autoOrInteger`, `outputTree`, `includePath`)
  come from [`schema/common.json`](schema/common.json). Reference them; don't redefine them.
- Action types are listed in [`schema/package.json`](schema/package.json)'s action `oneOf`.
- `jsonschema` is **not** a dependency. The loader validates by hand and names the
  offending field (pattern: `xi.ability.xi_compose.validate_recipe`). Keep the validator
  and the schema file in step, and test the schema's `examples` against the validator
  (`tests/test_dats_ability.py`); `tests/_minischema.py` checks a command's real output
  against its schema file.
- Changing a format: a breaking change gets a new version (`xi.<area>.v2`) and the
  loader keeps reading the old one, or refuses it with a message that says what
  changed (as `like` → `copy_from` does).

---

## 10. Code conventions

- **Layout.** A command lives in `src/xi/<area>/xi_<verb>.py`; an area's shared
  parsing in `src/xi/<area>/xi_core.py`; a group with several commands wires them in its
  own `cli.py` (`xi.dll.cli`, `xi.ability.cli`); every group is registered in
  `src/xi/xi_cli.py` under its own `# ── <area>` section. Uniform `json` / `search`
  commands live in `xi_simplified.py`.
- **Thin commands.** The Click function (named `cmd`, or `<verb>_cmd` when a module
  holds several) parses options and calls library functions that return data (a dict)
  and that the `dats` builder, `xi bridge` and the tests can call too. The command only
  formats the result.
- **Module docstring** first, naming the command and explaining the design
  (``"""``xi server check``: …"""``).
- **Match the surrounding code**: naming, type hints, comment density. Comments say
  *why*, with the evidence (the bytes measured, what the client did, the plugin value
  followed; see the band notes in `xi_config.py`). Don't narrate what the code does.
- **No drive-by changes**: no refactors, renames, reformatting or import reshuffles
  outside the task.
- **Dependencies**: avoid new ones. If one is unavoidable, add it to `pyproject.toml`
  **and** `requirements.txt` (the launcher's embedded Python installs from it).
- **Python ≥ 3.11 syntax** (3.14 recommended). Windows is the main platform, Linux must
  work too: `pathlib`, explicit `encoding="utf-8"`, external tools through their
  `xi_config` path (`TEXCONV_PATH`, `BLENDER_PATH`), no shell-specific commands.
- **Formats are documented, never invented.** Read `docs/` before writing a parser or
  writer. When a doc and the code disagree, trust bytes verified in game and say which
  one you followed. Mark what is measured and what is inference.
- **Model-viewer pick lists** are targets of `xi mv update` writing `mv/lists/`
  (published through its `manifest.json`), never a file each user builds under
  `exports/`. Targets are append-only or name-preserving: never drop a curated name.
- **Never commit** `.env`, `exports/`, `projects/`, `*.DAT.base`, game trees, `mv/db/`, or
  large binaries. Bundled binaries (`misc/*.exe`, `src/xi/libs/`) change only when the
  maintainer asks.

---

## 11. Documentation: everything in `docs/`

Every user-visible change is documented in `docs/` in the same PR as the code. Docs
describe how things work now; they are not a changelog.

| When you… | Update |
|---|---|
| add or change a command or flag | `docs/<area>/<cmd>.md` (or the area's `README.md`), `QUICKY.md`, the command's `--help` |
| add a domain group | `docs/<area>/README.md`, its row in [`docs/README.md`](docs/README.md), the table in SKILL.md §3, the top-level list in `QUICKY.md`, a short entry in the README's Features |
| add a `dats` action type | `docs/dats/README.md` (wizard section + "Current builders"), the domain doc, `QUICKY.md`, SKILL.md |
| learn something about a format | `docs/<area>/format.md` or `docs/dats/ROM_<a>_<b>.md`, indexed in `docs/README.md` / `docs/dat_index.md`; say what is measured and what is inferred |
| find a crash cause | [`docs/common_crashes.md`](docs/common_crashes.md) |
| add a model-viewer list | [`docs/mv/README.md`](docs/mv/README.md) |
| add a setting | `.env.sample`, and the README's Environment Variables table if users set it |
| rename anything | every doc that mentions the old name (grep for it) |

How the docs are written:

- Plain language. Lead with what it does and a command that runs, then a table of
  options, then the details and the in-game notes.
- ROM-relative paths (`ROM/1/41`), real ids and real output in examples.
- Say what has been verified in game, and what hasn't yet.
- `CHANGELOG.md` and version numbers are written by the maintainer when a release is
  cut (`changelog: vX.Y.Z` commits, the `xi-tools-deploy` workflow). Don't edit them in
  a PR unless asked; the PR description is where the plain-language summary goes.

---

## 12. Tests

- `tests/test_<area>_<thing>.py`, pytest. Prefer synthetic bytes. A test that needs
  the game takes the `root` fixture (`tests/conftest.py`) and skips without `FFXI_DIR`.
- Every schema's `examples` validate against its hand validator; a command's JSON
  output is checked against its schema with `tests/_minischema.py`.
- A `dats` type is tested for build, dry run, rebuild on top, `--reset`, `undo` and
  `prepare --replace` (the `tests/test_dats_*.py` files show how).
- Run the suite before you push, and say the result in the PR:

  ```bash
  uv run --with pytest pytest -q
  uv run xi --help && uv run xi <group> <cmd> --help   # must work without FFXI_DIR
  ```

  Tests that need an install fail or skip without one. Report them as such ("the N
  failures are the same as on main, all needing FFXI_DIR"), don't hide them.

---

## 13. Commits and pull requests

**Branches.** One branch per change, off `main`. One topic per PR; a follow-up is a
new PR.

**Commit subject**: `<area>: <what changed, in plain words>`.

- `<area>` is the command group or folder, lowercase: `dats:`, `zone export:`,
  `ability:`, `database:`, `docs:`, `textures:`.
- The rest states the new behaviour as a fact, not "fix bug" or "update code". From
  the history:
  - `dats: a pivot build registers a file_id, it does not raise the ceiling`
  - `ability: the job-ability band starts at 1024, above the teleport effects`
  - `zone export: --sub-areas writes each sub-area as its own file`

**Commit body**: what changed and why, in prose; name the commands, flags and files;
give the evidence for format or client-behaviour claims. End with the tests you added
and the suite result.

**Pull request.**

- Title in the same style as a commit subject.
- Fill in [the template](.github/pull_request_template.md): what and why in plain
  language, commands to try, the checklist, the test result.
- One PR carries the code **and** its docs, schema, tests, `QUICKY.md` and SKILL.md
  updates. A PR that adds a command without docs isn't finished.
- No generated output, no version bump, no `CHANGELOG.md` edit unless asked.
- A reviewer (person or AI) checks the PR against this file first.

---

## 14. Anti-patterns

| Don't | Do instead |
|---|---|
| `xi spell create`, `xi gear add`, `xi mount new` | a `dats` action type (§5, §8) |
| a `--add` / `--new` flag on an export or import command | a `dats` action type |
| `xi actions spells …`, `xi content …` | `xi spell …` at the root (§6) |
| `xi zone export-fbx` | `xi zone export --fbx` (§7) |
| a prompt in an ordinary command | an option with a default; wizards only in `xi dats new` |
| your own `.env` parsing, `FFXI_DIR` joins, FTABLE lookups or section walker | the helpers in §4 |
| writing into `FFXI_PIVOT_DIR` directly | `--pivot` on a `dats` build |
| opening a DAT with `open(path, "r+b")` | `editable_dat`, so `.base` is kept |
| a JSON file with no schema | `schema/<name>.json` + a `schema` field (§9) |
| `jsonschema` or another new dependency | a hand validator, as the others are |
| guessing a binary layout | read `docs/`, measure, document what you found |
| removing or renaming a command outright | a new name plus a hidden alias (§6) |
| rewording console lines the viewer parses | add a line (§7) |
| writing test output into `exports/` | a temp dir, or a folder named for the build |
| refactoring code next to your change | leave it, or propose it in its own PR |
| changing the core because it looked wrong | an issue with the evidence (§4) |

---

## 15. For AI assistants: what to keep in memory

Save this block to your persistent memory or project instructions, as written, and
keep it until the revision number changes:

```text
xi-tools (vekien/xi-tools), design doc DESIGN.md revision 2 (2026-09-30):
- Before any change: read DESIGN.md, QUICKY.md, `xi <group> <cmd> --help`, docs/<area>/,
  and the closest existing feature. Copy its shape; don't invent a parallel way.
- Don't change the core: .env loading (xi_config), path/file-id resolution
  (ROM-relative args, FTABLE/VTABLE, resolve_dat_in_root), in-place writes with .base
  (editable_dat), the build redirect, pivot (FFXI_PIVOT_DIR, --pivot, pivot sync).
  Call the helpers; raise doubts with evidence instead of editing them.
- Anything that ADDS content (new id/slot/record/NPC/line/event/file/file_id) is an
  `xi dats` action type: _build_<type>, dispatch, result, changelog/undo/package hooks,
  a `dats new` wizard branch AND `dats prepare --type` with every flag defaulted.
  Never a domain command like `xi spell create`; no new convenience aliases.
- Domain commands only export / import / json / search / inspect / manipulate what
  exists / one-time set-up.
- Flat tree: `xi <domain> <verb>`, domains at the root (xi spell, xi ability), no
  umbrella groups, variants are options. Renames keep a hidden alias.
- Simple commands: positional = the thing acted on; every option defaulted; no
  prompts outside `dats new`; --dry-run on writes; --help works without FFXI_DIR;
  reuse flag names (--dry-run --pivot --ffxi --output --source-dir --force --only --all
  --json).
- Every JSON format: schema/<name>.json (2020-12, additionalProperties false, real
  examples, "schema": "xi.<area>.v1"), hand validator in step, test over examples.
- Document everything in docs/ in the same PR (+ QUICKY.md, docs/README.md, SKILL.md).
- Tests in tests/, `uv run --with pytest pytest -q`, report the result.
- Commits/PR titles: "<area>: <what changed, in plain words>"; one topic per PR; no
  drive-by refactors, generated output, version bumps or CHANGELOG edits.
```

When a request conflicts with this file (for example, "add `xi spell create`"), say
which section it conflicts with and offer the design-conforming way. Go ahead with the
conflicting version only if the maintainer confirms it, and then update this file in
the same PR so the rule and the code agree.

---

## 16. Revision history

Bump the revision (top of the file and the §15 block) whenever a rule changes, so
assistants that saved an older one know to re-read.

| Rev | Date | Change |
|---|---|---|
| 1 | 2026-09-29 | First version: the core not to change, `xi dats` as the only way to add, the flat tree, simple commands, schemas, docs, tests, commits and PRs. |
| 2 | 2026-09-30 | `--source-dir DIR` joins the flag vocabulary (§7): where an import reads from instead of its export folder. |
