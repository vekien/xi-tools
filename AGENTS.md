# Agent notes — xi-tools

Read `.claude/skills/xitool/SKILL.md` first: how to run `xi`, the FFXI data quirks, and
where every format is documented. This file is the short list of rules for *changing*
the library.

## New content goes through `xi dats` (hard rule)

Anything that **puts new content into the game** — a model, gear, a mount, an ability,
a zone patch, a texture pack, an event — is an **action type of `xi dats`**, not a
command with its own placement code. `xi dats` is where new content lives because it:

- supports bulk pipelines: one manifest can carry gear, an ability and a mount together,
  and `dats build` places all of them in one pass, in order, with the same table
  patching and `.base` backups;
- carries custom logic per type (id allocation, per-race fan-out, companion files,
  server snippets) behind one interface: `prepare` / `new` / `build` / `changelog` /
  `undo` / `package` / `release`;
- **records what it did on the action** (`result`: ids, file ids, DAT paths) so the same
  JSON, kept in Git, recreates the change exactly on another install or after a client
  update, and `undo` knows what to clear.

Adding a type means all of these, in `src/xi/dats/xi_dats.py`:

1. `_build_<type>(action, manifest_path, manifest, force, dry_run)` — returns the
   placement result; `dry_run` must plan without writing (the plan is what the viewer
   shows before confirming). Reuse `_place_raw_dat_in_build` for any verbatim DAT
   placement rather than patching tables yourself.
2. A branch in `build_cmd`'s `_dispatch` and in the `pack_actions` type list; the inline
   `result` recorded after the build (either `_plan_result` when the allocation is
   deterministic from the definition, or the build's own result when it is decided
   against the live tables).
3. `_action_summary`, `_result_rows`, `_print_placements`, `_action_placements` and
   `_project_dat_rels` so `changelog`, the build listing, `undo` and `package` all see
   it.
4. **Both ways in**: a `_wizard_<type>` branch in `dats new` (interactive, defaults
   preloaded from a previous action of that type) **and** the non-interactive path —
   `dats prepare <source> --type <type> [--flags]` with every parameter defaulted, so a
   script or the model viewer can drive it with arguments alone. `prepare --replace`
   must preserve a recorded `result` (and the target block unless a flag changed it)
   so a re-prepare never loses the slot a build took.
5. The library behind the type lives in its own package (`xi.ability.xi_publish`,
   `xi.mount.xi_core`, …); the domain command group may keep a convenience command,
   but it must be a thin alias of `prepare` + `build` (see `xi ability publish`), never
   a second implementation of placement.
6. Docs: `docs/dats/README.md` (wizard section + builders table), the domain doc, and
   `QUICKY.md`.

If an existing command places content on its own today, the right fix is to move it
into a `dats` action and alias the old command, not to extend it.

## Every JSON format has a schema (hard rule)

Any JSON the tools read or write as an interchange format — a `dats` action, a recipe, a
manifest, an export — gets a file in `schema/` (JSON Schema 2020-12, `$id`
`https://xi.tools/schema/<name>.json`, `additionalProperties: false`, an `examples`
entry that is a real working document). Rules:

- Action types are listed in `schema/package.json`'s `actions.items.oneOf`.
- Documents carry a `schema` field (`"xi.<area>.v1"`) so a file says what it is.
- `jsonschema` is not a dependency: the loader validates by hand (see
  `xi.ability.xi_compose.validate_recipe`) and names the offending field; keep the
  validator and the schema file in step, and test the schema's `examples` against the
  validator (`tests/test_dats_ability.py` is the pattern).
- Shared pieces (`romPath`, `actionId`, `autoOrInteger`, `outputTree`) come from
  `schema/common.json` — reference them, do not redefine them.

## Model-viewer lists

Anything the viewer needs as a pick list is a target of `xi mv update` writing into
`mv/lists/` (published through `manifest.json`), never a file each user has to build
under `exports/`. Targets are append-only or name-preserving: never drop a curated
name. See `docs/mv/README.md`.

## Conventions worth keeping

- Commands live in `src/xi/<area>/xi_<cmd>.py`; a group with several commands wires
  them in its own `cli.py` (`xi.dll.cli`, `xi.ability.cli`), registered in
  `src/xi/xi_cli.py` under its own `# ── <area>` section.
- Outputs go under `exports/<area>/…`; `exports/` is the user's master art, so build
  products only ever land in a folder named for the thing being built.
- Edits are in place with a `.base` backup — never break that contract (`xi_config`).
- Every command has `--dry-run` where it writes into the install, and `--help` that
  works without `FFXI_DIR`.
- Tests that need the game use the `root` fixture (skip without it); everything else
  runs on synthetic bytes.
