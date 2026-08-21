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
    """Write every camera path to JSON.

    Each keyframe is an eye position, a look-at point and a normalised time, which is
    what a viewer needs to replay the shot -- no engine-specific packing.

    Writes exports/title/camera.json unless a path is given.
    """
    _apply_ffxi(ffxi)
    out_json = Path(output) if output else CAMERA_JSON
    path = resolve(dat_path)
    data = path.read_bytes()
    nodes = parse_nodes(data)
    zones = parse_zones(data)
    names = _zone_names()

    doc = {'dat': str(path), 'sections': []}
    for z in zones:
        if section and z.index != section:
            continue
        entry = {'section': z.index, 'zone_id': z.zone_id,
                 'zone_name': _name_of(names, z.zone_id), 'tracks': []}
        named = set(tracks_for(z, nodes))
        for tname in family_tracks(z, nodes):
            track = parse_track(data, tname, nodes[tname])
            entry['tracks'].append({
                'name': track.name,
                'offset': track.offset,
                # True when a weather record names this track, i.e. the weather changes
                # as this shot begins. The rest play under the weather already running.
                'weather_change': track.name in named,
                # Values are written unrounded. They are float32 widened to double, so
                # printing them in full means an untouched export re-imports
                # byte-identically; rounding even to 6dp drifts the small coordinates.
                # focal is what the DAT stores; fov_deg is the same value as the client
                # uses it, so an editor can consume it without repeating the conversion.
                'shape': 'spline' if len(track.keyframes) > 2 else 'line',
                'keyframes': [{
                    't': k.t,
                    'eye': list(k.eye),
                    'look': list(k.look),
                    'focal': k.focal,
                    'fov_deg': round(k.fov_deg, 3),
                } for k in track.keyframes],
            })
        doc['sections'].append(entry)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(doc, indent=1), encoding='utf-8')
    total = sum(len(t['keyframes']) for s in doc['sections'] for t in s['tracks'])
    click.echo(f'wrote {out_json}: {len(doc["sections"])} section(s), '
               f'{sum(len(s["tracks"]) for s in doc["sections"])} track(s), '
               f'{total} keyframes')


@camera_group.command('import')
@click.argument('json_file', metavar='JSON_FILE', required=False)
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--output', default=None, metavar='DAT_FILE', help='Write elsewhere.')
@click.option('--ffxi', default=None, metavar='DIR')
def camera_import_cmd(json_file, dat_path, output, ffxi):
    """Write camera paths from JSON back into the scene.

    Tracks are matched by name. A keyframe count may not grow: the nodes sit in a fixed
    layout with the next node immediately after, so extra frames would overwrite it.
    Supplying fewer than a track holds leaves the remainder untouched.

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

    written = skipped = 0
    for sec in doc.get('sections', []):
        for t in sec.get('tracks', []):
            name = t['name']
            off = nodes.get(name)
            if off is None:
                click.echo(f'  skip {name}: no such track in this DAT')
                skipped += 1
                continue
            have = struct.unpack_from('<I', data, off + NODE_COUNT_OFF)[0]
            frames = t.get('keyframes', [])
            if len(frames) > have:
                raise click.ClickException(
                    f'{name}: JSON has {len(frames)} keyframes but the track holds {have}. '
                    f'Growing a track would overwrite the node after it.')
            for i, k in enumerate(frames):
                base = off + NODE_KEYFRAME_OFF + i * KEYFRAME_STRIDE
                ex, ey, ez = (float(v) for v in k['eye'])
                lx, ly, lz = (float(v) for v in k['look'])
                # Accept fov_deg as an alternative to focal, so a path authored in an
                # editor that thinks in degrees can be imported without converting first.
                if 'focal' in k:
                    focal = float(k['focal'])
                elif 'fov_deg' in k:
                    focal = fov_to_focal(float(k['fov_deg']))
                else:
                    focal = float(k.get('distance', 350.0))
                struct.pack_into('<4f', data, base, ex, ey, ez, focal)
                struct.pack_into('<3f', data, base + 0x10, lx, ly, lz)
                struct.pack_into('<f', data, base + 0x20, float(k.get('t', i)))
            click.echo(f'  {name}: {len(frames)} keyframe(s) written')
            written += 1

    out = Path(output) if output else output_path_for(path)
    if not out.is_absolute():
        out = Path(_cfg.FFXI_DIR) / out
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        ensure_base(out)
    out.write_bytes(data)
    click.echo(f'wrote {out} ({written} track(s) updated'
               + (f', {skipped} skipped)' if skipped else ')'))


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


DATA_JSON = Path('exports/title/data.json')

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
@click.option('--ffxi', default=None, metavar='DIR')
def export_cmd(output, dat_path, ffxi):
    """Dump everything the title screen is made of to one JSON file.

    Writes exports/title/data.json: the scene timeline with every camera path, the UI
    textures for each locale, and what is known about the music.
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
        records = parse_stream(data, z)
        shots = shot_list(records)
        named = set(tracks_for(z, nodes))
        cameras = []
        for tname in family_tracks(z, nodes):
            track = parse_track(data, tname, nodes[tname])
            cameras.append({
                'name': track.name,
                'offset': track.offset,
                'weather_change': track.name in named,
                'shape': 'spline' if len(track.keyframes) > 2 else 'line',
                'keyframes': [{'t': k.t, 'eye': list(k.eye), 'look': list(k.look),
                               'focal': k.focal, 'fov_deg': round(k.fov_deg, 3)}
                              for k in track.keyframes],
            })
        sections.append({
            'section': z.index,
            'offset': z.offset,
            'zone_id': z.zone_id,
            'zone_name': _name_of(names, z.zone_id),
            'file_table_index': z.file_table_index,
            'weather': [{'order': i, 'tag': r.tag, 'offset': r.offset,
                         'fog_rgb': list(r.rgb) if r.rgb else None,
                         'fog_near': r.fog_near, 'fog_far': r.fog_far,
                         'camera': r.track}
                        for i, r in enumerate(shots, 1)],
            'timing': [r.value for r in records if r.kind == 'timing'],
            'ambient': [{'offset': r.offset, 'rgba': list(r.rgba)}
                        for r in records if r.kind == 'ambient' and r.rgba],
            'cameras': cameras,
        })

    doc = {
        'timeline': {
            'dat': str(path),
            'bytes': len(data),
            'play_order': {
                'first': 'North Gustaberg on a fresh client launch',
                'subsequent': 'changes on each return from character select',
                'stored_here': False,
                'note': 'No permutation of the sections exists in this file as u8, u16 '
                        'or u32, and every section header is zeros. Both behaviours are '
                        'decided at runtime, so changing them means patching the client, '
                        'not this DAT.',
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
    ui_n = sum(len(v['textures']) for v in doc['ui'].values())
    click.echo(f'wrote {out_json}')
    click.echo(f'  timeline : {len(sections)} sections, {shots_n} weather shots, '
               f'{cams} cameras, {kfs} keyframes')
    click.echo(f'  ui       : {ui_n} textures across '
               f'{sum(1 for v in doc["ui"].values() if v["present"])} locale(s)')
    click.echo(f'  music    : unresolved (selected by the client)')
