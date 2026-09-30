<!--
Title: `<area>: <what changed, in plain words>`, e.g. `dats: zone_npcs action`
or `zone export: --sub-areas writes each sub-area as its own file` (DESIGN.md §13).
One topic per PR. Code, docs, schema and tests for it go in this PR together.
-->

## What and why

<!-- In plain language: what a user can do now that they couldn't, or what was wrong
and is now right. The maintainer lifts the CHANGELOG entry from here. -->

## Try it

```bash
uv run xi … --dry-run
uv run xi …
```

## Checklist ([DESIGN.md](https://github.com/vekien/xi-tools/blob/main/DESIGN.md))

- [ ] I read DESIGN.md (revision noted at its top) and followed the existing code closest to this change.
- [ ] The established core is untouched: config loading, path and file-id resolution, `.base` / in-place writes, the build redirect, pivot (§4). If not, the maintainer asked for it and the PR says why.
- [ ] Nothing new is added to the game outside `xi dats`: new content is a `dats` action type with a `dats new` wizard branch and a `dats prepare --type` path (§5, §8). No `create` / `add` / `new` command on a domain group.
- [ ] No new nesting: a new domain is a group at the root, variants are options (§6, §7). Renamed commands keep a hidden alias.
- [ ] Commands that write have `--dry-run`, keep the `.base` contract, and their `--help` works without `FFXI_DIR`.
- [ ] Any new or changed JSON format has its file in `schema/`, a `schema` field, a hand validator in step, and a test over its `examples` (§9).
- [ ] Docs updated in `docs/`, plus `QUICKY.md`, `docs/README.md` and the SKILL.md table where they apply (§11).
- [ ] No generated output committed (`exports/`, `projects/`, `*.base`, `.env`, `mv/db/`), no version bump or CHANGELOG edit unless asked.

## Tests

<!-- Which tests you added, and the suite result, e.g.
`uv run --with pytest pytest -q`: 560 passed; the 19 failures are the same as on main, all needing FFXI_DIR. -->
