"""`0x1F` ParticleMesh / `0x21` SpriteSheetMesh decoding (xi.fx.xi_particle_mesh).

The synthetic tests pin the material/vertex offset arithmetic — including the
multi-material padding branch that no retail DAT sampled so far exercises. The
install-backed tests use the ``root`` fixture and skip without FFXI_DIR."""
import struct
from pathlib import Path

import pytest

from xi.entity.anim.xi_export import Section, parse_sections
from xi.fx import xi_particle_mesh as pm


def _section(payload: bytes, type_code: int = pm.PARTICLE_MESH_TYPE) -> tuple:
    """Wrap a payload in a 16-byte section header, as it sits in a DAT."""
    size = 0x10 + len(payload)
    data = b"mesh" + struct.pack("<I", (size // 0x10 << 7) | type_code) + b"\0" * 8 + payload
    return data, Section(name="mesh", type_code=type_code, start=0, size=size, data_start=0x10)


def _vertex(x: float, y: float, z: float, color: int = 0x80808080) -> bytes:
    return (struct.pack("<3f", x, y, z) + struct.pack("<3f", 0.0, 0.0, -1.0)
            + struct.pack("<I", color) + struct.pack("<2f", 0.0, 0.0))


def _particle_payload(mat_count: int, extra_count: int, tris: int) -> bytes:
    counts = [tris] + [0] * (mat_count + extra_count - 1)
    head = struct.pack("<HHBBHHHH", 6, 0, mat_count, extra_count, tris, tris, 0, 0)
    assert len(head) == 14
    body = head[:8] + b"".join(struct.pack("<H", c) for c in counts)
    body += b"\0" * (pm._material_table_offset(mat_count, extra_count) - len(body))
    body += b"".join(f"grp{i:<5}tex{i:<5}".encode("latin1") for i in range(mat_count))
    body += b"".join(_vertex(float(i), 0.0, 0.0) for i in range(tris * 3))
    return body


# --- offset arithmetic (CMoD3m::GetAnotherShortPointer / GetFloatPointer) ----

@pytest.mark.parametrize("n,expected", [
    (0, 8 + 2 * 0),    # no counts at all
    (1, 14), (2, 14), (3, 14),      # 1-3 entries all round to +3 shorts
    (4, 16),                        # exact multiple of 4 -> no +3
    (5, 22), (6, 22), (7, 22),
    (8, 24),
])
def test_material_table_offset(n, expected):
    """A generic 16-byte alignment would put 1-3 entries at 16, not 14 — the
    two-byte skew that reads garbage positions out of every vertex."""
    assert pm._material_table_offset(n, 0) == expected


def test_single_material_vertices_start_at_30():
    data, sec = _section(_particle_payload(mat_count=1, extra_count=0, tris=2))
    mesh = pm.parse_particle_mesh(data, sec)
    assert mesh is not None
    assert mesh.triangles == 2
    assert mesh.materials == ["grp0    tex0    "]
    assert [p[0] for p in mesh.prims[0].positions] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_zero_material_vertices_start_at_14():
    """`gA=0, gB=1` (retail `coll`/`hi14` in ROM/0/0): no material table at all,
    so the vertex run begins where the materials would have."""
    data, sec = _section(_particle_payload(mat_count=0, extra_count=1, tris=2))
    mesh = pm.parse_particle_mesh(data, sec)
    assert mesh is not None
    assert mesh.materials == []
    assert mesh.prims[0].positions[0] == (0.0, 0.0, 0.0)
    assert len(mesh.prims[0].positions) == 6


def test_rejected_marker_is_skipped():
    payload = bytearray(_particle_payload(1, 0, 2))
    payload[0] = 7          # what the client rewrites the marker to on bad data
    data, sec = _section(bytes(payload))
    assert pm.parse_particle_mesh(data, sec) is None


def test_truncated_section_returns_none():
    payload = _particle_payload(1, 0, 8)[:60]     # claims 8 tris, carries ~1
    data, sec = _section(payload)
    assert pm.parse_particle_mesh(data, sec) is None


def test_diffuse_is_bgra():
    """D3DCOLOR: A in the high byte, so the bytes in memory read B, G, R, A."""
    r, g, b, a = pm._diffuse(0x80102030)
    assert [round(v * 255) for v in (r, g, b, a)] == [0x10, 0x20, 0x30, 0x80]


# --- against the installed client -------------------------------------------

HOME_POINT = "ROM/3/25.DAT"      # look kind 0, appearance 0x33 -> file_id 1351
EFFECTS = "ROM/0/0.DAT"          # combat effects: the 0-material `coll` case


def _meshes(root: Path, rel: str):
    p = root / rel
    data = bytearray((Path(str(p) + ".base") if Path(str(p) + ".base").exists() else p).read_bytes())
    return pm.particle_meshes(data, parse_sections(data))


def test_home_point_layers(root: Path):
    meshes = _meshes(root, HOME_POINT)
    assert {"wa  ", "sil ", "naka", "bind", "pou1"} <= set(meshes)
    assert [meshes[k].triangles for k in ("wa  ", "sil ", "naka", "bind", "pou1")] == [48, 192, 24, 30, 2]
    assert sum(meshes[k].triangles for k in ("wa  ", "sil ", "naka", "bind", "pou1")) == 296
    # the sprite cards: 1 card + 2 cards = 6 more triangles, 302 over 7 layers
    assert meshes["tama"].triangles == 2 and meshes["tubu"].triangles == 4
    assert sum(m.triangles for m in meshes.values()) == 302


def test_home_point_crystal_geometry(root: Path):
    crystal = _meshes(root, HOME_POINT)["bind"]
    assert crystal.materials == ["bind    kori    "]     # the 0x20 texture's own 16-char name
    ys = [p[1] for p in crystal.prims[0].positions]
    assert round(max(ys) - min(ys), 3) == 2.160          # Y is DOWN: -2.185 .. -0.025
    ground = _meshes(root, HOME_POINT)["wa  "]
    assert {round(p[1], 6) for p in ground.prims[0].positions} == {0.0}   # flat ground ring


def test_every_particle_mesh_decodes(root: Path):
    """Both offset branches over a real corpus — every 0x1F/0x21 section in these
    DATs must decode, with its vertex run landing inside the section."""
    for rel in (HOME_POINT, EFFECTS):
        data = bytearray((root / rel).read_bytes())
        for s in parse_sections(data):
            if s.type_code == pm.PARTICLE_MESH_TYPE:
                mesh = pm.parse_particle_mesh(data, s)
            elif s.type_code == pm.SPRITE_MESH_TYPE:
                mesh = pm.parse_sprite_mesh(data, s)
            else:
                continue
            assert mesh is not None, f"{rel}: {s.name!r} @0x{s.start:x} did not decode"
            corners = sum(len(p.positions) for p in mesh.prims)
            assert corners == mesh.triangles * 3


def test_meshes_are_keyed_by_fourcc(root: Path):
    """`particle_meshes` is a NAME map, matching how the client resolves a
    generator's DatId — ROM/0/0 reuses 14 names across scopes, and the map keeps
    the first of each rather than growing duplicate keys."""
    data = bytearray((root / EFFECTS).read_bytes())
    sections = parse_sections(data)
    unique = {s.name for s in sections
              if s.type_code in (pm.PARTICLE_MESH_TYPE, pm.SPRITE_MESH_TYPE)}
    assert set(_meshes(root, EFFECTS)) == unique
