"""The ``zone_clone`` action (schema/zone_clone.json): a new zone made from a retail one.

A copy of zone ``source.zone`` at a new zone id: its model DAT, recoloured or not, and its
event, dialog and NPC files as they are, each placed in ``ROM10/<subdir>/`` and registered at
the new zone's file ids; and the zone's name in the English and Japanese zone-name tables
(``ROM/165/84``, ``ROM/165/63``). The server half (zone_settings, the ``!zone`` entry) is the
server's.

``source.files`` gives files of its own for any of the four kinds (a model made outside
xi-tools, a changed NPC name list): those are placed instead of the retail zone's, and
``recolour`` applies to whichever model is placed. The kinds it leaves out still come from
``source.zone``, which every zone needs for its event and dialog files.

What ``xi zone inject`` did, and CatsEyeXI's own zone builder before it (its dats repo,
written by Loxley; its recipes are what :func:`action_from_recipe` reads), as an action:
built by ``xi dats build`` into the install or the pivot folder, recorded, undone.

The build reads the source zone from the install (its pristine ``.base`` when an edit
kept one). A rebuild keeps the zone id and the ROM10 slots the first build took. The
zone-name tables must already have a row for the zone id: they are grown once with
``xi database grow ROM/165/84.DAT 512`` (and ``ROM/165/63.DAT``), as for every table a
build writes past retail's rows.
"""
from __future__ import annotations

import re
import struct
import tempfile
from pathlib import Path

KINDS = ("model", "event", "dialog", "npc")
NAME_TABLES = {"en": "ROM/165/84.DAT", "jp": "ROM/165/63.DAT"}
CUSTOM_ZONE_MIN, CUSTOM_ZONE_MAX = 400, 800          # as xi.zone.xi_inject
MAX_SLOT = 0x7F                                      # FTABLE's file index is 7 bits

ACTION_KEYS = {"id", "type", "enabled", "description", "depends_on", "source", "target", "names",
               "recolour", "result"}
RECOLOUR_KEYS = {"hue", "saturation", "lightness", "tint", "blend", "env_lightness", "fog_tint",
                 "fog_end", "fog_start", "texture_overrides", "skip_textures", "color_ranges"}
BLENDS = ("normal", "multiply", "screen", "overlay", "add")
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_COLOUR = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RECIPE_KEYS = {"name", "name_jp", "clone", "zone_id", "hue", "saturation", "lightness", "tint",
                "blend_mode", "textures", "skip_textures", "color_ranges"}


class CloneError(ValueError):
    pass


def file_ids(zone_id: int) -> dict:
    from xi.zone.xi_inject import (zone_dialog_file_id, zone_event_file_id, zone_model_file_id,
                                   zone_npc_file_id)
    return {"model": zone_model_file_id(zone_id), "event": zone_event_file_id(zone_id),
            "dialog": zone_dialog_file_id(zone_id), "npc": zone_npc_file_id(zone_id)}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_adjust(d: dict, at: str, keys, errs: list) -> None:
    for k in sorted(set(d) - set(keys)):
        errs.append(f"{at}: unknown key {k!r}")
    for k in ("hue", "saturation", "lightness", "env_lightness", "fog_end", "fog_start",
              "hue_min", "hue_max", "sat_min", "sat_max", "val_min", "val_max"):
        if k in d and not _num(d[k]):
            errs.append(f"{at}.{k} must be a number")
    for k in ("tint", "fog_tint"):
        if k in d and not (isinstance(d[k], str) and _COLOUR.match(d[k])):
            errs.append(f"{at}.{k} must be a colour, #RRGGBB or #RRGGBBAA")
    for k in ("blend", "blend_mode"):
        if k in d and d[k] not in BLENDS:
            errs.append(f"{at}.{k} must be one of {', '.join(BLENDS)}")


_PASS_KEYS = ("hue", "saturation", "lightness", "tint", "blend_mode",
              "hue_min", "hue_max", "sat_min", "sat_max", "val_min", "val_max")
_ENV_KEYS = ("hue", "saturation", "lightness", "tint", "blend_mode", "fog_tint", "fog_end", "fog_start")


def validate_action(action) -> list[str]:
    """What's wrong with a zone_clone action, one line per problem (empty when it's fine)."""
    if not isinstance(action, dict):
        return ["the action must be an object"]
    errs = [f"unknown key {k!r}" for k in action if k not in ACTION_KEYS]
    if not isinstance(action.get("id"), str) or not _ID.match(action["id"]):
        errs.append("id must be lowercase letters, digits, '_', '.' or '-'")
    if action.get("type") != "zone_clone":
        errs.append("type must be 'zone_clone'")
    src = action.get("source")
    if not isinstance(src, dict) or set(src) - {"zone", "files"} or not isinstance(src.get("zone"), int) \
            or isinstance(src.get("zone"), bool) or src["zone"] < 0:
        errs.append("source must be {\"zone\": <the retail zone id to copy>} (and optionally \"files\")")
    elif "files" in src:
        files = src["files"]
        if not isinstance(files, dict) or not files:
            errs.append(f"source.files must be {{kind: path}} for any of {', '.join(KINDS)}")
        else:
            for k, v in files.items():
                if k not in KINDS:
                    errs.append(f"source.files: unknown kind {k!r} (one of {', '.join(KINDS)})")
                elif not isinstance(v, str) or not v.strip():
                    errs.append(f"source.files.{k} must be a path")
    tgt = action.get("target", {})
    if not isinstance(tgt, dict) or set(tgt) - {"zone_id", "subdir", "slot"}:
        errs.append("target takes zone_id, subdir and slot")
    else:
        zid = tgt.get("zone_id", "auto")
        if zid != "auto" and not (isinstance(zid, int) and not isinstance(zid, bool) and 0 <= zid < 0x1000):
            errs.append("target.zone_id must be a zone id or \"auto\"")
        if "subdir" in tgt and not (isinstance(tgt["subdir"], int) and 0 <= tgt["subdir"] < 0x200):
            errs.append("target.subdir must be a ROM10 folder number (0-511)")
        if "slot" in tgt and not (isinstance(tgt["slot"], int) and 0 <= tgt["slot"] <= MAX_SLOT - 3):
            errs.append(f"target.slot must be the first of four free slots, 0-{MAX_SLOT - 3}")
    names = action.get("names")
    if not isinstance(names, dict) or not isinstance(names.get("en"), str) or not names["en"].strip() \
            or set(names) - {"en", "jp"} or ("jp" in names and not isinstance(names["jp"], str)):
        errs.append("names must be {\"en\": <the zone's name>} (and optionally \"jp\")")
    rc = action.get("recolour")
    if rc is not None:
        if not isinstance(rc, dict):
            errs.append("recolour must be an object")
        else:
            _check_adjust(rc, "recolour", RECOLOUR_KEYS, errs)
            tov = rc.get("texture_overrides", {})
            if not isinstance(tov, dict):
                errs.append("recolour.texture_overrides must be {pattern: adjustments}")
            else:
                for pat, adj in tov.items():
                    keys = _ENV_KEYS if pat == "environment" else _PASS_KEYS
                    if not isinstance(adj, dict):
                        errs.append(f"recolour.texture_overrides[{pat!r}] must be an object")
                    else:
                        _check_adjust(adj, f"recolour.texture_overrides[{pat!r}]", keys, errs)
            if "skip_textures" in rc and not (isinstance(rc["skip_textures"], list)
                                              and all(isinstance(p, str) for p in rc["skip_textures"])):
                errs.append("recolour.skip_textures must be a list of name patterns")
            crs = rc.get("color_ranges", [])
            if not isinstance(crs, list):
                errs.append("recolour.color_ranges must be a list")
            else:
                for i, cr in enumerate(crs):
                    if not isinstance(cr, dict):
                        errs.append(f"recolour.color_ranges[{i}] must be an object")
                    else:
                        _check_adjust(cr, f"recolour.color_ranges[{i}]", _PASS_KEYS, errs)
    return errs


def action_from_recipe(recipe: dict, action_id: str | None = None) -> dict:
    """An action from a zone recipe: CatsEyeXI's (and LPS's zones.json entries) —
    ``{name, clone, zone_id, hue, saturation, lightness, tint, blend_mode, textures}`` — or
    the action's own fields (``source``, ``target``, ``names``, ``recolour``)."""
    if "source" in recipe or "names" in recipe:
        action = {k: v for k, v in recipe.items() if k in ACTION_KEYS and k not in ("id", "type", "result")}
    else:
        unknown = sorted(set(recipe) - _RECIPE_KEYS - {"schema", "$schema", "_source"})
        if unknown:
            raise CloneError(f"unknown recipe key(s): {', '.join(unknown)}")
        if not isinstance(recipe.get("clone"), int) or not isinstance(recipe.get("name"), str):
            raise CloneError("a zone recipe needs clone (the zone id to copy) and name")
        names = {"en": recipe["name"]}
        if recipe.get("name_jp"):
            names["jp"] = recipe["name_jp"]
        rc = {k: recipe[k] for k in ("hue", "saturation", "lightness", "tint", "skip_textures",
                                     "color_ranges") if recipe.get(k) not in (None, 0, "", [], {})}
        if recipe.get("blend_mode") and recipe["blend_mode"] != "normal":
            rc["blend"] = recipe["blend_mode"]
        if recipe.get("textures"):
            rc["texture_overrides"] = recipe["textures"]
        action = {"source": {"zone": recipe["clone"]}, "target": {"zone_id": recipe.get("zone_id", "auto")},
                  "names": names}
        if rc:
            action["recolour"] = rc
    slug = re.sub(r"[^a-z0-9]+", "_", (action.get("names") or {}).get("en", "zone").lower()).strip("_")
    return {"id": action_id or f"zone_clone.{slug}", "type": "zone_clone", **action}


def source_dats(zone: int, files: dict | None = None) -> dict:
    """``{kind: Path}`` of the retail zone's files in the install (their pristine copies),
    with ``files`` (``{kind: Path}``, already resolved: ``source.files``) in place of those
    kinds."""
    import xi.xi_config as cfg
    from xi.ftable.xi_core import resolve_dat_in_root
    out = {}
    for kind, fid in file_ids(zone).items():
        # The install as the client reads it: retail zones live in ROM/ … ROM9/, each pair
        # consulted (with the main pair) in turn.
        dat = next((d for idx in range(2, 11) if (d := resolve_dat_in_root(cfg.FFXI_DIR, fid, idx)[0])), None)
        if dat:
            p = cfg.pristine(Path(cfg.FFXI_DIR) / dat)
            if p is not None:
                out[kind] = p
    for kind, path in (files or {}).items():
        if not Path(path).is_file():
            raise CloneError(f"source.files.{kind}: {path} doesn't exist")
        out[kind] = Path(path)
    if "model" not in out:
        raise CloneError(f"zone {zone} has no model DAT in the install")
    return out


def model_bytes(src: Path, recolour: dict | None) -> tuple[bytes, dict | None]:
    """The model DAT's bytes, recoloured by ``recolour`` (xi.tex.xi_recolor), and the
    engine's stats; the file as it is when there is nothing to change."""
    if not recolour:
        return src.read_bytes(), None
    from xi.tex.xi_recolor import recolour_zone_dat
    rc = dict(recolour)
    kw = dict(hue=rc.pop("hue", 0), saturation=rc.pop("saturation", 0), lightness=rc.pop("lightness", 0),
              tint=rc.pop("tint", None), blend_mode=rc.pop("blend", "normal"), **rc)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "zone.DAT"
        stats = recolour_zone_dat(src, out, **kw)
        return out.read_bytes(), stats


def taken_slots(root, subdir: int, rom: int = 10) -> set:
    """ROM10/<subdir> slots ``root`` can't give out: registered in its ROM10 pair (the
    install's when it has none), on disk there, or claimed earlier in this build."""
    from xi.dats import xi_stage
    from xi.ftable.xi_core import root_table_pair
    import xi.xi_config as cfg
    taken = set()
    ft, vt = root_table_pair(root, rom)
    if not (Path(ft).is_file() and Path(vt).is_file()):
        ft, vt = root_table_pair(cfg.FFXI_DIR, rom)
    if Path(ft).is_file() and Path(vt).is_file():
        fdata, vdata = Path(ft).read_bytes(), Path(vt).read_bytes()
        for fid in range(min(len(vdata), len(fdata) // 2)):
            if vdata[fid] == rom:
                val = struct.unpack_from("<H", fdata, fid * 2)[0]
                if (val >> 7) == subdir:
                    taken.add(val & 0x7F)
    folder = Path(root) / f"ROM{rom}" / str(subdir)
    if folder.is_dir():
        taken |= {int(p.stem) for p in folder.glob("*.DAT") if p.stem.isdigit()}
    taken |= {s for (sd, s) in xi_stage.claimed("rom10_slot") if sd == subdir}
    return taken


def free_zone_id(root) -> int:
    """The lowest custom zone id (400+) whose model file id ``root`` doesn't resolve."""
    from xi.dats import xi_stage
    from xi.ftable.xi_core import resolve_dat_in_root
    claimed = xi_stage.claimed("zone_id")
    for zid in range(CUSTOM_ZONE_MIN, CUSTOM_ZONE_MAX):
        if zid not in claimed and resolve_dat_in_root(root, file_ids(zid)["model"])[0] is None:
            return zid
    raise CloneError(f"no free zone id in {CUSTOM_ZONE_MIN}-{CUSTOM_ZONE_MAX - 1}")


def _name_table(root, rel: str):
    from xi.common import xi_dmsg as D
    from xi.dats import xi_stage
    data = xi_stage.read(root, rel)
    if data is None:
        raise CloneError(f"{rel} isn't in the install")
    return D.parse(data)


def set_names(root, zone_id: int, names: dict, dry_run: bool, previous: dict | None = None) -> dict:
    """Write the zone's name into the zone-name tables; ``{lang: {dat, row, from, to}}``.
    ``from`` is the row's text before the action's first build (kept from ``previous``),
    which undo puts back."""
    from xi.common import xi_dmsg as D
    from xi.dats import xi_stage
    out = {}
    for lang, rel in NAME_TABLES.items():
        text = names.get(lang) or names["en"]
        t = _name_table(root, rel)
        if zone_id >= t.num:
            raise CloneError(f"{rel} has {t.num} rows, none for zone {zone_id}: grow it once with "
                             f"`xi database grow {rel} 512`")
        old = D.get_text(t.blocks[zone_id], 0)
        prev = (previous or {}).get(lang) or {}
        out[lang] = {"dat": rel, "row": zone_id, "from": prev.get("from", old), "to": text}
        if old == text:
            continue
        t.blocks[zone_id] = bytearray(D.set_text(t.blocks[zone_id], 0, text, t.stride if not t.variable else 0))
        xi_stage.write(root, rel, D.serialize(t), dry_run)
    return out


def undo_names(names: dict, root) -> list[str]:
    """Put back each zone-name row the build wrote, while it still says what the build
    wrote. Warnings for the rows left as they are."""
    from xi.common import xi_dmsg as D
    from xi.dats import xi_stage
    warns = []
    for lang, ch in (names or {}).items():
        try:
            t = _name_table(root, ch["dat"])
        except (CloneError, D.DmsgError):
            continue
        row = ch["row"]
        if row >= t.num:
            continue
        cur = D.get_text(t.blocks[row], 0)
        if cur == ch.get("from"):
            continue
        if cur != ch.get("to"):
            warns.append(f"{ch['dat']} row {row} changed since the build ({cur!r}); left as it is")
            continue
        t.blocks[row] = bytearray(D.set_text(t.blocks[row], 0, ch.get("from") or "", t.stride if not t.variable else 0))
        xi_stage.write(root, ch["dat"], D.serialize(t), False)
    return warns
