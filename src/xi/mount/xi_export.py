import json
import click

from xi.mount import xi_core as M


@click.command('export')
@click.argument('mount_id', type=int)
@click.option('--json', 'as_json', is_flag=True, help='Emit JSON (default).')
def cmd(mount_id, as_json):
    """Dump a mount's full record: EN/JP name + help, EN/JP key-item name + desc,
    file-id, ROM DAT."""
    if not (0 <= mount_id <= M.MODEL_CAP):
        raise click.ClickException(f'mount id must be 0-{M.MODEL_CAP}')
    rec = M.read_record(mount_id)

    if as_json:
        click.echo(json.dumps(rec, ensure_ascii=False, indent=2))
        return

    click.echo(f"mount {rec['id']}   key item {rec['key_item']}   "
               f"file-id {rec['file_id_hex']} ({rec['file_id']})")
    click.echo(f"  model   : {rec['model_dat'] or '— (free slot)'}")
    click.echo(f"  name EN : {rec['name_en'] or '—'}")
    click.echo(f"  name JP : {rec['name_jp'] or '—'}")
    click.echo(f"  help EN : {rec['help_en'] or '—'}")
    click.echo(f"  help JP : {rec['help_jp'] or '—'}")
    click.echo(f"  ki   EN : {rec['ki_name_en'] or '—'}   {rec['ki_desc_en'] or ''}")
    click.echo(f"  ki   JP : {rec['ki_name_jp'] or '—'}   {rec['ki_desc_jp'] or ''}")
    click.echo(f"  status  : {'real mount' if M.is_real_mount(rec) else 'occupied' if rec['occupied'] else 'free'}")
