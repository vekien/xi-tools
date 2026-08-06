import json
import click

from xi.mount import xi_core as M


@click.command('list')
@click.option('--all', 'show_all', is_flag=True,
              help='List ids 0-255 (default 0-63, the menu-visible range).')
@click.option('--occupied', is_flag=True, help='Only ids that point at a model.')
@click.option('--free', is_flag=True, help='Only free, name-able slots (no model).')
@click.option('--json', 'as_json', is_flag=True, help='Emit JSON.')
def cmd(show_all, occupied, free, as_json):
    """List mounts: id, name (EN), key item, file-id, ROM DAT, status."""
    hi = 256 if show_all else M.MENU_CAP
    cache: dict = {}
    rows = []
    for mid in range(hi):
        rec = M.read_record(mid, cache=cache)
        if occupied and not rec['occupied']:
            continue
        if free and rec['occupied']:
            continue
        rows.append(rec)

    if as_json:
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    click.echo(f"{'id':>3}  {'name (EN)':<16} {'keyitem':>7}  {'file-id':>8}  {'dat':<18} status")
    click.echo('-' * 72)
    for r in rows:
        real = M.is_real_mount(r)
        status = ('retail' if r['id'] < M.RETAIL_COUNT and real
                  else 'custom' if real
                  else 'occupied' if r['occupied']
                  else 'free')
        col = {'retail': 'white', 'custom': 'green', 'occupied': 'yellow', 'free': 'cyan'}[status]
        name = r['name_en'] or ('—' if not r['occupied'] else '(no name)')
        click.echo(f"{r['id']:>3}  {name:<16} {r['key_item']:>7}  {r['file_id_hex']:>8}  "
                   f"{(r['model_dat'] or '—'):<18} " + click.style(status, fg=col))
    click.echo('-' * 72)
    click.echo(f"{len(rows)} shown.  free menu-able ids live in 39-62 "
               f"(63 = chocobo placeholder; >63 needs a client patch).")
