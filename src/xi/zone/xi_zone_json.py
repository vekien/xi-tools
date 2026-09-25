"""The ``<stem>.zone.json`` that ``xi zone export --json`` writes (``export_zone_json`` in
xi.zone.xi_export): its schema id and ``validate_zone_json``, the hand validator for
``schema/zone_export.json``. jsonschema is not a dependency; the validator names the
offending field and is kept in step with the schema file, which is the specification
(tests/test_zone_export_json.py checks the two against each other)."""
import re
from typing import List

ZONE_JSON_SCHEMA = "xi.zone-export.v1"     # schema/zone_export.json

# Every object of the format as (required keys, optional keys), named as in the schema:
# "" is the document, the rest its $defs (and the inline "lod" and "collision"). In the
# _ALL_OR_NONE ones the optional keys come all together or not at all ({}).
SHAPES = {
    "": (("schema", "dat", "zone_id", "zone_name", "mesh_count", "placement_count", "directories",
          "meshes", "sky_meshes", "textures", "placements", "music", "weather", "sound_fx",
          "companion_dats", "sub_areas", "collision"), ("fbx_zero_coords",)),
    "collision": (("present",), ()),
    "mesh": (("name", "textures", "alpha_blend", "alpha_test", "double_sided"), ()),
    "placement": (("index", "name", "pos", "rot", "scale", "lod", "flags", "skip_decal", "effect_link",
                   "culling_table_link", "environment_link", "file_id_link", "point_lights", "tags"), ()),
    "lod": (("high", "mid", "low"), ()),
    "music": ((), ("day", "night", "battle_solo", "battle_party")),
    "musicTrack": (("id", "title"), ()),
    "weather": (("types", "ambient_sounds", "weights"), ()),
    "ambientSound": (("weather", "indoors", "time", "sound_id", "file", "spw_path", "title", "category"), ()),
    "weatherWeights": ((), ("total_days", "weights")),
    "weatherWeight": (("id", "name", "tag", "in_dat", "normal_days", "common_days", "rare_days"), ()),
    "soundFx": (("dir", "section", "sound_id", "file", "spw_path", "title", "category"), ()),
    "companions": ((), ("event", "dialog", "npc")),
    "subArea": (("id", "file_id", "dat"), ("placements",)),
    "zeroCoords": (("frame", "files"), ()),
    "zeroFile": (("file", "kind", "offset"), ("sub_area", "mesh", "instances")),
    "zeroInstance": (("matrix", "location", "rotation", "scale"), ("placement", "mirrored")),
}
_ALL_OR_NONE = ("music", "weatherWeights", "companions")
ZERO_KINDS = ("zone", "sub_area", "object")
# What each fbx_zero_coords file kind carries and never carries (the schema's allOf).
_ZERO_KIND_KEYS = {"zone": ((), ("sub_area", "mesh", "instances")),
                   "sub_area": (("sub_area",), ("mesh", "instances")),
                   "object": (("mesh", "instances"), ())}

_ROM_PATH_RX = re.compile(r"^ROM[0-9]*/[0-9]+/[0-9]+(?:\.DAT)?$")    # common.json romPath
_DAT_LINK_RX = re.compile(r"^[0-9a-f]{8}$")
_SPW_FILE_RX = re.compile(r"^se[0-9]{6,}\.spw$")
_SPW_PATH_RX = re.compile(r"^se/se[0-9]{3,}/se[0-9]{6,}\.spw$")
_FBX_RX = re.compile(r"^.+\.fbx$")
_U32 = 0xFFFFFFFF
_M = object()   # a missing key: already reported by _Check.shape, so every check skips it


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_vec(v, n: int = 3) -> bool:
    return isinstance(v, list) and len(v) == n and all(_is_num(x) for x in v)


class _Check:
    """Collects problems, each prefixed with the path of the field it is about. The
    checks take a value from ``obj.get(key, _M)`` and pass over a missing one."""

    def __init__(self):
        self.errs: List[str] = []

    def add(self, where: str, msg: str) -> None:
        self.errs.append(f"{where}: {msg}" if where else msg)

    def shape(self, where: str, obj, name: str) -> bool:
        """Unknown and missing keys of ``obj`` against SHAPES[name]; False when it is
        missing or isn't an object (nothing more to check)."""
        if obj is _M:
            return False
        if not isinstance(obj, dict):
            self.add(where, "must be an object")
            return False
        required, optional = SHAPES[name]
        for k in obj:
            if k not in required and k not in optional:
                self.add(where, f"unknown key {k!r}")
        for k in required:
            if k not in obj:
                self.add(where, f"missing {k!r}")
        if name in _ALL_OR_NONE and obj and set(obj) != set(optional):
            self.add(where, f"must have all of {', '.join(optional)}, or be {{}}")
        return True

    def items(self, where: str, v, each) -> list:
        """``v`` as a list, each item checked by ``each(path, item)``; [] when it isn't one."""
        if v is _M:
            return []
        if not isinstance(v, list):
            self.add(where, "must be a list")
            return []
        for i, x in enumerate(v):
            each(f"{where}[{i}]", x)
        return v

    def string(self, where: str, v, *, nullable: bool = False, nonempty: bool = False) -> None:
        if v is _M or (v is None and nullable):
            return
        if not isinstance(v, str) or (nonempty and not v):
            self.add(where, ("must be a non-empty string" if nonempty else "must be a string")
                     + (" or null" if nullable else ""))

    def integer(self, where: str, v, lo: int = 0, hi=None, *, nullable: bool = False) -> None:
        if v is _M or (v is None and nullable):
            return
        if not (_is_int(v) and v >= lo and (hi is None or v <= hi)):
            rng = f"from {lo} to {hi}" if hi is not None else ("non-negative" if lo == 0 else f"at least {lo}")
            self.add(where, f"must be an integer {rng}" + (" or null" if nullable else ""))

    def boolean(self, where: str, v) -> None:
        if v is not _M and not isinstance(v, bool):
            self.add(where, "must be true or false")

    def number(self, where: str, v) -> None:
        if v is not _M and not _is_num(v):
            self.add(where, "must be a number")

    def vec3(self, where: str, v) -> None:
        if v is not _M and not _is_vec(v):
            self.add(where, "must be a list of 3 numbers")

    def match(self, where: str, v, rx, what: str, *, nullable: bool = False) -> None:
        if v is _M or (v is None and nullable):
            return
        if not (isinstance(v, str) and rx.match(v)):
            self.add(where, f"must be {what}" + (" or null" if nullable else ""))


def _mesh(c: _Check, where: str, m) -> None:
    if not c.shape(where, m, "mesh"):
        return
    c.string(f"{where}.name", m.get("name", _M), nonempty=True)
    c.items(f"{where}.textures", m.get("textures", _M), c.string)
    for k in ("alpha_blend", "alpha_test", "double_sided"):
        c.boolean(f"{where}.{k}", m.get(k, _M))


def _placement(c: _Check, where: str, p) -> None:
    if not c.shape(where, p, "placement"):
        return
    c.integer(f"{where}.index", p.get("index", _M))
    name = p.get("name", _M)
    if name is not _M and not (isinstance(name, str) and 1 <= len(name) <= 16):
        c.add(f"{where}.name", "must be a mesh id of 1–16 characters")
    for k in ("pos", "rot", "scale"):
        c.vec3(f"{where}.{k}", p.get(k, _M))
    lod = p.get("lod", _M)
    if c.shape(f"{where}.lod", lod, "lod"):
        for k in ("high", "mid", "low"):
            c.number(f"{where}.lod.{k}", lod.get(k, _M))
    flags = p.get("flags", _M)
    if flags is not _M and not (isinstance(flags, list) and len(flags) == 4
                                and all(_is_int(b) and 0 <= b <= 255 for b in flags)):
        c.add(f"{where}.flags", "must be 4 bytes (integers 0–255)")
    c.boolean(f"{where}.skip_decal", p.get("skip_decal", _M))
    for k in ("effect_link", "environment_link"):
        c.match(f"{where}.{k}", p.get(k, _M), _DAT_LINK_RX, "8 lowercase hex digits", nullable=True)
    for k in ("culling_table_link", "file_id_link"):
        c.integer(f"{where}.{k}", p.get(k, _M), 1, _U32, nullable=True)
    if len(c.items(f"{where}.point_lights", p.get("point_lights", _M), c.integer)) > 4:
        c.add(f"{where}.point_lights", "must have at most 4 entries")
    c.items(f"{where}.tags", p.get("tags", _M), c.string)


def _music(c: _Check, where: str, music) -> None:
    if not c.shape(where, music, "music"):
        return
    for k in SHAPES["music"][1]:
        track = music.get(k, _M)
        if track is not None and c.shape(f"{where}.{k}", track, "musicTrack"):
            c.integer(f"{where}.{k}.id", track.get("id", _M), 1)
            c.string(f"{where}.{k}.title", track.get("title", _M), nullable=True)


def _sound(c: _Check, where: str, s, shape: str) -> None:
    if not c.shape(where, s, shape):
        return
    if shape == "ambientSound":
        c.string(f"{where}.weather", s.get("weather", _M), nullable=True)
        c.boolean(f"{where}.indoors", s.get("indoors", _M))
        c.string(f"{where}.time", s.get("time", _M), nullable=True)
    else:
        c.string(f"{where}.dir", s.get("dir", _M))
        c.string(f"{where}.section", s.get("section", _M))
    c.integer(f"{where}.sound_id", s.get("sound_id", _M))
    c.match(f"{where}.file", s.get("file", _M), _SPW_FILE_RX, "se<id>.spw")
    c.match(f"{where}.spw_path", s.get("spw_path", _M), _SPW_PATH_RX, "se/se<folder>/se<id>.spw")
    c.string(f"{where}.title", s.get("title", _M), nullable=True)
    c.string(f"{where}.category", s.get("category", _M), nullable=True)


def _weight(c: _Check, where: str, e) -> None:
    if not c.shape(where, e, "weatherWeight"):
        return
    c.integer(f"{where}.id", e.get("id", _M), 0, 31)
    c.string(f"{where}.name", e.get("name", _M))
    c.string(f"{where}.tag", e.get("tag", _M), nullable=True)
    c.boolean(f"{where}.in_dat", e.get("in_dat", _M))
    for k in ("normal_days", "common_days", "rare_days"):
        c.integer(f"{where}.{k}", e.get(k, _M))


def _weather(c: _Check, where: str, w) -> None:
    if not c.shape(where, w, "weather"):
        return
    c.items(f"{where}.types", w.get("types", _M), c.string)
    c.items(f"{where}.ambient_sounds", w.get("ambient_sounds", _M),
            lambda at, s: _sound(c, at, s, "ambientSound"))
    ww = w.get("weights", _M)
    if c.shape(f"{where}.weights", ww, "weatherWeights"):
        c.integer(f"{where}.weights.total_days", ww.get("total_days", _M))
        c.items(f"{where}.weights.weights", ww.get("weights", _M), lambda at, e: _weight(c, at, e))


def _companions(c: _Check, where: str, comp) -> None:
    if not c.shape(where, comp, "companions"):
        return
    for k in SHAPES["companions"][1]:
        c.match(f"{where}.{k}", comp.get(k, _M), _ROM_PATH_RX, "a ROM path such as ROM/21/54.DAT",
                nullable=True)


def _sub_area(c: _Check, where: str, sa) -> None:
    if not c.shape(where, sa, "subArea"):
        return
    c.integer(f"{where}.id", sa.get("id", _M))
    c.integer(f"{where}.file_id", sa.get("file_id", _M))
    c.match(f"{where}.dat", sa.get("dat", _M), _ROM_PATH_RX, "a ROM path such as ROM/2/86.DAT", nullable=True)
    c.items(f"{where}.placements", sa.get("placements", _M), lambda at, p: _placement(c, at, p))


def _zero_instance(c: _Check, where: str, inst) -> None:
    if not c.shape(where, inst, "zeroInstance"):
        return
    c.integer(f"{where}.placement", inst.get("placement", _M))
    m = inst.get("matrix", _M)
    if m is not _M and not (isinstance(m, list) and len(m) == 4 and all(_is_vec(r, 4) for r in m)):
        c.add(f"{where}.matrix", "must be 4 rows of 4 numbers")
    for k in ("location", "rotation", "scale"):
        c.vec3(f"{where}.{k}", inst.get(k, _M))
    if inst.get("mirrored", True) is not True:
        c.add(f"{where}.mirrored", "must be true (left out when not mirrored)")


def _zero_file(c: _Check, where: str, f) -> None:
    if not c.shape(where, f, "zeroFile"):
        return
    c.match(f"{where}.file", f.get("file", _M), _FBX_RX, "the .fbx's path relative to the JSON's folder")
    kind = f.get("kind", _M)
    if kind is not _M and kind not in ZERO_KINDS:
        c.add(f"{where}.kind", f"must be one of {', '.join(ZERO_KINDS)}")
    c.vec3(f"{where}.offset", f.get("offset", _M))
    c.integer(f"{where}.sub_area", f.get("sub_area", _M))
    c.string(f"{where}.mesh", f.get("mesh", _M), nonempty=True)
    instances = f.get("instances", _M)
    if instances is not _M and not (isinstance(instances, list) and instances):
        c.add(f"{where}.instances", "must be a non-empty list")
    else:
        c.items(f"{where}.instances", instances, lambda at, i: _zero_instance(c, at, i))
    need, never = _ZERO_KIND_KEYS.get(kind if isinstance(kind, str) else None, ((), ()))
    for k in need:
        if k not in f:
            c.add(where, f"kind {kind!r} needs {k!r}")
    for k in never:
        if k in f:
            c.add(where, f"kind {kind!r} has no {k!r}")


def validate_zone_json(doc) -> List[str]:
    """Problems with a ``.zone.json`` against ``schema/zone_export.json`` (empty when it
    conforms). Hand-rolled like ``xi.ability.xi_compose.validate_recipe``; beyond the
    schema it checks that mesh_count and placement_count are the lengths they count."""
    if not isinstance(doc, dict):
        return ["the zone JSON must be a JSON object"]
    c = _Check()
    c.shape("", doc, "")
    get = doc.get
    if get("schema", ZONE_JSON_SCHEMA) != ZONE_JSON_SCHEMA:
        c.add("", f"schema must be {ZONE_JSON_SCHEMA!r}")
    c.string("dat", get("dat", _M), nonempty=True)
    c.integer("zone_id", get("zone_id", _M), nullable=True)
    c.string("zone_name", get("zone_name", _M), nullable=True)
    c.items("directories", get("directories", _M), lambda at, v: c.string(at, v, nonempty=True))
    c.items("meshes", get("meshes", _M), lambda at, m: _mesh(c, at, m))
    c.items("sky_meshes", get("sky_meshes", _M), lambda at, m: _mesh(c, at, m))
    c.items("textures", get("textures", _M), c.string)
    c.items("placements", get("placements", _M), lambda at, p: _placement(c, at, p))
    for key, counted in (("mesh_count", "meshes"), ("placement_count", "placements")):
        n = get(key, _M)
        c.integer(key, n)
        if _is_int(n) and isinstance(get(counted), list) and n != len(doc[counted]):
            c.add(key, f"is {n}, but {counted} has {len(doc[counted])}")
    _music(c, "music", get("music", _M))
    _weather(c, "weather", get("weather", _M))
    c.items("sound_fx", get("sound_fx", _M), lambda at, s: _sound(c, at, s, "soundFx"))
    _companions(c, "companion_dats", get("companion_dats", _M))
    c.items("sub_areas", get("sub_areas", _M), lambda at, sa: _sub_area(c, at, sa))
    if c.shape("collision", get("collision", _M), "collision"):
        c.boolean("collision.present", doc["collision"].get("present", _M))
    zc = get("fbx_zero_coords", _M)
    if c.shape("fbx_zero_coords", zc, "zeroCoords"):
        c.string("fbx_zero_coords.frame", zc.get("frame", _M))
        c.items("fbx_zero_coords.files", zc.get("files", _M), lambda at, f: _zero_file(c, at, f))
    return c.errs
