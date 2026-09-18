"""Run *inside* Blender
(``blender -b --python xi_glb_to_fbx.py -- <in.glb> <out.fbx> <tex_dir> [bake_anim]``).

Imports a glTF/GLB, rewires every packed image to a file-backed PNG and connects
it directly to the Principled BSDF Base Color input (the GLB importer routes
through a vertex-color MIX node that Blender's FBX exporter can't trace),
then re-exports geometry + materials as FBX with absolute texture paths.

A 4th argument of ``1`` also bakes the GLB's skeletal animation into the FBX. Off by
default because a mesh export carries no clips, and baking none still costs a pass.
"""

import math
import os
import re
import sys

import bpy
import bmesh
from collections import defaultdict


def _weld_within_materials(dist: float) -> None:
    """Weld coincident vertices in every mesh, but only within a single material,
    keeping UVs and every face.

    A glTF splits a vertex at every UV seam (one UV per vertex), so tiled zone
    terrain imports as disconnected shells. Blender stores UVs per face-corner
    (loop), so welding the shared *vertices* fuses the topology while each face
    keeps its own UV — connected geometry with the texture intact, unlike
    collapsing the UVs in the GLB.

    Crucially this welds only verts whose faces are the SAME material. FFXI terrain
    layers a blended overlay (e.g. `sar_kk2_alpha`) on top of the opaque base
    (`sar_kk2`) as a second triangle at the same position; a blind merge-by-distance
    would fold those two into one vertex set, and Blender can't hold two faces with
    identical verts, so it would delete the overlay (a visible ~10% of the surface).
    Grouping by (material set, position) keeps base and overlay apart — the base
    still welds its own UV-seam splits, the overlay survives untouched. Operates on
    mesh data directly so shared (instanced) meshes weld once and every placement
    follows."""
    dp = max(0, round(-math.log10(dist))) if dist > 0 else 4
    for me in bpy.data.meshes:
        if not me.vertices:
            continue
        bm = bmesh.new()
        bm.from_mesh(me)
        # The glTF import stores the DAT's authored normals as custom split
        # (per-corner) normals — already correct (all "up") and matched between a
        # base tile and its alpha overlay. bmesh from_mesh/to_mesh drops them, and
        # Blender would recompute from the winding (wrong way for FFXI's
        # clockwise-front terrain, and it would make the overlay's normals diverge
        # from the base's). Carry each corner's normal on its own loop through a
        # custom loop layer: the weld keeps every loop (it moves vertices, never
        # removes faces here), so the normal rides along exactly, unaffected by the
        # vertex moving to its weld representative.
        nlay = bm.loops.layers.float_vector.new("xi_normal")
        src = [tuple(me.corner_normals[li].vector)
               for poly in me.polygons for li in poly.loop_indices]
        i = 0
        for f in bm.faces:
            for loop in f.loops:
                loop[nlay] = src[i]
                i += 1
        # Group verts to weld by (material-bucket, position). The bucket keeps the
        # blended overlay layers apart from the base while connecting the base as
        # much as possible: every opaque (non-"_alpha") material shares one bucket,
        # so the ground welds across a texture boundary too; each alpha material is
        # its own bucket, so an overlay never folds into the base (which would
        # delete it — Blender can't hold two faces with identical verts). A vert on
        # a base↔overlay boundary carries both and stays in its own bucket.
        alpha = [bool(m) and m.name.endswith("_alpha") for m in me.materials]

        def bucket(mis):
            return "opaque" if all(not alpha[i] for i in mis) else frozenset(mis)

        vmats = defaultdict(set)
        for f in bm.faces:
            for v in f.verts:
                vmats[v].add(f.material_index)
        groups = defaultdict(list)
        for v in bm.verts:
            key = (bucket(vmats.get(v, ())), tuple(round(c, dp) for c in v.co))
            groups[key].append(v)
        targetmap = {}
        for verts in groups.values():
            for v in verts[1:]:
                targetmap[v] = verts[0]
        # Never lose a face: weld_verts deletes any face that ends up degenerate
        # (two corners on one vert) or identical to another face, and a few cliff
        # meshes do draw the same triangle twice inside one bucket. Leave the
        # corners of such faces unwelded instead.
        for _ in range(4):
            seen, bad = {}, set()
            for f in bm.faces:
                key = frozenset(targetmap.get(v, v) for v in f.verts)
                if len(key) < len(f.verts):
                    bad.update(f.verts)
                elif key in seen:
                    bad.update(f.verts)
                    bad.update(seen[key].verts)
                else:
                    seen[key] = f
            bad = {v for v in bad if v in targetmap}
            if not bad:
                break
            for v in bad:
                del targetmap[v]
        if targetmap:
            bmesh.ops.weld_verts(bm, targetmap=targetmap)
        # Read the carried normals back in the post-weld loop order, which is the
        # order to_mesh writes, so it lines up with me.loops for the custom set.
        new_normals = [tuple(loop[nlay]) for f in bm.faces for loop in f.loops]
        bm.to_mesh(me)
        bm.free()
        me.normals_split_custom_set(new_normals)


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
    merge_dist = float(argv[4]) if len(argv) > 4 else 0.0

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    bpy.ops.import_scene.gltf(filepath=glb_in)

    if merge_dist > 0.0:
        _weld_within_materials(merge_dist)

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
