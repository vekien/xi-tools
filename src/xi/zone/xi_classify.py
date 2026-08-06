"""Auto-classify a zone mesh into a coarse ``category`` (+ finer ``kind``).

Two signals, combined:

1. **Name tokens** — FFXI's romaji asset names are remarkably consistent
   (``hasira``=pillar, ``gete``=gate, ``kaidan``/``step``=stairs, ``hasi``=bridge,
   ``gaitou``/``lamp``=light, ``saku``=fence, ``kanban``=sign, ``yuka``=floor,
   ``kabe``=wall, ``yane``=roof, ``yama``=mountain/terrain, ``mizu``/``umi``=water).
   When a token matches it sets the ``kind`` and usually the category — high confidence.

2. **Geometry** — area-weighted face-normal orientation + bbox aspect, computed from the
   mesh verts. Floors are flat + up-facing; walls are tall + side-facing; props are small;
   huge spans are terrain. Used when the name says nothing.

CAVEAT: overworld FFXI meshes are often *composite* (a whole ground tile with its
buildings baked in — ``block03``, ``en_*``), so a single per-mesh category is fuzzy for
those; they land in ``structure``/``terrain``. Interior/room zones separate floor/wall
geometry more cleanly. The category is a strong starting label, not ground truth.
"""

import math
import re

# Finer kind from a name token, plus the coarse category that kind implies.
# kind -> category
_KIND_CATEGORY = {
    "prop": "object", "light": "object", "sign": "object", "fence": "object",
    "stairs": "structure", "bridge": "structure", "pillar": "structure",
    "gate": "structure", "door": "structure", "tower": "structure", "roof": "structure",
    "wall": "wall", "floor": "floor", "ceiling": "ceiling",
    "water": "water", "terrain": "terrain",
}

# name token -> kind. Tokens are matched against name split on [_ + digits].
_TOKEN_KIND = {
    # props
    "box": "prop", "hako": "prop", "tubo": "prop", "talu": "prop", "isu": "prop",
    "tukue": "prop", "barrel": "prop", "baketu": "prop", "obj": "prop", "crate": "prop",
    # lights
    "lamp": "light", "gaitou": "light", "akari": "light", "light": "light", "torch": "light",
    # signs
    "kanban": "sign", "bord": "sign", "board": "sign", "sign": "sign",
    # fences
    "saku": "fence", "fence": "fence",
    # structure subtypes
    "kaidan": "stairs", "step": "stairs", "stair": "stairs",
    "hasi": "bridge", "bridge": "bridge",
    "hasira": "pillar", "pillar": "pillar", "hashira": "pillar", "column": "pillar",
    "gete": "gate", "gate": "gate", "mon": "gate",
    "door": "door", "tobira": "door",
    "tower": "tower",
    "yane": "roof", "roof": "roof",
    # explicit floor/wall when the author named it
    "yuka": "floor", "floor": "floor", "yuka2": "floor",
    "kabe": "wall", "wall": "wall",
    # terrain / water
    "yama": "terrain", "iwa": "terrain", "rock": "terrain", "cliff": "terrain",
    "mizu": "water", "umi": "water", "water": "water", "taki": "water", "kawa": "water",
}


def _name_kind(name: str) -> str | None:
    for tok in re.split(r"[_0-9]+", name.lower()):
        if tok in _TOKEN_KIND:
            return _TOKEN_KIND[tok]
    return None


def face_stats(prims) -> dict:
    """Area-weighted face-normal orientation + bbox of a mesh's primitives.

    Returns horiz/side/up/down fractions of surface area and bbox dims. Normals are
    derived geometrically from the triangle winding (independent of stored normals)."""
    up = down = side = 0.0
    xs: list = []
    ys: list = []
    zs: list = []
    for prim in prims:
        P = prim.positions
        for i in range(0, len(P) - 2, 3):
            a, b, c = P[i], P[i + 1], P[i + 2]
            xs.append(a[0]); ys.append(a[1]); zs.append(a[2])
            xs.append(b[0]); ys.append(b[1]); zs.append(b[2])
            xs.append(c[0]); ys.append(c[1]); zs.append(c[2])
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            mag = math.sqrt(nx * nx + ny * ny + nz * nz)
            if mag <= 1e-9:
                continue
            area = 0.5 * mag
            if abs(ny) / mag > 0.7:        # face is ~horizontal (normal points up/down)
                if ny > 0:
                    up += area
                else:
                    down += area
            else:                           # face is ~vertical (wall-like)
                side += area
    tot = up + down + side or 1.0
    dx = (max(xs) - min(xs)) if xs else 0.0
    dy = (max(ys) - min(ys)) if ys else 0.0
    dz = (max(zs) - min(zs)) if zs else 0.0
    return {"horiz": (up + down) / tot, "side": side / tot, "up": up / tot, "down": down / tot,
            "dx": dx, "dy": dy, "dz": dz, "footprint": max(dx, dz), "height": dy, "area": tot}


def classify_mesh(name: str, prims, object_footprint: float = 5.0) -> dict:
    """Classify a mesh into ``category`` (+ ``kind``) from its name and geometry.

    Returns ``{category, kind, source, footprint, height, aspect, horiz, side, up, down}``.
    ``source`` is ``"name"`` (a token matched), ``"geometry"`` (geometric fallback), or
    ``"none"`` (no geometry available and the name says nothing).
    """
    if not prims:
        # No geometry to measure (mesh absent / LOD variant) — fall back to the name only.
        kind = _name_kind(name)
        return {
            "category": _KIND_CATEGORY.get(kind, "unknown") if kind else "unknown",
            "kind": kind, "source": "name" if kind else "none",
            "footprint": 0.0, "height": 0.0, "aspect": 0.0, "dims": [0.0, 0.0, 0.0],
            "horiz": 0.0, "side": 0.0, "up": 0.0, "down": 0.0,
        }

    s = face_stats(prims)
    foot, h = s["footprint"], s["height"]
    aspect = h / max(foot, 0.01)

    kind = _name_kind(name)
    if kind is not None:
        category = _KIND_CATEGORY.get(kind, "structure")
        # A name-tagged prop that is physically large is really structure (e.g. a giant
        # signpost arch); keep small ones as object.
        if category == "object" and foot > object_footprint * 3:
            category = "structure"
        source = "name"
    else:
        source = "geometry"
        if foot <= object_footprint:
            category = "object"
        elif s["horiz"] >= 0.72 and aspect < 0.4:
            category = "floor" if s["up"] >= s["down"] else "ceiling"
        elif s["side"] >= 0.82 and aspect > 0.8:
            category = "wall"
        elif foot >= 55:
            category = "terrain"
        else:
            category = "structure"

    return {
        "category": category, "kind": kind, "source": source,
        "footprint": round(foot, 3), "height": round(h, 3), "aspect": round(aspect, 3),
        "dims": [round(s["dx"], 3), round(s["dy"], 3), round(s["dz"], 3)],
        "horiz": round(s["horiz"], 3), "side": round(s["side"], 3),
        "up": round(s["up"], 3), "down": round(s["down"], 3),
    }
