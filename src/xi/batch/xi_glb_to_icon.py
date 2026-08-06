"""Run *inside* Blender:

    blender -b --python xi_glb_to_icon.py -- <jobs.json> <render_size> <samples>

``jobs.json`` is a list of ``{"glb": "<in.glb>", "out": "<out.png>"}``. Each glb is
rendered to a square, transparent-background PNG icon from a fixed orthographic 3/4
view with neutral studio lighting — a thumbnail for a viewer. Many jobs run in one
Blender session so the (slow) startup is amortised across all the icons in a chunk.
"""

import json
import math
import sys

import bpy
from mathutils import Vector


def _pick_engine(scene) -> str:
    """Choose a render engine and return its id. EEVEE (rasteriser) is fast but needs a GL
    context — it crashes on a headless/GPU-less server. Cycles (CPU) renders fine headless.
    Honour XI_ICON_ENGINE=cycles|eevee; default to EEVEE if present, else Cycles."""
    import os
    avail = {it.identifier for it in scene.render.bl_rna.properties["engine"].enum_items}
    eevee = next((n for n in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE") if n in avail), None)
    want = os.environ.get("XI_ICON_ENGINE", "").lower()
    if want == "cycles" or eevee is None:
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        return "CYCLES"
    scene.render.engine = eevee
    return eevee


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    # Purge orphaned datablocks so memory stays bounded across many imports.
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.cameras,
                  bpy.data.lights, bpy.data.objects):
        for d in list(block):
            if d.users == 0:
                block.remove(d)


def _drop_gltf_placeholder() -> None:
    """The glTF importer drops a placeholder icosphere into 'glTF_not_exported'; remove
    it so it doesn't pollute the bounding box / render."""
    coll = bpy.data.collections.get("glTF_not_exported")
    if not coll:
        return
    for obj in list(coll.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(coll)


def _scene_bbox():
    mins = Vector((1e30, 1e30, 1e30))
    maxs = Vector((-1e30, -1e30, -1e30))
    found = False
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not obj.data.vertices:
            continue
        found = True
        for corner in obj.bound_box:
            wc = obj.matrix_world @ Vector(corner)
            for i in range(3):
                mins[i] = min(mins[i], wc[i])
                maxs[i] = max(maxs[i], wc[i])
    return (mins, maxs) if found else (None, None)


def _setup_world() -> None:
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
        bg.inputs[1].default_value = 0.7  # ambient fill


def _export_fbx(fbx_out: str) -> None:
    """Export the just-imported mesh content as an FBX (geometry + UVs + material slots).
    Called right after import, before the render camera/lights are added, so only the
    object's own meshes land in the FBX.

    Textures are NOT carried by the FBX here — headless Blender won't embed packed-GLB
    images. The batch writes the texture PNGs to ``fbx/textures/`` from Python instead, and
    the material slot names match the texture names so they can be re-associated on import.
    """
    bpy.ops.export_scene.fbx(
        filepath=fbx_out, path_mode="STRIP",
        add_leaf_bones=False, bake_anim=False,
        object_types={"MESH", "EMPTY"},          # exclude any stray camera/light
    )


def _render_one(glb: str, out: str, size: int, samples: int, fbx_out: str | None = None) -> bool:
    _clear_scene()
    bpy.ops.import_scene.gltf(filepath=glb)
    _drop_gltf_placeholder()

    if fbx_out:
        try:
            _export_fbx(fbx_out)
        except Exception as e:  # noqa: BLE001 — FBX failure must not lose the icon
            print(f"FBX_ERR {fbx_out}: {e}", file=sys.stderr)

    mins, maxs = _scene_bbox()
    if mins is None:
        return False
    center = (mins + maxs) / 2.0
    diag = (maxs - mins).length or 1.0

    # Orthographic 3/4 view — uniform framing, no perspective distortion between icons.
    cam_data = bpy.data.cameras.new("IconCam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = diag * 1.08          # diagonal fit = worst-case, always contained
    cam_data.clip_start = 0.001
    cam_data.clip_end = diag * 20.0 + 100.0
    cam = bpy.data.objects.new("IconCam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    view_dir = Vector((1.0, -1.0, 0.7)).normalized()
    cam.location = center + view_dir * (diag * 2.0 + 1.0)
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    light_data = bpy.data.lights.new("KeySun", type="SUN")
    light_data.energy = 3.5
    light = bpy.data.objects.new("KeySun", light_data)
    bpy.context.scene.collection.objects.link(light)
    light.rotation_euler = (math.radians(55.0), 0.0, math.radians(40.0))

    _setup_world()

    scene = bpy.context.scene
    engine = _pick_engine(scene)
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100  # default can be 50 -> half-size output
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    try:
        if engine == "CYCLES":
            scene.cycles.samples = max(1, samples)
        else:
            scene.eevee.taa_render_samples = samples
    except Exception:  # noqa: BLE001 — engine without that prop; default AA is fine
        pass
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)
    return True


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    jobs_path, size, samples = argv[0], int(argv[1]), int(argv[2])
    with open(jobs_path, encoding="utf-8") as f:
        jobs = json.load(f)

    ok = 0
    for job in jobs:
        try:
            if _render_one(job["glb"], job["out"], size, samples, job.get("fbx")):
                ok += 1
            else:
                print(f"ICON_EMPTY {job.get('out')}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — one bad mesh must not kill the chunk
            print(f"ICON_ERR {job.get('out')}: {e}", file=sys.stderr)
    print(f"ICON_DONE {ok}/{len(jobs)}")


if __name__ == "__main__":
    main()
