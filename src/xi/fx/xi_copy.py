#!/usr/bin/env python3
"""`xi fx copy` — duplicate an effect (same-DAT, or cross-DAT with --from,
bringing its texture/sub-resource/mesh/sound deps). --replace overwrites a slot; --at /
--pos / --offset place it. Sound emitters (0x05 + linked 0x3D SeSep) are handled
automatically: the SeSep is brought along when the name is absent in the destination."""

import re
import struct
from pathlib import Path
from typing import Dict, List, Optional

import click

from xi.xi_config import editable_dat, output_path_for
from xi.fx.xi_core import (parse_sections, resolve_dat_path, EFFECT_TYPE, _fourcc,
                          _mesh_fourccs, _texture_fourccs, _pos_offset, _read_pos_at)

_DEP_TYPES = (0x20, 0x21, 0x1F, 0x19, 0x2E, 0x3D)  # Texture / SpriteSheetMesh / ParticleMesh / ParticleKeyFrameData / ZoneMesh / SoundEffectPointer
_MESH_TYPES = {0x2E, 0x1F, 0x21}                    # the dependency types that name a texture of their own
TEXTURE_NAME_AT = 0x11                              # a 0x20 section's 16-char name (8-char namespace + localName)


def texture_key(name16: bytes) -> bytes:
    """A 16-character texture name as the key both sides of a binding compare: trailing
    spaces and NULs dropped. Retail pads with spaces and some writers with NULs, and a
    mesh's copy of the name may be padded differently from the texture's own."""
    return bytes(name16).rstrip(b" \0")


def texture_name_fields(sec: bytes, names=()) -> List[int]:
    """Section-relative offsets of the 16-byte texture names a mesh-type section binds.

    A 0x1F ParticleMesh (marker 5/6) holds one per textured material after its count
    table, and a 0x21 SpriteSheetMesh one at payload +0x08 (docs/fx/particle_mesh.md).
    Anything else — a 0x2E ZoneMesh names one per submesh inside its stream, a 0x1F of
    another marker — is searched for ``names`` (texture_key values): a field counts when
    its own 16 bytes come to that key."""
    sec = bytes(sec)
    tc = struct.unpack_from("<I", sec, 4)[0] & 0x7F if len(sec) >= 8 else None
    if tc == 0x21:
        return [0x18] if len(sec) >= 0x28 else []
    if tc == 0x1F and len(sec) >= 0x18 and sec[0x10] & 0xF in (5, 6):
        # xi_particle_mesh._material_table_offset: the count table's length is rounded
        # down to a multiple of 4 plus 3, not up to 16 bytes.
        n = sec[0x14] + sec[0x15]
        at = 0x18 + 2 * (n if n % 4 == 0 else n - n % 4 + 3)
        return [at + 16 * i for i in range(sec[0x14]) if at + 16 * (i + 1) <= len(sec)]
    found = set()
    for key in names:
        i = sec.find(key, 16) if key else -1
        while i >= 0:
            if i + 16 <= len(sec) and texture_key(sec[i:i + 16]) == key:
                found.add(i)
            i = sec.find(key, i + 1)
    return sorted(found)


def _effect_deps(data: bytes, sections, eff, body: Optional[bytes] = None) -> List[bytes]:
    """FourCCs referenced in the effect body that name a dependency section
    (texture/0x21/sub-resource/mesh), plus textures referenced by any referenced
    mesh. FourCC-keyed (a name may map to several sections, e.g. a 0x20 + 0x21).

    ``body`` stands in for the effect's own bytes when the caller has edited them
    (`xi ability compose` re-points a texture or mesh id): the walk then follows what
    the edited generator names, not what the DAT's copy does."""
    types_by_cc: Dict[bytes, set] = {}
    tex_by_name: Dict[bytes, List[bytes]] = {}
    for s in sections:
        types_by_cc.setdefault(bytes(data[s.start:s.start + 4]), set()).add(s.type_code)
        if s.type_code == 0x20:
            key = texture_key(data[s.start + TEXTURE_NAME_AT:s.start + TEXTURE_NAME_AT + 16])
            if key:
                tex_by_name.setdefault(key, []).append(bytes(data[s.start:s.start + 4]))
    self_cc = bytes(data[eff.start:eff.start + 4])
    body = bytes(data[eff.start:eff.start + eff.size]) if body is None else bytes(body)
    deps: List[bytes] = []
    for off in range(0x10, len(body) - 3):
        cc = body[off:off + 4]
        if cc in deps:
            continue
        types = types_by_cc.get(cc, set()) & set(_DEP_TYPES)
        # The body naming its own fourcc is not a dependency — unless a section of
        # another type carries that name too. Retail names an audio generator after
        # its sound pointer (gen `8211` → 0x3D `8211`), and dropping the pointer made
        # the client fall back to the shared DAT's same-named pointer (another sound).
        if cc == self_cc:
            types = types - {eff.type_code}
        if types:
            deps.append(cc)
    # Meshes name their own textures, and the generator does not: a ZoneMesh (0x2E),
    # a ParticleMesh (0x1F) or a SpriteSheetMesh (0x21) carries the texture fourcc in
    # its body. Cure III's `ob2` mesh draws with `obi` and its `shp1` sheet with `shu`;
    # copying the mesh without them leaves the client (and the viewer, on a cold cache)
    # drawing white quads.
    for cc in list(deps):
        mesh_types = types_by_cc.get(cc, set()) & _MESH_TYPES
        if not mesh_types:
            continue
        for s in sections:
            if bytes(data[s.start:s.start + 4]) == cc and s.type_code in mesh_types:
                mb = bytes(data[s.start:s.start + s.size])
                for off in range(0x10, len(mb) - 3):
                    c2 = mb[off:off + 4]
                    if c2 not in deps and 0x20 in types_by_cc.get(c2, set()):
                        deps.append(c2)
                # The client binds the texture by the 16-character name the mesh holds,
                # and a name need not contain its section's id: Fire's `fai0` sheet
                # draws "fai01   fai01", which is section `fai2`, and its `fa04` mesh
                # "fai01   fai04" = `fai3`. The id scan above finds only textures whose
                # name happens to spell their id (Cure III's "carel3  ho" is `ho␣␣`).
                for off in texture_name_fields(mb, tex_by_name):
                    for c2 in tex_by_name.get(texture_key(mb[off:off + 16]), ()):
                        if c2 not in deps:
                            deps.append(c2)
    return deps


def _placement_pos(data: bytes, sections, mesh_id: str):
    """Position of a 0x1C placed object by mesh id (decrypts the ZoneDef on a copy)."""
    zd = next((s for s in sections if s.type_code == 0x1C), None)
    if zd is None:
        return None
    from xi.zone.xi_decrypt import load_key_tables, decrypt_zone_objects
    from xi.xi_config import FFXI_DIR
    t1, _ = load_key_tables(Path(FFXI_DIR) / "FFXiMain.dll")
    buf = bytearray(data)
    nc = decrypt_zone_objects(buf, zd.data_start, zd.start, zd.size, t1)
    for i in range(nc):
        rec = zd.data_start + 0x20 + i * 0x64
        mid = bytes(buf[rec:rec + 16]).split(b"\x00")[0].decode("latin1").strip()
        if mid == mesh_id:
            return struct.unpack("<3f", buf[rec + 0x10:rec + 0x1c])
    return None


def _resolve_at(data: bytes, sections, ref: str):
    """Resolve a reference name to a position: an effect (0x05) by FourCC, else a
    placed object (0x1C) by mesh id."""
    ccs = _mesh_fourccs(data, sections) | _texture_fourccs(data, sections)
    for s in sections:
        if s.type_code == EFFECT_TYPE and _fourcc(data, s.start) == ref:
            p = _read_pos_at(bytes(data[s.start:s.start + s.size]), ccs)
            if p:
                return p
    return _placement_pos(data, sections, ref)


def _auto_name(src: str, used: set, cross_dat: bool = False) -> Optional[str]:
    """Pick an unused 4-char FourCC derived from src (vary the last char).
    Cross-DAT copies use xi_ / _xi prefix so transplanted effects are identifiable."""
    if cross_dat:
        # x + first 2 chars of src = 3-char stem; last char = numeric index (0-9, A-Z, a-z).
        # e.g. 'fd04' -> xfd0, xfd1 ... 'fi04' -> xfi0, xfi1 ... '_f04' -> _xf0, _xf1
        # 62 slots per role type — enough for any realistic number of copies.
        if src.startswith('_'):
            stem = ('_x' + src[1:2]).ljust(3)[:3]
        else:
            stem = ('x' + src[:2]).ljust(3)[:3]
    else:
        stem = src[:3]
    for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz":
        cand = (stem + c)[:4].ljust(4)
        if cand not in used and cand.strip() not in {u.strip() for u in used}:
            return cand
    return None


def copy_effect(dat_path: Path, src_name: str, new_name: Optional[str] = None,
                pos=None, offset=None, src_dat: Optional[Path] = None,
                replace: Optional[str] = None, at: Optional[str] = None) -> dict:
    """Duplicate a `0x05` effect. Same-DAT by default; with ``src_dat`` it copies
    *cross-DAT*, bringing any texture/sub-resource/mesh/sound dependencies the
    effect references that the destination lacks. Sound emitters carry a linked
    `0x3D` SeSep section (by name at 0xAC in the generator body); it is detected
    automatically and copied when absent in the target. ``replace`` overwrites an
    existing destination effect's slot (so it inherits that slot's spawn behaviour —
    the way to make a scheduler-gated foreign effect actually render); otherwise the
    copy is inserted as new. Optionally set/offset its position. Edits are written
    to the DAT in place, with a `<dat>.base` backup of the pristine bytes."""
    dat = editable_dat(dat_path, fresh=False)
    data = bytearray(dat.read_bytes())
    sections = parse_sections(data)
    used = {_fourcc(data, s.start) for s in sections}

    # --- source (this DAT, or another) ---
    if src_dat is not None:
        sdata = bytearray(Path(src_dat).read_bytes())
        ssecs = parse_sections(sdata)
    else:
        sdata, ssecs = data, sections
    src = next((s for s in ssecs if s.type_code == EFFECT_TYPE
                and _fourcc(sdata, s.start) == src_name), None)
    if src is None:
        raise ValueError(f"No effect named '{src_name}' in {'source DAT' if src_dat else 'this DAT'}.")

    # --- dependency sections to bring along (cross-DAT only, and only if missing) ---
    dep_blob = bytearray()
    copied_deps = []
    dep_ccs: set = set()
    if src_dat is not None:
        used_dep_pairs = {(bytes(data[s.start:s.start + 4]), s.type_code) for s in sections if s.type_code in _DEP_TYPES}
        for cc in _effect_deps(bytes(sdata), ssecs, src):
            copied_any = False
            copied_types = set()
            for ds in ssecs:
                if bytes(sdata[ds.start:ds.start + 4]) != cc or ds.type_code not in _DEP_TYPES:
                    continue
                dep_key = (cc, ds.type_code)
                if dep_key in used_dep_pairs or ds.type_code in copied_types:
                    continue
                dep_blob += sdata[ds.start:ds.start + ds.size]
                copied_deps.append(f"{cc.decode('latin1')}(0x{ds.type_code:02x})")
                used_dep_pairs.add(dep_key)
                copied_types.add(ds.type_code)
                copied_any = True
            if copied_any:
                dep_ccs.add(cc)

    # --- target name / replace slot ---
    if replace is not None:
        tgt = next((s for s in sections if s.type_code == EFFECT_TYPE
                    and _fourcc(data, s.start) == replace), None)
        if tgt is None:
            raise ValueError(f"No effect '{replace}' to replace in the destination DAT.")
        final_name = replace                                   # keep the slot's identity (spawns)
    else:
        final_name = new_name or _auto_name(src_name, used, cross_dat=src_dat is not None)
        if not final_name or final_name.strip() == "":
            raise ValueError("Could not pick a free name — pass --name.")
        if final_name.ljust(4) in used:
            raise ValueError(f"Name '{final_name}' already exists — pass a different --name.")

    body = bytearray(sdata[src.start:src.start + src.size])
    body[0:4] = final_name.encode("ascii", "replace")[:4].ljust(4)

    # position (after the first mesh/texture ref) — relative to the *destination* refs
    dest_ccs = _mesh_fourccs(data, sections) | _texture_fourccs(data, sections) | dep_ccs
    at_pos = None
    if at is not None:
        at_pos = _resolve_at(bytes(data), sections, at)
        if at_pos is None:
            raise ValueError(f"--at '{at}': no effect or placed object by that name in the destination DAT.")
    new_pos = None
    moff = _pos_offset(bytes(body), dest_ccs)
    if pos is not None or offset is not None or at is not None:
        if moff is None:
            raise ValueError(f"'{src_name}' has no position field; cannot place it.")
        old = struct.unpack("<3f", body[moff:moff + 12])
        base_xyz = at_pos if at_pos is not None else (pos if pos is not None else old)
        off = offset if offset is not None else (0.0, 0.0, 0.0)
        new_pos = (base_xyz[0] + off[0], base_xyz[1] + off[1], base_xyz[2] + off[2])
        struct.pack_into("<3f", body, moff, *new_pos)
    elif moff is not None:
        new_pos = struct.unpack("<3f", body[moff:moff + 12])

    # --- splice into destination ---
    if replace is not None:
        at_off = tgt.start
        data = data[:at_off] + body + dep_blob + data[at_off + tgt.size:]
    else:
        anchor = next(s for s in sections if s.type_code == EFFECT_TYPE)
        at_off = anchor.start + anchor.size
        data = data[:at_off] + body + dep_blob + data[at_off:]
    dat.write_bytes(data)
    return {"name": final_name.strip(), "from": src_name,
            "src_dat": str(src_dat) if src_dat else None,
            "replaced": replace, "deps_copied": copied_deps,
            "pos": [round(v, 3) for v in new_pos] if new_pos else None, "size": len(body)}


def copy_effect_group(dat_path: Path, seed_name: str, pos=None, offset=None,
                      src_dat: Optional[Path] = None, at: Optional[str] = None) -> List[dict]:
    """Copy every effect in the same fixture group as *seed_name*.

    Group = same alpha-stem (all but last letter) + same numeric suffix.
    e.g. 'fd04' -> stem 'f', suffix '04' -> matches fd04 fl04 fr04 fs04,
    but NOT l004 (different stem length) or fd05 (different suffix).

    Effects with no patchable position field are copied without --pos
    (a warning is printed) rather than aborting the whole group."""
    m = re.match(r'^([a-zA-Z_]+?)([a-zA-Z])(\d+)\s*$', seed_name.strip())
    if not m:
        raise ValueError(f"Cannot determine group from '{seed_name}' — expected alpha+digits e.g. fd04.")
    stem, _role, digits = m.group(1), m.group(2), m.group(3)
    pat = re.compile(rf'^{re.escape(stem)}[a-zA-Z]{re.escape(digits)}\s*$')
    if src_dat is not None:
        sdata = bytearray(Path(src_dat).read_bytes())
        ssecs = parse_sections(sdata)
    else:
        sdata = bytearray(dat_path.read_bytes())
        ssecs = parse_sections(sdata)
    members = [_fourcc(sdata, s.start).strip()
               for s in ssecs if s.type_code == EFFECT_TYPE
               and pat.match(_fourcc(sdata, s.start).strip())]
    if not members:
        raise ValueError(f"No effects matching group pattern for '{seed_name}' found in source DAT.")
    results = []
    for name in members:
        try:
            res = copy_effect(dat_path, name, src_dat=src_dat, pos=pos, offset=offset, at=at)
        except ValueError as e:
            if "no position field" in str(e):
                click.echo(f"  [warn] {name}: no position field — copying without --pos")
                res = copy_effect(dat_path, name, src_dat=src_dat, pos=None, offset=None, at=None)
            else:
                raise
        results.append(res)
    return results


@click.command()
@click.argument("dat_path")
@click.argument("src_name")
@click.option("--from", "from_dat", default=None, help="Copy SRC_NAME from this other DAT (path or ROM spec); brings its texture/sub-resource deps.")
@click.option("--replace", default=None, metavar="EFFECT", help="Overwrite this existing effect's slot (it inherits that slot's spawn behaviour — needed for scheduler-gated foreign effects).")
@click.option("--name", "new_name", default=None, help="FourCC for the copy (default: auto-derived). Ignored with --replace.")
@click.option("--at", "at_ref", default=None, metavar="REF", help="Place at the position of an existing effect or placed object (e.g. --at funsui). Combine with --offset to nudge.")
@click.option("--offset", nargs=3, type=float, metavar="DX DY DZ", help="Nudge position by this delta (from the source, or from --at/--pos).")
@click.option("--pos", "--at-pos", "pos", nargs=3, type=float, metavar="X Y Z", help="Set absolute position (-Y is up). Alias: --at-pos (matches the xitools /xi pos output).")
def copy_cmd(dat_path, src_name, from_dat, replace, new_name, at_ref, offset, pos):
    """Duplicate an effect — within a DAT, or cross-DAT with --from.

    For sound emitters the linked SeSep (0x3D) is brought along automatically.

    \b
    Examples:
      xi fx copy ROM/1/41 tki5 --offset 6 0 0
      xi fx copy ROM/1/41 lb09 --from ROM/0/60 --at funsui --offset 0 -2 0 --name fir0
      xi fx copy ROM/1/41 lb09 --from ROM/0/60 --at-pos -20.7 -2 5.2 --name fir0
      xi fx copy ROM/2/9 fl06 --from ROM/2/27 --pos -20.0 -5.0 8.0
      xi fx copy ROM/2/9 sf06 --from ROM/2/27 --pos -20.0 -5.0 8.0
    """
    if pos and at_ref:
        raise click.ClickException("Use either --pos/--at-pos or --at, not both.")
    try:
        resolved = resolve_dat_path(dat_path)
        src_resolved = resolve_dat_path(from_dat) if from_dat else None
        res = copy_effect(resolved, src_name, new_name=new_name, pos=pos or None,
                          offset=offset or None, src_dat=src_resolved, replace=replace, at=at_ref)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    verb = f"replaced {res['replaced']} with" if res["replaced"] else "copied"
    where = f" from {res['src_dat']}" if res["src_dat"] else ""
    click.echo(f"{verb} {res['from']}{where} -> {res['name']} (pos {res['pos']}) in {resolved}")
    if res["deps_copied"]:
        click.echo(f"  brought deps: {', '.join(res['deps_copied'])}")
    click.echo(f"Wrote: {output_path_for(resolved)}")


@click.command("copy-group")
@click.argument("dat_path")
@click.argument("seed_name")
@click.option("--from", "from_dat", default=None, help="Copy group from this other DAT (path or ROM spec).")
@click.option("--at", "at_ref", default=None, metavar="REF", help="Place at the position of an existing effect or object.")
@click.option("--offset", nargs=3, type=float, metavar="DX DY DZ", help="Nudge position by this delta.")
@click.option("--pos", "--at-pos", "pos", nargs=3, type=float, metavar="X Y Z", help="Set absolute position for the whole group.")
def copy_group_cmd(dat_path, seed_name, from_dat, at_ref, offset, pos):
    """Copy all effects sharing a numeric suffix (a fixture group) in one shot.

    Provide any member of the group as SEED_NAME — all effects whose name ends
    with the same digits are copied together. Works for both positioned effects
    (fd/fl) and runtime effects (fr/fs rim/spark) that previously had no --pos.

    \b
    Examples:
      xi fx copy-group ROM/10/2/8.DAT fd04 --from ROM/2/0/27.DAT --pos 0 0 0
      xi fx copy-group ROM/10/2/8.DAT fd04 --from ROM/2/0/27.DAT --at funsui
    """
    if pos and at_ref:
        raise click.ClickException("Use either --pos/--at-pos or --at, not both.")
    try:
        resolved = resolve_dat_path(dat_path)
        src_resolved = resolve_dat_path(from_dat) if from_dat else None
        results = copy_effect_group(resolved, seed_name, pos=pos or None,
                                    offset=offset or None, src_dat=src_resolved, at=at_ref)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    new_names = []
    for res in results:
        where = f" from {res['src_dat']}" if res["src_dat"] else ""
        click.echo(f"copied {res['from']}{where} -> {res['name']} (pos {res['pos']})")
        if res["deps_copied"]:
            click.echo(f"  brought deps: {', '.join(res['deps_copied'])}")
        new_names.append(res['name'])
    click.echo(f"Wrote: {output_path_for(resolved)}")
    click.echo(f"New names: {', '.join(new_names)}")
    if new_names:
        click.echo(f"To delete: xi fx delete-group \"{output_path_for(resolved)}\" {new_names[0]}")
