"""``xi zone export --unreal`` foliage (xi.zone.xi_export): the per-triangle cutout test that
splits a '_' mesh into cutout and solid materials, the wind weights written to TEXCOORD_1,
and the shared textures that replace the ``_opaque.png`` twins. Synthetic bytes only."""
import json
import struct

import pytest

from xi.entity.mesh.xi_export import TextureImage
from xi.zone.xi_export import (
    Placement,
    ZonePrimitive,
    build_glb,
    cutout_texels,
    split_cutout,
    triangle_cuts_out,
    wind_frame,
    wind_weights,
)

TEX = "model   tree"


def _texture(w=8, h=8, clear=()):
    """Opaque (FFXI half-scale 0x80) everywhere except the (x, y) texels in ``clear``."""
    rgba = bytearray()
    for y in range(h):
        for x in range(w):
            rgba += bytes((90, 120, 60, 0x00 if (x, y) in clear else 0x80))
    return TextureImage(name=TEX, width=w, height=h, rgba=bytes(rgba))


# the left half of the top row of 2x2 blocks is see-through: a "leaf" block at u 0-.5, v 0-.25
LEAF_TEXELS = {(x, y) for x in range(0, 3) for y in range(0, 1)}


def _prim(tris, name=TEX, two_sided=False):
    pos, uvs = [], []
    for tri in tris:
        for p, uv in tri:
            pos.append(p)
            uvs.append(uv)
    return ZonePrimitive(texture_name=name, positions=pos, normals=[(0.0, -1.0, 0.0)] * len(pos),
                         uvs=uvs, colors=[(0.5, 0.5, 0.5, 0.5)] * len(pos), alpha_test=two_sided)


# a trunk triangle sampling the solid right half, and a leaf card triangle sampling the leaf block
TRUNK = [((0.0, 0.0, 0.0), (0.6, 0.6)), ((0.2, -4.0, 0.0), (0.9, 0.6)), ((0.0, -4.0, 0.2), (0.6, 0.9))]
LEAF = [((1.0, -3.0, 0.0), (0.05, 0.05)), ((2.0, -4.0, 0.0), (0.3, 0.05)), ((1.0, -4.0, 1.0), (0.05, 0.1))]


def test_cutout_texels_use_the_scaled_alpha():
    img = _texture(clear={(0, 0)})
    m = cutout_texels(img, alpha_scale=2.0)
    assert m[0, 0] and m.sum() == 1              # row = v, column = u; no margin around it
    assert not cutout_texels(_texture()).any()   # 0x80 x2 = 255: all solid
    assert cutout_texels(_texture(), alpha_scale=0.5).all()   # 0x80 x0.5 = 64 < 128


def test_triangle_cuts_out():
    m = cutout_texels(_texture(clear=LEAF_TEXELS))
    assert triangle_cuts_out(m, [uv for _, uv in LEAF])
    assert not triangle_cuts_out(m, [uv for _, uv in TRUNK])
    # smaller than a texel, inside a see-through one: still counts
    assert triangle_cuts_out(m, [(0.06, 0.06), (0.061, 0.06), (0.06, 0.061)])
    # the same leaf block one texture period over (u 1.05): wraps
    assert triangle_cuts_out(m, [(u + 1.0, v) for _, (u, v) in LEAF])
    # a solid block whose edge sits exactly on the see-through block's boundary (u = 3/8,
    # the texel line between column 2 and 3) is solid: FFXI trunks sit on atlas edges
    assert not triangle_cuts_out(m, [(3 / 8, 0.0), (6 / 8, 0.0), (3 / 8, 1 / 8)])


def test_split_cutout_keeps_each_triangle_whole():
    m = cutout_texels(_texture(clear=LEAF_TEXELS))
    prim = _prim([TRUNK, LEAF, TRUNK], two_sided=True)
    cut, solid = split_cutout(prim, m)
    assert len(cut.positions) == 3 and len(solid.positions) == 6
    assert cut.uvs == [uv for _, uv in LEAF]
    assert cut.alpha_test and solid.alpha_test and len(solid.colors) == 6
    only_leaf = _prim([LEAF])
    assert split_cutout(only_leaf, m) == (only_leaf, None)


def test_wind_weights_base_is_the_largest_y():
    # FFXI Y points down: y=0 is the ground, y=-4 the top
    prims = [_prim([TRUNK, LEAF])]
    base, height, cx, cz, radius = wind_frame(prims)
    assert (base, height) == (0.0, 4.0)
    w = wind_weights([(cx, 0.0, cz), (cx, -4.0, cz), (cx, -2.0, cz)], wind_frame(prims))
    assert w == [(0.0, 0.0), (1.0, 0.0), (0.5, 0.0)]
    far = max(prims[0].positions, key=lambda p: (p[0] - cx) ** 2 + (p[2] - cz) ** 2)
    assert wind_weights([far], wind_frame(prims))[0][1] == pytest.approx(1.0)
    # a flat mesh doesn't sway
    flat = [_prim([[((0, 0, 0), (0, 0)), ((1, 0, 0), (0, 0)), ((0, 0, 1), (0, 0))]])]
    assert wind_weights(flat[0].positions, wind_frame(flat))[0][0] == 0.0


def _read_glb(path):
    b = path.read_bytes()
    off, gltf, binc = 12, None, None
    while off < len(b):
        clen, ctype = struct.unpack_from("<II", b, off)
        off += 8
        chunk = b[off:off + clen]
        off += clen
        if ctype == 0x4E4F534A:
            gltf = json.loads(chunk)
        elif ctype == 0x004E4942:
            binc = chunk

    def acc(i):
        a = gltf["accessors"][i]
        bv = gltf["bufferViews"][a["bufferView"]]
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        fmt = {5126: "f", 5123: "H", 5125: "I"}[a["componentType"]]
        v = struct.unpack_from("<%d%s" % (a["count"] * n, fmt), binc,
                               bv.get("byteOffset", 0) + a.get("byteOffset", 0))
        return [v[k:k + n] for k in range(0, len(v), n)] if n > 1 else list(v)
    return gltf, acc


def _export(tmp_path, **kw):
    meshes = {"_tree": [_prim([TRUNK, LEAF], two_sided=True)],
              "ground": [_prim([TRUNK])]}
    placements = [Placement(mesh_id=n, position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0),
                            scale=(1.0, 1.0, 1.0)) for n in meshes]
    paths = build_glb(tmp_path / "zone.dat", tmp_path, meshes, placements,
                      {TEX: _texture(clear=LEAF_TEXELS)}, right_handed=True, opaque_nonblend=True,
                      vertex_color="raw", **kw)
    return paths, *_read_glb(paths[0])


def test_unreal_foliage_export(tmp_path):
    paths, gltf, acc = _export(tmp_path, foliage=True, opaque_twins=False)
    mats = [m["name"] for m in gltf["materials"]]
    # the tree's trunk joins the ground's solid material; only the leaf is a cutout
    assert sorted(mats) == ["model   tree", "model   tree_cutout"]
    assert {m["name"]: m["alphaMode"] for m in gltf["materials"]} == {
        "model   tree": "OPAQUE", "model   tree_cutout": "MASK"}
    for mesh in gltf["meshes"]:
        for p in mesh["primitives"]:
            winds = acc(p["attributes"]["TEXCOORD_1"])
            assert len(winds) == len(acc(p["attributes"]["POSITION"]))
            if mats[p["material"]].endswith("_cutout"):
                assert mesh["name"] == "_tree"
                assert all(0.0 < h <= 1.0 and 0.0 < r <= 1.0 for h, r in winds)
            else:
                assert set(winds) == {(0.0, 0.0)}
    # one PNG per texture, shared by both materials: no _opaque twin
    assert [p.name for p in paths[1:]] == ["model_tree.png"]


def test_default_export_is_unchanged(tmp_path):
    paths, gltf, _ = _export(tmp_path)
    assert sorted(m["name"] for m in gltf["materials"]) == ["model   tree", "model   tree_cutout"]
    tree = next(m for m in gltf["meshes"] if m["name"] == "_tree")
    assert len(tree["primitives"]) == 1 and "TEXCOORD_1" not in tree["primitives"][0]["attributes"]
    assert sorted(p.name for p in paths[1:]) == ["model_tree.png", "model_tree_opaque.png"]
