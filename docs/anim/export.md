# xi anim export

Export an FFXI entity's skeleton + (optional) mesh + animation track(s) to glTF 2.0
for editing in a DCC tool (Blender recommended).

```bash
uv run xi anim export <dat> [--anim NAME] [--fbx] [--output DIR]
                                       [--mesh [LOOK]] [--race NAME] [--skeleton-dat PATH]
uv run xi anim export ROM/217/32 --anim idl
uv run xi anim export rom/37/13 --anim poi --fbx        # emote (see emotes.md)
```

`<dat>` may be a filesystem path or a ROM-relative spec like `ROM/217/32`
(resolved against `FFXI_DIR`; trailing `.DAT` optional).

This is the animation half of the **Mesh → Anim** workflow; for geometry/textures
use [../mesh/export.md](../mesh/export.md). Return trip: [import.md](import.md).
For emotes and skeleton-less DATs, read [emotes.md](emotes.md) first.

## What it does

1. Parses the skeleton (`0x29`), the first skinned mesh (`0x2A`) if present, and the
   chosen animation track (`0x2B`, e.g. `idl0`).
2. Writes a baked glTF 2.0 (`.gltf` + `.bin`) containing:
   - skeleton nodes named `bone0000`, `bone0001`, … under an `ffxi_root_correction`
     node (180° X rotation so the model is right-side-up in the DCC),
   - a `skin` so importers build a real **armature** (emitted even with no mesh),
   - the skinned mesh, if any, as a visual reference while animating,
   - the mesh's **textures**, decoded to PNGs next to the glTF and wired into its
     materials (like `mesh export`) — pass `--no-tex` for a geometry-only export,
   - the animation baked to keyframes at **30 fps**.

Edit geometry with the mesh tools, not here — any mesh is reference only.

## Options

- `--anim NAME` — track to export (default `idl`). A **digit-less** name merges all
  parts of the emote into one full-body clip (see *Digit-less = merged full-body clip*
  below); a numbered name exports just that one part. Loose match (`idl` → `idl0`).
- `--fbx` — also emit an animated `.fbx` (baked via Blender) for DCC tools. The bake
  now carries the **textures** too (unless `--no-tex`).
- `--no-tex` — skip decoding the mesh textures. By default they're written as PNGs
  beside the glTF and wired into its materials; pass this for a geometry-only export.
- `--output DIR` — override the output directory.
- `--race NAME` — base race skeleton + mesh race for **animation-only** DATs (no
  skeleton of their own). **Auto-detected from the DAT id** (motion files are
  race-specific: `rom/37/13` IS HumeFemale's emote file, `rom/61/8` is Galka's) — you
  normally don't pass it. A DAT that carries its **own** skeleton (monsters, NPCs,
  objects) rigs against that and needs **no race at all** — if the id isn't a known PC
  motion file, no race is assumed (it is **not** forced to HumeFemale). An
  animation-only DAT whose race can't be detected errors and asks for `--race` /
  `--skeleton-dat` rather than silently using the wrong skeleton. See [emotes.md](emotes.md).
- `--skeleton-dat PATH` — explicit base skeleton DAT; overrides `--race`.
- `--mesh [LOOK]` — attach a body mesh for visual reference, resolved from gear
  model ids for the `--race`:
  - **bare `--mesh`** → the race's basic **naked body** (gear model 0 for every slot,
    **including the face**).
  - **`--mesh ID,ID,ID,ID,ID,ID`** → a **"look"**: gear model ids mapped to
    face, head, body, hands, legs, feet (omitted trailing slots default to model 0, so
    `--mesh 0,0,5` = naked body wearing body-piece model 5).
  - Also accepts DAT path(s) or a race name (back-compat).
  - Omitted → **skeleton-only** export (the bone rig — still a valid armature).
  Works for any race; a slot whose model id doesn't exist is skipped with a note.

## Output

Defaults to `exports/anim/<rom path>/<stem>_<anim>/`, e.g.:

```
exports/anim/rom/217/32/32_idl/32_idl0.gltf
exports/anim/rom/217/32/32_idl/32_idl0.bin
exports/anim/rom/217/32/32_idl/Skin.png      # textures (unless --no-tex)
```

`anim import` finds this automatically, so you usually don't pass a path on import —
and a **bare glTF name** you drop in that folder (`--layer yap.gltf`, or
`import <dat> tlk yap.gltf`) is resolved here too.

> **Bulk mode:** omit `<dat>` entirely to export **every** track for **every** PC
> race straight from the game (FFXiMain motion tables + FTABLE) into
> `exports/anim/<Race>/<category>/…`. Scope with `--race` / `--category`; this is
> texture-free by design (it would otherwise write a PNG per track across ~180k tracks).

### Digit-less = merged full-body clip

A **digit-less** `--anim` exports the WHOLE emote merged onto one skeleton as a
single full-body clip — every part (lower/upper **and** the waist from the +6
sibling file) combined, animating all joints:

```
uv run xi anim export rom/37/13 --anim poi
→ exports/anim/rom/37/13/13_poi/13_poi.gltf   (animates all 97 joints)
```

Animate the entire body in that one file; `anim import poi` splits it back across
the parts (poi0/poi1 → 37/13, poi2 → 37/19). This is the right call for emotes —
see [emotes.md](emotes.md). A numbered name (`--anim poi1`) exports just that one
part into `<stem>_poi1/`.

## Editing notes (Blender)

- Keep the bone names (`bone0000…`) and hierarchy — the importer matches by name.
- Keyframe the **bones**; any mesh follows via its skin weights.
- A skeleton-only export imports into Blender as an **armature** (the `skin` makes
  this work even with no mesh), so the bones are real and animatable.
- On glTF export from Blender, `+Y Up` and exporting animation are the defaults
  that round-trip correctly.
- If the source track is empty (some idles have **0 tracks**), the export has no
  keyframes to edit — keyframe the bones from scratch, or pick a track with motion.

## Format reference

Quaternions are `(x,y,z,w)`, interpolated with **NLERP** (rotation) and linear
(translation/scale); the root bone's translation is special-cased by the engine.
Full binary layout: [format.md](format.md).
