"""ffxi_zone_setup.py - part of xi-tools/unreal-engine (see README.md there).

Set up a freshly imported FFXI zone's material instances:
  ground *_alpha -> moved onto M_FFXIZone_Overlay, a mesh-decal material for FFXI's blended
                    ground overlays (path edges, terrain transitions)
  wall *_alpha   -> moved onto M_FFXIZone_OverlayWall: MasterMaterial's look (masked, two-sided)
                    plus a small pull toward the camera, so they stop flickering against the wall
                    they're painted on (a decal on a wall paints over anything hanging in front
                    of it, like the gate flags, so walls don't use the decal)
  *_cutout       -> "Enable - Alpha" = 1 on their MasterMaterial instance (foliage, fences, grates)
  textures       -> mipmaps off (NO_MIPMAPS), to stop atlas bleeding drawing lines on tile edges

Ground vs wall is measured from the zone's .glb (xi zone export writes it next to the .fbx):
an overlay whose triangles mostly face up and are painted onto the surface under them is ground.
The .glb is found from the selected static mesh (the .fbx it was imported from), so the script
can live anywhere; it stops if the .glb doesn't know the selected overlays (another zone's).

Run in UE 5.6: open the zone's folder in the Content Browser, Ctrl+A to select everything
(including the zone's static mesh), then Tools > Execute Python Script... and pick this file.
Anything else is left alone.
With nothing selected it scans ZONE_FOLDERS instead. Safe to run again.
Needs /Game/CORE/MasterMaterial (xi-tools/unreal-engine/Content/CORE); it builds the three
M_FFXIZone_* overlay materials itself.
"""
import glob
import json
import math
import os
import re
import struct

import unreal

ZONE_FOLDERS = []                     # only used when nothing is selected, e.g. ["/Game/ZONES/gustaberg"]
GLB_PATH = ""                         # the zone's .glb; blank = found from the selected static mesh's
                                      # import source (<stem>.fbx -> <stem>.glb beside it)
MAT_DIR = "/Game/CORE"
MAT_NAME = "M_FFXIZone_Overlay"            # the decal version
TRANS_MAT_NAME = "M_FFXIZone_OverlayTranslucent"
WALL_MAT_NAME = "M_FFXIZone_OverlayWall"
OVERLAY_MODE = "decal"                # ground overlays: "translucent" (FFXI's own alpha blend over
                                      # the ground) or "decal" (DBuffer mesh decal). Flip and rerun to compare.
MASTER = "/Game/CORE/MasterMaterial"
DEPTH_PULL_CM = 2.0                   # nudge toward the camera so overlays never tie with the ground
WALL_DEPTH_PULL_CM = 0.5              # the same for wall overlays; kept small so they can't draw over
                                      # flags and signs hanging just in front of the wall. Raise it
                                      # (on the material, or here and rerun) if a wall still flickers.
VERTEX_COLOUR_SCALE = 2.0             # FFXI: pixel = texture x vertexColour x 2, in gamma space
VERTEX_COLOUR_POWER = 2.2             # ...which in UE's linear space is Tex x Power(VertexColor x 2, 2.2).
                                      # MasterMaterial's base colour must do the same, or the overlays won't
                                      # match the ground (Power 1.0 + Scale 1.0 = the old Tex x VertexColor).
CUTOUT_PARAM = "Enable - Alpha"       # MasterMaterial switch that turns on the texture-alpha cutout
GROUND_SHARE = 0.40                   # an overlay is ground when its surface faces up at least this much
PAINTED_SHARE = 0.90                  # ...and at least this much of it is painted onto the surface under it.
                                      # A decal only paints onto what's behind it, so water, windows and
                                      # other free-standing overlays go to the wall material instead.
DEBUG_VERTEX_ALPHA = False            # True: the decal shows its vertex alpha as greyscale (black = faded out,
                                      # white = full) so you can see whether the fades reach it. Set back to
                                      # False and run again afterwards.
NO_MIPMAPS = True                     # zone textures are atlases; UE's mips blur neighbouring atlas blocks
                                      # together and draw lines along tile edges. FFXI and the XI viewer
                                      # use no mips. False puts them back (Mip Gen Settings: FromTextureGroup).

EAL = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary
MP = unreal.MaterialProperty


def norm_name(name):
    """GLB 'model   kabe_alpha' and UE 'model___kabe_alpha' compare equal."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# --- which *_alpha overlays lie on the ground (measured from the .glb) ----------------------
def ground_overlays(glb_path):
    b = open(glb_path, "rb").read()
    off, gltf, binc = 12, None, None
    while off < len(b):
        clen, ctype = struct.unpack_from("<II", b, off); off += 8
        chunk = b[off:off + clen]; off += clen
        if ctype == 0x4E4F534A: gltf = json.loads(chunk)
        elif ctype == 0x004E4942: binc = chunk

    def acc(i):
        a = gltf["accessors"][i]; bv = gltf["bufferViews"][a["bufferView"]]
        s = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        n = {"SCALAR": 1, "VEC3": 3}[a["type"]]
        fmt = {5126: "f", 5123: "H", 5125: "I"}[a["componentType"]]
        v = struct.unpack_from("<%d%s" % (a["count"] * n, fmt), binc, s)
        return [v[k:k + n] for k in range(0, len(v), n)] if n > 1 else list(v)

    def corner(v):
        return (round(v[0], 3), round(v[1], 3), round(v[2], 3))

    mats = [m["name"] for m in gltf["materials"]]
    area, up, on_base = {}, {}, {}
    for mesh in gltf["meshes"]:
        opaque, overlay = set(), []
        for p in mesh["primitives"]:
            m = mats[p["material"]]
            pos = acc(p["attributes"]["POSITION"])
            idx = acc(p["indices"]) if "indices" in p else list(range(len(pos)))
            tris = [(pos[idx[t]], pos[idx[t + 1]], pos[idx[t + 2]]) for t in range(0, len(idx) - 2, 3)]
            if m.strip().endswith("_alpha"):
                overlay += [(norm_name(m), t) for t in tris]
            else:
                opaque.update(frozenset(map(corner, t)) for t in tris)
        for key, (a, bb, c) in overlay:
            e1 = [bb[i] - a[i] for i in range(3)]; e2 = [c[i] - a[i] for i in range(3)]
            n = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
            A = math.sqrt(sum(x * x for x in n))
            if A:
                area[key] = area.get(key, 0.0) + A
                up[key] = up.get(key, 0.0) + abs(n[1])   # mesh-local Y is FFXI's vertical axis
                # painted onto the surface under it: shares that triangle's exact corners
                if frozenset(map(corner, (a, bb, c))) in opaque:
                    on_base[key] = on_base.get(key, 0.0) + A
    return {k: (up[k] / area[k], on_base.get(k, 0.0) / area[k]) for k in area}


# --- what to work on: the selection (or the zone folders) -----------------------------------
candidates = []
try:
    candidates = list(unreal.EditorUtilityLibrary.get_selected_assets())
except Exception as e:
    print(f"[FFXI overlay] could not read the Content Browser selection: {e}")
source = "selection"
if not candidates:
    source = "folder scan"
    for folder in ZONE_FOLDERS:
        found = EAL.list_assets(folder, recursive=True, include_folder=False)
        print(f"[FFXI overlay] {folder}: {len(found)} assets")
        candidates += [EAL.load_asset(p) for p in found]
if not candidates:
    raise RuntimeError("[FFXI overlay] nothing selected - open the zone's folder in the Content Browser, "
                       "Ctrl+A, then run the script again (or list the folder in ZONE_FOLDERS)")
print(f"[FFXI overlay] checking {len(candidates)} assets from the {source}")


def glb_next_to_mesh(assets):
    """xi zone export writes <stem>.glb beside <stem>.fbx, and the zone's static mesh remembers
    the .fbx it was imported from."""
    for a in assets:
        if not isinstance(a, unreal.StaticMesh):
            continue
        try:
            fbx = a.get_editor_property("asset_import_data").get_first_filename()
        except Exception:
            continue
        glb_path = os.path.splitext(fbx)[0] + ".glb" if fbx else ""
        if glb_path and os.path.isfile(glb_path):
            return glb_path
    return ""


glb = GLB_PATH or glb_next_to_mesh(candidates)
if not glb:
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
    found = glob.glob(os.path.join(here, "*.glb")) if here else []
    glb = found[0] if len(found) == 1 else ""
if not glb or not os.path.isfile(glb):
    raise RuntimeError("[FFXI overlay] can't find the zone's .glb - select the zone's static mesh too, "
                       "or set GLB_PATH at the top of the script to the .glb xi zone export wrote next to the .fbx")
shares = ground_overlays(glb)
print(f"[FFXI overlay] read {glb}: {len(shares)} overlay materials")

# Every selected *_alpha must be in this .glb, or it's the wrong zone's .glb: stop before changing anything.
alphas = [a.get_name() for a in candidates
          if isinstance(a, unreal.MaterialInstanceConstant) and a.get_name().lower().endswith("_alpha")]
missing = [n for n in alphas if norm_name(n) not in shares]
if alphas and len(missing) == len(alphas):
    raise RuntimeError(f"[FFXI overlay] none of the {len(alphas)} selected *_alpha instances are in {glb} - "
                       "that's another zone's .glb. Set GLB_PATH to this zone's .glb and run again.")

# --- build the overlay materials ------------------------------------------------------------
problems = []


def build_overlay(name, mode, pull_cm):
    """decal: a DBuffer mesh decal painted into the ground's surface before lighting.
    translucent: a lit translucent surface drawn over the ground, the same alpha blend FFXI
    itself does.
    wall: MasterMaterial's masked, two-sided surface, for overlays painted on walls.
    All: colour = Tex x VertexColor x scale, opacity = Tex.a x VertexColor.a x 2, nudged toward
    the camera so they never tie with the surface they sit on (the XI viewer gives every
    overlay a depth bias for the same reason)."""
    decal, wall = mode == "decal", mode == "wall"
    path = f"{MAT_DIR}/{name}"
    if EAL.does_asset_exist(path):
        mat = EAL.load_asset(path)
        MEL.delete_all_material_expressions(mat)
    else:
        mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

    # Blend mode first: a decal-domain material with a non-translucent blend mode fails to compile.
    if wall:
        mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
        mat.set_editor_property("material_domain", unreal.MaterialDomain.MD_SURFACE)
        mat.set_editor_property("two_sided", True)
        mat.set_editor_property("opacity_mask_clip_value", master.get_editor_property("opacity_mask_clip_value"))
    else:
        mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    if decal:
        mat.set_editor_property("material_domain", unreal.MaterialDomain.MD_DEFERRED_DECAL)
    elif not wall:
        mat.set_editor_property("material_domain", unreal.MaterialDomain.MD_SURFACE)
        mat.set_editor_property("translucency_lighting_mode",
                                unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
        mat.set_editor_property("two_sided", False)

    def node(cls, x, y):
        return MEL.create_material_expression(mat, cls, x, y)

    def link(a, out, b, inp):
        if not MEL.connect_material_expressions(a, out, b, inp):
            problems.append(f"{name}: {a.get_name()}.{out or 'out'} -> {b.get_name()}.{inp or 'in'}")

    def to_pin(a, out, prop):
        if not MEL.connect_material_property(a, out, prop):
            problems.append(f"{name}: {a.get_name()}.{out or 'out'} -> {prop}")

    def scalar(pname, value, x, y):
        q = node(unreal.MaterialExpressionScalarParameter, x, y)
        q.set_editor_property("parameter_name", pname)
        q.set_editor_property("default_value", value)
        return q

    def times(a, a_out, b=None, b_out="", const_b=None, x=0, y=0):
        m = node(unreal.MaterialExpressionMultiply, x, y)
        link(a, a_out, m, "A")
        if b is not None:
            link(b, b_out, m, "B")
        else:
            m.set_editor_property("const_b", const_b)
        return m

    # Same parameter name as MasterMaterial ("Texture"), so each instance keeps its texture.
    tex = node(unreal.MaterialExpressionTextureSampleParameter2D, -1100, -250)
    tex.set_editor_property("parameter_name", "Texture")
    tex.set_editor_property("texture", unreal.load_asset("/Engine/EngineResources/DefaultTexture"))
    vc = node(unreal.MaterialExpressionVertexColor, -1100, 150)

    # Base Color = Texture.rgb x Power(VertexColor.rgb x Scale, Power) x Texture Strength
    # FFXI multiplies in gamma space; Power(.., 2.2) is that multiply carried into linear space.
    # (must match MasterMaterial, or the overlays come out brighter/darker than the ground)
    strength = scalar("Texture Strength", 1.0, -1100, 400)
    vscale = scalar("Vertex Colour Scale", VERTEX_COLOUR_SCALE, -1000, -100)
    vpow = scalar("Vertex Colour Power", VERTEX_COLOUR_POWER, -1000, 0)
    v1 = times(vc, "", vscale, "", x=-850, y=-100)
    v2 = node(unreal.MaterialExpressionPower, -700, -100)
    link(v1, "", v2, "Base")
    link(vpow, "", v2, "Exp")
    c2 = times(tex, "RGB", v2, "", x=-550, y=-250)
    c3 = times(c2, "", strength, "", x=-400, y=-250)

    # Opacity = min(Texture.a x VertexColor.a x 2, 1)
    # (FFXI is 4 x vertexAlpha x texAlpha; the exported PNG already carries one of the x2s).
    # Min rather than Saturate: Saturate's input would not connect from Python.
    o1 = times(tex, "A", vc, "A", x=-800, y=100)
    o2 = times(o1, "", const_b=2.0, x=-600, y=100)
    o3 = node(unreal.MaterialExpressionMin, -400, 100)
    link(o2, "", o3, "A")
    o3.set_editor_property("const_b", 1.0)

    opacity_pin = MP.MP_OPACITY_MASK if wall else MP.MP_OPACITY
    if DEBUG_VERTEX_ALPHA:
        # Vertex alpha x 2 as greyscale, fully opaque: FFXI's neutral 0.5 shows as white.
        dbg = times(vc, "A", const_b=2.0, x=-400, y=-400)
        one = node(unreal.MaterialExpressionConstant, -400, 250)
        one.set_editor_property("r", 1.0)
        to_pin(dbg, "", MP.MP_BASE_COLOR)
        to_pin(one, "", opacity_pin)
    else:
        to_pin(c3, "", MP.MP_BASE_COLOR)
        to_pin(o3, "", opacity_pin)

    if wall:
        # Same parameters and defaults as MasterMaterial, so each instance keeps its values.
        for i, pname in enumerate(("Specular", "Metallic", "Roughness")):
            to_pin(scalar(pname, 0.0, -400, 350 + 100 * i), "",
                   getattr(MP, f"MP_{pname.upper()}"))
    elif not decal:
        # Match the ground: MasterMaterial's Specular/Metallic default to 0, so no highlight.
        zero = node(unreal.MaterialExpressionConstant, -400, 350)
        rough = node(unreal.MaterialExpressionConstant, -400, 450)
        rough.set_editor_property("r", 1.0)
        to_pin(zero, "", MP.MP_SPECULAR)
        to_pin(zero, "", MP.MP_METALLIC)
        to_pin(rough, "", MP.MP_ROUGHNESS)

    # World Position Offset = normalize(camera - position) x Depth Pull
    cam = node(unreal.MaterialExpressionCameraPositionWS, -1100, 650)
    wp = node(unreal.MaterialExpressionWorldPosition, -1100, 750)
    sub = node(unreal.MaterialExpressionSubtract, -900, 700)
    link(cam, "", sub, "A")
    link(wp, "", sub, "B")
    nrm = node(unreal.MaterialExpressionNormalize, -700, 700)
    link(sub, "", nrm, "")
    pull = scalar("Depth Pull", pull_cm, -700, 800)
    w = times(nrm, "", pull, "", x=-500, y=700)
    to_pin(w, "", MP.MP_WORLD_POSITION_OFFSET)

    MEL.recompile_material(mat)
    EAL.save_asset(path, only_if_is_dirty=False)
    return mat, path


master = EAL.load_asset(MASTER)
decal_mat, decal_path = build_overlay(MAT_NAME, "decal", DEPTH_PULL_CM)
trans_mat, trans_path = build_overlay(TRANS_MAT_NAME, "translucent", DEPTH_PULL_CM)
wall_mat, wall_path = build_overlay(WALL_MAT_NAME, "wall", WALL_DEPTH_PULL_CM)
mat, path = (trans_mat, trans_path) if OVERLAY_MODE == "translucent" else (decal_mat, decal_path)
if DEBUG_VERTEX_ALPHA:
    print("[FFXI overlay] DEBUG_VERTEX_ALPHA is on: overlays show vertex alpha, not their texture")
print(f"[FFXI overlay] ground overlays use the {OVERLAY_MODE} material ({path})")
print("[FFXI overlay] if the Output Log shows 'M_FFXIZone_Overlay... Failed to compile' after this line, "
      "the ground overlays are not drawing - send that message")

# --- apply ------------------------------------------------------------------------------------

def clear_overrides(mi):
    # Instances carry a Masked blend override from their first parent; the decal needs it gone.
    overrides = mi.get_editor_property("base_property_overrides")
    for flag in ("override_blend_mode", "override_opacity_mask_clip_value", "override_two_sided",
                 "override_shading_model", "override_dithered_lod_transition"):
        try:
            overrides.set_editor_property(flag, False)
        except Exception:
            pass
    mi.set_editor_property("base_property_overrides", overrides)


def enable_alpha(mi):
    # The setter's return value is unreliable (it can report False after setting the value),
    # so read the value back instead.
    MEL.set_material_instance_scalar_parameter_value(mi, CUTOUT_PARAM, 1.0)
    ok = abs(MEL.get_material_instance_scalar_parameter_value(mi, CUTOUT_PARAM) - 1.0) < 1e-4
    if not ok:
        print(f"    could not set '{CUTOUT_PARAM}' on {mi.get_name()} (is its parent MasterMaterial?)")
    return ok


decals, walls, cutouts, unknown, retextured, srgb_fixed = [], [], [], [], [], []
mips = (unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS if NO_MIPMAPS
        else unreal.TextureMipGenSettings.TMGS_FROM_TEXTURE_GROUP)
for mi in candidates:
    name = mi.get_name() if mi else "(none)"
    kind = name.lower()
    if isinstance(mi, unreal.Texture2D):
        changed = False
        if mi.get_editor_property("mip_gen_settings") != mips:
            mi.set_editor_property("mip_gen_settings", mips)
            changed = True
        # Zone textures are colour: read them as sRGB. A texture left linear comes out washed
        # pale, and on the overlay textures that shows as pale tile-shaped patches.
        if not mi.get_editor_property("srgb"):
            mi.set_editor_property("srgb", True)
            srgb_fixed.append(name)
            changed = True
        if changed:
            EAL.save_asset(mi.get_path_name(), only_if_is_dirty=False)
            retextured.append(name)
        continue
    if not (kind.endswith("_alpha") or kind.endswith("_cutout")):
        continue
    if not isinstance(mi, unreal.MaterialInstanceConstant):
        print(f"    skip {name}: it's a {type(mi).__name__}, not a material instance")
        continue
    if kind.endswith("_cutout"):
        if enable_alpha(mi):
            cutouts.append(name)
    else:
        found = shares.get(norm_name(name))
        if found is None:
            unknown.append(name)
            continue
        share, painted = found
        note = f"{name} ({100 * share:.0f}% up, {100 * painted:.0f}% painted on a surface)"
        clear_overrides(mi)
        if share >= GROUND_SHARE and painted >= PAINTED_SHARE:
            MEL.set_material_instance_parent(mi, mat)
            decals.append(note)
        else:
            MEL.set_material_instance_parent(mi, wall_mat)
            walls.append(note)
    MEL.update_material_instance(mi)
    EAL.save_asset(mi.get_path_name(), only_if_is_dirty=False)

print(f"[FFXI overlay] built {path}")
print(f"[FFXI overlay] {len(decals)} ground overlays -> {OVERLAY_MODE}:")
for n in decals:
    print("    " + n)
print(f"[FFXI overlay] {len(walls)} wall overlays -> {wall_path} (Depth Pull {WALL_DEPTH_PULL_CM} cm):")
for n in walls:
    print("    " + n)
print(f"[FFXI overlay] '{CUTOUT_PARAM}' = 1 on {len(cutouts)} *_cutout instances")
print(f"[FFXI overlay] updated {len(retextured)} textures (mipmaps {'off' if NO_MIPMAPS else 'on'})")
print(f"[FFXI overlay] sRGB was OFF on {len(srgb_fixed)} textures, now on:")
for n in srgb_fixed:
    print("    " + n)
if unknown:
    print("[FFXI overlay] not in the .glb (wrong GLB_PATH?), left alone:")
    for n in unknown:
        print("    " + n)
if problems:
    print("[FFXI overlay] these connections failed:")
    for p in problems:
        print("    " + p)
