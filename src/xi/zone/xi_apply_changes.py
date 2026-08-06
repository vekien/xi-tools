#!/usr/bin/env python3
"""`xi zone import-json` — apply a JSON change-set exported by the web level
editor back to the zone DAT.

Supports:
  placements.modify  — patch TRS of an existing placement in-place
  placements.add     — duplicate an existing placement (same mesh_id) at a new TRS
  placements.delete  — blank the mesh_id so the engine skips the object
  vfx.modify         — patch position (and optionally scale) of a 0x05 generator
  vfx.remove         — splice out a 0x05 generator section entirely
  vfx.add            — duplicate an existing 0x05 generator with a new name + position

JSON format (as exported by web/leveleditor via Changes > Export JSON):

  {
    "zone": "game/ROM/1/41.DAT",      // used to locate the source DAT when dat_path
    "placements": [                   // omitted or unresolved
      {"op": "modify", "name": "block03",
       "pos": [x,y,z], "rot": [rx,ry,rz], "scale": [sx,sy,sz]},
      {"op": "add", "name": "block03",
       "pos": [x,y,z], "rot": [rx,ry,rz], "scale": [sx,sy,sz]},
      {"op": "delete", "name": "hasi"}
    ],
    "vfx": [
      {"op": "modify", "id": "seap", "pos": [x,y,z]},
      {"op": "remove", "id": "seap"},
      {"op": "add",    "source_id": "seap", "new_id": "seaq", "pos": [x,y,z]}
    ]
  }

VFX "id" is the 4-char section FourCC stored in the generator header — the same
value the web editor captures from gen.id via parseGenerator().
"""

import json
import shutil
import struct
from pathlib import Path

import click

from xi.entity.anim.xi_export import parse_sections
from xi.xi_config import FFXI_DIR, FFXI_PIVOT_DIR, editable_dat
from xi.zone.xi_decrypt import (
    decrypt_zone_mesh,
    decrypt_zone_objects,
    reencrypt_zone_mesh,
    reencrypt_zone_objects,
)
from xi.zone.xi_export import (
    SECTION_TYPE_ZONE_DEF,
    SECTION_TYPE_ZONE_MESH,
    SECTION_TYPE_TEXTURE,
    trs_matrix,
    parse_zone_mesh_section,
)
from xi.zone.xi_import import _zero_placement
from xi.zone.xi_zonedef import (add_placements, add_collision_transforms, add_to_culling_tables,
                                  assign_placements_to_nearest_leaf, expand_placement_bounds_points, parse_zonedef,
                                  hide_placement, remove_index_from_its_leaf, TRANSFORM_SIZE, TRANSFORM_CULLGROUP)
from xi.fx.xi_core import (
    parse_sections as fx_parse_sections,
    EFFECT_TYPE,
    _fourcc,
    _mesh_fourccs,
    _texture_fourccs,
    _pos_offset,
    _tag_payload,
    _TAG_SCALE,
)
from xi.zone.xi_decrypt import load_key_tables  # noqa: F401 (for key loading)


# ---------------------------------------------------------------------------
# Debug logging (toggled by `--debug` on `xi zone import-json`)
# ---------------------------------------------------------------------------
# When on, each stage prints a dim-grey, elapsed-since-start timestamped line so
# the import can be traced step by step. Mirrors `xi ftable expand`'s style.
_DEBUG = False


def _set_debug(on: bool) -> None:
    """Enable/disable debug logging."""
    global _DEBUG
    _DEBUG = on
    import xi.zone.xi_object as _xi_object
    _xi_object._DEBUG = on


def _dbg(msg: str) -> None:
    """Print a dim-grey debug line (no-op unless --debug was passed)."""
    if _DEBUG:
        click.echo(click.style(f"[debug] {msg}", fg="bright_black"))


# ---------------------------------------------------------------------------
# Cooperative cancellation (editor "Stop publish" button)
# ---------------------------------------------------------------------------
# The editor can ask the bridge to abort an in-progress publish. There's no way to
# safely kill the worker mid-write, so instead a threading.Event is checked at phase
# boundaries (and, crucially, right before the single final write_bytes). Bailing
# before that write means we never corrupt the DAT mid-write — but if the reset
# already overwrote the live DAT, it's left reverted/partial, hence the editor's
# "bad state" warning.

class PublishCancelled(Exception):
    """Raised at a checkpoint when the editor requested the publish be stopped."""


def _check_cancel(cancel) -> None:
    """Raise PublishCancelled if the editor tripped the cancel Event."""
    if cancel is not None and cancel.is_set():
        raise PublishCancelled("Publish cancelled by user")


# ---------------------------------------------------------------------------
# Placement helpers
# ---------------------------------------------------------------------------

def _xi_prefixed(name: str) -> str:
    """Return the xi-namespaced mesh name used for cross-zone imports (max 16 chars).

    CRITICAL: the retail client enables alpha-test (foliage/leaf cutout) ONLY when the
    mesh name's FIRST byte is '_' ('#' selects the opaque-clamp variant) — verified in
    the client decompile (ZoneRenderer: `if (*data == '_') SetRenderState(ALPHATESTENABLE)`)
    and the xim reference. A plain `xi_` prefix turns `_jag_w02_m` into `xi__jag_w02_m`,
    whose byte[0]='x', so alpha-test goes OFF and the transparent leaf texels render black.
    So when the source name starts with '_' or '#', PRESERVE that leading byte and insert
    the xi namespace after it: `_jag_w02_m` -> `_xi_jag_w02_m` (still byte[0]=='_')."""
    if name.startswith(("xi_", "_xi_", "#xi_")):
        return name[:16]
    c = name[:1]
    if c in ("_", "#"):
        return (c + "xi_" + name[1:])[:16]
    return ("xi_" + name)[:16]


def _read_name(data: bytes, data_start: int, index: int) -> str:
    """Read the 16-char mesh_id of a placement record (null-terminated ASCII)."""
    base = data_start + 0x20 + index * 0x64
    raw = data[base: base + 0x10]
    return raw.split(b"\x00")[0].decode("ascii", errors="replace").strip()


def _mesh_bboxes(data: bytearray, table1: bytes, table2: bytes) -> dict[str, tuple[float, float, float, float, float, float]]:
    out = {}
    for section in parse_sections(data):
        if section.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        decrypt_zone_mesh(data, section.data_start, table1, table2)
        name = data[section.data_start + 0x10:section.data_start + 0x20].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        mesh_count = struct.unpack_from("<I", data, section.data_start + 0x20)[0]
        if name and mesh_count:
            out[name] = struct.unpack_from("<6f", data, section.data_start + 0x24)
        reencrypt_zone_mesh(data, section.data_start, table1, table2)
    return out


def _bbox_points(bbox, pos, rot, scale):
    xmin, xmax, ymin, ymax, zmin, zmax = bbox
    m = trs_matrix(pos, rot, scale)
    points = []
    for x in (xmin, xmax):
        for y in (ymin, ymax):
            for z in (zmin, zmax):
                points.append((
                    m[0] * x + m[4] * y + m[8] * z + m[12],
                    m[1] * x + m[5] * y + m[9] * z + m[13],
                    m[2] * x + m[6] * y + m[10] * z + m[14],
                ))
    return points


def _strip_instance_suffix(name: str) -> str:
    """Drop the level-editor's '.NNN' duplicate-name uniquifier (e.g. 'hata.012' → 'hata',
    '_ron_w10_m.243' → '_ron_w10_m'). That suffix is display-only — the source zone's mesh
    name never carries it — so cross-zone copies must match on the bare name."""
    base, dot, tail = name.rpartition(".")
    return base if (dot and tail.isdigit()) else name


def _norm_zone_rel(path: str) -> str:
    return (path or "").replace("\\", "/").removeprefix("game-hd/").removeprefix("game/").lstrip("/")


def _import_zone_mesh_sections(data: bytearray, cross_zone_adds: list,
                               table1: bytes, table2: bytes,
                               use_hd: bool = False) -> tuple[dict, dict]:
    """Copy 0x2E ZoneMesh (and dependent 0x20 texture) sections from source zone DATs
    into *data* in-place.  Sections already present in the target are skipped.

    Meshes are renamed to the 'xi_' prefix form on import so editor-created objects
    are easy to distinguish from native zone geometry.

    When ``use_hd`` is set (an HD publish), the copied mesh + its textures are sourced
    from the HD asset-pack version of the source zone (high-res textures) when one
    exists — so an HD-published map gets the HD textures for cross-zone copies, not the
    low-res ones. Falls back to the standard source DAT when no HD variant is present.

    Returns (source_cull, name_map):
      source_cull — prefixed_name → bytes (collision transform tail 0x80-0xBF from the
                    source zone, with cull_group zeroed).
      name_map    — source_name → prefixed_target_name for each successfully copied mesh."""
    # Collect names already in target
    existing_mesh: set = set()
    existing_tex: set = set()
    for s in parse_sections(data):
        if s.type_code == SECTION_TYPE_ZONE_MESH:
            decrypt_zone_mesh(data, s.data_start, table1, table2)
            nm = data[s.data_start + 0x10:s.data_start + 0x20].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
            reencrypt_zone_mesh(data, s.data_start, table1, table2)
            if nm:
                existing_mesh.add(nm)
        elif s.type_code == SECTION_TYPE_TEXTURE:
            nm = data[s.data_start + 1:s.data_start + 0x11].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
            if nm:
                existing_tex.add(nm)

    source_cull: dict = {}  # prefixed_name → bytes(64): collision transform tail 0x80-0xBF
    name_map: dict = {}    # source_name → prefixed_target_name

    # Group requests by source zone
    by_source: dict = {}
    for ch in cross_zone_adds:
        by_source.setdefault(ch["sourceZone"], []).append(ch)

    for src_zone_rel, zone_changes in by_source.items():
        src_path = Path(FFXI_DIR) / src_zone_rel
        if use_hd:
            # HD publish: pull the copied mesh + its dependent textures from the HD
            # asset-pack version of the source zone so the copy carries HD textures.
            try:
                from xi.xi_config import hd_path_for
                hd_src = hd_path_for(src_path)
                if hd_src.exists():
                    click.echo(f"[placement] HD publish: sourcing meshes from HD zone {hd_src}")
                    src_path = hd_src
                else:
                    click.echo(f"[placement] HD publish: no HD source for {src_zone_rel} "
                               f"— using standard DAT (low-res textures)", err=True)
            except Exception as exc:
                _dbg(f"HD source resolve failed for {src_zone_rel}: {exc} — using standard DAT")
        if not src_path.exists():
            click.echo(f"[placement] source zone not found: {src_path}", err=True)
            continue
        src_data = bytearray(src_path.read_bytes())
        src_sections = parse_sections(src_data)
        needed = {_strip_instance_suffix(ch.get("sourceName") or ch["name"]) for ch in zone_changes}

        needed_tex: set = set()
        mesh_blobs: list = []      # [(target_name, bytes)]
        tex_blobs: list = []       # [(name, bytes)]
        source_to_target: dict = {}  # source_name → target_name for this zone's copies

        # Decrypt each 0x2E section, collect texture refs, re-encrypt, then queue.
        # Each imported mesh is renamed to its xi_-prefixed form in the blob.
        for s in src_sections:
            if s.type_code != SECTION_TYPE_ZONE_MESH:
                continue
            decrypt_zone_mesh(src_data, s.data_start, table1, table2)
            nm = src_data[s.data_start + 0x10:s.data_start + 0x20].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
            target_nm = _xi_prefixed(nm)
            if nm in needed and target_nm not in existing_mesh:
                try:
                    _, prims = parse_zone_mesh_section(bytes(src_data), s)
                    for prim in prims:
                        if prim.texture_name:
                            needed_tex.add(prim.texture_name)
                except Exception:
                    pass  # can't parse textures — mesh still worth copying
            reencrypt_zone_mesh(src_data, s.data_start, table1, table2)
            if nm in needed and target_nm not in existing_mesh:
                # Copy the encrypted blob and rename its internal mesh name
                blob_mut = bytearray(src_data[s.start:s.start + s.size])
                decrypt_zone_mesh(blob_mut, 0x10, table1, table2)
                blob_mut[0x20:0x30] = target_nm.encode("ascii")[:16].ljust(16, b" ")
                reencrypt_zone_mesh(blob_mut, 0x10, table1, table2)
                mesh_blobs.append((target_nm, bytes(blob_mut)))
                source_to_target[nm] = target_nm
                name_map[nm] = target_nm
                existing_mesh.add(target_nm)  # deduplicate if same name appears twice

        # Collect required texture sections
        for s in src_sections:
            if s.type_code != SECTION_TYPE_TEXTURE:
                continue
            tn = src_data[s.data_start + 1:s.data_start + 0x11].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
            if tn in needed_tex and tn not in existing_tex:
                tex_blobs.append((tn, bytes(src_data[s.start:s.start + s.size])))
                existing_tex.add(tn)

        # Extract collision transform tails for every copied mesh so the caller
        # can patch the new per-object transform with the mesh's own local bounds
        # (bytes 0x80-0xBF contain mesh-geometry-derived culling data that differs
        # per mesh type — using a wrong template corrupts spatial partitioning).
        if source_to_target:
            src_zd_sec = next((s for s in src_sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
            if src_zd_sec is not None:
                try:
                    src_nc = decrypt_zone_objects(src_data, src_zd_sec.data_start,
                                                  src_zd_sec.start, src_zd_sec.size, table1)
                    src_zd_obj = parse_zonedef(src_data, src_zd_sec.data_start,
                                               src_zd_sec.start, src_zd_sec.size)
                    coll_rel = src_zd_obj.header_offsets.get("collision", 0)
                    if coll_rel:
                        cb = src_zd_sec.data_start + coll_rel
                        transforms_rel = struct.unpack_from("<I", src_data, cb + 0x14)[0]
                        tbase = src_zd_sec.data_start + transforms_rel
                        remaining = set(source_to_target.keys())  # original source names
                        for i in range(src_nc):
                            if not remaining:
                                break
                            nm_i = _read_name(src_data, src_zd_sec.data_start, i)
                            if nm_i in remaining:
                                tail = bytearray(
                                    src_data[tbase + i * TRANSFORM_SIZE + 0x80:
                                             tbase + i * TRANSFORM_SIZE + TRANSFORM_SIZE])
                                # Zero the zone-specific culling group offset
                                struct.pack_into("<I", tail, TRANSFORM_CULLGROUP - 0x80, 0)
                                source_cull[source_to_target[nm_i]] = bytes(tail)  # keyed by target name
                                remaining.discard(nm_i)
                    reencrypt_zone_objects(src_data, src_zd_sec.data_start,
                                          src_zd_sec.start, src_zd_sec.size, table1)
                except Exception as exc:
                    click.echo(f"[placement] warning: could not extract cull data from {src_zone_rel}: {exc}", err=True)

        if not mesh_blobs and not tex_blobs:
            continue

        # Splice the new sections in BEFORE the trailing end\0 terminator run.
        # The game's zone loader stops enumerating sections at the first end\0
        # terminator (type 0x00), so anything appended *past* it is dead space:
        # it renders in the multi-pass JS editor (which walks to EOF) but is
        # invisible in-game. parse_sections also walks past end\0, so we locate the
        # start of the trailing contiguous type-0x00 run and insert there. This
        # mirrors the proven `xi zone object import` path (insert before the
        # terminators), which is verified in-game.
        secs = parse_sections(data)
        insert_at = len(data)
        i = len(secs) - 1
        while i >= 0 and secs[i].type_code == 0x00:
            insert_at = secs[i].start
            i -= 1
        if _DEBUG:
            term_run = [s for s in secs if s.type_code == 0x00 and s.start >= insert_at]
            _dbg(f"file size={len(data)} sections={len(secs)}")
            _dbg(f"trailing end\\0 terminator run: {len(term_run)} section(s) starting @{insert_at:#x}")
            _dbg(f"insert_at={insert_at:#x}"
                 + ("  (WARNING: no terminator found — appending at EOF)" if insert_at == len(data) else ""))

        blob_all = bytearray()
        for target_nm, blob in mesh_blobs:
            blob_all += blob
            src_nm = next((s for s, t in source_to_target.items() if t == target_nm), target_nm)
            click.echo(f"[placement] imported mesh '{src_nm}' -> '{target_nm}' from {src_zone_rel}")
        for tn, blob in tex_blobs:
            blob_all += blob
            click.echo(f"[placement] imported texture '{tn}' from {src_zone_rel}")
        data[insert_at:insert_at] = blob_all

        if _DEBUG:
            # Re-parse and assert every imported section now precedes the terminator run.
            secs2 = parse_sections(data)
            j = len(secs2) - 1
            term_start = len(data)
            while j >= 0 and secs2[j].type_code == 0x00:
                term_start = secs2[j].start
                j -= 1
            bad = [hex(s.start) for s in secs2
                   if insert_at <= s.start < insert_at + len(blob_all) and s.start >= term_start]
            _dbg(f"spliced {len(blob_all)} bytes; terminator now @{term_start:#x}")
            _dbg(f"all imported sections precede terminator: "
                 f"{'PASS' if not bad else 'FAIL ' + str(bad)}")

    return source_cull, name_map


def _base_name(name: str) -> str:
    """Strip a trailing LOD suffix (_l/_m/_h) — mirrors resolveMeshName in the editor,
    so a placement named ``funsui`` groups with mesh sections ``funsui``/``funsui_h``."""
    return name[:-2] if name[-2:] in ("_l", "_m", "_h") else name


def _collect_mesh_refs(data: bytearray, table1: bytes) -> tuple[set[str], set[bytes]]:
    """Snapshot which zone meshes are currently *referenced* in the DAT, two ways:

      * placement_bases — LOD-base names from the 0x1C ZoneDef placement records
        (placements link a mesh by its 16-byte internal name).
      * gen_fourccs — 4-byte mesh DatIds referenced by 0x05 effect generators. A
        generator stores the id verbatim in its (plaintext) body — the same scan
        the VFX position-patch uses — so ``cc in body`` finds every real reference
        with no false negatives (critical: we must never miss a live reference).

    Taken before and after the edits, the difference tells us exactly which meshes
    a delete/VFX-remove orphaned, without disturbing pre-existing unplaced geometry."""
    placement_bases: set[str] = set()
    sections = parse_sections(data)
    zd = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zd is not None:
        nc = decrypt_zone_objects(data, zd.data_start, zd.start, zd.size, table1)
        for i in range(nc):
            nm = _read_name(data, zd.data_start, i)
            if nm:
                placement_bases.add(_base_name(nm))
        reencrypt_zone_objects(data, zd.data_start, zd.start, zd.size, table1)

    fx_secs = fx_parse_sections(data)
    mesh_ccs = _mesh_fourccs(data, fx_secs)
    gen_fourccs: set[bytes] = set()
    for s in fx_secs:
        if s.type_code != EFFECT_TYPE:
            continue
        body = bytes(data[s.start:s.start + s.size])
        for cc in mesh_ccs:
            if cc in body:
                gen_fourccs.add(cc)
    return placement_bases, gen_fourccs


def _remove_newly_orphaned(data: bytearray, before: tuple[set[str], set[bytes]],
                           after: tuple[set[str], set[bytes]],
                           table1: bytes, table2: bytes) -> tuple[int, int]:
    """Splice out 0x2E mesh sections that were referenced *before* the edits but are
    referenced by nothing *after* (a placement delete or VFX removal orphaned them),
    plus any 0x20 texture sections that become private to those meshes. Returns
    (meshes_removed, textures_removed).

    This is what makes delete/VFX-remove a *full* removal: the placement record is
    blanked elsewhere (real removal would need unsupported culling-table re-indexing)
    and here the orphaned geometry + private textures are physically deleted, so
    nothing — game or editor — resurrects the object.

    Safety:
      * A mesh is removed only when it has NO reference after the edit (neither a
        surviving placement nor a surviving generator) — so it can never dangle.
      * Pre-existing unplaced geometry (no reference before *or* after) is untouched.
      * A texture is removed only when no surviving mesh references it AND its name
        appears in no other (non-mesh) section, so shared zone/VFX textures survive.
      * Sections are self-describing; splicing is offset-safe (the game re-walks the
        list) and the encrypted ZoneDef is left intact (indices/culling stay valid)."""
    before_pb, before_gc = before
    after_pb, after_gc = after
    from xi.entity.mesh.xi_export import parse_texture as _parse_texture

    sections = parse_sections(data)
    remove_ranges: list[tuple[int, int]] = []   # (start, end) byte ranges to splice
    removed_tex: set[str] = set()                # texture names used by removed meshes
    survivor_tex: set[str] = set()               # texture names used by surviving meshes

    for s in sections:
        if s.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        fourcc = bytes(data[s.start:s.start + 4])
        decrypt_zone_mesh(data, s.data_start, table1, table2)
        name, prims = parse_zone_mesh_section(data, s)
        reencrypt_zone_mesh(data, s.data_start, table1, table2)
        base = _base_name(name)
        ref_before = (base in before_pb) or (fourcc in before_gc)
        ref_after  = (base in after_pb) or (fourcc in after_gc)
        tex = {(p.texture_name or "").strip() for p in prims if (p.texture_name or "").strip()}
        if ref_before and not ref_after:
            remove_ranges.append((s.start, s.start + s.size))
            removed_tex |= tex
        else:
            survivor_tex |= tex

    meshes_removed = len(remove_ranges)
    if not meshes_removed:
        return 0, 0

    # Candidate orphan textures: referenced by removed meshes, by no surviving mesh.
    candidate_tex = removed_tex - survivor_tex
    if candidate_tex:
        # Drop any candidate whose name appears in a non-mesh section (VFX particle
        # meshes / sprite sheets are plaintext and reference textures by name).
        for s in sections:
            if s.type_code in (SECTION_TYPE_ZONE_MESH, SECTION_TYPE_ZONE_DEF,
                               SECTION_TYPE_TEXTURE):
                continue
            raw = bytes(data[s.start:s.start + s.size])
            for tname in list(candidate_tex):
                if tname.encode("ascii", "replace") in raw:
                    candidate_tex.discard(tname)

    removed_tex_names: set[str] = set()
    if candidate_tex:
        for s in sections:
            if s.type_code != SECTION_TYPE_TEXTURE:
                continue
            img = _parse_texture(bytes(data), s)
            if img and img.name.strip() in candidate_tex:
                remove_ranges.append((s.start, s.start + s.size))
                removed_tex_names.add(img.name.strip())

    # Splice highest offset first so the earlier ranges stay valid.
    for start, end in sorted(remove_ranges, key=lambda r: r[0], reverse=True):
        del data[start:end]

    return meshes_removed, len(removed_tex_names)


def _remove_meshes_by_name(data: bytearray, names: set[str],
                           table1: bytes, table2: bytes) -> tuple[int, int]:
    """Splice out unplaced meshes named in *names*, removing their COMPANIONS too.

    Weather/sky meshes live in nested ``weat/<weather>/<effect>/`` directories
    alongside companion sections — ``0x07`` effect generators, ``0x19`` records,
    ``0x20`` textures — that reference the mesh. Removing only the ``0x2E`` mesh leaves
    those companions pointing at a dead mesh and the client CRASHES (this is exactly
    what broke when ``wasi``/``tornado`` were deleted). So for a target mesh inside a
    dedicated effect subdir under ``weat/`` — a directory that has NO ``0x2F`` weather
    env of its own, e.g. ``weat/suny/wasi/`` — this removes the WHOLE directory (mesh
    + every companion) in one offset-safe splice.

    A mesh sitting DIRECTLY in a weather-root directory (one holding ``0x2F`` envs, e.g.
    ``tornado1`` in ``weat/sand/``) cannot have its directory removed without destroying
    that weather, so it is REFUSED (logged + skipped — hide it with the editor's
    visibility toggle instead). A target outside ``weat/`` falls back to plain
    single-section removal. Returns (meshes_removed, textures_removed)."""
    if not names:
        return 0, 0

    sections = parse_sections(data)

    def mesh_name(s) -> str:
        decrypt_zone_mesh(data, s.data_start, table1, table2)
        nm, _ = parse_zone_mesh_section(data, s)
        reencrypt_zone_mesh(data, s.data_start, table1, table2)
        return nm.strip()

    # Walk the 0x01/0x00 directory tree: for each dir record its byte range, whether it
    # directly holds a 0x2F weather env, and whether it lives under weat/. For each
    # target 0x2E mesh remember its innermost containing dir.
    stack: list[list] = []                       # [name4, open_sec, has_env, under_weat]
    dir_range: dict[int, tuple[int, int]] = {}
    dir_has_env: dict[int, bool] = {}
    targets: list[tuple] = []                     # (mesh_sec, innermost_open_sec|None, under_weat)
    for s in sections:
        if s.type_code == 0x01:
            nm4 = bytes(data[s.start:s.start + 4]).decode("latin1", "replace")
            under = (nm4 == "weat") or (stack[-1][3] if stack else False)
            stack.append([nm4, s, False, under])
            continue
        if s.type_code == 0x00:
            if stack:
                _, open_sec, has_env, _under = stack.pop()
                dir_range[id(open_sec)] = (open_sec.start, s.start + s.size)
                dir_has_env[id(open_sec)] = has_env
            continue
        if s.type_code == 0x2F:
            if stack:
                stack[-1][2] = True
            continue
        if s.type_code == SECTION_TYPE_ZONE_MESH and mesh_name(s) in names:
            targets.append((s, stack[-1][1] if stack else None, stack[-1][3] if stack else False))

    remove_ranges: list[tuple[int, int]] = []
    refused: list[str] = []
    for mesh_sec, open_sec, under in targets:
        if open_sec is not None and under and not dir_has_env.get(id(open_sec), False):
            rng = dir_range.get(id(open_sec))                  # whole effect subdir
            if rng and rng not in remove_ranges:
                remove_ranges.append(rng)
        elif open_sec is not None and under:
            refused.append(mesh_name(mesh_sec))                # shares a weather-root dir
        else:
            r = (mesh_sec.start, mesh_sec.start + mesh_sec.size)   # standalone, outside weat/
            if r not in remove_ranges:
                remove_ranges.append(r)

    if refused:
        click.echo("[mesh] refused (shares a weather-root dir; hide via visibility instead): "
                   + ", ".join(sorted(set(refused))), err=True)
    if not remove_ranges:
        return 0, 0

    in_range = lambda off: any(a <= off < b for a, b in remove_ranges)
    mesh_n = sum(1 for s in sections if s.type_code == SECTION_TYPE_ZONE_MESH and in_range(s.start))
    tex_n = sum(1 for s in sections if s.type_code in (SECTION_TYPE_TEXTURE, 0x21) and in_range(s.start))
    for start, end in sorted(remove_ranges, key=lambda r: r[0], reverse=True):
        del data[start:end]
    return mesh_n, tex_n


def _authored_collision_tris(collisions) -> list:
    """Editor collision-prim records -> world-space AuthoredTri. Each rec carries a
    flat ``tris`` array (9 floats / triangle = 3 verts x xyz, already in FFXI world
    coords, baked by the editor's exact three.js matrices) plus ``wall``/``terrain``
    flags. We consume the soup verbatim — no TRS/Euler reconstruction, no frame
    guesswork. ``add_collision`` derives normals + double-sides walls itself."""
    from xi.zone.xi_collision import AuthoredTri
    out = []
    for rec in collisions or []:
        flat = rec.get("tris") or []
        wall = bool(rec.get("wall", True))
        terrain = int(rec.get("terrain", 0) or 0)
        n = (len(flat) // 9) * 9
        for i in range(0, n, 9):
            out.append(AuthoredTri(
                (flat[i], flat[i + 1], flat[i + 2]),
                (flat[i + 3], flat[i + 4], flat[i + 5]),
                (flat[i + 6], flat[i + 7], flat[i + 8]),
                wall, terrain))
    return out


def _apply_placements(data: bytearray, changes: list, table1: bytes, table2: bytes,
                      collisions: list | None = None, use_hd: bool = False,
                      protect_above: int | None = None, dest_zone_rel: str = "") -> dict:
    """Decrypt, patch placement TRS / blank deleted objects, bake authored collision
    prims, re-encrypt. Returns stats."""
    # Pre-import cross-zone mesh sections (extends `data` if needed; must happen before
    # bboxes are computed so the new meshes appear in the bbox map). On an HD publish the
    # copied meshes/textures come from the HD source zone (see _import_zone_mesh_sections).
    xzone = [ch for ch in changes if ch.get("op") == "add" and ch.get("sourceZone")
             and _norm_zone_rel(ch.get("sourceZone")) != _norm_zone_rel(dest_zone_rel)]
    source_cull: dict = {}
    name_map: dict = {}
    if xzone:
        source_cull, name_map = _import_zone_mesh_sections(data, xzone, table1, table2, use_hd=use_hd)

    sections = parse_sections(data)
    zonedef = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zonedef is None:
        raise ValueError("DAT has no 0x1C ZoneDef (placement) section")

    bboxes = _mesh_bboxes(data, table1, table2)
    node_count = decrypt_zone_objects(data, zonedef.data_start, zonedef.start, zonedef.size, table1)
    zd = parse_zonedef(data, zonedef.data_start, zonedef.start, zonedef.size)

    # Build name → index map (decrypted names are now readable)
    # name_to_index: first occurrence — used as template for modify/add
    # delete_queues: all occurrences — consumed one per delete op so every
    #                instance of a shared mesh name is zeroed independently
    # protect_above: placements at indices >= this count were appended by THIS publish's GLB
    # injection (import_object). They are excluded from delete_queues so a same-name delete op
    # can't hide the object that was just added (see baseline_placement_count, apply_changes_data).
    name_to_index: dict[str, int] = {}
    delete_queues: dict[str, list[int]] = {}
    for i in range(node_count):
        n = _read_name(data, zonedef.data_start, i)
        if n:
            name_to_index.setdefault(n, i)
            if protect_above is None or i < protect_above:
                delete_queues.setdefault(n, []).append(i)

    # Resolve the final mesh name every ADD lands on, BEFORE processing deletes. A cross-zone
    # add resolves through name_map (its imported xi_ name); an in-place add keeps its own name.
    # A delete-op that names one of these must NOT splice the shared 0x2E mesh section out from
    # under the surviving copies (see the unplaced-delete guard below).
    add_target_names: set[str] = set()
    for ch in changes:
        if ch.get("op") != "add":
            continue
        nm = ch.get("name", "")
        if name_to_index.get(nm) is None and ch.get("sourceZone"):
            sn = _strip_instance_suffix(ch.get("sourceName") or nm)
            add_target_names.add(name_map.get(sn, _xi_prefixed(sn)))
        else:
            add_target_names.add(nm)

    modified = deleted = added = skipped = 0
    unplaced_mesh_deletes: set[str] = set()   # delete-ops naming an unplaced 0x2E mesh
    modified_positions = []
    modified_bounds = []
    adds = []  # (src_index, pos, rot, scale, name) for add_placements
    for ch in changes:
        op = ch.get("op")
        name = ch.get("name", "")
        if op == "add" and ch.get("sourceZone") and _norm_zone_rel(ch.get("sourceZone")) != _norm_zone_rel(dest_zone_rel):
            # Cross-zone copies must land on the copied source mesh, not a destination
            # mesh with the same name. The source mesh was imported above as xi_<name>.
            source_name = _strip_instance_suffix(ch.get("sourceName") or name)
            target_name = name_map.get(source_name, _xi_prefixed(source_name))
            if target_name not in bboxes:
                click.echo(f"[placement] '{name}' mesh import failed — skipped", err=True)
                skipped += 1
            else:
                adds.append((name_to_index.get(target_name, 0),
                             tuple(ch["pos"]),
                             tuple(ch["rot"]) if ch.get("rot") else (0.0, 0.0, 0.0),
                             tuple(ch["scale"]) if ch.get("scale") else (1.0, 1.0, 1.0),
                             target_name))
                added += 1
                _dbg(f"cross-zone add '{source_name}' -> '{target_name}'"
                     f"  template={'self' if name_to_index.get(target_name) is not None else 'idx0'}"
                     f"  bbox={'found' if target_name in bboxes else 'MISSING'}")
            continue
        idx = name_to_index.get(name)
        if idx is None:
            if op == "delete" and name in bboxes and name in add_target_names:
                # The mesh has no baseline placement, but a live ADD in this same change-set
                # resolves to it (cross-zone copies all share one xi_ name). Splicing its 0x2E
                # section would delete the geometry every surviving copy points at — the
                # "deleted some walls -> ALL walls vanished" bug. Treat as a no-op.
                _dbg(f"delete '{name}' IGNORED — a live add still references this mesh section")
                skipped += 1
            elif op == "delete" and name in bboxes:
                # Not a placement, but a real 0x2E mesh section — pre-existing UNPLACED
                # geometry (sky/weather meshes). The placement-orphan cleanup protects
                # these; an explicit delete physically splices out every matching section.
                unplaced_mesh_deletes.add(name)
                deleted += 1
                _dbg(f"delete unplaced mesh '{name}' (no placement -> splice mesh section)")
            else:
                click.echo(f"[placement] '{name}' not found — skipped", err=True)
                skipped += 1
            continue

        if op == "modify":
            # Prefer the exact DAT-local index when the editor supplies one (building-interior
            # objects, and any shared-name instance) — name_to_index only yields the FIRST match,
            # which would move the wrong copy. Falls back to name-first-occurrence when absent.
            want_idx = ch.get("index")
            if want_idx is not None and 0 <= want_idx < node_count:
                idx = want_idx
            rec = zonedef.data_start + 0x20 + idx * 0x64
            data[rec:rec + 0x10] = name.encode("ascii", "replace")[:0x10].ljust(0x10, b" ")
            struct.pack_into("<3f", data, rec + 0x10, *ch["pos"])
            struct.pack_into("<3f", data, rec + 0x1C, *ch["rot"])
            struct.pack_into("<3f", data, rec + 0x28, *ch["scale"])
            points = _bbox_points(bboxes[name], ch["pos"], ch["rot"], ch["scale"]) if name in bboxes else [ch["pos"]]
            expand_placement_bounds_points(data, zd, idx, points)
            modified_positions.append((idx, ch["pos"]))
            modified_bounds.append((idx, points))
            modified += 1
        elif op == "delete":
            queue = delete_queues.get(name)
            if queue:
                # Pick the SPECIFIC instance: a shared mesh name (e.g. many '_ju_w02_h')
                # has multiple placements. Prefer the editor-supplied DAT object index
                # (exact); fall back to nearest position; only then pop the first in DAT
                # order. Blindly popping deletes the wrong instance and makes multi-deletes
                # look like no-ops.
                want_idx = ch.get("index")
                want = ch.get("pos")
                if want_idx is not None and want_idx in queue:
                    pick = want_idx
                    queue.remove(pick)
                elif want is not None and len(queue) > 1:
                    def _d2(i):
                        px, py, pz = struct.unpack_from("<3f", data, zonedef.data_start + 0x20 + i * 0x64 + 0x10)
                        return (px - want[0]) ** 2 + (py - want[1]) ** 2 + (pz - want[2]) ** 2
                    pick = min(queue, key=_d2)
                    queue.remove(pick)
                else:
                    pick = queue.pop(0)
                # HIDE (relocate far out of the world) rather than blank+remove: a true
                # blank/remove + a newly-appended object crashes the client on camera
                # collision. Hiding keeps every object fully registered (known-good
                # append-only state). See hide_placement().
                hide_placement(data, zonedef.data_start, pick, zd.header_offsets.get("collision", 0))
                # NOTE: do NOT de-leaf the hidden object here. The leaf-repair experiment
                # (remove_index_from_its_leaf) did NOT fix the dead-center crash in-game and
                # was unverified, so we keep the PROVEN hide-by-move behaviour (object stays
                # in its leaf, just relocated). Placing a new mesh dead-centre on a deleted
                # one still crashes — place it a few yalms to the SIDE (different leaf).
                deleted += 1
            else:
                click.echo(f"[placement] '{name}' — all instances already deleted, skipped", err=True)
                skipped += 1
        elif op == "add":
            # Duplicate the existing record `idx` (same mesh_id + flags) at a new TRS.
            adds.append((idx, tuple(ch["pos"]),
                         tuple(ch["rot"]) if ch.get("rot") else (0.0, 0.0, 0.0),
                         tuple(ch["scale"]) if ch.get("scale") else (1.0, 1.0, 1.0),
                         name))
            added += 1
        else:
            click.echo(f"[placement] unknown op '{op}' for '{name}' — skipped", err=True)
            skipped += 1

    # modify (leaf re-assignment) and add (record append) both grow/repack the
    # section, so they share the extract → patch → re-encrypt → splice path.
    # Authored collision prims (editor box/plane/mesh) bake into the SAME 0x1C
    # section, so they ride the same extract/re-encrypt window.
    authored_tris = _authored_collision_tris(collisions)
    collision_tris = 0
    if modified_positions or adds or authored_tris:
        sec = bytearray(data[zonedef.start:zonedef.start + zonedef.size])
        if modified_positions:
            sec = assign_placements_to_nearest_leaf(sec, modified_positions)
            zd2 = parse_zonedef(sec, 0x10, 0, len(sec))
            for idx, points in modified_bounds:
                expand_placement_bounds_points(sec, zd2, idx, points)
        if adds:
            base_index = node_count  # new records are appended after the existing array
            sec = add_placements(sec, adds, register_in_tree=True)
            zd3 = parse_zonedef(sec, 0x10, 0, len(sec))
            xform_entries = []
            for k, (src, pos, rot, scale, nm) in enumerate(adds):
                # Widen the space-tree leaf to the full transformed mesh bbox (broad-phase).
                if nm in bboxes:
                    points = _bbox_points(bboxes[nm], pos, rot, scale)
                    expand_placement_bounds_points(sec, zd3, base_index + k, points)
                xform_entries.append((src, trs_matrix(pos, rot, scale)))
            # Full visibility registration (same four structures as import-object): the
            # per-object collision transform + culling-table (PVS) membership. Without these
            # a duplicated object vanishes depending on camera position. See docs/zone/object/import.md.
            sec = add_collision_transforms(sec, xform_entries)
            # Patch collision transform tails with source-zone mesh geometry data.
            # Bytes 0x80-0xBF encode mesh-local culling bounds (Y extent, etc.) that
            # differ per mesh type.  Using the wrong template (e.g. block03 for a boat)
            # places the object in the wrong spatial bucket → invisible in-game.
            if source_cull:
                zd_patched = parse_zonedef(sec, 0x10, 0, len(sec))
                coll_rel_p = zd_patched.header_offsets.get("collision", 0)
                if coll_rel_p:
                    cb_p = 0x10 + coll_rel_p
                    t_rel_p = struct.unpack_from("<I", sec, cb_p + 0x14)[0]
                    tbase_p = 0x10 + t_rel_p
                    for k, (_, _, _, _, nm) in enumerate(adds):
                        if nm in source_cull:
                            t_off = tbase_p + (base_index + k) * TRANSFORM_SIZE
                            sec[t_off + 0x80: t_off + TRANSFORM_SIZE] = source_cull[nm]
            for k in range(len(adds)):
                sec = add_to_culling_tables(sec, base_index + k)

        if _DEBUG and adds:
            _debug_dump_registration(sec, [(base_index + k, nm) for k, (_, _, _, _, nm) in enumerate(adds)])

        # Bake authored collision LAST — it re-parses the (possibly placement-grown)
        # section and only appends to the collision sub-block (mesh chain + pair-group
        # + grid cell + one shared identity transform with a wide cull AABB).
        if authored_tris:
            from xi.zone.xi_collision import add_collision
            sec = add_collision(sec, authored_tris, camera_transparent=True)
            collision_tris = len(authored_tris)
            _dbg(f"baked {collision_tris} authored collision triangle(s) into 0x1C block")

        reencrypt_zone_objects(sec, 0x10, 0, len(sec), table1)
        data[zonedef.start:zonedef.start + zonedef.size] = sec
    else:
        reencrypt_zone_objects(data, zonedef.data_start, zonedef.start, zonedef.size, table1)
    return {"modified": modified, "deleted": deleted, "added": added, "skipped": skipped,
            "collision_tris": collision_tris, "unplaced_mesh_deletes": unplaced_mesh_deletes}


def _debug_dump_registration(sec: bytearray, new_objs: list) -> None:
    """Print the four visibility structures for each newly added index (decrypted
    section, data_start = 0x10). ``new_objs`` = [(index, name), ...]. Verifies the
    invariant node_count == collision.indexCount == transform-array-length and that
    each new index is in a space-tree leaf + every culling table."""
    ds = 0x10
    zd = parse_zonedef(sec, ds, 0, len(sec))
    nc = zd.node_count
    coll = zd.header_offsets.get("collision", 0)
    idxc = struct.unpack_from("<I", sec, ds + coll + 0x18)[0] if coll else -1
    trel = struct.unpack_from("<I", sec, ds + coll + 0x14)[0] if coll else 0
    prel = struct.unpack_from("<I", sec, ds + coll + 0x0C)[0] if coll else 0
    tcount = (prel - trel) // TRANSFORM_SIZE if coll else -1
    ok = (nc == idxc == tcount)
    _dbg(f"structure check: {nc} objects / {idxc} collision-index / {tcount} transforms "
         f"(should all match) — invariant={'PASS' if ok else 'FAIL'}")
    # culling tables
    cull = zd.header_offsets.get("culling", 0)
    tables = []
    if cull:
        p = ds + cull
        ntab = struct.unpack_from("<I", sec, p)[0]; p += 4
        for _ in range(ntab):
            cnt = struct.unpack_from("<I", sec, p)[0]; p += 4
            tables.append(set(struct.unpack_from("<%dI" % cnt, sec, p)) if cnt else set()); p += 4 * cnt
    for index, nm in new_objs:
        leaf = next((n for n in zd.nodes if index in n.contained), None)
        in_tables = sum(1 for t in tables if index in t)
        _dbg(f"added object placement '{nm}' (index {index}): "
             f"{'in space-tree leaf, ' if leaf else 'NO LEAF — BAD, '}"
             f"visible in {in_tables}/{len(tables)} culling tables")


# ---------------------------------------------------------------------------
# VFX helpers (thin wrappers around fx primitives, but operating on in-memory
# data rather than going through editable_dat each time)
# ---------------------------------------------------------------------------

def _apply_vfx_add_xzone(data: bytearray, src_id: str, new_id: str, pos, source_dat_rel: str, source_offset=None,
                         reserved_names: Optional[set[str]] = None) -> str:
    """Copy a 0x05 effect (and its deps: textures, meshes, SeSep 0x3D, …) from
    *source_dat_rel* into *data* in-memory.  Returns the final FourCC stamped."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.fx.xi_copy import _effect_deps, _DEP_TYPES, _auto_name

    src_dat_path = read_path_for(Path(FFXI_DIR) / source_dat_rel)
    sdata = bytearray(src_dat_path.read_bytes())
    ssecs = fx_parse_sections(sdata)
    src = None
    if source_offset is not None:
        try:
            off = int(source_offset)
            src = next((s for s in ssecs
                        if s.start == off and s.type_code == EFFECT_TYPE and _fourcc(sdata, s.start).strip() == src_id.strip()), None)
        except (TypeError, ValueError):
            src = None
    if src is None:
        matches = [s for s in ssecs
                   if s.type_code == EFFECT_TYPE and _fourcc(sdata, s.start).strip() == src_id.strip()]
        if matches:
            src_ccs = _mesh_fourccs(sdata, ssecs) | _texture_fourccs(sdata, ssecs)
            src = next((s for s in matches
                        if _pos_offset(bytes(sdata[s.start:s.start + s.size]), src_ccs) is not None), matches[0])
    if src is None:
        raise ValueError(f"effect '{src_id}' not found in {source_dat_rel}")

    sections = fx_parse_sections(data)
    if not new_id:
        used = {_fourcc(data, s.start) for s in sections}
        if reserved_names:
            used |= {nm.ljust(4)[:4] for nm in reserved_names if nm}
        src_name = src_id.strip()
        if src_name and src_name not in used:
            new_id = src_name
        else:
            new_id = _auto_name(src_id, used)
            if not new_id:
                raise ValueError(f"could not pick a free effect id for '{src_id}'")

    # Dependency sections: reuse exact byte-identical matches already in the destination,
    # but when a source dep reuses an EXISTING FourCC for DIFFERENT content (common with
    # generic light/fire texture ids like `ligh`), allocate a fresh FourCC and rewrite the
    # copied effect + copied dep sections to point at that new id.
    used_names = {_fourcc(data, s.start) for s in sections}
    dep_remap: dict[bytes, bytes] = {}
    dep_sections: list[bytearray] = []
    dep_ccs: set[bytes] = set()
    for cc in _effect_deps(bytes(sdata), ssecs, src):
        src_matches = [s for s in ssecs if bytes(sdata[s.start:s.start + 4]) == cc and s.type_code in _DEP_TYPES]
        if not src_matches:
            continue
        same_cc_dest = [s for s in sections if bytes(data[s.start:s.start + 4]) == cc and s.type_code in _DEP_TYPES]
        need_rename = False
        for ds in src_matches:
            sblob = bytes(sdata[ds.start:ds.start + ds.size])
            dst_same_type = [d for d in same_cc_dest if d.type_code == ds.type_code]
            if dst_same_type and not any(bytes(data[d.start:d.start + d.size]) == sblob for d in dst_same_type):
                need_rename = True
                break
        target_cc = cc
        if need_rename:
            src_name = cc.decode("latin1", "replace").strip() or "fx"
            new_name = _auto_name(src_name, used_names) or src_name
            used_names.add(new_name)
            target_cc = new_name.encode("ascii", "replace")[:4].ljust(4)
            dep_remap[cc] = target_cc
        copied_types: set[int] = set()
        for ds in src_matches:
            if ds.type_code in copied_types:
                continue
            sblob = bytes(sdata[ds.start:ds.start + ds.size])
            if not need_rename:
                dst_same_type = [d for d in same_cc_dest if d.type_code == ds.type_code]
                if any(bytes(data[d.start:d.start + d.size]) == sblob for d in dst_same_type):
                    copied_types.add(ds.type_code)
                    continue
            sec = bytearray(sblob)
            if target_cc != cc:
                sec[0:4] = target_cc
            dep_sections.append(sec)
            copied_types.add(ds.type_code)
            dep_ccs.add(target_cc)

    body = bytearray(sdata[src.start:src.start + src.size])
    for old_cc, new_cc in dep_remap.items():
        body = body.replace(old_cc, new_cc)
        dep_sections = [sec.replace(old_cc, new_cc) for sec in dep_sections]

    body[0:4] = new_id.encode("ascii", "replace")[:4].ljust(4)
    if pos is not None:
        dest_ccs = _mesh_fourccs(data, sections) | _texture_fourccs(data, sections) | dep_ccs
        off = _pos_offset(bytes(body), dest_ccs)
        if off is not None:
            struct.pack_into("<3f", body, off, float(pos[0]), float(pos[1]), float(pos[2]))

    anchor = next((s for s in sections if s.type_code == EFFECT_TYPE), None)
    at = (anchor.start + anchor.size) if anchor else len(data)
    data[at:at] = bytes(body) + b"".join(bytes(sec) for sec in dep_sections)
    return new_id


def _apply_vfx_in_memory(data: bytearray, changes: list) -> dict:
    """Apply VFX ops directly on a bytearray. Returns stats."""
    _dbg(f"[vfx] applying {len(changes)} op(s)")
    modified = removed = added = skipped = 0
    removed_fx_ids: set[str] = set()  # fourccs already fully removed this run
    reserved_remove_ids: set[str] = {str(ch.get("id", "")).strip() for ch in changes if ch.get("op") == "remove" and ch.get("id")}

    for ch in changes:
        op = ch.get("op")
        if op == "modify":
            fx_id = ch.get("id", "")
            sections = fx_parse_sections(data)
            mesh_ccs = _mesh_fourccs(data, sections) | _texture_fourccs(data, sections)
            patched = False
            for s in sections:
                if s.type_code != EFFECT_TYPE or _fourcc(data, s.start).strip() != fx_id.strip():
                    continue
                if ch.get("pos") is not None:
                    body = bytes(data[s.start: s.start + s.size])
                    off = _pos_offset(body, mesh_ccs)
                    if off is not None:
                        struct.pack_into("<3f", data, s.start + off, *ch["pos"])
                        patched = True
                if ch.get("scale") is not None:
                    body = bytes(data[s.start: s.start + s.size])
                    off = _tag_payload(body, _TAG_SCALE)
                    if off is not None:
                        struct.pack_into("<3f", data, s.start + off, *ch["scale"])
                        patched = True
                break
            if patched:
                modified += 1
            else:
                click.echo(f"[vfx] '{fx_id}' modify — effect not found or no patchable fields", err=True)
                skipped += 1

        elif op == "remove":
            fx_id = ch.get("id", "")
            # A single remove deletes EVERY generator sharing this fourcc, so the
            # editor's duplicate entries (e.g. "[awa1]" and "[awa1].002", same
            # sectionId) collapse to one op — a second remove of the same id is a
            # redundant no-op, not a failure. Track ids so we don't report it as one.
            if fx_id.strip() in removed_fx_ids:
                removed += 1
                continue
            sections = fx_parse_sections(data)
            matched = [s for s in sections
                       if s.type_code == EFFECT_TYPE and _fourcc(data, s.start).strip() == fx_id.strip()]
            if matched:
                # Splice from end so earlier offsets stay valid
                for s in sorted(matched, key=lambda x: x.start, reverse=True):
                    del data[s.start: s.start + s.size]
                removed_fx_ids.add(fx_id.strip())
                removed += 1
            else:
                click.echo(f"[vfx] '{fx_id}' remove — effect not found", err=True)
                skipped += 1

        elif op == "add":
            src_id = ch.get("source_id", "")
            source_offset = ch.get("source_offset")
            new_id = ch.get("new_id", "")
            pos = ch.get("pos")
            source_dat_rel = ch.get("source_dat")  # cross-zone when present

            if source_dat_rel:
                try:
                    final = _apply_vfx_add_xzone(data, src_id, new_id or "", pos, source_dat_rel, source_offset, reserved_remove_ids)
                    _dbg(f"[vfx] cross-zone add '{src_id}' from {source_dat_rel} → '{final}'")
                    added += 1
                except (ValueError, FileNotFoundError) as e:
                    click.echo(f"[vfx] cross-zone add '{src_id}': {e}", err=True)
                    skipped += 1
                continue  # skip same-DAT path

            # Same-DAT clone
            sections = fx_parse_sections(data)
            src = next((s for s in sections
                        if s.type_code == EFFECT_TYPE and _fourcc(data, s.start).strip() == src_id.strip()), None)
            if src is None:
                click.echo(f"[vfx] source '{src_id}' not found for add — skipped", err=True)
                skipped += 1
                continue
            # Copy the section, rename, optionally set position
            mesh_ccs = _mesh_fourccs(data, sections) | _texture_fourccs(data, sections)
            body = bytearray(data[src.start: src.start + src.size])
            body[0:4] = new_id.encode("ascii", "replace")[:4].ljust(4)
            if pos is not None:
                off = _pos_offset(bytes(body), mesh_ccs)
                if off is not None:
                    struct.pack_into("<3f", body, off, *pos)
            # Insert after the first existing effect section
            anchor = next((s for s in sections if s.type_code == EFFECT_TYPE), None)
            at = (anchor.start + anchor.size) if anchor else len(data)
            data[at:at] = body
            added += 1

        else:
            click.echo(f"[vfx] unknown op '{op}' — skipped", err=True)
            skipped += 1

    unique_removed = len(removed_fx_ids)
    _dbg(f"[vfx] {len(changes)} op(s) -> {unique_removed} unique fourcc(s) removed, {skipped} skipped")
    return {"modified": modified, "removed": unique_removed, "added": added, "skipped": skipped}


# ---------------------------------------------------------------------------
# Sound emitters (add) — clone-and-patch a donor template
# ---------------------------------------------------------------------------
# A placed sound is a 0x05 audio generator + a 0x3D "SeSep" pointer that holds the
# u32 soundId. Both are too complex to synthesise, so we ship a donor pair captured
# from a retail zone (Abyssea-Altepa: generator 'aws6' → SeSep '2041') and patch only
# the fields that vary. The generator references the SeSep BY NAME, so no byte-offset
# fix-ups are needed — just stamp a fresh fourcc into both, the soundId into the SeSep,
# and position/flags into the generator. Both blobs are 16-aligned (272 / 128 B).
import base64 as _b64  # noqa: E402

_SND_GEN_B64 = ("YXdzNoUIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                "AAAAAAAAAAAAgD8AAIA/AACAPwAAAAAAAIA/AACAPwAAgD8AAAAAUIHFAQAAAAAAAAAAAAAA"
                "AAAAAAAAAAAAABAAQAAAAACQAAAAoAAAAPAAAAAAAQAAAAEAAAAAAAAAAAAAAAAAAAEMAAAB"
                "AAAAAAAAADIwNDEAAAAAM/MZxGZmxj+aGZvDAD0AAAAAAAAAAAAAAAAAAEwEAAAAAKBBAAAA"
                "AAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAA=")
_SND_SES_B64 = ("MjA0MT0EAAAAAAAAAAAAAFNlU2VwICAA+QcAAHAAAIACAAAAAC8AKAAAYAAqACECBQAiMABQ"
                "CwQAAEDACwQASGgYBAB4ABwAeAEpAHgCCgAEABI2DAYAAAAqACECBgAiIwBQEAQEAEBA6AAA"
                "eAAZAHgBMAB4AhoABAASOAoGAAA=")
_GEN_NAME_OFF, _GEN_LINKED_OFF, _GEN_BASEPOS_OFF, _GEN_FLAGS_OFF = 0x00, 0xAC, 0xB4, 0x79
_SES_NAME_OFF, _SES_SOUNDID_OFF = 0x00, 0x18


def _alloc_fourcc(used: set) -> str:
    """A 4-char section name not already present (xi custom range 'c000'…'czzz')."""
    import string
    chars = string.digits + string.ascii_lowercase
    for a in chars:
        for b in chars:
            for c in chars:
                nm = f"c{a}{b}{c}"
                if nm not in used:
                    used.add(nm)
                    return nm
    raise ValueError("ran out of free section fourccs")


def _apply_sounds_in_memory(data: bytearray, sounds: list) -> dict:
    """Add each placed sound emitter (soundId + position + repeat) to the zone DAT by
    splicing a fresh generator + SeSep cloned from the donor template. Read-back-safe:
    the new sections self-size and resolve by name."""
    gen_tpl = _b64.b64decode(_SND_GEN_B64)
    ses_tpl = _b64.b64decode(_SND_SES_B64)
    used = {s.name for s in fx_parse_sections(data)}
    added = skipped = 0
    for ch in sounds:
        if ch.get("op") not in (None, "add"):
            skipped += 1
            continue
        sid = int(ch.get("soundId") or 0)
        if sid <= 0:
            click.echo(f"[sounds] skipped record with no soundId: {ch.get('name')}", err=True)
            skipped += 1
            continue
        pos = ch.get("pos") or [0.0, 0.0, 0.0]
        repeat = bool(ch.get("repeat"))
        ses_name = _alloc_fourcc(used)
        gen_name = _alloc_fourcc(used)
        ses = bytearray(ses_tpl)
        ses[_SES_NAME_OFF:_SES_NAME_OFF + 4] = ses_name.encode("ascii")
        struct.pack_into("<I", ses, _SES_SOUNDID_OFF, sid)
        gen = bytearray(gen_tpl)
        gen[_GEN_NAME_OFF:_GEN_NAME_OFF + 4] = gen_name.encode("ascii")
        gen[_GEN_LINKED_OFF:_GEN_LINKED_OFF + 4] = ses_name.encode("ascii")
        struct.pack_into("<3f", gen, _GEN_BASEPOS_OFF, float(pos[0]), float(pos[1]), float(pos[2]))
        # autoRun (plays on zone load) + a best-guess loop bit for repeat (verify in-game).
        gen[_GEN_FLAGS_OFF] = 0x10 | (0x04 if repeat else 0x00)
        sections = fx_parse_sections(data)
        anchor = next((s for s in sections if s.type_code == EFFECT_TYPE), None)
        at = (anchor.start + anchor.size) if anchor else len(data)
        data[at:at] = bytes(ses) + bytes(gen)
        added += 1
    _dbg(f"[sounds] added {added}, skipped {skipped}")
    return {"added": added, "skipped": skipped}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def strip_zone_interactions(data: bytes, kinds: set) -> tuple[bytes, dict]:
    """Drop entries from every ``0x36`` ZoneInteraction section whose ``sourceId[0]`` is in
    *kinds* (e.g. ``{'m','z'}`` = sub-area shop-swaps + zone-line edge teleports).

    The kept entries are compacted to the front and the section's entry count is lowered;
    the section SIZE is left unchanged (freed slots zero-filled), so no downstream section
    offset shifts — the client only ever reads ``count`` entries. Plaintext section, mirrors
    ``parseZoneInteractions`` / ``_scan_zone_subarea_params``. Returns ``(new_data, info)``
    where ``info = {removed: {kind: n}, kept: N, sections: K}``."""
    buf = bytearray(data)
    want = {(k.encode("ascii")[:1] if isinstance(k, str) else bytes(k)[:1]) for k in kinds}
    removed: dict[str, int] = {}
    kept_total = sections = 0
    pos, length = 0, len(buf)
    while pos + 16 <= length:
        meta = struct.unpack_from("<I", buf, pos + 4)[0]
        size = ((meta >> 7) & 0xFFFFF) * 0x10
        if size <= 0:
            break
        if (meta & 0x7F) == 0x36 and buf[pos + 0x10:pos + 0x13] == b"RID":
            ds = pos + 0x10
            q = ds + struct.unpack_from("<I", buf, ds + 0x10)[0]   # ds + dataOffset
            n = struct.unpack_from("<I", buf, q)[0]
            entry_base = q + 0x10                                   # skip count + three zero u32
            kept: list[bytes] = []
            for i in range(n):
                b = entry_base + i * 0x40
                if b + 0x40 > pos + size:
                    break
                kind = bytes(buf[b + 0x24:b + 0x25])
                if kind in want:
                    removed[kind.decode("latin1")] = removed.get(kind.decode("latin1"), 0) + 1
                else:
                    kept.append(bytes(buf[b:b + 0x40]))
            if len(kept) != n:   # something to drop in this section
                for i, e in enumerate(kept):
                    buf[entry_base + i * 0x40: entry_base + i * 0x40 + 0x40] = e
                for i in range(len(kept), n):       # zero the freed slots
                    b = entry_base + i * 0x40
                    if b + 0x40 > pos + size:
                        break
                    buf[b:b + 0x40] = b"\x00" * 0x40
                struct.pack_into("<I", buf, q, len(kept))
                sections += 1
            kept_total += len(kept)
        pos = (pos + size + 0xF) & ~0xF
    return bytes(buf), {"removed": removed, "kept": kept_total, "sections": sections}


def apply_changes_data(dat_path: Path, changes: dict, debug: bool = False, use_hd: bool = False,
                       cancel=None) -> dict:
    """Apply an in-memory change-set dict to a zone DAT.
    Writes to FFXI_HD_DIR when use_hd=True, else in place (with .base backup).
    Shared core for both `apply-changes` (whole JSON file) and `set-placement` (one op).

    ``cancel`` (optional threading.Event) lets the editor abort a publish: it is checked
    at phase boundaries and immediately before the final write, raising PublishCancelled."""
    _set_debug(debug)
    # Resolve dat_path: accept absolute, FFXI_DIR-relative, or fall back to
    # the "zone" field in the change-set (strips leading "game/" for the junction).
    if dat_path is None:
        zone_rel = changes.get("zone", "").removeprefix("game/")
        dat_path = Path(FFXI_DIR) / zone_rel
    if not dat_path.exists():
        raise FileNotFoundError(f"Zone DAT not found: {dat_path}")

    # Load the key tables (needed for placement decrypt/re-encrypt)
    dll_path = Path(FFXI_DIR) / "FFXiMain.dll"
    if not dll_path.exists():
        raise FileNotFoundError(f"FFXiMain.dll not found at {dll_path}")
    from xi.zone.xi_decrypt import load_key_tables
    table1, table2 = load_key_tables(dll_path)

    # Get an editable copy (the HD DAT when use_hd, else the DAT itself + .base backup)
    if use_hd:
        from xi.xi_config import hd_editable_dat
        out_path = hd_editable_dat(dat_path)
    else:
        out_path = editable_dat(dat_path, fresh=False)

    # GLB adds: inject each NEW mesh once, then place every instance of it. Adds are grouped
    # by xiId (the editor's stable per-import identity): the FIRST glb-add in a group injects
    # the mesh + its first placement, and the backend OWNS the resulting 16-byte name. That
    # name is then stamped onto every sibling instance below — so a name collision (or any
    # rename import_object does to dodge one) can never leave copies pointing at a dead name.
    # import_object runs before the data is loaded into memory so the meshes exist for the
    # in-memory instance adds that follow.
    plc_changes = changes.get("placements", [])
    glb_adds = [ch for ch in plc_changes if ch.get("op") == "add" and ch.get("glb")]
    glb_added = glb_skipped = 0
    # Same-name add+delete guard. import_object (below) APPENDS each new GLB placement to the
    # DAT *before* the delete ops run. If a delete shares the new object's name (e.g. delete the
    # old "xi_devfloor", then re-import devfloor.glb), the delete would consume the freshly
    # injected placement and hide it (relocated to y=-100000 → invisible in-game). Snapshot the
    # pristine placement count now; deletes are later restricted to indices below it, so they
    # only ever hit pre-existing objects — never this publish's new injections.
    baseline_placement_count: int | None = None
    if glb_adds and any(ch.get("op") == "delete" for ch in plc_changes):
        try:
            _bdata = bytearray(out_path.read_bytes())
            _bsec = next((s for s in parse_sections(_bdata)
                          if s.type_code == SECTION_TYPE_ZONE_DEF), None)
            if _bsec is not None:
                baseline_placement_count = decrypt_zone_objects(
                    _bdata, _bsec.data_start, _bsec.start, _bsec.size, table1)
        except Exception as exc:   # non-fatal: fall back to unguarded behaviour
            _dbg(f"baseline placement count failed (delete-protect disabled): {exc}")
    resolved_by_xi: dict[str, str] = {}   # xiId -> final injected mesh name
    injector_ids: set[int] = set()          # id(ch) of the adds consumed by import_object
    if glb_adds:
        from xi.zone.xi_object import import_object as _import_object
        seen_xi: set[str] = set()
        for ch in glb_adds:
            _check_cancel(cancel)   # GLB imports are the slow phase — bail promptly between each
            cid = ch.get("xiId")
            if cid is not None and cid in seen_xi:
                continue   # already injected this group; this add is an extra instance (handled below)
            injector_ids.add(id(ch))
            if cid is not None:
                seen_xi.add(cid)
            glb_path = Path(ch["glb"])
            if not glb_path.exists():
                click.echo(f"[placement] GLB not found: {glb_path} — skipped", err=True)
                glb_skipped += 1
                continue
            try:
                # The editor imports GLBs at their authored scale (placement scale 1,1,1),
                # so we keep the placement's scale as-is — no geometry baking. Export the
                # GLB at FFXI world scale.
                _out, final_name = _import_object(dat_path, glb_path, ch["name"],
                               pos=tuple(ch["pos"]) if ch.get("pos") else None,
                               rot=tuple(ch["rot"]) if ch.get("rot") else None,
                               scale=tuple(ch["scale"]) if ch.get("scale") else None,
                               opaque=bool(ch.get("opaque") or ch.get("noAlpha")),
                               shade=float(ch.get("shade", 1.0)),
                               double_sided=bool(ch.get("doubleSided") or ch.get("twoSided")),
                               bake_scale=False,
                               bake_ao=bool(ch.get("ao", False)),
                               ao_floor=float(ch.get("aoFloor", 0.45)),
                               use_hd=use_hd)
                glb_added += 1
                if cid is not None:
                    resolved_by_xi[cid] = final_name
            except Exception as exc:
                click.echo(f"[placement] GLB '{ch['name']}' failed: {exc} — skipped", err=True)
                glb_skipped += 1

    data = bytearray(out_path.read_bytes())
    _dbg(f"dat={out_path}  in_size={len(data)}")

    results = {}
    plc_changes_in = changes.get("placements", [])
    vfx_changes = changes.get("vfx", [])
    # Snapshot mesh references before any edits so we can later remove only the
    # meshes a delete / VFX-remove actually orphans (full delete). Skip the scan
    # entirely when nothing is being removed — adds/modifies never orphan a mesh.
    will_remove = (any(ch.get("op") in ("delete",) for ch in plc_changes_in)
                   or any(ch.get("op") == "remove" for ch in vfx_changes))
    refs_before = _collect_mesh_refs(data, table1) if will_remove else None

    # Everything that import_object did NOT inject becomes a placement op. Extra glb-adds in an
    # already-injected xiId group drop their `glb` and place as plain instances; any add whose
    # group got a backend-resolved mesh name is re-pointed onto it (so all copies of one import
    # resolve to the single injected placement, whatever it ended up being named).
    non_glb_plc = []
    for ch in plc_changes:
        if id(ch) in injector_ids:
            continue   # mesh + first placement already created by import_object
        cid = ch.get("xiId")
        if ch.get("op") == "add" and ch.get("glb"):
            ch = {k: v for k, v in ch.items() if k != "glb"}
        if cid is not None and cid in resolved_by_xi and ch.get("name") != resolved_by_xi[cid]:
            ch = {**ch, "name": resolved_by_xi[cid]}
        non_glb_plc.append(ch)
    coll_changes = changes.get("collisions", [])
    _check_cancel(cancel)
    if non_glb_plc or coll_changes:
        results["placements"] = _apply_placements(data, non_glb_plc, table1, table2, collisions=coll_changes, use_hd=use_hd,
                                                   protect_above=baseline_placement_count,
                                                   dest_zone_rel=changes.get("zone", ""))
        # Explicitly delete named UNPLACED meshes (sky/weather geometry with no placement
        # record); the orphan cleanup below only removes placement-orphaned meshes.
        _unplaced = results["placements"].pop("unplaced_mesh_deletes", set())
        if _unplaced:
            _mr, _tr = _remove_meshes_by_name(data, _unplaced, table1, table2)
            _dbg(f"unplaced-mesh delete: removed {_mr} mesh + {_tr} texture section(s) {sorted(_unplaced)}")
            r = results["placements"]
            r["meshes_removed"] = r.get("meshes_removed", 0) + _mr
            r["textures_removed"] = r.get("textures_removed", 0) + _tr
    if glb_adds:
        r = results.setdefault("placements", {"modified": 0, "deleted": 0, "added": 0, "skipped": 0})
        r["added"] += glb_added
        r["skipped"] += glb_skipped
    if vfx_changes:
        results["vfx"] = _apply_vfx_in_memory(data, vfx_changes)
    snd_changes = changes.get("sounds", [])
    if snd_changes:
        results["sounds"] = _apply_sounds_in_memory(data, snd_changes)

    # Full-delete cleanup: physically splice out meshes (+ private textures) that
    # were referenced before but are now orphaned by the deletes / VFX removals.
    if refs_before is not None:
        refs_after = _collect_mesh_refs(data, table1)
        m_removed, t_removed = _remove_newly_orphaned(data, refs_before, refs_after, table1, table2)
        if m_removed or t_removed:
            _dbg(f"orphan cleanup: removed {m_removed} mesh + {t_removed} texture section(s)")
            r = results.setdefault("placements", {"modified": 0, "deleted": 0, "added": 0, "skipped": 0})
            r["meshes_removed"] = m_removed
            r["textures_removed"] = t_removed

    # Strip client-side 0x36 ZoneInteraction links (sub-areas 'm' / zone lines 'z' / …) when
    # requested — e.g. templating a city into a standalone custom zone, where the donor's
    # shop-swaps + edge teleports would otherwise point back at the original city.
    strip_kinds = changes.get("stripInteractions") or []
    if strip_kinds:
        stripped, info = strip_zone_interactions(bytes(data), set(strip_kinds))
        data = bytearray(stripped)
        results["interactions"] = info
        _dbg(f"stripped 0x36 interactions {sorted(set(strip_kinds))}: {info}")

    # Final point of no return: once these bytes hit disk the publish is committed.
    # A cancel here means we never started the write, so the DAT isn't corrupted mid-write.
    _check_cancel(cancel)
    out_path.write_bytes(bytes(data))
    results["dat"] = str(dat_path)
    results["output"] = str(out_path)
    return results


def _rom_relative_path(path: Path) -> Path:
    path = path.resolve()
    try:
        return path.relative_to(Path(FFXI_DIR).resolve())
    except ValueError:
        pass
    parts = list(path.parts)
    for i, part in enumerate(parts):
        if part.upper().startswith("ROM"):
            return Path(*parts[i:])
    return Path(path.name)


def copy_to_pivot(src: Path, rel_source: Path | None = None) -> Path:
    rel = _rom_relative_path(rel_source or src)
    dst = Path(FFXI_PIVOT_DIR).resolve() / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def apply_changes(dat_path: Path, changes_path: Path, debug: bool = False) -> dict:
    """Apply a JSON change-set *file* to a zone DAT. Writes the DAT in place."""
    changes = json.loads(changes_path.read_text(encoding="utf-8"))
    json_dir = changes_path.resolve().parent
    for ch in changes.get("placements", []):
        if ch.get("op") == "add" and ch.get("glb"):
            p = Path(ch["glb"])
            if not p.is_absolute():
                ch["glb"] = str(json_dir / p)
    return apply_changes_data(dat_path, changes, debug=debug)


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

@click.command("import-json")
@click.argument("changes_json", nargs=-1, required=True)
@click.option("--dat", "dat_path", default=None, metavar="DAT_PATH",
              help="Zone DAT path override (ROM-relative or absolute). Only valid for a single JSON; "
                   "falls back to the 'zone' field in each JSON otherwise.")
@click.option("--dry-run", is_flag=True, help="Parse and validate without writing.")
@click.option("--reset", is_flag=True, help="Reset each zone DAT from .base before applying changes (once per unique DAT).")
@click.option("--pivot", is_flag=True, help="After applying changes, copy the modified DAT to FFXI_PIVOT_DIR.")
@click.option("--debug", is_flag=True, help="Print per-stage diagnostics: section insert position vs the "
              "end\\0 terminator, mesh/texture import, placement records, and the four visibility structures "
              "(counts / space-tree leaf / culling-table membership) for each added object.")
def apply_changes_cmd(changes_json, dat_path, dry_run, reset, pivot, debug):
    """Apply one or more JSON change-sets (from the web level editor) to zone DAT(s).

    CHANGES_JSON  One or more paths to changes.json files. Glob patterns are
                  expanded (useful on Windows where the shell does not expand them):
                  e.g. "zone-custom/*/zone-changes.json".

    Examples:

    \b
      xi zone import-json changes.json
      xi zone import-json changes.json --dat ROM/1/41.DAT
      xi zone import-json changes.json --dat ROM/1/41.DAT --reset
      xi zone import-json "zone-custom/*/zone-changes.json" --reset
      xi zone import-json a/zone-changes.json b/zone-changes.json --reset --debug
    """
    from xi.entity.mesh.xi_export import resolve_dat_path

    # Expand any glob patterns (handles Windows PowerShell which doesn't glob).
    resolved_paths: list[Path] = []
    for pattern in changes_json:
        p = Path(pattern)
        # Only treat as a glob if the path doesn't exist as-is and contains a wildcard.
        if not p.exists() and ('*' in pattern or '?' in pattern):
            matches = sorted(Path('.').glob(pattern))
            if not matches:
                raise click.ClickException(f"No files matched: {pattern}")
            resolved_paths.extend(matches)
        else:
            if not p.exists():
                raise click.ClickException(f"Changes file not found: {pattern}")
            resolved_paths.append(p)

    if dat_path and len(resolved_paths) > 1:
        raise click.ClickException("DAT_PATH override cannot be used with multiple JSON files.")

    # Resolve explicit DAT override once if provided.
    explicit_dat = None
    if dat_path:
        try:
            explicit_dat = resolve_dat_path(dat_path)
        except FileNotFoundError as e:
            raise click.ClickException(str(e))

    def _resolve_dat_for(changes_path: Path) -> Path | None:
        if explicit_dat:
            return explicit_dat
        zone_rel = json.loads(changes_path.read_text(encoding="utf-8")).get("zone", "").removeprefix("game/")
        if not zone_rel:
            return None
        try:
            return resolve_dat_path(zone_rel)
        except FileNotFoundError:
            return None

    if dry_run:
        for changes_path in resolved_paths:
            changes = json.loads(changes_path.read_text(encoding="utf-8"))
            plc = changes.get("placements", [])
            vfx = changes.get("vfx", [])
            click.echo(f"{changes_path}: {len(plc)} placement(s), {len(vfx)} VFX op(s)")
            for c in plc:
                click.echo(f"  placement {c['op']:8s}  {c.get('name', '?')}")
            for c in vfx:
                click.echo(f"  vfx       {c['op']:8s}  {c.get('id') or c.get('source_id', '?')}")
        return

    if len(resolved_paths) > 1:
        click.echo(f"Processing {len(resolved_paths)} JSON file(s)...")

    # Group by resolved DAT so --reset fires once per unique zone, not once per file.
    from collections import defaultdict
    groups: dict[Path | None, list[Path]] = defaultdict(list)
    dat_for: dict[Path, Path | None] = {}
    for changes_path in resolved_paths:
        dat = _resolve_dat_for(changes_path)
        dat_for[changes_path] = dat
        groups[dat].append(changes_path)

    reset_done: set[Path | None] = set()

    for changes_path in resolved_paths:
        dat = dat_for[changes_path]
        if len(resolved_paths) > 1:
            click.echo(f"\n--- {changes_path} ---")

        if reset and dat not in reset_done:
            from xi.zone.xi_reset import reset_dat
            reset_target = dat if dat is not None else None
            if reset_target is None:
                zone_rel = json.loads(changes_path.read_text(encoding="utf-8")).get("zone", "").removeprefix("game/")
                reset_target = Path(FFXI_DIR) / zone_rel
            msg = reset_dat(reset_target)
            click.echo(f"Reset: {msg}")
            reset_done.add(dat)
        elif reset:
            dat_name = dat.name if dat else "DAT"
            click.echo(f"Reset: {dat_name} already reset this run — skipping")

        try:
            results = apply_changes(dat, changes_path, debug=debug)
        except (FileNotFoundError, ValueError) as e:
            raise click.ClickException(str(e))

        if "placements" in results:
            r = results["placements"]
            click.echo(f"Placements: {r['modified']} modified, {r['added']} added, {r['deleted']} deleted, {r['skipped']} skipped")
            if r.get("meshes_removed") or r.get("textures_removed"):
                click.echo(f"Removed sections: {r.get('meshes_removed', 0)} mesh, "
                           f"{r.get('textures_removed', 0)} texture (orphaned by delete)")
        if "vfx" in results:
            r = results["vfx"]
            click.echo(f"VFX: {r['modified']} modified, {r['removed']} removed, {r['added']} added, {r['skipped']} skipped")
        click.echo(f"Wrote: {results['output']}")
        if pivot:
            pivot_path = copy_to_pivot(Path(results["output"]), Path(results["dat"]))
            click.echo(f"Pivot: {pivot_path}")


@click.command("set-placement")
@click.argument("dat_path")
@click.argument("name")
@click.option("--pos", nargs=3, type=float, metavar="X Y Z", required=True,
              help="New FFXI-space position.")
@click.option("--rot", nargs=3, type=float, metavar="RX RY RZ", default=(0.0, 0.0, 0.0),
              show_default=True, help="Rotation in radians.")
@click.option("--scale", nargs=3, type=float, metavar="SX SY SZ", default=(1.0, 1.0, 1.0),
              show_default=True, help="Scale.")
def set_placement_cmd(dat_path, name, pos, rot, scale):
    """Move/rotate/scale a single existing placement by mesh NAME.

    The per-object equivalent of one `apply-changes` modify op — patches the full
    TRS of the first record matching NAME and re-homes it in the nearest space-tree
    leaf. This is what the web editor's "Export Commands" emits per moved object.

    Example:

      xi zone object set-placement ROM/1/41 block03 --pos -8.8 0 -7.7 --rot 0 1.04 0
    """
    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    change = {"placements": [{"op": "modify", "name": name,
                              "pos": list(pos), "rot": list(rot), "scale": list(scale)}]}
    try:
        results = apply_changes_data(resolved, change)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    r = results.get("placements", {})
    if r.get("skipped"):
        raise click.ClickException(f"Placement '{name}' not found in {resolved.name}.")
    click.echo(f"Set '{name}' -> pos={tuple(pos)} rot={tuple(rot)} scale={tuple(scale)}")
    click.echo(f"Wrote: {results['output']}")
