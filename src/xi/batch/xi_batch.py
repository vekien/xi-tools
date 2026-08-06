#!/usr/bin/env python3
"""`xi batch ...` — run a fixed xi operation across many zone DATs in one pass.

Each subcommand wraps an existing single-DAT command and fans it out over the full
set of known zones + sub-rooms (the same list the level editor shows, from
:func:`xi.zone.xi_list.get_zone_entries`). Commands here are intentionally
specific (not a generic "run any command") so each can shape its own output layout.
"""

import hashlib
import json
import os
import re
import struct
from pathlib import Path

import click

from xi.entity.mesh.xi_export import resolve_dat_path, SECTION_TYPE_NAMES
from xi.xi_config import BLENDER_PATH, XI_TOOLS_DIR, FFXI_DIR
from xi.zone.xi_list import get_zone_entries
from xi.zone.xi_objects import DEFAULT_OBJECT_FOOTPRINT, build_payload, list_objects

_ICON_SCRIPT = Path(__file__).with_name("xi_glb_to_icon.py")


def _dat_key(rom_rel: str) -> str:
    """``ROM/1/41.DAT`` -> ``ROM_1_41`` (drop extension, ``/`` -> ``_``)."""
    rom_rel = rom_rel.replace("\\", "/").removeprefix("game/")
    leaf = rom_rel.rsplit("/", 1)[-1]
    if "." in leaf:  # strip a filename extension, never a folder dot
        rom_rel = rom_rel[: rom_rel.rfind(".")]
    return rom_rel.replace("/", "_")


def _zone_targets(rooms: bool, name_filter: str | None) -> list[tuple[dict, str]]:
    """The de-duplicated, optionally-filtered list of (zone-entry, dat-key) to process.

    De-dupes by DAT key so a DAT reachable through two zone entries is handled once.
    """
    zones = get_zone_entries(path_prefix="", include_rooms=rooms)
    if name_filter:
        flt = name_filter.lower()
        zones = [z for z in zones if flt in z["name"].lower() or flt in z["path"].lower()]
    seen: set[str] = set()
    targets = []
    for z in zones:
        key = _dat_key(z["path"])
        if key in seen:
            continue
        seen.add(key)
        targets.append((z, key))
    return targets


@click.group("batch")
def batch():
    """Run a fixed xi operation across many zones at once."""
    pass


# ── zone_object_list_dump ─────────────────────────────────────────────────────

def _attach_categories(resolved, entries) -> dict:
    """Parse the zone geometry once, classify each placed mesh (category + kind), attach
    those to every placement entry in place, and return a ``{category: count}`` histogram.

    Classification needs vertex normals (floor vs wall), which the bbox-based list does
    not carry — hence a second ``parse_zone`` pass, gated behind ``--classify``.
    """
    from collections import Counter

    from xi.xi_config import read_path_for
    from xi.zone.xi_classify import classify_mesh
    from xi.zone.xi_export import parse_zone, resolve_mesh_name

    meshes_by_name, _placements, _textures = parse_zone(read_path_for(resolved))
    cache: dict = {}
    hist: Counter = Counter()
    for e in entries:
        name = e["name"]
        if name not in cache:
            rn = name if name in meshes_by_name else resolve_mesh_name(name, meshes_by_name)
            prims = meshes_by_name.get(rn) if rn else None
            cache[name] = classify_mesh(name, prims)  # name-only fallback when prims is None
        c = cache[name]
        e["category"], e["kind"], e["category_source"] = c["category"], c["kind"], c["source"]
        hist[c["category"]] += 1
    return dict(hist)


@batch.command("zone_object_list_dump")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=Path("exports/zone-data"),
              show_default=True, help="Directory to write objects_<ROM>_<FOLDER>_<DAT>.json files.")
@click.option("--max-footprint", type=float, default=DEFAULT_OBJECT_FOOTPRINT, show_default=True,
              help="Mesh footprint (yalms) at/under which a placement is tagged 'object'.")
@click.option("--rooms/--no-rooms", default=True, show_default=True,
              help="Include sub-rooms (mog houses, mission rooms, private zones).")
@click.option("--footprint/--no-footprint", "with_footprint", default=True, show_default=True,
              help="Compute mesh footprints + 'object' tags (off = faster, no tags).")
@click.option("--classify", is_flag=True,
              help="Auto-detect a category (object/wall/floor/structure/terrain/...) + kind "
                   "for every placement from name + geometry. Adds a geometry pass per zone.")
@click.option("--skip-existing", is_flag=True,
              help="Skip a zone whose output JSON already exists (resume a partial run).")
@click.option("--filter", "-f", "name_filter", default=None,
              help="Only dump zones whose name or path matches this substring.")
@click.option("--limit", type=int, default=0,
              help="Stop after N zones (0 = all). Handy for a quick test.")
def zone_object_list_dump(output_dir, max_footprint, rooms, with_footprint, classify,
                          skip_existing, name_filter, limit):
    """Dump every zone's placement list (with object tags) to one JSON file per zone.

    Walks the full known-zone + sub-room list and, for each, writes
    ``<output-dir>/objects_<ROM>_<FOLDER>_<DAT>.json`` — the same document
    ``xi zone object list <dat> --json`` produces, plus the zone's name/id.

    \b
      xi batch zone_object_list_dump
      xi batch zone_object_list_dump -o exports/zone-data --max-footprint 6
      xi batch zone_object_list_dump --filter jeuno --limit 5
    """
    try:
        targets = _zone_targets(rooms, name_filter)
    except Exception as e:  # noqa: BLE001 — surface any resolver failure as a CLI error
        raise click.ClickException(f"Could not build the zone list: {e}")

    output_dir.mkdir(parents=True, exist_ok=True)
    click.echo(f"Dumping object lists for {len(targets)} zone(s) -> {output_dir}/\n")

    written = skipped = errors = 0
    total_objs = total_tagged = 0
    done = 0

    for z, key in targets:
        if limit and done >= limit:
            break
        done += 1
        rom_rel, label = z["path"], f"{z['name']} [{z['path']}]"
        out_file = output_dir / f"objects_{key}.json"
        if skip_existing and out_file.exists():
            click.echo(f"  [{done}/{len(targets)}] skip (exists)  {label}")
            skipped += 1
            continue

        try:
            resolved = resolve_dat_path(rom_rel)
            entries = list_objects(resolved, with_footprint=with_footprint,
                                   object_max_footprint=max_footprint)
        except FileNotFoundError as e:
            click.echo(f"  [{done}/{len(targets)}] MISS  {label}: {e}", err=True)
            errors += 1
            continue
        except ValueError as e:
            # No 0x1C placement table (collision-only / non-zone DAT) — expected for some.
            click.echo(f"  [{done}/{len(targets)}] skip ({e})  {label}")
            skipped += 1
            continue
        except Exception as e:  # noqa: BLE001 — one bad DAT must not abort the batch
            click.echo(f"  [{done}/{len(targets)}] ERROR  {label}: {e}", err=True)
            errors += 1
            continue

        categories = None
        if classify:
            try:
                categories = _attach_categories(resolved, entries)
            except Exception as e:  # noqa: BLE001 — classification is best-effort
                click.echo(f"      classify failed for {label}: {e}", err=True)

        payload = build_payload(resolved, entries, max_footprint)
        payload = {"zone": z["name"], "zone_id": z.get("id"),
                   "group": z.get("group", "Zones"), **payload}
        if categories is not None:
            payload["categories"] = categories
        out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        tagged = sum(1 for e in entries if "object" in e["tags"])
        total_objs += len(entries)
        total_tagged += tagged
        written += 1
        cat_note = f", {len(categories)} categories" if categories else ""
        click.echo(f"  [{done}/{len(targets)}] ok    {label}: {len(entries)} placements, "
                   f"{tagged} tagged 'object'{cat_note} -> {out_file.name}")

    click.echo(f"\nDone. {written} written, {skipped} skipped, {errors} error(s). "
               f"{total_objs} placements total, {total_tagged} tagged 'object'.")


# ── zone_fbx ──────────────────────────────────────────────────────────────────

def _export_one(rom_rel: str, name: str, fbx: bool, collision: bool,
                skip_sky: bool, use_base: bool, skip_existing: bool) -> tuple:
    """Export one zone. Module-level + picklable so it can run in a worker process.

    Returns ``(status, rom_rel, name, detail, nfiles, output_dir)`` where ``status`` is
    one of ``ok`` | ``skip`` | ``miss`` | ``error``. Never raises — a failure on one zone
    must not bring down the pool.
    """
    from xi.entity.mesh.xi_export import resolve_dat_path
    from xi.xi_config import read_path_for
    from xi.zone.xi_export import _pristine_source, default_output_dir, export_zone

    try:
        resolved = resolve_dat_path(rom_rel)
    except FileNotFoundError as e:
        return ("miss", rom_rel, name, str(e), 0, "")

    output_dir = default_output_dir(resolved)
    target_ext = ".fbx" if fbx else ".glb"
    if skip_existing and (output_dir / f"{resolved.stem}{target_ext}").exists():
        return ("skip", rom_rel, name, "exists", 0, str(output_dir))

    if use_base:
        source = _pristine_source(resolved)
        if not source.is_file():
            return ("skip", rom_rel, name, "no pristine source", 0, str(output_dir))
    else:
        source = read_path_for(resolved)  # edited mirror if present, else original

    try:
        paths = export_zone(resolved, output_dir, fbx=fbx, skip_sky=skip_sky,
                            collision=collision, source=source)
    except ValueError as e:
        # No 0x2E geometry (collision-only / non-zone DAT) — expected for some.
        return ("skip", rom_rel, name, str(e), 0, str(output_dir))
    except Exception as e:  # noqa: BLE001 — Blender/parse failure, isolate to this zone
        return ("error", rom_rel, name, str(e), 0, str(output_dir))
    return ("ok", rom_rel, name, "", len(paths), str(output_dir))


@batch.command("zone_fbx")
@click.option("--fbx/--no-fbx", default=True, show_default=True,
              help="Convert each zone to a texture-embedded .fbx via Blender (off = .glb only).")
@click.option("--collision/--no-collision", default=True, show_default=True,
              help="Also dump each zone's player-collision mesh (0x1C MZB) as <stem>.collision.obj.")
@click.option("--skip-sky", is_flag=True,
              help="Omit the skybox/celestial chunks (sun, moon, stars, clouds).")
@click.option("--base", "use_base", is_flag=True,
              help="Export from the pristine original instead of your edited DAT.")
@click.option("--rooms/--no-rooms", default=True, show_default=True,
              help="Include sub-rooms (mog houses, mission rooms, private zones).")
@click.option("--skip-existing", is_flag=True,
              help="Skip a zone whose .fbx (or .glb when --no-fbx) already exists.")
@click.option("--workers", "-w", type=int, default=1, show_default=True,
              help="Export this many zones in parallel (each runs its own Blender). "
                   "Set to your core count for a big speed-up.")
@click.option("--filter", "-f", "name_filter", default=None,
              help="Only export zones whose name or path matches this substring.")
@click.option("--limit", type=int, default=0,
              help="Stop after N zones (0 = all). Handy for a quick test.")
def zone_fbx(fbx, collision, skip_sky, use_base, rooms, skip_existing, workers, name_filter, limit):
    """Export every zone to .glb + .fbx (+ collision .obj) — `zone export --fbx --collision` for all.

    Each zone lands in its standard ``exports/zone/<rom path>/<stem>/`` folder (the
    same place ``xi zone export`` writes), as ``<stem>.glb``, ``<stem>.fbx`` and
    ``<stem>.collision.obj``.

    \b
      xi batch zone_fbx
      xi batch zone_fbx --workers 8        # 8 zones (and 8 Blenders) at once
      xi batch zone_fbx --filter jeuno --limit 3
      xi batch zone_fbx --no-fbx           # glb + collision only (no Blender)
    """
    if fbx and not Path(BLENDER_PATH).is_file():
        raise click.ClickException(
            f"Blender not found at {BLENDER_PATH}. Set BLENDER_PATH to your blender.exe, "
            f"or pass --no-fbx to export .glb + collision only.")

    try:
        targets = _zone_targets(rooms, name_filter)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"Could not build the zone list: {e}")
    if limit:
        targets = targets[:limit]

    workers = max(1, workers)
    total = len(targets)
    click.echo(f"Exporting {total} zone(s) "
               f"({'glb+fbx' if fbx else 'glb'}{'+collision' if collision else ''}) "
               f"-> exports/zone/...  [{workers} worker(s)]\n")
    if fbx:
        click.echo("  (each .fbx spawns headless Blender — a full run takes a while)\n")

    stats = {"ok": 0, "skip": 0, "miss": 0, "error": 0, "files": 0}

    def _report(done: int, res: tuple) -> None:
        status, rom_rel, name, detail, nfiles, outdir = res
        label = f"{name} [{rom_rel}]"
        prefix = f"  [{done}/{total}]"
        if status == "ok":
            stats["ok"] += 1
            stats["files"] += nfiles
            try:
                shown = Path(outdir).relative_to(XI_TOOLS_DIR)
            except ValueError:
                shown = outdir
            click.echo(f"{prefix} ok    {label}: {nfiles} file(s) -> {shown}")
        elif status == "skip":
            stats["skip"] += 1
            click.echo(f"{prefix} skip ({detail})  {label}")
        else:  # miss | error
            stats[status] += 1
            click.echo(f"{prefix} {status.upper():5s} {label}: {detail}", err=True)

    args = (fbx, collision, skip_sky, use_base, skip_existing)
    if workers == 1:
        for i, (z, _key) in enumerate(targets, 1):
            _report(i, _export_one(z["path"], z["name"], *args))
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_export_one, z["path"], z["name"], *args): z for z, _key in targets}
            for i, fut in enumerate(as_completed(futs), 1):
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001 — worker process crashed outright
                    z = futs[fut]
                    res = ("error", z["path"], z["name"], f"worker crashed: {e}", 0, "")
                _report(i, res)

    click.echo(f"\nDone. {stats['ok']} exported, {stats['skip']} skipped, "
               f"{stats['miss'] + stats['error']} error(s). {stats['files']} file(s) written.")


# ── zone_asset_icons ──────────────────────────────────────────────────────────

def _mesh_hash(prims, textures, tex_cache: dict, resolve_texture) -> str:
    """Content identity of a mesh: geometry (positions + UVs) + the bytes of each
    texture it uses. Identical props across zones collapse to one hash → one icon."""
    h = hashlib.sha1()
    for prim in prims:
        h.update((prim.texture_name or "").encode("utf-8"))
        h.update(b"\x00")
        for (x, y, z) in prim.positions:
            h.update(struct.pack("<3f", x, y, z))
        for (u, v) in prim.uvs:
            h.update(struct.pack("<2f", u, v))
        tk = resolve_texture(prim.texture_name, textures)
        if tk and tk in textures:
            if tk not in tex_cache:
                tex_cache[tk] = hashlib.sha1(textures[tk].rgba).digest()
            h.update(tex_cache[tk])
    return h.hexdigest()[:16]


def _render_icons(jobs: list, render_size: int, samples: int, workers: int) -> None:
    """Render the GLBs to PNGs via headless Blender, ``workers`` sessions in parallel.

    Jobs are round-robin sharded so each Blender process renders a chunk in one
    session (amortising the slow startup). Threads are fine here — each just waits on
    a Blender subprocess, which releases the GIL.
    """
    import os
    import subprocess
    import tempfile
    from concurrent.futures import ThreadPoolExecutor

    chunks = [c for c in ([jobs[i::workers] for i in range(workers)]) if c]
    click.echo(f"Rendering {len(jobs)} icon(s) at {render_size}px via Blender "
               f"[{len(chunks)} session(s)]...")

    def _run(idx_chunk):
        idx, chunk = idx_chunk
        fd, jf = tempfile.mkstemp(suffix=f"_icons{idx}.json", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(chunk, f)
        try:
            done = subprocess.run(
                [str(BLENDER_PATH), "-b", "--python", str(_ICON_SCRIPT), "--",
                 jf, str(render_size), str(samples)],
                capture_output=True, text=True)
            tail = next((ln for ln in reversed((done.stdout or "").splitlines())
                         if ln.startswith("ICON_DONE")), f"rc={done.returncode}")
            return idx, len(chunk), tail, done.stderr
        finally:
            try:
                os.unlink(jf)
            except OSError:
                pass

    with ThreadPoolExecutor(max_workers=len(chunks)) as ex:
        for idx, n, tail, err in ex.map(_run, list(enumerate(chunks))):
            click.echo(f"  session {idx}: {tail}")
            errs = [ln for ln in (err or "").splitlines() if ln.startswith(("ICON_ERR", "ICON_EMPTY"))]
            for ln in errs[:5]:
                click.echo(f"    {ln}", err=True)


def _downscale_icons(png_paths: list, size: int) -> None:
    try:
        from PIL import Image
    except ImportError:
        click.echo("  (Pillow not installed — leaving icons at render size)", err=True)
        return
    n = 0
    for p in png_paths:
        pp = Path(p)
        if not pp.exists():
            continue
        try:
            with Image.open(pp) as im:
                if im.size != (size, size):
                    im.convert("RGBA").resize((size, size), Image.LANCZOS).save(pp)
                    n += 1
        except Exception as e:  # noqa: BLE001
            click.echo(f"  downscale failed {pp.name}: {e}", err=True)
    click.echo(f"  downscaled {n} icon(s) to {size}px")


def _convert_to_webp(jobs: list, icons: dict, quality: int = 90) -> None:
    """Convert rendered temp PNGs (job["out"]) to WebP at job["final"], update icons dict."""
    try:
        from PIL import Image
    except ImportError:
        click.echo("  (Pillow not installed — keeping PNG, WebP conversion skipped)", err=True)
        return
    # build reverse map: temp png path → hash (so we can update icons[h]["png"])
    out_to_hash = {j["out"]: j["hash"] for j in jobs if "final" in j}
    n = 0
    for job in jobs:
        if "final" not in job:
            continue
        src, dst = Path(job["out"]), Path(job["final"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not src.exists():
            continue
        try:
            with Image.open(src) as im:
                im.convert("RGBA").save(dst, format="WEBP", quality=quality, method=6)
            src.unlink(missing_ok=True)
            h = out_to_hash.get(job["out"])
            if h and h in icons:
                icons[h]["png"] = dst
            n += 1
        except Exception as e:  # noqa: BLE001
            click.echo(f"  webp convert failed {src.name}: {e}", err=True)
    click.echo(f"  converted {n} icon(s) to WebP (quality={quality})")


@batch.command("zone_asset_icons")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=Path("exports/assets"),
              show_default=True,
              help="Output root. Icons go to <format>/<category>/<hash>.<ext>, FBX to "
                   "fbx/<category>/<meshid>.fbx, plus manifest.json.")
@click.option("--objects-dir", type=click.Path(path_type=Path), default=Path("exports/zone-data"),
              show_default=True,
              help="Where the per-zone objects_<KEY>.json dumps live; the manifest links to them "
                   "and reports how many exist (run `batch zone_object_list_dump` to generate them).")
@click.option("--size", "--res", "size", type=int, default=128, show_default=True,
              help="Final icon size in pixels (square). Alias: --res.")
@click.option("--format", "--fmt", "fmt", type=click.Choice(["png", "webp"], case_sensitive=False),
              default="png", show_default=True,
              help="Output image format. webp is ~30-50% smaller; PNG is the safe default.")
@click.option("--webp-quality", type=int, default=90, show_default=True,
              help="WebP compression quality (1-100). Only used with --format webp.")
@click.option("--supersample", type=int, default=2, show_default=True,
              help="Render at size*N then downscale (crisper edges; needs Pillow).")
@click.option("--samples", type=int, default=64, show_default=True, help="EEVEE AA samples per icon.")
@click.option("--fbx", is_flag=True,
              help="Also export an .fbx per asset to <output-dir>/fbx/, named by mesh id "
                   "(meshid.fbx, then meshid_1.fbx on a name clash). For Unreal/DCC import.")
@click.option("--object-footprint", type=float, default=DEFAULT_OBJECT_FOOTPRINT, show_default=True,
              help="Footprint (yalms) at/under which an asset is categorized 'object' (a prop).")
@click.option("--category", "categories", multiple=True,
              help="Only render assets in these categories (repeatable: --category wall --category "
                   "floor). One of: object, wall, floor, ceiling, structure, terrain, water, unknown. "
                   "Default: every category.")
@click.option("--workers", "-w", type=int, default=1, show_default=True,
              help="Parallel Blender render sessions.")
@click.option("--batch-size", type=int, default=200, show_default=True,
              help="Render + save the manifest every N new icons (incremental output; an "
                   "interrupt keeps everything rendered so far). 0 = render once at the end.")
@click.option("--rooms/--no-rooms", default=True, show_default=True,
              help="Include sub-rooms (mog houses, mission rooms, private zones).")
@click.option("--skip-existing", is_flag=True,
              help="Skip meshes whose icon PNG already exists (resume a partial run).")
@click.option("--filter", "-f", "name_filter", default=None,
              help="Only scan zones whose name or path matches this substring.")
@click.option("--limit", type=int, default=0, help="Only scan the first N zones (quick test).")
@click.option("--webgl", is_flag=True,
              help="(not yet implemented) render via the web editor's three.js instead of Blender.")
@click.option("--pack-sprites/--no-pack-sprites", default=True, show_default=True,
              help="After rendering, auto-pack icons into sprite sheets (64px tiles) for the "
                   "browser. Skipped when --limit is set (partial run).")
def zone_asset_icons(output_dir, objects_dir, size, fmt, webp_quality, supersample, samples, fbx,
                     object_footprint, categories, workers, batch_size, rooms, skip_existing,
                     name_filter, limit, webgl, pack_sprites):
    """Render a PNG (or WebP) icon for every unique asset mesh across all zones — objects,
    walls, floors, structures, terrain, everything — and categorize each.

    Scans every zone, takes each placed mesh, de-dupes globally by content hash,
    auto-classifies it (category + kind, see xi.zone.xi_classify), builds a one-mesh GLB
    and renders it to ``<output-dir>/png/<category>/<hash>.png`` via Blender (orthographic
    3/4 view, transparent background) — so each category is its own folder. ``manifest.json``
    maps every (zone, mesh) to its icon hash + category, with a category histogram, so a
    viewer can tab by type.

    \b
      xi batch zone_asset_icons
      xi batch zone_asset_icons -w 8 --fbx --skip-existing
      xi batch zone_asset_icons --category wall --category floor   # just those
    """
    if webgl:
        raise click.ClickException(
            "--webgl is not implemented yet — Blender is the default renderer. "
            "(Planned: drive the web editor's three.js for viewer-identical icons.)")
    if not Path(BLENDER_PATH).is_file():
        raise click.ClickException(
            f"Blender not found at {BLENDER_PATH}. Set BLENDER_PATH to your blender.exe.")
    if size < 1:
        raise click.ClickException("--size must be >= 1")

    from collections import Counter

    from xi.xi_config import read_path_for
    from xi.zone.xi_classify import classify_mesh
    from xi.zone.xi_export import (DEFAULT_ALPHA_SCALE, build_glb, encode_png_rgba, parse_zone,
                                     resolve_mesh_name, resolve_texture, sanitize_filename,
                                     scale_alpha)

    cat_filter = {c.strip().lower() for c in categories if c.strip()}
    cat_counts: Counter = Counter()   # category -> unique-icon count (manifest histogram)
    # A category-filtered run writes its own manifest so it never clobbers the full one.
    manifest_name = "manifest.json" if not cat_filter else f"manifest_{'_'.join(sorted(cat_filter))}.json"

    try:
        targets = _zone_targets(rooms, name_filter)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"Could not build the zone list: {e}")
    if limit:
        targets = targets[:limit]

    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir = output_dir.resolve()  # Blender render-output needs absolute paths
    icon_root = output_dir / fmt         # png/<category>/<hash>.png  OR  webp/<category>/<hash>.webp
    glb_dir = output_dir / "_glb"        # intermediate, flat by hash
    glb_dir.mkdir(exist_ok=True)
    fbx_root = output_dir / "fbx"         # fbx/<category>/<meshid>.fbx
    fbx_tex_dir = fbx_root / "textures"   # shared, not per-category
    if fbx:
        fbx_tex_dir.mkdir(parents=True, exist_ok=True)
    _made_dirs: set = set()
    fbx_used: dict = {}   # (category, sanitized mesh id) -> times seen, for collision suffixes
    fbx_tex_written: set = set()   # texture keys already written to fbx/textures/
    workers = max(1, workers)
    render_size = size * max(1, supersample)

    def _cat_dir(root: Path, category: str) -> Path:
        d = root / category
        if d not in _made_dirs:
            d.mkdir(parents=True, exist_ok=True)
            _made_dirs.add(d)
        return d

    def _fbx_name(category: str, mesh_id: str) -> str:
        """meshid.fbx, then meshid_1.fbx / meshid_2.fbx on a name clash (distinct geometry
        under the same FFXI mesh name) within the same category folder."""
        base = re.sub(r"[^A-Za-z0-9._-]", "_", mesh_id) or "mesh"
        n = fbx_used.get((category, base), 0)
        fbx_used[(category, base)] = n + 1
        return f"{base}.fbx" if n == 0 else f"{base}_{n}.fbx"

    scope = f" in {', '.join(sorted(cat_filter))}" if cat_filter else ""
    click.echo(f"Scanning {len(targets)} zone(s) for asset meshes{scope} "
               f"(object footprint <= {object_footprint})...\n")

    icons: dict = {}          # hash -> render record
    manifest_objects: list = []  # [{zone, dat, mesh, hash, category, kind}]
    jobs: list = []           # [{glb, out}] queued for the next render flush
    seen_zone_keys: set = set()  # which zones contributed >=1 asset (for json-link report)
    objects_dir = objects_dir.resolve()
    rendered = 0

    def write_manifest():
        manifest = {
            "size": size, "object_footprint": object_footprint, "renderer": "blender",
            "objects_json_dir": str(objects_dir),
            "categories": dict(cat_counts),
            "icon_count": len(icons),
            "icons": {h: {"file": r["png"].relative_to(output_dir).as_posix(), "fbx": r.get("fbx"),
                          "category": r["category"], "kind": r["kind"],
                          "sample_zone": r["zone"], "sample_mesh": r["mesh"],
                          "sample_dat": r["sample_dat"], "sample_json": r["sample_json"],
                          "footprint": r["footprint"], "dims": r["dims"]} for h, r in icons.items()},
            "objects": manifest_objects,
        }
        (output_dir / manifest_name).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def flush():
        """Render the queued GLBs now and snapshot the manifest, so icons appear during the
        scan and a Ctrl-C keeps everything rendered so far."""
        nonlocal rendered
        if not jobs:
            return
        _render_icons(jobs, render_size, samples, workers)
        if supersample > 1:
            _downscale_icons([j["out"] for j in jobs], size)
        if fmt == "webp":
            _convert_to_webp(jobs, icons, quality=webp_quality)
        rendered += len(jobs)
        jobs.clear()
        write_manifest()
    for zi, (z, key) in enumerate(targets, 1):
        try:
            resolved = resolve_dat_path(z["path"])
            meshes_by_name, placements, textures = parse_zone(read_path_for(resolved))
        except Exception as e:  # noqa: BLE001 — missing/garbled DAT, keep going
            click.echo(f"  [{zi}/{len(targets)}] skip {z['name']}: {e}", err=True)
            continue
        if not meshes_by_name:
            continue
        # The actual DAT file (ROM/0/28.DAT) + its objects_<KEY>.json companion dump,
        # so the manifest links each icon/object back to its source zone + placement list.
        dat_rel = z["path"].replace("\\", "/").removeprefix("game/")
        json_name = f"objects_{key}.json"

        placed, seen = [], set()
        for plc in placements:
            rn = resolve_mesh_name(plc.mesh_id, meshes_by_name)
            if rn and rn not in seen:
                seen.add(rn)
                placed.append(rn)

        tex_cache: dict = {}
        zone_assets = 0
        for name in placed:
            prims = meshes_by_name.get(name)
            if not prims or not any(p.positions for p in prims):
                continue
            cls = classify_mesh(name, prims, object_footprint=object_footprint)
            if cat_filter and cls["category"] not in cat_filter:
                continue  # only the requested categories
            h = _mesh_hash(prims, textures, tex_cache, resolve_texture)
            manifest_objects.append({"zone": z["name"], "dat": dat_rel, "dat_key": key,
                                     "json": json_name, "mesh": name, "hash": h,
                                     "category": cls["category"], "kind": cls["kind"]})
            seen_zone_keys.add(key)
            zone_assets += 1
            if h in icons:
                continue
            icon_file = _cat_dir(icon_root, cls["category"]) / f"{h}.{fmt}"
            icons[h] = {"png": icon_file, "zone": z["name"], "mesh": name,
                        "category": cls["category"], "kind": cls["kind"],
                        "sample_dat": dat_rel, "sample_json": json_name,
                        "footprint": cls["footprint"], "dims": cls["dims"]}
            cat_counts[cls["category"]] += 1
            if skip_existing and icon_file.exists():
                continue
            glb = glb_dir / f"{h}.glb"
            try:
                build_glb(Path(f"{h}.dat"), glb_dir, {name: prims}, [], textures,
                          right_handed=True, write_loose_textures=False, opaque_nonblend=True)
            except Exception as e:  # noqa: BLE001
                click.echo(f"      glb build failed for {name} ({h}): {e}", err=True)
                icons.pop(h, None)
                cat_counts[cls["category"]] -= 1
                manifest_objects.pop()
                zone_assets -= 1
                continue
            if fmt == "webp":
                # Blender always renders PNG; we convert after. Temp render lives in glb_dir.
                render_tmp = glb_dir / f"{h}_icon.png"
                job = {"glb": str(glb), "out": str(render_tmp), "final": str(icon_file), "hash": h}
            else:
                job = {"glb": str(glb), "out": str(icon_file)}
            if fbx:
                fbx_path = _cat_dir(fbx_root, cls["category"]) / _fbx_name(cls["category"], name)
                icons[h]["fbx"] = fbx_path.relative_to(output_dir).as_posix()
                job["fbx"] = str(fbx_path)
                # FBX can't carry the packed textures from headless Blender, so write each
                # referenced texture once to fbx/textures/ (material slot names match these).
                for prim in prims:
                    tk = resolve_texture(prim.texture_name, textures)
                    if tk and tk in textures and tk not in fbx_tex_written:
                        img = textures[tk]
                        (fbx_tex_dir / f"{sanitize_filename(tk)}.png").write_bytes(
                            encode_png_rgba(img.width, img.height,
                                            scale_alpha(img.rgba, DEFAULT_ALPHA_SCALE)))
                        fbx_tex_written.add(tk)
            jobs.append(job)
        click.echo(f"  [{zi}/{len(targets)}] {z['name']}: {zone_assets} asset mesh(es) "
                   f"[{len(icons)} unique, {rendered + len(jobs)} done/queued]")
        if batch_size and len(jobs) >= batch_size:
            flush()

    flush()            # render the final partial batch
    write_manifest()   # ensure a manifest even if nothing rendered (all skipped)

    # Verify the link to the per-zone object dumps: how many source zones already have
    # an objects_<KEY>.json next door (the viewer joins on these for full placement data).
    linked = sorted(k for k in seen_zone_keys if (objects_dir / f"objects_{k}.json").is_file())
    missing = sorted(seen_zone_keys - set(linked))

    click.echo(f"\nDone. {len(icons)} unique icon(s) ({rendered} rendered this run), "
               f"{len(manifest_objects)} asset ref(s). Manifest -> {output_dir / manifest_name}")
    if cat_counts:
        brk = " · ".join(f"{c} {n}" for c, n in sorted(cat_counts.items(), key=lambda x: -x[1]))
        click.echo(f"Categories: {brk}")
    if fbx:
        click.echo(f"FBX: {fbx_root}/<category>/ (one per mesh id) + {len(fbx_tex_written)} "
                   f"texture(s) in fbx/textures/ (material slots match the texture names for Unreal).")
    click.echo(f"Object-JSON link: {len(linked)}/{len(seen_zone_keys)} source zone(s) "
               f"have a matching objects_*.json in {objects_dir}")
    if missing:
        click.echo(f"  {len(missing)} missing (run `xi batch zone_object_list_dump` to generate): "
                   f"{', '.join(missing[:6])}{' ...' if len(missing) > 6 else ''}")

    if pack_sprites and not limit and icons:
        # Derive subdir from the category filter so each category's sheets stay separate.
        subdir = "_".join(sorted(cat_filter)) if cat_filter else None
        click.echo(f"\nPacking sprite sheets...")
        _pack_sprites_inline(output_dir / manifest_name, output_dir, tile_size=64, cols=25,
                             fmt=fmt, webp_quality=webp_quality, subdir=subdir)


def _pack_sprites_inline(manifest_path, output_dir, tile_size=64, cols=25, fmt="png",
                         webp_quality=90, subdir=None):
    """Shared sprite-packing logic used by zone_asset_icons --pack-sprites and pack_sprites.

    subdir: subdirectory under sprites/ for this category's sheets (e.g. "floor").
    Each category gets its own folder so sheets from different categories never collide.
    """
    try:
        from PIL import Image
    except ImportError:
        click.echo("  warn: Pillow not installed — skipping sprite pack (pip install pillow)",
                   err=True)
        return

    import math

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    icons    = manifest.get("icons", {})
    if not icons:
        return

    sprites_dir = output_dir / "sprites" / subdir if subdir else output_dir / "sprites"
    sprites_dir.mkdir(parents=True, exist_ok=True)

    icon_list  = list(icons.items())
    sheet_size = cols * 20
    n_sheets   = math.ceil(len(icon_list) / sheet_size)
    ext        = "." + fmt

    click.echo(f"  {len(icon_list)} icons → {n_sheets} sheet(s) "
               f"({cols}×{sheet_size // cols}, {tile_size}px, {fmt})")

    for si in range(n_sheets):
        batch   = icon_list[si * sheet_size: (si + 1) * sheet_size]
        n_rows  = math.ceil(len(batch) / cols)
        sheet   = Image.new("RGBA", (cols * tile_size, n_rows * tile_size), (0, 0, 0, 0))

        for i, (h, entry) in enumerate(batch):
            col = i % cols
            row = i // cols
            sx  = col * tile_size
            sy  = row * tile_size
            src = output_dir / entry["file"]
            if src.exists():
                try:
                    tile_img = Image.open(src).convert("RGBA")
                    if tile_img.size != (tile_size, tile_size):
                        tile_img = tile_img.resize((tile_size, tile_size), Image.LANCZOS)
                    sheet.paste(tile_img, (sx, sy))
                except Exception as exc:  # noqa: BLE001
                    click.echo(f"    warn: skip {src.name}: {exc}", err=True)
            entry["sprite"] = {"si": si, "sx": sx, "sy": sy}

        sheet_name = f"sheet_{si:03d}{ext}"
        sheet_path = sprites_dir / sheet_name
        if fmt == "webp":
            sheet.save(sheet_path, "WEBP", quality=webp_quality, method=6)
        else:
            sheet.save(sheet_path, "PNG", optimize=True)
        click.echo(f"  [{si + 1}/{n_sheets}] {sheet_name}  "
                   f"({cols * tile_size}×{n_rows * tile_size}px, "
                   f"{sheet_path.stat().st_size // 1024} KB)")

    manifest["spritesheet"] = {
        "tile": tile_size, "cols": cols, "sheet_size": sheet_size,
        "n_sheets": n_sheets, "fmt": fmt,
        "subdir": subdir or "",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(f"  Done. Sprites → {sprites_dir}")


# ── pack_sprites ───────────────────────────────────────────────────────────────

@batch.command("pack_sprites")
@click.option("--manifest", "-m", "manifest_path",
              type=click.Path(path_type=Path, exists=True),
              default=Path("exports/assets/manifest_object.json"),
              show_default=True,
              help="manifest_object.json produced by zone_asset_icons.")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path),
              default=Path("exports/assets"),
              show_default=True,
              help="Root that contains the icon PNGs (same --output-dir used for zone_asset_icons). "
                   "Sprite sheets are written to <output-dir>/sprites/.")
@click.option("--tile", "tile_size", type=int, default=64, show_default=True,
              help="Sprite tile size in pixels (square). Match the CSS display size.")
@click.option("--cols", type=int, default=25, show_default=True,
              help="Tiles per row in each sheet (25 cols × 20 rows = 500 icons/sheet).")
@click.option("--format", "--fmt", "fmt",
              type=click.Choice(["png", "webp"], case_sensitive=False),
              default="png", show_default=True,
              help="Sprite sheet image format.")
@click.option("--webp-quality", type=int, default=90, show_default=True,
              help="WebP quality (1-100). Only used with --format webp.")
def pack_sprites(manifest_path, output_dir, tile_size, cols, fmt, webp_quality):
    """Pack individual icon PNGs into chunked sprite sheets for faster browser loading.

    Reads the manifest produced by zone_asset_icons, resizes every icon to --tile
    pixels, packs them into sheets of --cols × rows (default 500 icons/sheet) and
    writes them to <output-dir>/sprites/sheet_NNN.<fmt>.

    The manifest is updated in-place with a ``sprite`` key per icon entry and a top-
    level ``spritesheet`` metadata block; the browser reads these to render icons via
    CSS background-position instead of individual <img> requests.
    """
    manifest_path = Path(manifest_path).resolve()
    output_dir    = Path(output_dir).resolve()
    if not manifest_path.exists():
        raise click.ClickException(f"Manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("icons"):
        raise click.ClickException("No icons found in manifest.")
    # Derive subdir from manifest filename: manifest_floor.json → "floor"
    stem = manifest_path.stem  # e.g. "manifest_floor"
    subdir = stem.removeprefix("manifest_") or None  # "floor", or None for bare "manifest"
    click.echo(f"Packing {len(manifest['icons'])} icons  ({manifest_path.name}) ...")
    _pack_sprites_inline(manifest_path, output_dir, tile_size=tile_size, cols=cols,
                         fmt=fmt, webp_quality=webp_quality, subdir=subdir)
    sprites_dir = output_dir / "sprites" / subdir if subdir else output_dir / "sprites"
    click.echo(f"Done. Sheets → {sprites_dir}  |  Manifest updated: {manifest_path}")


# ── audio_music / audio_sfx ────────────────────────────────────────────────────

# A sensible default for CPU-bound pure-Python decode (each worker is a cheap
# Python process, unlike the Blender-spawning zone commands which default to 1).
_DEFAULT_AUDIO_WORKERS = min(8, os.cpu_count() or 1)


def _decode_audio_one(job: tuple) -> tuple:
    """Decode one .bgw/.spw. Module-level + picklable so it runs in a worker process.

    Returns ``(status, name, detail)`` where status is ``ok`` | ``skip`` | ``error``.
    Never raises — one bad file must not bring down the pool.
    """
    src_s, root, rel_s, out_dir_s, loops, vgm_s, skip_existing = job
    from xi.audio import xi_core as core

    src = Path(src_s)
    dest = Path(out_dir_s) / root / Path(rel_s).with_suffix(".wav")
    if skip_existing and dest.exists():
        return ("skip", src.name, "exists")
    try:
        header = core.parse_header(src.read_bytes()[:core.DATA_OFFSET])
    except core.AudioError as e:
        return ("skip", src.name, str(e))  # encrypted-variant / unrecognised
    if header.sample_format == core.FMT_ATRAC3 and not vgm_s:
        return ("skip", src.name, "ATRAC3 (no vgmstream)")
    try:
        core.decode_file(src, dest, loops=loops,
                         vgmstream=(Path(vgm_s) if vgm_s else None))
    except (core.AudioError, OSError) as e:
        return ("error", src.name, str(e))
    tag = "atrac3" if header.sample_format == core.FMT_ATRAC3 else "native"
    return ("ok", src.name, tag)


def _run_audio_batch(kind, output_dir: Path, skip_existing: bool, name_filter,
                     limit: int, workers: int, loops: bool, vgmstream_opt, native_only: bool,
                     catalog: bool = True):
    """Shared driver for ``audio_music`` / ``audio_sfx``: enumerate every file of
    ``kind`` under FFXI_DIR and decode them all to ``output_dir`` (source tree
    mirrored as ``<root>/<rel>.wav`` so nothing collides). Also writes a
    ``catalog.json`` grouping the files by game category for a viewer."""
    from xi.audio import xi_core as core
    from xi.xi_config import FFXI_DIR

    base = Path(FFXI_DIR)
    if not base.is_dir():
        raise click.ClickException(
            f"FFXI_DIR not found: {base}  (set the FFXI_DIR env var)")

    vgm = None if native_only else core.find_vgmstream(vgmstream_opt)
    if vgmstream_opt and vgm is None:
        raise click.ClickException(f"vgmstream not found at: {vgmstream_opt}")

    patterns = (name_filter,) if name_filter else ()
    entries = core.list_entries(kind, base, core.SOUND_ROOTS, patterns)
    if not entries:
        raise click.ClickException("No matching files found.")
    if limit:
        entries = entries[:limit]

    output_dir.mkdir(parents=True, exist_ok=True)
    out_abs = output_dir.resolve()
    workers = max(1, workers)
    total = len(entries)

    atrac_note = "" if vgm else "  (ATRAC3 will be skipped: no vgmstream)"
    click.echo(f"Decoding {total} {kind.name} file(s) -> {output_dir}/  "
               f"[{workers} worker(s)]{atrac_note}\n")

    jobs = [(str(e.path), e.root, str(e.rel), str(out_abs), loops,
             (str(vgm) if vgm else ""), skip_existing) for e in entries]

    stats = {"ok": 0, "skip": 0, "error": 0, "atrac": 0}
    issues: list[tuple] = []  # capped sample of skip/error detail to show at the end

    def _tally(res: tuple) -> None:
        status, name, detail = res
        if status == "ok":
            stats["ok"] += 1
            if detail == "atrac3":
                stats["atrac"] += 1
        else:
            stats[status] += 1
            if len(issues) < 40:
                issues.append((status, name, detail))

    with click.progressbar(length=total, label=kind.name, show_pos=True) as bar:
        if workers == 1:
            for job in jobs:
                _tally(_decode_audio_one(job))
                bar.update(1)
        else:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=workers) as ex:
                for res in ex.map(_decode_audio_one, jobs):
                    _tally(res)
                    bar.update(1)

    click.echo(f"\nDone. {stats['ok']} decoded"
               + (f" ({stats['atrac']} via vgmstream)" if stats['atrac'] else "")
               + f", {stats['skip']} skipped, {stats['error']} error(s) -> {output_dir}/")
    if issues:
        click.echo("Notable skips/errors:")
        for status, name, detail in issues:
            click.echo(f"  {status:5s} {name}: {detail}")
        shown = stats['skip'] + stats['error']
        if shown > len(issues):
            click.echo(f"  … and {shown - len(issues)} more")

    if catalog:
        from xi.audio.xi_catalog import build_catalog
        import json
        doc = build_catalog(kind, entries)
        cat_path = output_dir / "catalog.json"
        cat_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        click.echo(f"Catalog: {doc['count']} file(s) in {doc['group_count']} "
                   f"category group(s) -> {cat_path}")


def _audio_batch_options(func):
    """The option set shared by both audio batch commands."""
    func = click.option("--workers", "-w", type=int, default=_DEFAULT_AUDIO_WORKERS,
                        show_default=True,
                        help="Decode this many files in parallel (CPU-bound).")(func)
    func = click.option("--skip-existing", is_flag=True,
                        help="Skip a file whose .wav already exists (resume a run).")(func)
    func = click.option("--filter", "-f", "name_filter", default=None,
                        help="Only decode files whose name matches this substring.")(func)
    func = click.option("--limit", type=int, default=0,
                        help="Stop after N files (0 = all). Handy for a quick test.")(func)
    func = click.option("--loops/--no-loops", default=True, show_default=True,
                        help="Embed a WAV smpl loop chunk for looped audio.")(func)
    func = click.option("--vgmstream", "vgmstream_opt", type=click.Path(), default=None,
                        help="Path to vgmstream-cli for ATRAC3 (else auto-detected).")(func)
    func = click.option("--native-only", is_flag=True,
                        help="Decode only ADPCM/PCM natively; skip ATRAC3 entirely.")(func)
    func = click.option("--catalog/--no-catalog", default=True, show_default=True,
                        help="Also write catalog.json grouping files by game category.")(func)
    return func


@batch.command("audio_music")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path),
              default=Path("exports/music"), show_default=True,
              help="Directory to write decoded WAVs into.")
@_audio_batch_options
def audio_music(output_dir, workers, skip_existing, name_filter, limit, loops,
                vgmstream_opt, native_only, catalog):
    """Decode ALL FFXI music (.bgw) to WAV under exports/music/.

    Walks every sound root, decoding each track to
    ``exports/music/<root>/<stem>.wav``. ADPCM decodes natively (byte-exact);
    ATRAC3 (~36% of music) routes through vgmstream-cli (auto-detected). Also
    writes ``exports/music/catalog.json`` (tracks grouped by sound root, each
    with its title from MusicInfo) for a viewer.

    \b
      xi batch audio_music
      xi batch audio_music -w 8 --skip-existing
      xi batch audio_music --filter music02 --limit 5
    """
    from xi.audio import xi_core as core
    _run_audio_batch(core.MUSIC, output_dir, skip_existing, name_filter, limit,
                     workers, loops, vgmstream_opt, native_only, catalog)


@batch.command("audio_sfx")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path),
              default=Path("exports/sfx"), show_default=True,
              help="Directory to write decoded WAVs into.")
@_audio_batch_options
def audio_sfx(output_dir, workers, skip_existing, name_filter, limit, loops,
              vgmstream_opt, native_only, catalog):
    """Decode ALL FFXI sound effects (.spw) to WAV under exports/sfx/.

    Walks every sound root, decoding each effect to
    ``exports/sfx/<root>/seNNN/<stem>.wav`` (the seNNN subfolders are kept so
    the ~12k files don't collide). Most are ADPCM/PCM (native, byte-exact); a few
    are ATRAC3 (via vgmstream) and 15 are an encrypted variant that's skipped.
    Also writes ``exports/sfx/catalog.json`` grouping every effect by its game
    category (Spell Sounds, Combat Sounds, Skillchain, Monster SFX, …) for a viewer.

    \b
      xi batch audio_sfx
      xi batch audio_sfx -w 8 --skip-existing
      xi batch audio_sfx --filter se002 --limit 20
    """
    from xi.audio import xi_core as core
    _run_audio_batch(core.SFX, output_dir, skip_existing, name_filter, limit,
                     workers, loops, vgmstream_opt, native_only, catalog)


# ── dat_header_dump ───────────────────────────────────────────────────────────

@batch.command("dat_header_dump")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Output JSON path. Default: exports/dat_headers/all.json, or "
                   "models_slot_<slot>.json when --slot is used.")
@click.option("--rom", default=None,
              help="Limit to one top-level dir (e.g. ROM, ROM2). Default: every DAT under FFXI_DIR.")
@click.option("--type", "type_filter", default=None,
              help="Only include DATs that CONTAIN a section of this type_code (decimal or 0xNN hex). "
                   "E.g. 0x2A = SkeletonMesh -> every character/equipment/weapon model DAT.")
@click.option("--slot", "slot_filter", default=None,
              help="Only include gear-model DATs in this equipment slot "
                   "(face/head/body/hands/legs/feet/main/sub/ranged). main = main-hand weapons. "
                   "Implies --ids.")
@click.option("--race", "race_filter", default=None,
              help="Only include gear-model DATs for this race (HumeMale, HumeFemale, ElvaanMale, "
                   "ElvaanFemale, TaruMale, TaruFemale, Mithra, Galka). Implies --ids.")
@click.option("--id-range", "id_range", default=None,
              help="Only include DATs whose model_id (gear or entity) falls in X,Y inclusive, "
                   "e.g. --id-range 100,200. Implies --ids.")
@click.option("--header-bytes", default=16, show_default=True,
              help="How many raw leading bytes to record as header_hex.")
@click.option("--max-sections", default=8192, show_default=True,
              help="Cap the section walk per DAT (zones have ~2000; protects against runaway sizes).")
@click.option("--ids/--no-ids", "with_ids", default=True, show_default=True,
              help="Attach the file_id (reverse FTABLE) and the model_id that maps to each DAT "
                   "(gear race/slot/model_id from the embedded gear tables; entity model_id from the offset ranges).")
@click.option("--limit", type=int, default=0,
              help="Stop after N files (0 = all). Handy for a quick test.")
def dat_header_dump(output, rom, type_filter, slot_filter, race_filter, id_range,
                    header_bytes, max_sections, with_ids, limit):
    """Dump the section-type profile of every FFXI .DAT to one JSON, keyed by ROM-relative path.

    Every DAT is a flat sequence of 16-byte-headed sections: 4-char FourCC + a meta
    u32 where ``type_code = meta & 0x7F`` and ``size = ((meta>>7) & 0xFFFFF) * 0x10``.
    Almost every DAT opens with a ``Directory`` (0x01) wrapper, so the *first* header
    tells you nothing — the content type lives in later sections. This walks the whole
    section chain (header reads + seeks only, no bodies) and records, per DAT:

    \b
      header_hex   first N raw bytes
      sections     total section count
      types        {type_name (or 0xNN): count} across the file
      type_codes   sorted distinct type codes present (decimal)

    With --ids (default) each entry also carries the identifiers that point AT the DAT,
    all derived self-sufficiently (FTABLE + xi's embedded model tables, no DLL/DB):

    \b
      file_id          the FTABLE file_id that resolves to this DAT (reverse lookup)
      gear_model       {race, slot, model_id} if file_id is a gear/weapon model
                       (slot main/sub/ranged = weapons); else null
      entity_model_id  the entity model_id (NPC/monster/player race) that maps to
                       this file_id via the offset ranges; else null

    So to find models, filter for DATs that CONTAIN ``SkeletonMesh`` (0x2A) — that's
    every character / equipment / weapon model — then read ``gear_model``/``entity_model_id``
    to know exactly which model id (and, for gear, which race+slot) loads it. Gear models
    are per-race, so one model_id maps to a different DAT per race. Encrypted item DATs
    (rotate-left-3) don't decode to real sections, so they show up as a single bogus
    section — itself a signal that a DAT is an item table.

    \b
      xi batch dat_header_dump
      xi batch dat_header_dump --type 0x2A -o exports/dat_headers/models.json
      xi batch dat_header_dump --slot main -o exports/dat_headers/main_weapons.json
      xi batch dat_header_dump --rom ROM2 --limit 2000
    """
    from collections import Counter

    root = Path(FFXI_DIR)
    if not root.is_dir():
        raise click.ClickException(f"FFXI_DIR not found: {root}")

    tf = None
    if type_filter is not None:
        try:
            tf = int(type_filter, 0)
        except ValueError:
            raise click.ClickException(f"--type must be decimal or 0xNN hex, got {type_filter!r}")

    from xi.gear.xi_core import SLOTS as GEAR_SLOTS, RACE_TABLES, match_race
    if slot_filter is not None:
        slot_filter = slot_filter.lower()
        if slot_filter not in GEAR_SLOTS:
            raise click.ClickException(f"--slot must be one of {', '.join(GEAR_SLOTS)}; got {slot_filter!r}")
        with_ids = True  # slot comes from gear_model, which only exists with --ids

    if race_filter is not None:
        canon = match_race(race_filter)
        if canon is None:
            raise click.ClickException(f"--race must be one of {', '.join(RACE_TABLES)}; got {race_filter!r}")
        race_filter = canon
        with_ids = True

    id_lo = id_hi = None
    if id_range is not None:
        try:
            id_lo, id_hi = (int(x.strip()) for x in id_range.split(","))
        except ValueError:
            raise click.ClickException("--id-range must be 'X,Y' (two integers), e.g. --id-range 100,200")
        if id_lo > id_hi:
            id_lo, id_hi = id_hi, id_lo
        with_ids = True

    read_n = max(16, header_bytes)
    base = (root / rom) if rom else root
    if not base.is_dir():
        raise click.ClickException(f"--rom path not found: {base}")

    def _walk(fh) -> tuple[bytes, int, Counter]:
        """Return (first read_n bytes, section count, {type_code: count}) by walking
        only the 16-byte section headers (matches xi_export.parse_sections)."""
        head = fh.read(read_n)
        types: Counter = Counter()
        total = 0
        pos = 0
        while total < max_sections:
            fh.seek(pos)
            hdr = fh.read(16)
            if len(hdr) < 16:
                break
            meta = struct.unpack_from("<I", hdr, 4)[0]
            size = ((meta >> 7) & 0xFFFFF) * 0x10
            if size <= 0:
                break
            types[meta & 0x7F] += 1
            total += 1
            pos = (pos + size + 0xF) & ~0xF
        return head, total, types

    files = sorted({*base.rglob("*.DAT"), *base.rglob("*.dat")})
    if limit:
        files = files[:limit]
    click.echo(f"Scanning {len(files)} DAT(s) under {base} ...", err=True)

    # --- id enrichment (all self-sufficient: FTABLE + xi's embedded model tables) ---
    dat_file_id: dict = {}     # 'ROM/x/y.DAT' -> file_id (reverse FTABLE)
    gear_index: dict = {}      # file_id -> (race, slot, model_id)
    entity_ranges = ()
    entity_cap = 0
    if with_ids:
        click.echo("Building file_id / model_id maps ...", err=True)
        from xi.ftable.xi_core import load_all_tables, resolve_dat
        from xi.gear.xi_core import build_gear_index
        from xi.entity.xi_core import RANGES as entity_ranges, MAX_3500_MODELID as entity_cap
        for _rom, (fd, vd) in sorted(load_all_tables().items()):
            n = min(len(fd) // 2, len(vd))
            for fid in range(n):
                dat, _vt = resolve_dat(fd, vd, fid)
                if dat and dat not in dat_file_id:   # first (lowest) file_id wins
                    dat_file_id[dat] = fid
        gear_index = build_gear_index()

    def _entity_model_id(fid: int):
        """Reverse the entity model_id -> file_id offset ranges (modelid_to_file_id)."""
        for start, end, offset in entity_ranges:
            m = fid - offset
            if m < start:
                continue
            if end is None:
                if m <= entity_cap:
                    return m
            elif m <= end:
                return m
        return None

    out: dict = {}
    contains_hist: Counter = Counter()   # how many DATs CONTAIN each type
    scanned = errors = 0
    for f in files:
        scanned += 1
        if scanned % 10000 == 0:
            click.echo(f"  scanned {scanned}/{len(files)} ...", err=True)
        try:
            with open(f, "rb") as fh:
                head, total, types = _walk(fh)
        except OSError:
            errors += 1
            continue

        codes = sorted(types)
        if tf is not None and tf not in types:
            continue

        def _label(code: int) -> str:
            return SECTION_TYPE_NAMES.get(code) or f"0x{code:02x}"

        rel = f.relative_to(root).as_posix()
        entry = {
            "header_hex": head[:header_bytes].hex(),
            "sections": total,
            "types": {_label(c): types[c] for c in codes},
            "type_codes": codes,
        }
        if with_ids:
            fid = dat_file_id.get(rel)
            entry["file_id"] = fid
            entry["gear_model"] = None
            entry["entity_model_id"] = None
            if fid is not None:
                gm = gear_index.get(fid)
                if gm:
                    race, slot, model_id = gm
                    entry["gear_model"] = {"race": race, "slot": slot, "model_id": model_id}
                else:
                    entry["entity_model_id"] = _entity_model_id(fid)
        if slot_filter is not None:
            gm = entry.get("gear_model")
            if not gm or gm["slot"] != slot_filter:
                continue
        if race_filter is not None:
            gm = entry.get("gear_model")
            if not gm or gm["race"] != race_filter:
                continue
        if id_lo is not None:
            gm = entry.get("gear_model")
            mid = gm["model_id"] if gm else entry.get("entity_model_id")
            if mid is None or not (id_lo <= mid <= id_hi):
                continue
        out[rel] = entry
        for c in codes:
            contains_hist[_label(c)] += 1

    if output is None:
        parts = []
        if slot_filter:
            parts.append(f"slot_{slot_filter}")
        if race_filter:
            parts.append(f"race_{race_filter}")
        if id_lo is not None:
            parts.append(f"id_{id_lo}-{id_hi}")
        name = ("models_" + "_".join(parts)) if parts else "all"
        output = Path("exports/dat_headers") / f"{name}.json"
    else:
        output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    click.echo(f"Scanned {scanned} DAT(s); wrote {len(out)} entries -> {output}")
    if errors:
        click.echo(f"  ({errors} unreadable, skipped)")
    click.echo("DATs containing each section type:", err=True)
    for name, count in contains_hist.most_common():
        click.echo(f"  {count:>7}  {name}", err=True)
