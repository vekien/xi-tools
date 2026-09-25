"""Run *inside* Blender
(``blender -b --python xi_glb_to_fbx.py -- <in.glb> <out.fbx> <tex_dir> [bake_anim]``).

Imports a glTF/GLB, rewires every packed image to a file-backed PNG and connects
it directly to the Principled BSDF Base Color input (the GLB importer routes
through a vertex-color MIX node that Blender's FBX exporter can't trace),
then re-exports geometry + materials as FBX with absolute texture paths.

A 4th argument of ``1`` also bakes the GLB's skeletal animation into the FBX. Off by
default because a mesh export carries no clips, and baking none still costs a pass.

An 11th argument of ``1`` is ``--zero-coords`` (see ``_zero_coords``): it prints the
offset it subtracted as ``XI_ZERO_OFFSET x y z``.
"""

import math
import os
import re
import sys

import bpy
import bmesh
from collections import defaultdict
from mathutils import Matrix, Vector


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
        view = scene.view_settings
        fmt, mode, depth = settings.file_format, settings.color_mode, settings.color_depth
        transform, look, exposure, gamma = view.view_transform, view.look, view.exposure, view.gamma
        settings.file_format, settings.color_mode, settings.color_depth = "PNG", "RGB", "8"
        # save_render applies the scene's view transform (AgX/Filmic by default), which
        # tone-maps every texel. Standard with no look is a plain sRGB round trip.
        # (Zone exports write this twin themselves; this is the fallback.)
        view.view_transform, view.look, view.exposure, view.gamma = "Standard", "None", 0.0, 1.0
        try:
            flat.save_render(out, scene=scene)
        finally:
            settings.file_format, settings.color_mode, settings.color_depth = fmt, mode, depth
            view.view_transform, view.look, view.exposure, view.gamma = transform, look, exposure, gamma
            bpy.data.images.remove(flat)
    return out


# ---------------------------------------------------------------------------
# "(Test) Alpha Split Mesh" — the decal split for Unreal (zone export)
#
# FFXI paints ground decals (paths, cracks, stains) as an alpha-blended overlay
# drawn *coplanar* with the opaque terrain it sits on. That is fine for the
# retail client but z-fights in Unreal, where the two coplanar surfaces flicker.
# This runs the recipe a friend worked out for a clean FBX import:
#
#   1. Weld the opaque polys together, and the alpha polys together (separately,
#      so a decal never fuses into the base and gets deleted).
#   2. Smooth both by polygon angle.
#   3. Separate the alpha polys into their own mesh (named <mesh>_A).
#   4. Push the alpha polys a hair along their normal so they no longer z-fight.
#   5. Transfer the base surface's normals onto the decal so it still shades as
#      one with the ground.
#
# Output is two FBX files — <stem>.fbx (opaque base) and <stem>_A.fbx (decals) —
# imported into Unreal as two separate meshes. Alpha materials are the ones the
# GLB export already suffixes "_alpha" (the 0x8000 blend bit); "_cutout"/opaque
# foliage is not a decal and stays with the base.
# ---------------------------------------------------------------------------


def _is_alpha_flags(me):
    """Per-material-slot flags: True where the slot is an FFXI alpha-blend decal."""
    return [bool(m) and m.name.endswith("_alpha") for m in me.materials]


def _weld_buckets(me, tol: float) -> None:
    """Weld coincident vertices within the opaque set and within the alpha set,
    but never across the two — the friend's "select non-alpha > WELD, then select
    alpha > WELD". A vertex shared by both an opaque and an alpha face sits on the
    boundary and is left unwelded so the decal is not pulled into the base (Blender
    cannot hold two faces on the same vertices and would delete one). UVs and
    vertex colours ride along on the loops; the DAT normals are dropped here on
    purpose because the next step recomputes them by angle."""
    if not me.polygons:
        return
    dp = max(0, round(-math.log10(tol))) if tol > 0 else 4
    alpha = _is_alpha_flags(me)
    bm = bmesh.new()
    bm.from_mesh(me)

    vmats = defaultdict(set)
    for f in bm.faces:
        for v in f.verts:
            vmats[v].add(f.material_index)

    def bucket(mis):
        flags = [alpha[i] for i in mis]
        if not any(flags):
            return "opaque"
        if all(flags):
            return "alpha"
        return None  # opaque<->alpha boundary: keep separate

    groups = defaultdict(list)
    for v in bm.verts:
        b = bucket(vmats.get(v, ()))
        if b is None:
            continue
        key = (b, tuple(round(c, dp) for c in v.co))
        groups[key].append(v)
    targetmap = {}
    for verts in groups.values():
        for v in verts[1:]:
            targetmap[v] = verts[0]
    # weld_verts deletes any face left degenerate or duplicated by the weld; a few
    # meshes draw the same triangle twice inside one bucket, so un-weld the corners
    # of such faces rather than lose them (same guard as _weld_within_materials).
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
    bm.to_mesh(me)
    bm.free()


def _extract_faces(src_me, want_alpha: bool, new_name: str):
    """Return a new mesh holding only the opaque (or, with want_alpha, only the
    alpha) faces of ``src_me``, with the material slots pruned to those it uses and
    UVs/colours preserved. None if there are no such faces."""
    alpha = _is_alpha_flags(src_me)
    bm = bmesh.new()
    bm.from_mesh(src_me)
    bm.faces.ensure_lookup_table()
    kill = [f for f in bm.faces if bool(alpha[f.material_index]) != want_alpha]
    if kill:
        bmesh.ops.delete(bm, geom=kill, context="FACES")
    if not bm.faces:
        bm.free()
        return None
    used = sorted({f.material_index for f in bm.faces})
    remap = {old: i for i, old in enumerate(used)}
    for f in bm.faces:
        f.material_index = remap[f.material_index]
    new_me = bpy.data.meshes.new(new_name)
    for old in used:
        new_me.materials.append(src_me.materials[old])
    bm.to_mesh(new_me)
    bm.free()
    return new_me


def _angle_custom_normals(me, angle_rad: float) -> None:
    """Auto-smooth by polygon angle and freeze the result as custom split normals.

    Every face is set smooth; an edge whose two faces meet at more than the
    threshold is marked sharp (as is any boundary/non-manifold edge), which is how
    Blender splits the normal there. The computed corner normals are then written
    back as custom normals so they survive the separate/push below and export
    verbatim to FBX."""
    if not me.polygons:
        return
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.update()
    face_n = [Vector(p.normal) for p in me.polygons]
    edge_faces = defaultdict(list)
    for pi, poly in enumerate(me.polygons):
        vs = poly.vertices
        n = len(vs)
        for i in range(n):
            a, b = vs[i], vs[(i + 1) % n]
            edge_faces[(a, b) if a < b else (b, a)].append(pi)
    for e in me.edges:
        faces = edge_faces.get(tuple(e.vertices) if e.vertices[0] < e.vertices[1]
                               else (e.vertices[1], e.vertices[0]), [])
        if len(faces) == 2:
            e.use_edge_sharp = face_n[faces[0]].angle(face_n[faces[1]], 0.0) > angle_rad
        else:
            e.use_edge_sharp = True
    me.update()
    me.normals_split_custom_set([tuple(me.corner_normals[i].vector)
                                 for i in range(len(me.loops))])


def _pos_normal_map(me, dp: int):
    """Rounded vertex position -> unit surface normal, averaged from the (frozen)
    corner normals — the base surface's normals to transfer onto the decal."""
    acc = defaultdict(lambda: Vector((0.0, 0.0, 0.0)))
    for li, loop in enumerate(me.loops):
        co = me.vertices[loop.vertex_index].co
        acc[(round(co.x, dp), round(co.y, dp), round(co.z, dp))] += Vector(me.corner_normals[li].vector)
    return {k: (n.normalized() if n.length > 0 else Vector((0.0, 0.0, 1.0)))
            for k, n in acc.items()}


def _push_and_transfer(me, pos_normal, offset: float, dp: int) -> None:
    """Push every decal vertex ``offset`` along the base surface normal at its
    position (step 4), then set the decal's custom normals to those same base
    normals (step 5), so the lifted decal still shades as one with the ground.
    Falls back to the decal's own smoothed normal where no coincident base vertex
    exists (a decal with no opaque twin)."""
    if not me.polygons:
        return
    own = defaultdict(lambda: Vector((0.0, 0.0, 0.0)))
    for li, loop in enumerate(me.loops):
        own[loop.vertex_index] += Vector(me.corner_normals[li].vector)
    vnorm = {}
    for v in me.vertices:
        co = v.co
        n = pos_normal.get((round(co.x, dp), round(co.y, dp), round(co.z, dp)))
        if n is None:
            o = own[v.index]
            n = o.normalized() if o.length > 0 else Vector((0.0, 0.0, 1.0))
        vnorm[v.index] = n
    if offset:
        for v in me.vertices:
            v.co = v.co + vnorm[v.index] * offset
        me.update()
    me.normals_split_custom_set([tuple(vnorm[loop.vertex_index]) for loop in me.loops])


def _alpha_out_path(fbx_out: str) -> str:
    base, ext = os.path.splitext(fbx_out)
    return base + "_A" + ext


def _export_selection(objs, path: str, colors_type: str = "SRGB") -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        try:
            o.select_set(True)
        except RuntimeError:
            pass
    bpy.ops.export_scene.fbx(
        filepath=path,
        use_selection=True,
        path_mode="ABSOLUTE",
        embed_textures=False,
        add_leaf_bones=False,
        bake_anim=False,
        use_custom_props=True,
        colors_type=colors_type,
    )


def _alpha_split_export(fbx_out: str, angle_rad: float, offset: float, tol: float,
                        colors_type: str = "SRGB") -> None:
    """Run the friend's decal recipe on the imported scene and write two FBX files:
    ``fbx_out`` (opaque base) and its ``_A`` twin (the separated decals). Operates
    on mesh data so instanced placements follow, then rebuilds the instancing by
    giving every object that had decals an ``<name>_A`` sibling."""
    dp = max(0, round(-math.log10(tol))) if tol > 0 else 4
    scene = bpy.context.scene

    mesh_objs = defaultdict(list)
    for obj in list(bpy.data.objects):
        if obj.type == "MESH" and obj.data is not None:
            mesh_objs[obj.data].append(obj)

    alpha_objs = []
    for me, objs in mesh_objs.items():
        _weld_buckets(me, tol)
        base_me = _extract_faces(me, want_alpha=False, new_name=f"{me.name}_base")
        alpha_me = _extract_faces(me, want_alpha=True, new_name=f"{me.name}_A")
        has_base = base_me is not None and len(base_me.polygons) > 0
        has_alpha = alpha_me is not None and len(alpha_me.polygons) > 0
        if has_base:
            _angle_custom_normals(base_me, angle_rad)
        if has_alpha:
            _angle_custom_normals(alpha_me, angle_rad)
            _push_and_transfer(alpha_me, _pos_normal_map(base_me, dp) if has_base else {},
                               offset, dp)
        for obj in objs:
            if has_alpha:
                twin = obj.copy()
                twin.data = alpha_me
                twin.name = f"{obj.name}_A"
                colls = obj.users_collection or (scene.collection,)
                for coll in colls:
                    coll.objects.link(twin)
                alpha_objs.append(twin)
            if has_base:
                obj.data = base_me
            else:
                bpy.data.objects.remove(obj, do_unlink=True)

    alpha_ids = {id(o) for o in alpha_objs}
    empties = [o for o in bpy.data.objects if o.type != "MESH"]
    base_objs = [o for o in bpy.data.objects if o.type == "MESH" and id(o) not in alpha_ids]
    _export_selection(base_objs + empties, fbx_out, colors_type)
    _export_selection(alpha_objs + empties, _alpha_out_path(fbx_out), colors_type)


def _zero_coords() -> Vector:
    """``--zero-coords``: every object imports at location 0, rotation 0, scale 1.

    Bakes each object's world transform into its data — the ``ffxi_root_correction``
    orientation fix and, in a zone, every placement — and drops the empties that held
    them. A skinned mesh is re-parented to its armature, both now identity. Then, unless
    the file is rigged, the geometry moves so the centre of its base (bounds centre in
    X/Y, lowest Z) sits on the origin; a rigged model keeps its skeleton root there,
    which is already the origin, rather than sliding off it by its pose's bounds.

    Returns the offset subtracted, in Blender's frame: placing the imported file at it
    puts the geometry back where it was."""
    objs = [o for o in bpy.data.objects if o.type in ("MESH", "ARMATURE")]
    offset = Vector((0.0, 0.0, 0.0))
    if not objs:
        return offset
    rigged = {o: o.parent for o in objs if o.parent is not None and o.parent.type == "ARMATURE"}
    for o in objs:
        if o.parent is not None:
            mw = o.matrix_world.copy()
            o.parent = None
            o.matrix_world = mw
        # A zone instances one mesh at many placements, and each placement needs the
        # geometry baked at its own transform. (transform_apply's isolate_users does
        # not do this from a background run; it aborts on the first shared mesh.)
        if o.type == "MESH" and o.data.users > 1:
            o.data = o.data.copy()
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for o, arm in rigged.items():
        o.parent = arm
    for o in [o for o in bpy.data.objects if o.type == "EMPTY"]:
        bpy.data.objects.remove(o, do_unlink=True)

    meshes = [o.data for o in objs if o.type == "MESH" and o.data.vertices]
    if any(o.type == "ARMATURE" for o in objs) or not meshes:
        return offset
    import numpy as np
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for me in meshes:
        co = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        lo = np.minimum(lo, co.min(axis=0))
        hi = np.maximum(hi, co.max(axis=0))
    offset = Vector(((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, lo[2]))
    shift = Matrix.Translation(-offset)
    for me in set(meshes):
        me.transform(shift)
    return offset


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    glb_in, fbx_out, tex_dir = argv[0], argv[1], argv[2]
    bake_anim = len(argv) > 3 and argv[3] == "1"
    merge_dist = float(argv[4]) if len(argv) > 4 else 0.0
    alpha_split = len(argv) > 5 and argv[5] == "1"
    smooth_angle = math.radians(float(argv[6])) if len(argv) > 6 else math.radians(45.0)
    decal_offset = float(argv[7]) if len(argv) > 7 else 0.0
    weld_tol = 10.0 ** (-int(argv[8])) if len(argv) > 8 else 1e-4
    # FBX vertex-colour space. Blender's default ('SRGB') sRGB-encodes the glTF's
    # linear COLOR_0, so FFXI's neutral 0x80 lands in the FBX as ~0.73 instead of
    # 0.5. 'LINEAR' writes the values untouched — zone export --vertex-color raw
    # (--unreal) needs that so the engine material's *2 lands neutral at 1.0.
    colors_type = argv[9] if len(argv) > 9 and argv[9] in ("SRGB", "LINEAR") else "SRGB"
    zero_coords = len(argv) > 10 and argv[10] == "1"

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

    if alpha_split:
        _alpha_split_export(fbx_out, smooth_angle, decal_offset, weld_tol, colors_type)
        return

    if zero_coords:
        offset = _zero_coords()
        # Read back by convert_glb_to_fbx_zeroed, for the zone JSON.
        print(f"XI_ZERO_OFFSET {offset.x!r} {offset.y!r} {offset.z!r}")

    bpy.ops.export_scene.fbx(
        filepath=fbx_out,
        path_mode="ABSOLUTE",
        embed_textures=False,
        add_leaf_bones=False,
        bake_anim=bake_anim,
        use_custom_props=True,
        colors_type=colors_type,
    )


if __name__ == "__main__":
    main()
