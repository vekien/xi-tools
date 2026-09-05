"""`xi title` — inspect and edit the login screen's zones, cameras and weather."""

import json
import struct
from pathlib import Path

import click

import xi.xi_config as _cfg
from xi.xi_config import ensure_base, output_path_for
from xi.title.xi_title import (KEYFRAME_STRIDE, NODE_COUNT_OFF, NODE_KEYFRAME_OFF,
                               OPENING_SECTION, family_tracks, fov_to_focal, parse_nodes,
                               parse_stream, parse_track, parse_zones, resolve, shot_list,
                               tracks_for)


def _zone_names() -> list:
    try:
        from xi.zone.xi_list import ZONE_NAME_DAT, parse_dmsg
        return parse_dmsg((Path(_cfg.FFXI_DIR) / ZONE_NAME_DAT).read_bytes())
    except Exception:
        return []


def _name_of(names: list, zone_id: int) -> str:
    return names[zone_id] if 0 <= zone_id < len(names) else '?'


def _apply_ffxi(ffxi):
    if ffxi:
        _cfg.FFXI_DIR = ffxi


# There is only ever one camera file, so the path is a default rather than an argument.
# Mirrors where the UI commands put their work: exports/<area>/...
CAMERA_JSON = Path('exports/title/camera.json')
DATA_JSON = Path('exports/title/data.json')

# 0x0210 timing records store a u16 at +6. Believed to be a duration / hold in engine
# ticks, but the unit has not been proven against wall-clock playback — export them so
# they can be edited, import writes the u16 back as-is.
TIMING_VALUE_OFF = 6


def _write_out(path: Path, data: bytes | bytearray, output) -> Path:
    out = Path(output) if output else output_path_for(path)
    if not out.is_absolute():
        out = Path(_cfg.FFXI_DIR) / out
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        ensure_base(out)
    out.write_bytes(data)
    return out


def _track_dict(track, named: set) -> dict:
    """One camera track as JSON — eye/look are the editable fields."""
    return {
        'name': track.name,
        'offset': track.offset,
        # True when a weather record names this track (weather flips as the shot starts).
        'weather_change': track.name in named,
        # Fixed capacity: import may not exceed this (nodes are packed end-to-end).
        'keyframe_slots': len(track.keyframes),
        'shape': 'spline' if len(track.keyframes) > 2 else 'line',
        'keyframes': [{
            't': k.t,
            'eye': list(k.eye),
            'look': list(k.look),
            'focal': k.focal,
            'fov_deg': round(k.fov_deg, 3),
        } for k in track.keyframes],
    }


def _timing_dicts(data: bytes, zone) -> list:
    """0x0210 records in file order — value is the editable u16 at +6."""
    return [{
        'offset': r.offset,
        'value': r.value,
        'note': '0x0210 duration/hold; unit unproven (likely engine ticks, not seconds)',
    } for r in parse_stream(data, zone) if r.kind == 'timing']


def _focal_from_keyframe(k: dict) -> float:
    if 'focal' in k:
        return float(k['focal'])
    if 'fov_deg' in k:
        return fov_to_focal(float(k['fov_deg']))
    if 'fovDeg' in k:
        return fov_to_focal(float(k['fovDeg']))
    return float(k.get('distance', 350.0))


def _look_from_keyframe(k: dict) -> tuple:
    look = k.get('look', k.get('look_at', k.get('lookAt')))
    if look is None:
        raise click.ClickException('keyframe missing look / look_at')
    return tuple(float(v) for v in look)


def _eye_from_keyframe(k: dict) -> tuple:
    eye = k.get('eye', k.get('pos', k.get('position')))
    if eye is None:
        raise click.ClickException('keyframe missing eye / pos')
    return tuple(float(v) for v in eye)


def _iter_track_entries(doc: dict):
    """Yield (name, keyframes) from any supported JSON shape.

    Accepts:
      - camera export:  {sections:[{tracks:[{name,keyframes}]}]}
      - full export:    {timeline:{sections:[{cameras:[{name,keyframes}]}]}}
      - flat authoring: {tracks:[{name,keyframes}]}  or  {cameras:[...]}
      - bare list:      [{name,keyframes}, ...]
    """
    if isinstance(doc, list):
        for t in doc:
            yield t['name'], t.get('keyframes', [])
        return

    if not isinstance(doc, dict):
        raise click.ClickException('JSON root must be an object or a list of tracks')

    # Full `xi title export` payload.
    if 'timeline' in doc and isinstance(doc['timeline'], dict):
        doc = doc['timeline']

    if 'sections' in doc:
        for sec in doc.get('sections') or []:
            for t in sec.get('tracks') or sec.get('cameras') or []:
                yield t['name'], t.get('keyframes', [])
        return

    for t in doc.get('tracks') or doc.get('cameras') or []:
        yield t['name'], t.get('keyframes', [])


def _iter_timing_entries(doc: dict):
    """Yield (offset, value) timing edits from camera/data JSON."""
    if not isinstance(doc, dict):
        return
    root = doc.get('timeline', doc) if isinstance(doc.get('timeline'), dict) else doc
    if 'sections' in root:
        for sec in root.get('sections') or []:
            for t in sec.get('timing') or []:
                if isinstance(t, dict) and 'offset' in t and 'value' in t:
                    yield int(t['offset']), int(t['value'])
        return
    for t in root.get('timing') or []:
        if isinstance(t, dict) and 'offset' in t and 'value' in t:
            yield int(t['offset']), int(t['value'])


def _apply_camera_json(data: bytearray, nodes: dict, doc) -> tuple[int, int]:
    """Write track keyframes from JSON into `data`. Returns (written, skipped)."""
    written = skipped = 0
    entries = list(_iter_track_entries(doc))
    if not entries:
        raise click.ClickException(
            'no camera tracks found in JSON — expected sections[].tracks, '
            'timeline.sections[].cameras, or a top-level tracks/cameras list')

    for name, frames in entries:
        off = nodes.get(name)
        if off is None:
            click.echo(f'  skip {name}: no such track in this DAT')
            skipped += 1
            continue
        have = struct.unpack_from('<I', data, off + NODE_COUNT_OFF)[0]
        if len(frames) > have:
            raise click.ClickException(
                f'{name}: JSON has {len(frames)} keyframes but the track holds {have}. '
                f'Growing a track would overwrite the node after it.')
        for i, k in enumerate(frames):
            base = off + NODE_KEYFRAME_OFF + i * KEYFRAME_STRIDE
            ex, ey, ez = _eye_from_keyframe(k)
            lx, ly, lz = _look_from_keyframe(k)
            focal = _focal_from_keyframe(k)
            t = float(k['t']) if 't' in k else (i / max(1, len(frames) - 1) if len(frames) > 1 else 0.0)
            struct.pack_into('<4f', data, base, ex, ey, ez, focal)
            struct.pack_into('<3f', data, base + 0x10, lx, ly, lz)
            struct.pack_into('<f', data, base + 0x20, t)
        click.echo(f'  {name}: {len(frames)} keyframe(s) written')
        written += 1
    return written, skipped


def _apply_timing_json(data: bytearray, doc, *, dat_len: int) -> int:
    """Write 0x0210 duration values from JSON. Returns count written."""
    n = 0
    for offset, value in _iter_timing_entries(doc):
        if not (0 <= offset < dat_len - 8):
            raise click.ClickException(f'timing offset 0x{offset:x} out of range')
        kind = struct.unpack_from('<H', data, offset)[0]
        if kind != 0x0210:
            raise click.ClickException(
                f'timing offset 0x{offset:x}: expected record type 0x0210, found 0x{kind:x}')
        if not (0 <= value <= 0xFFFF):
            raise click.ClickException(f'timing value {value} out of u16 range')
        struct.pack_into('<H', data, offset + TIMING_VALUE_OFF, value)
        click.echo(f'  timing @0x{offset:x}: {value}')
        n += 1
    return n


# ---------------------------------------------------------------------------

@click.command('list')
@click.argument('dat_path', metavar='DAT_FILE', required=False)
@click.option('--json', 'as_json', is_flag=True, help='Emit JSON instead of a table.')
@click.option('--ffxi', default=None, metavar='DIR')
def list_cmd(dat_path, as_json, ffxi):
    """List the title screen's zone segments, their cameras and weather."""
    _apply_ffxi(ffxi)
    path = resolve(dat_path)
    data = path.read_bytes()
    nodes = parse_nodes(data)
    zones = parse_zones(data)
    names = _zone_names()

    if as_json:
        out = [{
            'section': z.index, 'offset': z.offset, 'zone_id': z.zone_id,
            'zone_name': _name_of(names, z.zone_id),
            'file_table_index': z.file_table_index,
            'tracks': family_tracks(z, nodes),
            'tracks_named_by_weather': tracks_for(z, nodes),
            'weather': [{'tag': w.tag, 'rgb': list(w.rgb), 'fog_near': w.fog_near,
                         'fog_far': w.fog_far, 'track': w.track} for w in z.weather],
        } for z in zones]
        click.echo(json.dumps(out, indent=1))
        return

    click.echo(f'{path}  {len(data):,} bytes  {len(zones)} zone sections')
    click.echo()
    click.echo(f'{"#":>3}  {"offset":>8}  {"zone":>4}  {"name":<28} {"weather":>7}  cameras')
    for z in zones:
        mark = ' <' if z.index == OPENING_SECTION else '  '
        click.echo(f'{z.index:3}{mark} 0x{z.offset:06x}  {z.zone_id:4}  '
                   f'{_name_of(names, z.zone_id)[:28]:<28} {len(z.weather):7}  '
                   f'{" ".join(family_tracks(z, nodes)) or "-"}')
    click.echo()
    click.echo(f'section {OPENING_SECTION} (<) is the opening screen on a fresh launch; '
               f'the rest are picked at runtime.')


@click.command('set-zone')
@click.argument('section', type=int)
@click.argument('zone_id', type=int)
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--output', default=None, metavar='DAT_FILE', help='Write elsewhere.')
@click.option('--ffxi', default=None, metavar='DIR')
def set_zone_cmd(section, zone_id, dat_path, output, ffxi):
    """Point title screen SECTION at a different ZONE_ID.

    The camera keeps flying the coordinates it was authored for, so a swap on its own
    usually puts the shot underground. Re-aim that section's tracks afterwards with
    `xi title camera export` / `import`.
    """
    _apply_ffxi(ffxi)
    path = resolve(dat_path)
    data = bytearray(path.read_bytes())
    zones = parse_zones(data)
    names = _zone_names()

    match = next((z for z in zones if z.index == section), None)
    if match is None:
        raise click.ClickException(f'No section {section}; file has {len(zones)}.')

    struct.pack_into('<I', data, match.offset, zone_id)
    out = Path(output) if output else output_path_for(path)
    if not out.is_absolute():
        out = Path(_cfg.FFXI_DIR) / out
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        ensure_base(out)
    out.write_bytes(data)

    nodes = parse_nodes(bytes(data))
    click.echo(f'section {section} @0x{match.offset:06x}: '
               f'{match.zone_id} ({_name_of(names, match.zone_id)}) -> '
               f'{zone_id} ({_name_of(names, zone_id)})')
    click.echo(f'  cameras to re-aim: {" ".join(family_tracks(match, nodes)) or "(none found)"}')
    if section == OPENING_SECTION:
        click.echo('  this is the opening screen on a fresh client launch')
    click.echo(f'wrote {out}')


@click.group('camera')
def camera_group():
    """Export and import the title screen camera paths."""


@camera_group.command('export')
@click.argument('output', metavar='JSON_FILE', required=False)
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--section', type=int, default=None, help='Only this zone section.')
@click.option('--ffxi', default=None, metavar='DIR')
def camera_export_cmd(output, dat_path, section, ffxi):
    """Write camera paths to JSON for editing eye / look-at / zoom.

    Each keyframe is world-space eye XYZ, look-at XYZ, normalised t (0..1), and focal
    length (or fov_deg). Edit those fields, then `xi title camera import` (or
    `xi title import`) to write them back.

    Also exports each section's 0x0210 timing values (duration/hold candidates) with
    file offsets so they can be patched on import via --timing.

    Writes exports/title/camera.json unless a path is given.
    """
    _apply_ffxi(ffxi)
    out_json = Path(output) if output else CAMERA_JSON
    path = resolve(dat_path)
    data = path.read_bytes()
    nodes = parse_nodes(data)
    zones = parse_zones(data)
    names = _zone_names()

    doc = {
        'format': 'xi-title-camera/1',
        'dat': str(path),
        'edit': {
            'eye': 'camera world position [x, y, z] — FFXI Y points DOWN (smaller Y = higher)',
            'look': 'look-at world position [x, y, z]; orientation is look - eye',
            'focal': 'zoom; larger = tighter. Or set fov_deg instead (vertical degrees)',
            't': '0.0 .. 1.0 along the shot (2 keyframes = line, 3+ = spline)',
            'keyframe_slots': 'hard cap per track — import refuses more frames than this',
            'timing': '0x0210 records; unit unproven. Import with --timing to write values back',
        },
        'sections': [],
    }
    for z in zones:
        if section is not None and z.index != section:
            continue
        named = set(tracks_for(z, nodes))
        entry = {
            'section': z.index,
            'zone_id': z.zone_id,
            'zone_name': _name_of(names, z.zone_id),
            'opening': z.index == OPENING_SECTION,
            'tracks': [_track_dict(parse_track(data, tname, nodes[tname]), named)
                       for tname in family_tracks(z, nodes)],
            'timing': _timing_dicts(data, z),
        }
        doc['sections'].append(entry)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    # Unrounded floats: float32 widened to double; rounding even to 6dp drifts coords.
    out_json.write_text(json.dumps(doc, indent=1), encoding='utf-8')
    total = sum(len(t['keyframes']) for s in doc['sections'] for t in s['tracks'])
    timing_n = sum(len(s['timing']) for s in doc['sections'])
    click.echo(f'wrote {out_json}: {len(doc["sections"])} section(s), '
               f'{sum(len(s["tracks"]) for s in doc["sections"])} track(s), '
               f'{total} keyframes, {timing_n} timing value(s)')


@camera_group.command('import')
@click.argument('json_file', metavar='JSON_FILE', required=False)
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--output', default=None, metavar='DAT_FILE', help='Write elsewhere.')
@click.option('--timing/--no-timing', default=False,
              help='Also write 0x0210 timing values when the JSON carries offset+value.')
@click.option('--ffxi', default=None, metavar='DIR')
def camera_import_cmd(json_file, dat_path, output, timing, ffxi):
    """Write camera paths from JSON back into the scene.

    Edit eye / look (look_at) / focal|fov_deg / t on each keyframe, then import.
    Tracks are matched by name. Accepts camera.json, the full data.json from
    `xi title export`, or a flat {tracks:[...]} / {cameras:[...]} object.

    A keyframe count may not grow: nodes sit end-to-end. Fewer frames than a track
    holds leaves the remainder untouched.

    Reads exports/title/camera.json unless a path is given.
    """
    _apply_ffxi(ffxi)
    src = Path(json_file) if json_file else CAMERA_JSON
    if not src.exists():
        raise click.ClickException(f'{src} not found. Run `xi title camera export` first.')
    path = resolve(dat_path)
    data = bytearray(path.read_bytes())
    nodes = parse_nodes(bytes(data))
    doc = json.loads(src.read_text(encoding='utf-8'))

    written, skipped = _apply_camera_json(data, nodes, doc)
    timing_n = _apply_timing_json(data, doc, dat_len=len(data)) if timing else 0

    out = _write_out(path, data, output)
    msg = f'wrote {out} ({written} track(s) updated'
    if skipped:
        msg += f', {skipped} skipped'
    if timing:
        msg += f', {timing_n} timing value(s)'
    click.echo(msg + ')')


@click.command('import')
@click.argument('json_file', metavar='JSON_FILE', required=False)
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--output', default=None, metavar='DAT_FILE', help='Write elsewhere.')
@click.option('--timing/--no-timing', default=False,
              help='Also write 0x0210 timing values when the JSON carries offset+value.')
@click.option('--ffxi', default=None, metavar='DIR')
def import_cmd(json_file, dat_path, output, timing, ffxi):
    """Import title cameras (and optional timing) from export JSON.

    Same writer as `xi title camera import`, but defaults to exports/title/data.json
    from `xi title export`. Edit eye/look on timeline.sections[].cameras[].keyframes
    (or sections[].tracks in camera.json), then run this.
    """
    _apply_ffxi(ffxi)
    src = Path(json_file) if json_file else DATA_JSON
    if not src.exists():
        raise click.ClickException(
            f'{src} not found. Run `xi title export` or `xi title camera export` first.')
    path = resolve(dat_path)
    data = bytearray(path.read_bytes())
    nodes = parse_nodes(bytes(data))
    doc = json.loads(src.read_text(encoding='utf-8'))

    written, skipped = _apply_camera_json(data, nodes, doc)
    timing_n = _apply_timing_json(data, doc, dat_len=len(data)) if timing else 0

    out = _write_out(path, data, output)
    msg = f'wrote {out} ({written} track(s) updated'
    if skipped:
        msg += f', {skipped} skipped'
    if timing:
        msg += f', {timing_n} timing value(s)'
    click.echo(msg + ')')


@click.command('weather')
@click.argument('dat_path', metavar='DAT_FILE', required=False)
@click.option('--section', type=int, default=None, help='Only this zone section.')
@click.option('--ffxi', default=None, metavar='DIR')
def weather_cmd(dat_path, section, ffxi):
    """Show each segment's weather, fog colour and fog range.

    The title screen has no clock. What reads as time of day is these authored values:
    the fog colour tints the scene, and the near/far pair sets how far you can see.
    """
    _apply_ffxi(ffxi)
    path = resolve(dat_path)
    data = path.read_bytes()
    zones = parse_zones(data)
    names = _zone_names()

    for z in zones:
        if section and z.index != section:
            continue
        click.echo(f'section {z.index}  zone {z.zone_id} '
                   f'({_name_of(names, z.zone_id)})')
        for w in z.weather:
            click.echo(f'   0x{w.offset:06x}  {w.tag:5} fog rgb('
                       f'{w.rgb[0]:3},{w.rgb[1]:3},{w.rgb[2]:3}) '
                       f'near={w.fog_near:5} far={w.fog_far:5} '
                       f'blend {w.blend_in}/{w.blend_out}  track={w.track or "-"}')
        click.echo()


TIMELINE_JSON = Path('exports/title/timeline.json')


@click.command('timeline')
@click.argument('output', metavar='JSON_FILE', required=False)
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--section', type=int, default=None, help='Only this zone section.')
@click.option('--json', 'as_json', is_flag=True, help='Write JSON as well as printing.')
@click.option('--ffxi', default=None, metavar='DIR')
def timeline_cmd(output, dat_path, section, as_json, ffxi):
    """Show each segment's shot list -- weather, camera and timing, in play order.

    A zone section is a stream rather than a table, so reading it in file order gives
    the sequence the segment plays: each weather state names the camera track that flies
    while it is showing, and the timing entries between them carry the frame counts.

    Which section plays first is NOT in this file as far as the data shows -- see the
    note printed at the end.
    """
    _apply_ffxi(ffxi)
    path = resolve(dat_path)
    data = path.read_bytes()
    nodes = parse_nodes(data)
    zones = parse_zones(data)
    names = _zone_names()

    doc = {'dat': str(path), 'sections': []}
    for z in zones:
        if section and z.index != section:
            continue
        records = parse_stream(data, z)
        shots = shot_list(records)
        entry = {'section': z.index, 'zone_id': z.zone_id,
                 'zone_name': _name_of(names, z.zone_id), 'shots': [], 'timing': []}

        click.echo(f'section {z.index}  zone {z.zone_id} ({_name_of(names, z.zone_id)})')
        for i, r in enumerate(shots, 1):
            has_cam = r.track in nodes
            frames = [t.value for t in records
                      if t.kind == 'timing' and r.offset < t.offset <
                      (shots[i].offset if i < len(shots) else z.end)]
            click.echo(f'   {i}. {r.tag:5} fog {r.fog_near:5}/{r.fog_far:<5} '
                       f'{"rgb%-16s" % (str(r.rgb),) if r.rgb else "":<20}'
                       f'camera={r.track or "-":6}{"" if has_cam else " (no keyframes)"}'
                       + (f'  timing={frames}' if frames else ''))
            entry['shots'].append({
                'order': i, 'weather': r.tag, 'offset': r.offset,
                'fog_near': r.fog_near, 'fog_far': r.fog_far,
                'fog_rgb': list(r.rgb) if r.rgb else None,
                'camera': r.track, 'camera_has_keyframes': has_cam,
                'timing': frames,
            })
        entry['timing'] = [r.value for r in records if r.kind == 'timing']
        entry['ambient'] = [{'offset': r.offset, 'rgba': list(r.rgba)}
                            for r in records if r.kind == 'ambient' and r.rgba]
        doc['sections'].append(entry)
        click.echo()

    if as_json or output:
        out_json = Path(output) if output else TIMELINE_JSON
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(doc, indent=1), encoding='utf-8')
        click.echo(f'wrote {out_json}')

    click.echo('note: this is the order within a segment. Which segment the title screen '
               'picks on launch is not identifiable in this file -- if the zone differs '
               'between launches it is chosen at runtime, not stored here.')


# The login screen is assembled from more than the scene file: the panel, logos and
# expansion banners live in a UI container, one per locale.
TITLE_UI = {
    'en': 'ROM/119/50.DAT',
    'jp': 'ROM/91/16.DAT',
    'de': 'ROM/176/74.DAT',
    'fr': 'ROM/178/13.DAT',
}


def _ui_textures(rel: str) -> dict:
    """Texture inventory for one locale's title UI container."""
    from xi.ui.xi_core import compression_name, parse_textures
    from xi.ui.xi_palette import parse_palettized

    path = Path(_cfg.FFXI_DIR) / rel
    if not path.exists():
        return {'dat': rel, 'present': False, 'textures': []}
    data = path.read_bytes()
    tex = [{'name': e.name, 'width': e.width, 'height': e.height,
            'format': compression_name(e), 'pixel_format': e.pixel_format}
           for e in parse_textures(data)]
    tex += [{'name': t.name, 'width': t.width, 'height': t.height,
             'format': 'palettized', 'pixel_format': None}
            for t in parse_palettized(data)]
    return {'dat': rel, 'present': True, 'bytes': len(data), 'textures': tex}


@click.command('export')
@click.argument('output', metavar='JSON_FILE', required=False)
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--section', type=int, default=None, help='Only this zone section.')
@click.option('--ffxi', default=None, metavar='DIR')
def export_cmd(output, dat_path, section, ffxi):
    """Dump the title screen to one JSON file (cameras + timing + UI inventory).

    Writes exports/title/data.json. Camera keyframes (eye/look/focal) and timing
    records (offset+value) are importable via `xi title import` after you edit them.
    """
    _apply_ffxi(ffxi)
    out_json = Path(output) if output else DATA_JSON
    path = resolve(dat_path)
    data = path.read_bytes()
    nodes = parse_nodes(data)
    zones = parse_zones(data)
    names = _zone_names()

    sections = []
    for z in zones:
        if section is not None and z.index != section:
            continue
        records = parse_stream(data, z)
        shots = shot_list(records)
        named = set(tracks_for(z, nodes))
        cameras = [_track_dict(parse_track(data, tname, nodes[tname]), named)
                   for tname in family_tracks(z, nodes)]
        sections.append({
            'section': z.index,
            'offset': z.offset,
            'zone_id': z.zone_id,
            'zone_name': _name_of(names, z.zone_id),
            'opening': z.index == OPENING_SECTION,
            'file_table_index': z.file_table_index,
            'weather': [{'order': i, 'tag': r.tag, 'offset': r.offset,
                         'fog_rgb': list(r.rgb) if r.rgb else None,
                         'fog_near': r.fog_near, 'fog_far': r.fog_far,
                         'camera': r.track}
                        for i, r in enumerate(shots, 1)],
            # Importable: keep offset+value (plain ints alone are display-only).
            'timing': _timing_dicts(data, z),
            'ambient': [{'offset': r.offset, 'rgba': list(r.rgba)}
                        for r in records if r.kind == 'ambient' and r.rgba],
            'cameras': cameras,
        })

    doc = {
        'format': 'xi-title/1',
        'import': {
            'cameras': 'xi title import [data.json]  — writes eye/look/focal/t by track name',
            'timing': 'xi title import --timing     — writes 0x0210 values by offset',
            'camera_only': 'xi title camera export/import for a slimmer file',
        },
        'timeline': {
            'dat': str(path),
            'bytes': len(data),
            'play_order': {
                'first': f'section {OPENING_SECTION} (whatever zone_id it holds)',
                'subsequent': 'changes on each return from character select',
                'stored_here': False,
                'note': 'No permutation of the sections exists in this file as u8, u16 '
                        'or u32, and every section header is zeros. Later segments are '
                        'picked at runtime; only the opening slot is fixed by position.',
            },
            'edit': {
                'eye': 'camera world position [x,y,z] — FFXI Y points DOWN',
                'look': 'look-at [x,y,z]; orientation = look - eye',
                'focal_or_fov_deg': 'zoom (focal in DAT, or vertical FOV degrees)',
                't': '0..1 along the shot',
                'timing.value': '0x0210 duration/hold; unit unproven (likely ticks)',
            },
            'sections': sections,
        },
        'ui': {locale: _ui_textures(rel) for locale, rel in TITLE_UI.items()},
        'music': {
            'resolved': False,
            'note': 'No title or lobby DAT references a sound: ROM/0/23, ROM/0/1, '
                    'ROM/0/2, ROM/0/24 and ROM/119/50 all report zero sound references. '
                    'The track is selected by the client, not the data.',
            'catalog_hint': 'uv run xi audio json --type music',
        },
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(doc, indent=1), encoding='utf-8')

    cams = sum(len(s['cameras']) for s in sections)
    kfs = sum(len(c['keyframes']) for s in sections for c in s['cameras'])
    shots_n = sum(len(s['weather']) for s in sections)
    timing_n = sum(len(s['timing']) for s in sections)
    ui_n = sum(len(v['textures']) for v in doc['ui'].values())
    click.echo(f'wrote {out_json}')
    click.echo(f'  timeline : {len(sections)} sections, {shots_n} weather shots, '
               f'{cams} cameras, {kfs} keyframes, {timing_n} timing')
    click.echo(f'  ui       : {ui_n} textures across '
               f'{sum(1 for v in doc["ui"].values() if v["present"])} locale(s)')
    click.echo(f'  music    : unresolved (selected by the client)')
    click.echo(f'  re-import: uv run xi title import {out_json}')


def _zone_placements(zone_id: int, game_root: str | None = None) -> list:
    """Every placement position in a zone, as [x, y, z] in FFXI coordinates.

    Zone geometry is read from the full game install, which is not necessarily where the
    title DAT is: editing a pivot/override tree means FFXI_DIR points at a partial ROM
    holding only the overridden files. `game_root` gives the zone data its own root.
    """
    from xi.zone.xi_list import get_zone_entries
    from xi.zone.xi_objects import list_objects

    # xi_list and xi_objects bind FFXI_DIR at import, so reassigning the config object
    # is not enough -- the module globals have to be swapped for the call.
    import xi.zone.xi_list as _zl
    import xi.zone.xi_objects as _zo

    prev = (_cfg.FFXI_DIR, _zl.FFXI_DIR, getattr(_zo, 'FFXI_DIR', None))
    if game_root:
        _cfg.FFXI_DIR = _zl.FFXI_DIR = game_root
        if hasattr(_zo, 'FFXI_DIR'):
            _zo.FFXI_DIR = game_root
    try:
        entry = next((z for z in get_zone_entries(path_prefix='') if z['id'] == zone_id), None)
        if entry is None:
            raise click.ClickException(
                f'zone {zone_id} not found in the zone table under {_zl.FFXI_DIR}. '
                f'Pass --game with the full game install if this is an override tree.')
        dat = Path(_zl.FFXI_DIR) / entry['path'].replace('game/', '')
        if not dat.exists():
            raise click.ClickException(f'zone DAT not found: {dat}')
        return [o['pos'] for o in list_objects(dat, with_footprint=False) if o.get('pos')]
    finally:
        _cfg.FFXI_DIR, _zl.FFXI_DIR = prev[0], prev[1]
        if prev[2] is not None:
            _zo.FFXI_DIR = prev[2]


def _pick_vantages(points: list, count: int) -> list:
    """Choose `count` well-separated spots with scenery around them.

    Placements cluster where a zone has something worth looking at, so the busiest
    neighbourhoods make the best shots. Candidates are taken on a coarse grid, scored by
    how many placements fall nearby, and then thinned so two shots do not end up on the
    same landmark.
    """
    import collections

    if not points:
        return []
    CELL = 60.0
    buckets = collections.Counter()
    for x, y, z in points:
        buckets[(int(x // CELL), int(z // CELL))] += 1

    ranked = [k for k, _n in buckets.most_common()]
    chosen = []
    for gx, gz in ranked:
        cx, cz = (gx + 0.5) * CELL, (gz + 0.5) * CELL
        if any((cx - ox) ** 2 + (cz - oz) ** 2 < (CELL * 2.5) ** 2 for ox, oz, _ in chosen):
            continue                      # too close to a spot already taken
        near = [p for p in points
                if (p[0] - cx) ** 2 + (p[2] - cz) ** 2 < (CELL * 1.5) ** 2]
        if len(near) < 4:
            continue
        ground = sorted(p[1] for p in near)[len(near) // 2]     # median, ignores outliers
        chosen.append((cx, cz, ground))
        if len(chosen) >= count:
            break
    return chosen


@click.command('aim')
@click.argument('section', type=int)
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--clearance', default=17.0, show_default=True,
              help='How far above local ground to put the camera.')
@click.option('--distance', default=17.0, show_default=True,
              help='Horizontal distance from the camera to what it looks at.')
@click.option('--travel', default=12.0, show_default=True,
              help='How far the camera moves across a shot.')
@click.option('--pitch', default=4.5, show_default=True,
              help='Degrees above horizontal to aim; vanilla shots look slightly up.')
@click.option('--game', default=None, metavar='DIR',
              help='Where the zone geometry lives, when the title DAT is in an override '
                   'tree that has no zone DATs of its own.')
@click.option('--output', default=None, metavar='DAT_FILE')
@click.option('--ffxi', default=None, metavar='DIR')
def camera_aim_cmd(section, dat_path, clearance, distance, travel, pitch, game, output, ffxi):
    """Re-aim a section's cameras into the zone it now points at.

    Camera routes are absolute world positions, so a zone swap leaves them flying the old
    zone's terrain -- usually underground or off the map. This rewrites every keyframe to
    sit above the new zone's scenery.

    The framing comes from measuring all 440 vanilla keyframes rather than being invented:
    the camera sits about 17 units above ground, looks very nearly level (median pitch
    +4.5 degrees, i.e. slightly UP -- not down at the dirt), at something about 17 units
    away, and travels only about 12 units across a shot. An earlier version aimed down and
    dollied 26 units, which is why the results looked nothing like the real screen.

    Ground is sampled per keyframe from the placements nearest that spot, so a shot that
    crosses a slope follows it instead of clipping through.

    Keyframe counts and timings are preserved: only eye and look-at move, so a 3-point
    spline stays a spline.
    """
    _apply_ffxi(ffxi)
    path = resolve(dat_path)
    data = bytearray(path.read_bytes())
    nodes = parse_nodes(bytes(data))
    zones = parse_zones(data)
    names = _zone_names()

    zone = next((z for z in zones if z.index == section), None)
    if zone is None:
        raise click.ClickException(f'no section {section}; the file has {len(zones)}')
    tracks = family_tracks(zone, nodes)
    if not tracks:
        raise click.ClickException(f'section {section} has no camera tracks')

    points = _zone_placements(zone.zone_id, game)
    vantages = _pick_vantages(points, len(tracks))
    if not vantages:
        raise click.ClickException(
            f'zone {zone.zone_id} has too few placements to pick vantages from')

    import math
    import statistics

    def ground_at(x, z):
        """Local ground height: median Y of the placements nearest this spot.

        Median rather than min or mean, so one object on a ledge does not drag the whole
        shot up or down with it.
        """
        near = sorted(points, key=lambda p: (p[0] - x) ** 2 + (p[2] - z) ** 2)[:12]
        return statistics.median(p[1] for p in near) if near else 0.0

    click.echo(f'section {section}: zone {zone.zone_id} '
               f'({_name_of(names, zone.zone_id)}), {len(points):,} placements')
    click.echo(f'  {len(tracks)} track(s), {len(vantages)} vantage(s)')

    for i, tname in enumerate(tracks):
        off = nodes[tname]
        count = struct.unpack_from('<I', data, off + NODE_COUNT_OFF)[0]
        if not (0 < count <= 64):
            continue
        cx, cz, _cell_ground = vantages[i % len(vantages)]
        bearing = (i / max(1, len(tracks))) * math.tau       # vary approach per shot
        # Track direction: move across the view rather than straight at the subject, which
        # is what makes a short move read as a move at all.
        cross = bearing + math.pi / 2

        for k in range(count):
            t = k / max(1, count - 1)
            slide = (t - 0.5) * travel
            ex = cx + math.cos(bearing) * distance + math.cos(cross) * slide
            ez = cz + math.sin(bearing) * distance + math.sin(cross) * slide
            # FFXI's Y points down, so above the ground is a SMALLER value. Verified
            # against vanilla: cameras sit a median 17 units below their local ground
            # value, i.e. 17 above the terrain.
            eye_y = ground_at(ex, ez) - clearance
            # Aim at the landmark, slightly up from level.
            look_y = eye_y - math.tan(math.radians(pitch)) * distance
            base = off + NODE_KEYFRAME_OFF + k * KEYFRAME_STRIDE
            focal = struct.unpack_from('<f', data, base + 0x0C)[0] or 350.0
            struct.pack_into('<4f', data, base, ex, eye_y, ez, focal)
            struct.pack_into('<3f', data, base + 0x10, cx, look_y, cz)
            struct.pack_into('<f', data, base + 0x20, t)

        first = struct.unpack_from('<3f', data, off + NODE_KEYFRAME_OFF)
        click.echo(f'  {tname}: {count} kf  eye ({first[0]:.0f}, {first[1]:.0f}, {first[2]:.0f})'
                   f'  looking at ({cx:.0f}, {cz:.0f})')

    out = Path(output) if output else output_path_for(path)
    if not out.is_absolute():
        out = Path(_cfg.FFXI_DIR) / out
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        ensure_base(out)
    out.write_bytes(data)
    click.echo(f'wrote {out}')


# ---------------------------------------------------------------------------
# Title UI 0x31 UiElementGroup sprites (dest quads + src rects)
# ---------------------------------------------------------------------------

def _parse_xy_pair(s: str, label: str) -> tuple[int, int]:
    parts = str(s).replace(' ', '').split(',')
    if len(parts) != 2:
        raise click.ClickException(f'{label} needs X,Y (got {s!r})')
    try:
        x, y = int(parts[0], 0), int(parts[1], 0)
    except ValueError as e:
        raise click.ClickException(f'{label}: invalid ints in {s!r}') from e
    for n, tag in ((x, 'X'), (y, 'Y')):
        if not (0 <= n <= 65535):
            raise click.ClickException(f'{label} {tag}={n} out of u16 range')
    return x, y


def _parse_wh_pair(s: str, label: str) -> tuple[int, int]:
    parts = str(s).replace(' ', '').split(',')
    if len(parts) != 2:
        raise click.ClickException(f'{label} needs W,H (got {s!r})')
    try:
        w, h = int(parts[0], 0), int(parts[1], 0)
    except ValueError as e:
        raise click.ClickException(f'{label}: invalid ints in {s!r}') from e
    for n, tag in ((w, 'W'), (h, 'H')):
        if not (0 <= n <= 65535):
            raise click.ClickException(f'{label} {tag}={n} out of u16 range')
    return w, h


@click.command('sprite')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--owner', 'owner', default=None,
              help='Texture owner name (e.g. ex1us, titlwin, 20logo).')
@click.option('--index', 'sprite_index', default=None, type=int,
              help='Sprite index under --owner (0-based). Required when patching.')
@click.option('--offset', 'file_offset', default=None,
              help='Target by payload file offset (hex ok), alternative to --owner/--index.')
@click.option('--dest-tl', 'dest_tl', default=None, metavar='X,Y',
              help='Destination top-left (screen).')
@click.option('--dest-br', 'dest_br', default=None, metavar='X,Y',
              help='Destination bottom-right (screen).')
@click.option('--dx', 'delta_x', default=None, type=int, help='Add to all dest X.')
@click.option('--dy', 'delta_y', default=None, type=int, help='Add to all dest Y.')
@click.option('--src-xy', 'src_xy', default=None, metavar='X,Y',
              help='Source top-left on the texture.')
@click.option('--src-wh', 'src_wh', default=None, metavar='W,H',
              help='Source width,height on the texture.')
@click.option('--hide', is_flag=True, help='Zero dest quad (not drawn).')
@click.option('--list-owners', is_flag=True, help='List owner names and counts only.')
@click.option('--output', 'output', default=None, metavar='DAT_FILE',
              help='Write elsewhere (default: overwrite DAT_FILE).')
@click.option('--dry-run', is_flag=True, help='Show changes without writing.')
def sprite_cmd(dat_path, owner, sprite_index, file_offset,
               dest_tl, dest_br, delta_x, delta_y, src_xy, src_wh,
               hide, list_owners, output, dry_run):
    """Inspect / patch 0x31 UiElementGroup sprites (dest quads + src rects).

    Same DAT as title chrome (usually ROM/119/50.DAT). Owner is the texture that
    *follows* the payload in the layout stream (titlwin, ex1us, wardrb, …).

    \b
    Examples:
      uv run xi title sprite path/to/50.DAT
      uv run xi title sprite path/to/50.DAT --owner ex1us
      uv run xi title sprite path/to/50.DAT --owner ex1us --index 0 --dest-tl 768,24 --dest-br 960,62
      uv run xi title sprite path/to/50.DAT --owner titlwin --index 0 --dx 12
      uv run xi title sprite path/to/50.DAT --offset 0x143f11 --hide
    """
    from xi.ui.xi_core import _rects_by_owner

    path = _resolve_title_ui_dat(dat_path)
    raw = path.read_bytes()
    data = bytearray(raw)
    by_owner = _rects_by_owner(bytes(data))

    if list_owners or (owner is None and file_offset is None
                       and not any(v is not None for v in (
                           dest_tl, dest_br, delta_x, delta_y, src_xy, src_wh)) and not hide):
        click.echo(f'{path}')
        click.echo(f'{"owner":<16} {"n":>5}')
        for name in sorted(by_owner.keys(), key=lambda s: (-len(by_owner[s]), s)):
            click.echo(f'{name:<16} {len(by_owner[name]):5d}')
        click.echo(f'({sum(len(v) for v in by_owner.values())} sprites, '
                   f'{len(by_owner)} owners)')
        if owner is None and file_offset is None:
            return

    # Build flat list for offset lookup / display
    def rows_for(own: str | None):
        if own:
            key = own.strip()
            # case-insensitive match
            hit = next((k for k in by_owner if k.lower() == key.lower()), None)
            if hit is None:
                raise click.ClickException(
                    f'no owner {own!r}; try: '
                    + ', '.join(sorted(by_owner.keys())[:24])
                )
            return hit, list(by_owner[hit])
        return None, []

    # List one owner
    if owner and sprite_index is None and file_offset is None and not hide and all(
            v is None for v in (dest_tl, dest_br, delta_x, delta_y, src_xy, src_wh)):
        name, rows = rows_for(owner)
        click.echo(f'{path}')
        click.echo(f'owner {name!r}  ({len(rows)} sprites)')
        click.echo(
            f'{"idx":>4}  {"offset":>10}  {"dest TL":>14}  {"dest BR":>14}  '
            f'{"src WH":>10}  {"src XY":>12}'
        )
        for i, (off, _pre, rect, quad) in enumerate(rows):
            sw, sh, sx, sy = rect
            x0, y0, x1, y1, x2, y2, x3, y3 = quad
            click.echo(
                f'{i:4d}  0x{off:08x}  ({x0},{y0})  ({x3},{y3})  '
                f'{sw}x{sh}  ({sx},{sy})'
            )
        return

    # Resolve target sprite
    target_owner = None
    target_index = None
    payload_off = None
    prefix = None
    rect = None
    quad = None

    if file_offset is not None:
        want = int(str(file_offset), 0)
        for name, rows in by_owner.items():
            for i, (off, pre, rct, qd) in enumerate(rows):
                if off == want:
                    target_owner, target_index = name, i
                    payload_off, prefix, rect, quad = off, pre, rct, qd
                    break
            if payload_off is not None:
                break
        if payload_off is None:
            raise click.ClickException(f'no sprite payload at offset {file_offset}')
    else:
        if not owner:
            raise click.ClickException('--owner or --offset required when patching')
        if sprite_index is None:
            raise click.ClickException('--index required when patching with --owner')
        name, rows = rows_for(owner)
        if not (0 <= sprite_index < len(rows)):
            raise click.ClickException(
                f'index {sprite_index} out of range (0–{len(rows) - 1}) for {name!r}'
            )
        target_owner, target_index = name, sprite_index
        payload_off, prefix, rect, quad = rows[sprite_index]

    x0, y0, x1, y1, x2, y2, x3, y3 = quad
    sw, sh, sx, sy = rect
    click.echo(f'{path}')
    click.echo(
        f'[{target_owner}] idx {target_index}  @0x{payload_off:x}  pref={prefix}'
    )
    click.echo(
        f'  before: dest TL=({x0},{y0}) BR=({x3},{y3})  '
        f'src {sw}x{sh}@({sx},{sy})'
    )

    nx0, ny0, nx1, ny1, nx2, ny2, nx3, ny3 = x0, y0, x1, y1, x2, y2, x3, y3
    nsw, nsh, nsx, nsy = sw, sh, sx, sy

    if hide:
        nx0 = ny0 = nx1 = ny1 = nx2 = ny2 = nx3 = ny3 = 0

    if dest_tl is not None or dest_br is not None:
        if dest_tl is None or dest_br is None:
            raise click.ClickException('--dest-tl and --dest-br must be used together')
        tlx, tly = _parse_xy_pair(dest_tl, '--dest-tl')
        brx, bry = _parse_xy_pair(dest_br, '--dest-br')
        # TL TR BL BR
        nx0, ny0 = tlx, tly
        nx1, ny1 = brx, tly
        nx2, ny2 = tlx, bry
        nx3, ny3 = brx, bry

    if delta_x is not None:
        vals = [v + delta_x for v in (nx0, nx1, nx2, nx3)]
        if any(v < 0 or v > 65535 for v in vals):
            raise click.ClickException(f'--dx {delta_x} pushes dest X out of u16 range')
        nx0, nx1, nx2, nx3 = vals

    if delta_y is not None:
        vals = [v + delta_y for v in (ny0, ny1, ny2, ny3)]
        if any(v < 0 or v > 65535 for v in vals):
            raise click.ClickException(f'--dy {delta_y} pushes dest Y out of u16 range')
        ny0, ny1, ny2, ny3 = vals

    if src_xy is not None:
        nsx, nsy = _parse_xy_pair(src_xy, '--src-xy')
    if src_wh is not None:
        nsw, nsh = _parse_wh_pair(src_wh, '--src-wh')

    changed = (
        (nx0, ny0, nx1, ny1, nx2, ny2, nx3, ny3) != (x0, y0, x1, y1, x2, y2, x3, y3)
        or (nsw, nsh, nsx, nsy) != (sw, sh, sx, sy)
    )
    if not changed:
        click.echo('  (no changes)')
        return

    click.echo(
        f'  after:  dest TL=({nx0},{ny0}) BR=({nx3},{ny3})  '
        f'src {nsw}x{nsh}@({nsx},{nsy})'
    )
    if dry_run:
        click.echo('(dry-run — not written)')
        return

    base = payload_off + prefix
    struct.pack_into(
        '<8H', data, base,
        nx0, ny0, nx1, ny1, nx2, ny2, nx3, ny3,
    )
    struct.pack_into('<4H', data, base + 16, nsw, nsh, nsx, nsy)

    out = Path(output) if output else path
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        try:
            ensure_base(out)
        except Exception:
            pass
    out.write_bytes(data)
    click.echo(f'wrote {out}')


def _wardrobe_sprites(data: bytes) -> list:
    """The twelve wardrobe 3-8 badge sprites: six `wardrb` icons and six `font` digits.

    `xi title sprite --owner wardrb` reaches only the icons: a payload is owned by the
    texture name that follows it, and the digit payloads are owned by `font`, which is
    not a texture in this DAT, so the by-owner index never lists them. The digits are
    the payloads whose own header says `wardrb` and whose following name is `font`.
    Returns (kind, quad_offset, src_rect, dest_quad).
    """
    from xi.ui.xi_core import (SRC_RECT_OFFSET, _record_prefix, all_texture_sizes,
                               parse_layout_records)

    dims = all_texture_sizes(data)
    recs = parse_layout_records(data)
    out = []
    for i in range(len(recs) - 1):
        name, off, length = recs[i]
        owner = recs[i + 1][0]
        if owner == 'wardrb' and 'wardrb' in dims:
            pre = _record_prefix(data, off, length, dims['wardrb'])
            if pre is None:
                continue
            base = off + pre
            rect = struct.unpack_from('<4H', data, base + SRC_RECT_OFFSET)
            if rect[0] in (31, 32) and rect[1] in (31, 32) and rect[2:] == (0, 0):
                out.append(('icon', base, rect, struct.unpack_from('<8H', data, base)))
        elif name == 'wardrb' and owner == 'font':
            base = off + (1 if length == 42 else 0)
            quad = struct.unpack_from('<8H', data, base)
            dw, dh = abs(quad[2] - quad[0]), abs(quad[5] - quad[1])
            if dw <= 20 and dh <= 30:
                rect = struct.unpack_from('<4H', data, base + SRC_RECT_OFFSET)
                out.append(('digit', base, rect, quad))
    return out


@click.command('wardrobe')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--hide', is_flag=True, help='Zero the dest quads so nothing is drawn.')
@click.option('--icons/--no-icons', default=True, help='Include the wardrb icons.')
@click.option('--digits/--no-digits', default=True, help='Include the 3-8 digits.')
@click.option('--output', default=None, metavar='DAT_FILE',
              help='Write elsewhere (default: overwrite DAT_FILE).')
@click.option('--dry-run', is_flag=True, help='Show changes without writing.')
def wardrobe_cmd(dat_path, hide, icons, digits, output, dry_run):
    """List or hide the wardrobe 3-8 badges on the title screen.

    Each badge is an icon sprite (texture `wardrb`) plus a digit sprite drawn from the
    shared `font` atlas. `xi title sprite --owner wardrb --hide` clears the icons only;
    the digits are owned by `font`, which is not in this DAT, so this command is the
    way to clear them.

    \b
    Examples:
      uv run xi title wardrobe path/to/50.DAT
      uv run xi title wardrobe path/to/50.DAT --hide --dry-run
      uv run xi title wardrobe path/to/50.DAT --hide --no-icons
    """
    path = _resolve_title_ui_dat(dat_path)
    data = bytearray(path.read_bytes())
    found = _wardrobe_sprites(bytes(data))
    wanted = [s for s in found if (s[0] == 'icon' and icons) or (s[0] == 'digit' and digits)]

    click.echo(f'{path}')
    click.echo(f'{"kind":<6} {"quad@":>10}  {"dest TL":>10}  {"dest BR":>10}  {"src WH":>8}  {"src XY":>10}')
    for kind, base, rect, quad in found:
        sw, sh, sx, sy = rect
        drawn = '' if any(quad) else '  (hidden)'
        click.echo(f'{kind:<6} 0x{base:08x}  ({quad[0]},{quad[1]})  ({quad[6]},{quad[7]})  '
                   f'{sw}x{sh}  ({sx},{sy}){drawn}')
    n_icon = sum(1 for s in found if s[0] == 'icon')
    n_digit = sum(1 for s in found if s[0] == 'digit')
    click.echo(f'({n_icon} icons, {n_digit} digits; expected 6 + 6)')
    if not hide:
        return

    targets = [s for s in wanted if any(s[3])]
    if not targets:
        click.echo('  (nothing to hide — already zero)')
        return
    click.echo(f'hide: {len(targets)} sprite(s)')
    if dry_run:
        click.echo('(dry-run — not written)')
        return
    for _kind, base, _rect, _quad in targets:
        struct.pack_into('<8H', data, base, *([0] * 8))

    out = Path(output) if output else path
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        try:
            ensure_base(out)
        except Exception:
            pass
    out.write_bytes(data)
    click.echo(f'wrote {out}')


@click.command('swap-sections')
@click.argument('a', type=int)
@click.argument('b', type=int)
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--output', default=None, metavar='DAT_FILE')
@click.option('--ffxi', default=None, metavar='DIR')
def swap_sections_cmd(a, b, dat_path, output, ffxi):
    """Exchange two zone segments, moving each one's whole block.

    The client always opens on section 12, so moving another segment into that position
    is how the opening screen changes -- and unlike `set-zone`, the segment brings its own
    weather and its own control-track names with it, so the camera family authored for
    that zone plays instead of the one that used to live in the slot.

    Safe because the segments are a plain stream with nothing pointing into it: no u32
    anywhere in the file equals any segment's offset, so shifting them changes no
    reference. Camera nodes live earlier in the file and are addressed by name, not
    position, so they are untouched.
    """
    _apply_ffxi(ffxi)
    path = resolve(dat_path)
    data = path.read_bytes()
    zones = parse_zones(data)
    names = _zone_names()
    nodes = parse_nodes(data)

    za = next((z for z in zones if z.index == a), None)
    zb = next((z for z in zones if z.index == b), None)
    if za is None or zb is None:
        raise click.ClickException(f'sections {a} and {b} must both exist (file has {len(zones)})')
    if a == b:
        raise click.ClickException('pick two different sections')
    if za.offset > zb.offset:
        za, zb = zb, za

    # Each block runs from its 8-byte marker to the start of the next one's marker.
    a_start, a_end = za.offset - 8, zb.offset - 8 if zb.index == za.index + 1 else None
    order = sorted(zones, key=lambda z: z.offset)
    bounds = {}
    for i, z in enumerate(order):
        start = z.offset - 8
        end = (order[i + 1].offset - 8) if i + 1 < len(order) else z.end
        bounds[z.index] = (start, end)

    sa, ea = bounds[za.index]
    sb, eb = bounds[zb.index]
    block_a, block_b = data[sa:ea], data[sb:eb]

    rebuilt = (data[:sa] + block_b + data[ea:sb] + block_a + data[eb:])
    if len(rebuilt) != len(data):
        raise click.ClickException('rebuild changed the file length; refusing to write')

    out = Path(output) if output else output_path_for(path)
    if not out.is_absolute():
        out = Path(_cfg.FFXI_DIR) / out
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        ensure_base(out)
    out.write_bytes(rebuilt)

    click.echo(f'swapped section {za.index} <-> {zb.index}')
    for z in (za, zb):
        click.echo(f'  {z.index:2}: zone {z.zone_id} ({_name_of(names, z.zone_id)}) '
                   f'{bounds[z.index][1] - bounds[z.index][0]} bytes, '
                   f'cameras {" ".join(family_tracks(z, nodes)) or "-"}')
    click.echo()
    after = parse_zones(rebuilt)
    nodes2 = parse_nodes(rebuilt)
    for z in after:
        if z.index in (min(a, b), max(a, b)):
            mark = ' <- opening screen' if z.index == OPENING_SECTION else ''
            click.echo(f'  now section {z.index}: zone {z.zone_id} '
                       f'({_name_of(names, z.zone_id)}), '
                       f'cameras {" ".join(family_tracks(z, nodes2)) or "-"}{mark}')
    click.echo(f'wrote {out}')


# ---------------------------------------------------------------------------
# Title / lobby UiMenu editing (ROM/119/50.DAT) — positions, size, nav
# ---------------------------------------------------------------------------

_TITLE_UI_DEFAULT = Path('ROM/119/50.DAT')

_TITLE_MENU_SHOW = {
    'loby': 'Main list (Select/Create/Delete/Config/Back)',
    'chsw': 'Same list — keyboard layout',
    'chs3': 'Same list — controller layout',
    'lob3': 'Lobby character window',
    'lob4': 'Lobby character window (360)',
    'chfw': 'Character focus (2 opts)',
    'chf3': 'Character focus 360',
}


def _resolve_title_ui_dat(dat_path: str | None) -> Path:
    """Resolve the title UI DAT path (same style as `xi ui tex si DAT_FILE`)."""
    if not dat_path:
        raise click.ClickException(
            'DAT_FILE is required, e.g.\n'
            '  uv run xi title menu D:\\…\\ROM\\119\\50.DAT\n'
            '  uv run xi title menu D:\\…\\50.DAT --menu loby --elem 0 --x 100 --y 200'
        )
    p = Path(dat_path)
    if not p.is_file():
        # allow relative to FFXI_DIR when set
        alt = Path(_cfg.FFXI_DIR) / dat_path if _cfg.FFXI_DIR else None
        if alt is not None and alt.is_file():
            return alt
        raise click.ClickException(f'Title UI DAT not found: {p}')
    return p


@click.command('menu')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--menu', 'menu_tag', default=None,
              help='4-char section tag (e.g. loby, chsw). Required when patching.')
@click.option('--elem', 'elem_index', default=None, type=int,
              help='Element index 0..N-1 (omit = frame). Ignored if --btn is set.')
@click.option('--btn', 'button_id', default=None, type=int,
              help='Target by ButtonID instead of element index (e.g. 2 = Create).')
@click.option('--x', 'new_x', default=None, type=int, help='New X (i16).')
@click.option('--y', 'new_y', default=None, type=int, help='New Y (i16).')
@click.option('--w', 'new_w', default=None, type=int, help='New width (i16).')
@click.option('--h', 'new_h', default=None, type=int, help='New height (i16).')
@click.option('--nav-up', 'nav_up', default=None, type=int,
              help='Nav Up → ButtonID (−1 = none).')
@click.option('--nav-down', 'nav_down', default=None, type=int,
              help='Nav Down → ButtonID (−1 = none).')
@click.option('--nav-left', 'nav_left', default=None, type=int,
              help='Nav Left → ButtonID (−1 = none).')
@click.option('--nav-right', 'nav_right', default=None, type=int,
              help='Nav Right → ButtonID (−1 = none).')
@click.option('--isolate', is_flag=True,
              help='Set all four nav links to −1 (skip in pad/key chain).')
@click.option('--all', 'show_all', is_flag=True, help='List every UiMenu in the DAT.')
@click.option('--output', 'output', default=None, metavar='DAT_FILE',
              help='Write to a different path (default: overwrite DAT_FILE).')
@click.option('--dry-run', is_flag=True, help='Show changes without writing.')
def menu_cmd(dat_path, menu_tag, elem_index, button_id,
             new_x, new_y, new_w, new_h,
             nav_up, nav_down, nav_left, nav_right, isolate,
             show_all, output, dry_run):
    """Inspect / patch title-screen UiMenus in a UI DAT (usually ROM/119/50.DAT).

    Pass the DAT path like other ui commands — no --ffxi needed.

    Moves hitboxes (labels follow), resizes, and rewires pad/keyboard nav.
    Character strip: --menu loby (chsw / chs3 for keyboard / controller).

    \b
    Examples:
      uv run xi title menu D:\\…\\ROM\\119\\50.DAT
      uv run xi title menu D:\\…\\50.DAT --menu loby --elem 1 --x 554 --y 671
      uv run xi title menu D:\\…\\50.DAT --menu loby --btn 2 --isolate --x -800 --y -800
      uv run xi title menu D:\\…\\50.DAT --menu loby --btn 1 --nav-down 3
    """
    from xi.ui.xi_menu_pos import (find_element_by_button_id, find_menu_sections,
                                  find_section, patch_element_nav, patch_element_size,
                                  patch_element_xy, _print_section)

    path = _resolve_title_ui_dat(dat_path)
    data = bytearray(path.read_bytes())
    sections = find_menu_sections(bytes(data))

    patching = any(v is not None for v in (
        new_x, new_y, new_w, new_h, nav_up, nav_down, nav_left, nav_right)) or isolate

    if not patching:
        click.echo(f'{path}')
        click.echo()
        if show_all:
            to_show = sections
        else:
            to_show = [s for s in sections if s.tag in _TITLE_MENU_SHOW]
            if not to_show:
                to_show = sections[:12]
        for s in to_show:
            label = _TITLE_MENU_SHOW.get(s.tag, '')
            if label:
                click.echo(f'-- {label} --')
            _print_section(s, with_nav=True)
            click.echo()
        if not show_all:
            click.echo(
                f'(showing {len(to_show)} title menus; --all for all {len(sections)})'
            )
            click.echo(
                'Common tags: loby (main), chsw (kb), chs3 (pad), lob3, chfw'
            )
        return

    if not menu_tag:
        raise click.ClickException('--menu TAG is required when patching')
    section = find_section(sections, menu_tag)
    if section is None:
        raise click.ClickException(
            f'no menu tag {menu_tag!r}; try: '
            + ' '.join(sorted({s.tag for s in sections})[:30])
        )

    if button_id is not None:
        target = find_element_by_button_id(section, button_id)
        if target is None:
            raise click.ClickException(
                f'no button id {button_id} in [{menu_tag}]; '
                f'ids: {[e.button_id for e in section.elements]}'
            )
        target_label = f'btn {button_id}'
    elif elem_index is None:
        target = section.frame
        target_label = 'frame'
    else:
        if not (0 <= elem_index < len(section.elements)):
            raise click.ClickException(
                f'elem {elem_index} out of range (0–{len(section.elements) - 1})'
            )
        target = section.elements[elem_index]
        target_label = f'elem[{elem_index}] btn={target.button_id}'

    use_up, use_down, use_left, use_right = nav_up, nav_down, nav_left, nav_right
    if isolate:
        use_up = use_down = use_left = use_right = -1

    changes = []
    for name, cur, new in (
        ('x', target.x, new_x), ('y', target.y, new_y),
        ('w', target.width, new_w), ('h', target.height, new_h),
        ('up', target.nav_up, use_up), ('down', target.nav_down, use_down),
        ('left', target.nav_left, use_left), ('right', target.nav_right, use_right),
    ):
        if new is not None and new != cur:
            changes.append(f'{name} {cur}->{new}')

    state = (
        f'xy=({target.x},{target.y}) wh=({target.width},{target.height}) '
        f'nav=({target.nav_up},{target.nav_down},{target.nav_left},{target.nav_right})'
    )
    head = f'[{menu_tag}] {target_label}'
    if changes:
        click.echo(f'{head}: ' + ', '.join(changes))
    else:
        click.echo(f'{head}: {state}  (no change)')

    if dry_run:
        click.echo('(dry-run — not written)')
        return

    try:
        if new_x is not None or new_y is not None:
            patch_element_xy(data, target, new_x, new_y)
        if new_w is not None or new_h is not None:
            patch_element_size(data, target, new_w, new_h)
        if any(v is not None for v in (use_up, use_down, use_left, use_right)):
            patch_element_nav(data, target, use_up, use_down, use_left, use_right)
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    # Write in-place when --ffxi was the DAT itself (FFXI_DIR is a file then).
    if output:
        out = Path(output)
    else:
        out = path
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        try:
            ensure_base(out)
        except Exception:
            pass
    out.write_bytes(data)
    click.echo(f'wrote {out}')
