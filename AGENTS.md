# Agent notes — xi-tools

**Stop: read [`DESIGN.md`](DESIGN.md) in full before you plan, answer a question about
changing xi-tools, or write code.** It is the maintainer's design doc: how xi-tools is
developed, extended and PR'd. Save its §15 block to your memory, and re-read it whenever
its revision number is newer than the one you saved. This file is the one-screen
version; where they differ, DESIGN.md wins.

Then `.claude/skills/xitool/SKILL.md`: how to run `xi`, the FFXI data quirks, and where
every format is documented.

## Hard rules

1. **Don't change the established core**: `.env` loading, path and file-id resolution
   (ROM-relative args, FTABLE/VTABLE, `resolve_dat_in_root`), in-place writes with
   `.base` (`editable_dat`), the build redirect, pivot (`FFXI_PIVOT_DIR`, `--pivot`,
   `sync_pivot_from_base`). Call the helpers listed in DESIGN.md §4.1. If one looks
   wrong, raise it with evidence; don't edit it in passing.
2. **New content goes through `xi dats`.** Anything that puts something new into the
   game (a model, gear, a mount, an ability, a spell, a record, an NPC, a line, an
   event, a file, a file_id) is an **action type of `xi dats`**, not a command with its
   own placement code. No `xi spell create`. A type needs a builder, dispatch, a
   recorded `result`, the changelog/undo/package hooks, a `dats new` wizard branch
   **and** `dats prepare --type` with every flag defaulted (checklist: DESIGN.md §8).
   Don't add new convenience aliases; the existing ones (`xi ability publish`,
   `xi event cutscene compile`, `xi event dialogue new`) stay thin. If an existing
   command places content on its own, the fix is to move it into a `dats` action and
   alias the old command, not to extend it.
3. **Domain commands only export, import, inspect, manipulate what exists, or set an
   install up once** (`export`, `import`, `json`, `search`, `set`, `edit`, `copy`,
   `delete`, `reset`, `expand`, `grow`).
4. **A flat command tree**: `xi <domain> <verb>`, domains at the root (`xi spell`,
   `xi ability`), never under an umbrella (`xi actions spells`). Variants are options.
   A renamed command keeps its old name as a hidden alias.
5. **Simple commands, options for complexity**: positional arguments name the thing
   acted on; every option has a default; nothing prompts outside `xi dats new`;
   `--dry-run` wherever the install is written; `--help` works without `FFXI_DIR`. Reuse
   the flag names in DESIGN.md §7.
6. **Every JSON format has a schema** in `schema/` (2020-12, `$id`
   `https://xi.tools/schema/<name>.json`, `additionalProperties: false`, a real
   `examples` entry, a `"schema": "xi.<area>.v1"` field; action types in
   `schema/package.json`; shared pieces from `schema/common.json`). `jsonschema` is not
   a dependency: keep the hand validator in step and test the examples against it
   (DESIGN.md §9).
7. **Document everything in `docs/`** in the same PR, plus `QUICKY.md`, `docs/README.md`
   and the SKILL.md table where they apply (DESIGN.md §11).
8. **Model-viewer lists** are targets of `xi mv update` writing `mv/lists/`, append-only
   or name-preserving; never a file each user builds under `exports/`
   (`docs/mv/README.md`).

## Conventions worth keeping

- Commands live in `src/xi/<area>/xi_<cmd>.py`; a group with several commands wires
  them in its own `cli.py` (`xi.dll.cli`, `xi.ability.cli`), registered in
  `src/xi/xi_cli.py` under its own `# ── <area>` section.
- Outputs go under `exports/<area>/…`; `exports/` is the user's master art, so build
  products only ever land in a folder named for the thing being built.
- Tests that need the game use the `root` fixture (skip without it); everything else
  runs on synthetic bytes. Run `uv run --with pytest pytest -q` and report the result.
- Commits and PR titles: `<area>: <what changed, in plain words>`. One topic per PR, no
  drive-by refactors, no generated output, no version bump or CHANGELOG edit unless
  asked. Fill in `.github/pull_request_template.md`.
