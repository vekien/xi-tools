"""Run *inside* Blender (``blender -b --python xi_glb_to_fbx.py -- <in.glb> <out.fbx> <tex_dir>``).

Imports a glTF/GLB, rewires every packed image to a file-backed PNG and connects
it directly to the Principled BSDF Base Color input (the GLB importer routes
through a vertex-color MIX node that Blender's FBX exporter can't trace),
then re-exports geometry + materials as FBX with absolute texture paths.
"""

import os
import re
import sys

import bpy


def _png_for_mat(mat_name: str, tex_dir: str):
    """Return the absolute PNG path for a material, or None if not found."""
    key = re.sub(r"\s+", "_", mat_name.strip())
    if key.endswith("_alpha"):
        key = key[:-6]
    path = os.path.join(tex_dir, key + ".png")
    return path if os.path.exists(path) else None


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    glb_in, fbx_out, tex_dir = argv[0], argv[1], argv[2]

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    bpy.ops.import_scene.gltf(filepath=glb_in)

    leftover = bpy.data.collections.get("glTF_not_exported")
    if leftover:
        for obj in list(leftover.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(leftover)

    used = {
        slot.material.name
        for obj in bpy.data.objects if obj.type == "MESH"
        for slot in obj.material_slots if slot.material is not None
    }
    for material in list(bpy.data.materials):
        if material.name not in used:
            bpy.data.materials.remove(material)

    # For each material: replace the packed GLB image with an external PNG and
    # wire it directly to the Principled BSDF Base Color socket.  The GLB
    # importer routes TEX_IMAGE through a vertex-color MIX node, which breaks
    # Blender's FBX exporter's texture detection (it only traces direct links).
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        png_path = _png_for_mat(mat.name, tex_dir)
        if png_path is None:
            continue
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        tex_node = next((n for n in nodes if n.type == "TEX_IMAGE"), None)
        bsdf_node = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
        if tex_node is None or bsdf_node is None:
            continue
        tex_node.image = bpy.data.images.load(png_path, check_existing=True)
        # Replace intermediate (MIX+vertex-color) link with a direct connection
        links.new(tex_node.outputs["Color"], bsdf_node.inputs["Base Color"])
        # Wire the alpha channel so Blender's FBX exporter can trace it.
        # _alpha materials = FFXI softblend (0x8000): keep BLEND for smooth transparency.
        # CLIP materials = alphaMode MASK from GLB (e.g. foliage cutout): keep threshold.
        key = re.sub(r"\s+", "_", mat.name.strip())
        if key.endswith("_alpha"):
            links.new(tex_node.outputs["Alpha"], bsdf_node.inputs["Alpha"])
            mat.blend_method = "BLEND"
        elif mat.blend_method == "CLIP":
            links.new(tex_node.outputs["Alpha"], bsdf_node.inputs["Alpha"])

    bpy.ops.export_scene.fbx(
        filepath=fbx_out,
        path_mode="ABSOLUTE",
        embed_textures=False,
        add_leaf_bones=False,
        bake_anim=False,
        use_custom_props=True,
    )


if __name__ == "__main__":
    main()
