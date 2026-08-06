"""Run *inside* Blender (``blender -b --python xi_gltf_to_fbx.py -- <in.gltf> <out.fbx>``).

Imports a glTF animation export (the .gltf plus its sibling .bin) and re-exports the
skinned mesh + skeleton + animation as FBX. Unlike the mesh exporter's converter
(``xi_glb_to_fbx.py``), this bakes the animation track (``bake_anim=True``) so DCC
tools such as Cinema 4D / Maya / 3ds Max read the motion. The anim glTF carries no
textures, so there is no image-rewiring step here.
"""

import sys

import bpy


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    gltf_in, fbx_out = argv[0], argv[1]

    # Clear the startup scene (default Cube/Camera/Light) before importing.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    bpy.ops.import_scene.gltf(filepath=gltf_in)

    # Blender's glTF importer spawns a stray placeholder Ico Sphere — an unparented
    # ~42-vertex mesh that is NOT in our glTF — alongside the real model. Drop every
    # mesh that is neither parented to nor skinned by an armature so it can't leak
    # into the exported FBX.
    armatures = {obj for obj in bpy.data.objects if obj.type == "ARMATURE"}
    for obj in list(bpy.data.objects):
        if obj.type != "MESH":
            continue
        bound = obj.parent in armatures or any(
            mod.type == "ARMATURE" and mod.object in armatures for mod in obj.modifiers)
        if not bound:
            bpy.data.objects.remove(obj, do_unlink=True)

    # The glTF importer leaves the scene on its default 1-250 frame range; widen it
    # to span every imported action so the FBX bake covers the whole clip.
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


if __name__ == "__main__":
    main()
