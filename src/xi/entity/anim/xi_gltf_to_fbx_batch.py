"""Run *inside* Blender to convert MANY glTF animation exports to FBX in one process:

    blender -b --python xi_gltf_to_fbx_batch.py -- <manifest.json>

``manifest.json`` is a list of ``[gltf_in, fbx_out]`` pairs. Launching Blender once
and looping is the only feasible way to bake thousands of per-track clips (a fresh
``blender`` process per file would take days). This is the batch sibling of
``xi_gltf_to_fbx.py``; per-clip behaviour (drop the importer's stray Ico Sphere,
widen the frame range, ``bake_anim=True``) is identical.

Progress lines (``[i/N] ok|FAIL  <fbx>``) are printed to stdout so the caller can
stream them; a final ``BATCH DONE ok=.. fail=..`` summary line is emitted. Failures
are isolated per clip — one bad glTF does not abort the rest.
"""

import json
import sys
import traceback

import bpy


def _clear_scene() -> None:
    """Wipe the scene AND purge orphan datablocks so memory doesn't grow across
    thousands of import/export cycles."""
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    # Recursively free meshes/armatures/actions/etc. left unreferenced by the delete.
    for _ in range(4):
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True,
                                       do_recursive=True)


def _convert_one(gltf_in: str, fbx_out: str) -> None:
    _clear_scene()
    bpy.ops.import_scene.gltf(filepath=gltf_in)

    # Blender's glTF importer spawns a stray placeholder Ico Sphere — an unparented
    # mesh that is NOT in our glTF. Drop every mesh not bound to an armature.
    armatures = {obj for obj in bpy.data.objects if obj.type == "ARMATURE"}
    for obj in list(bpy.data.objects):
        if obj.type != "MESH":
            continue
        bound = obj.parent in armatures or any(
            mod.type == "ARMATURE" and mod.object in armatures for mod in obj.modifiers)
        if not bound:
            bpy.data.objects.remove(obj, do_unlink=True)

    # Widen the scene frame range to span every imported action so the bake is whole.
    if bpy.data.actions:
        starts = [action.frame_range[0] for action in bpy.data.actions]
        ends = [action.frame_range[1] for action in bpy.data.actions]
        bpy.context.scene.frame_start = int(min(starts))
        bpy.context.scene.frame_end = int(round(max(ends)))

    bpy.ops.export_scene.fbx(
        filepath=fbx_out,
        path_mode="ABSOLUTE",
        add_leaf_bones=False,
        bake_anim=True,
        bake_anim_use_all_actions=True,
        bake_anim_use_nla_strips=False,
        use_custom_props=True,
    )


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    manifest_path = argv[0]
    with open(manifest_path, "r", encoding="utf-8") as f:
        pairs = json.load(f)

    ok = fail = 0
    total = len(pairs)
    for i, (gltf_in, fbx_out) in enumerate(pairs, 1):
        try:
            _convert_one(gltf_in, fbx_out)
            ok += 1
            print(f"[{i}/{total}] ok   {fbx_out}", flush=True)
        except Exception:  # noqa: BLE001 — isolate per-clip failures
            fail += 1
            print(f"[{i}/{total}] FAIL {fbx_out}", flush=True)
            traceback.print_exc()

    print(f"BATCH DONE ok={ok} fail={fail}", flush=True)


if __name__ == "__main__":
    main()
