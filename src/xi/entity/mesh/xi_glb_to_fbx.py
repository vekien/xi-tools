"""Run *inside* Blender
(``blender -b --python xi_glb_to_fbx.py -- <in.glb> <out.fbx> <tex_dir> [bake_anim]``).

Imports a glTF/GLB, rewires every packed image to a file-backed PNG and connects
it directly to the Principled BSDF Base Color input (the GLB importer routes
through a vertex-color MIX node that Blender's FBX exporter can't trace),
then re-exports geometry + materials as FBX with absolute texture paths.

A 4th argument of ``1`` also bakes the GLB's skeletal animation into the FBX. Off by
default because a mesh export carries no clips, and baking none still costs a pass.
"""

import os
import re
import sys

import bpy


def _png_for_mat(mat_name: str, tex_dir: str):
    """Return the absolute PNG path for a material, or None if not found."""
    key = re.sub(r"\s+", "_", mat_name.strip())
    for suffix in ("_alpha", "_cutout"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
    path = os.path.join(tex_dir, key + ".png")
    return path if os.path.exists(path) else None


def _opaque_png(image, png_path: str, scene) -> str:
    """Write an RGB-only twin of `png_path` and return its path.

    Blender's FBX importer links a diffuse texture's alpha into the Principled
    Alpha input whenever the PNG has an alpha channel (import_fbx.py: "if image
    and image.depth == 32"), so an OPAQUE material would come back HASHED and
    punch holes wherever the zone texture's alpha is junk. A 24-bit copy is the
    only thing the importer will leave alone."""
    out = png_path[:-4] + "_opaque.png"
    if not os.path.exists(out):
        # save_render composites the alpha into an RGB write (low-alpha texels go
        # black), so flatten a throwaway copy to alpha 1 first. The original stays
        # untouched for the _alpha / cutout materials that share it.
        import numpy as np
        flat = image.copy()
        px = np.empty(len(flat.pixels), dtype=np.float32)
        flat.pixels.foreach_get(px)
        px[3::4] = 1.0
        flat.pixels.foreach_set(px)
        flat.alpha_mode = "NONE"
        settings = scene.render.image_settings
        fmt, mode, depth = settings.file_format, settings.color_mode, settings.color_depth
        settings.file_format, settings.color_mode, settings.color_depth = "PNG", "RGB", "8"
        try:
            flat.save_render(out, scene=scene)
        finally:
            settings.file_format, settings.color_mode, settings.color_depth = fmt, mode, depth
            bpy.data.images.remove(flat)
    return out


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    glb_in, fbx_out, tex_dir = argv[0], argv[1], argv[2]
    bake_anim = len(argv) > 3 and argv[3] == "1"

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
        # The glTF importer only feeds the BSDF Alpha socket for MASK and BLEND
        # materials; an OPAQUE one leaves it unlinked. Read that before rewiring —
        # it is the one signal that survives every Blender version (blend_method
        # stopped saying CLIP in 4.2).
        alpha_wanted = bsdf_node.inputs["Alpha"].is_linked
        key = re.sub(r"\s+", "_", mat.name.strip())
        image = bpy.data.images.load(png_path, check_existing=True)
        if not alpha_wanted and not key.endswith("_alpha"):
            image = bpy.data.images.load(_opaque_png(image, png_path, bpy.context.scene),
                                         check_existing=True)
        tex_node.image = image
        # Replace intermediate (MIX+vertex-color) link with a direct connection
        links.new(tex_node.outputs["Color"], bsdf_node.inputs["Base Color"])
        # Wire the alpha channel so Blender's FBX exporter can trace it.
        # _alpha materials = FFXI softblend (0x8000): keep BLEND for smooth transparency.
        # MASK materials (foliage cutout): direct texture alpha, keep the threshold.
        # OPAQUE materials got the 24-bit PNG above, so nothing to wire.
        if key.endswith("_alpha"):
            links.new(tex_node.outputs["Alpha"], bsdf_node.inputs["Alpha"])
            mat.blend_method = "BLEND"
        elif alpha_wanted:
            links.new(tex_node.outputs["Alpha"], bsdf_node.inputs["Alpha"])

    bpy.ops.export_scene.fbx(
        filepath=fbx_out,
        path_mode="ABSOLUTE",
        embed_textures=False,
        add_leaf_bones=False,
        bake_anim=bake_anim,
        use_custom_props=True,
    )


if __name__ == "__main__":
    main()
