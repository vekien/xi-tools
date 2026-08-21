"""`xi title` — inspect and edit the login screen's zones, cameras and weather."""

import json
import struct
from pathlib import Path

import click

import xi.xi_config as _cfg
from xi.xi_config import ensure_base, output_path_for
from xi.title.xi_title import (KEYFRAME_STRIDE, NODE_COUNT_OFF, NODE_KEYFRAME_OFF,
                               parse_nodes, parse_track, parse_zones, resolve, tracks_for)


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
            'tracks': tracks_for(z, nodes),
            'weather': [{'tag': w.tag, 'rgb': list(w.rgb), 'fog_near': w.fog_near,
                         'fog_far': w.fog_far, 'track': w.track} for w in z.weather],
        } for z in zones]
        click.echo(json.dumps(out, indent=1))
        return

    click.echo(f'{path}  {len(data):,} bytes  {len(zones)} zone sections')
    click.echo()
    click.echo(f'{"#":>3}  {"offset":>8}  {"zone":>4}  {"name":<28} {"weather":>7}  cameras')
    for z in zones:
        click.echo(f'{z.index:3}  0x{z.offset:06x}  {z.zone_id:4}  '
                   f'{_name_of(names, z.zone_id)[:28]:<28} {len(z.weather):7}  '
                   f'{" ".join(tracks_for(z, nodes)) or "-"}')


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
    click.echo(f'  cameras to re-aim: {" ".join(tracks_for(match, nodes)) or "(none found)"}')
    click.echo(f'wrote {out}')


@click.group('camera')
def camera_group():
    """Export and import the title screen camera paths."""


@camera_group.command('export')
@click.argument('output', metavar='JSON_FILE')
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--section', type=int, default=None, help='Only this zone section.')
@click.option('--ffxi', default=None, metavar='DIR')
def camera_export_cmd(output, dat_path, section, ffxi):
    """Write every camera path to JSON.

    Each keyframe is an eye position, a look-at point and a normalised time, which is
    what a viewer needs to replay the shot -- no engine-specific packing.
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
        entry = {'section': z.index, 'zone_id': z.zone_id,
                 'zone_name': _name_of(names, z.zone_id), 'tracks': []}
        for tname in tracks_for(z, nodes):
            track = parse_track(data, tname, nodes[tname])
            entry['tracks'].append({
                'name': track.name,
                'offset': track.offset,
                # Values are written unrounded. They are float32 widened to double, so
                # printing them in full means an untouched export re-imports
                # byte-identically; rounding even to 6dp drifts the small coordinates.
                'keyframes': [{
                    't': k.t,
                    'eye': list(k.eye),
                    'look': list(k.look),
                    # Per keyframe, not per track: it varies within a track (cga tracks
                    # run 312 -> 350), so hoisting it flattens real camera movement.
                    'distance': k.view_distance,
                } for k in track.keyframes],
            })
        doc['sections'].append(entry)

    Path(output).write_text(json.dumps(doc, indent=1), encoding='utf-8')
    total = sum(len(t['keyframes']) for s in doc['sections'] for t in s['tracks'])
    click.echo(f'wrote {output}: {len(doc["sections"])} section(s), '
               f'{sum(len(s["tracks"]) for s in doc["sections"])} track(s), '
               f'{total} keyframes')


@camera_group.command('import')
@click.argument('json_file', metavar='JSON_FILE')
@click.option('--dat', 'dat_path', default=None, metavar='DAT_FILE')
@click.option('--output', default=None, metavar='DAT_FILE', help='Write elsewhere.')
@click.option('--ffxi', default=None, metavar='DIR')
def camera_import_cmd(json_file, dat_path, output, ffxi):
    """Write camera paths from JSON back into the scene.

    Tracks are matched by name. A keyframe count may not grow: the nodes sit in a fixed
    layout with the next node immediately after, so extra frames would overwrite it.
    Supplying fewer than a track holds leaves the remainder untouched.
    """
    _apply_ffxi(ffxi)
    path = resolve(dat_path)
    data = bytearray(path.read_bytes())
    nodes = parse_nodes(bytes(data))
    doc = json.loads(Path(json_file).read_text(encoding='utf-8'))

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
            fallback = float(t.get('view_distance') or 0.0)
            for i, k in enumerate(frames):
                base = off + NODE_KEYFRAME_OFF + i * KEYFRAME_STRIDE
                ex, ey, ez = (float(v) for v in k['eye'])
                lx, ly, lz = (float(v) for v in k['look'])
                dist = float(k.get('distance', fallback))
                struct.pack_into('<4f', data, base, ex, ey, ez, dist)
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
