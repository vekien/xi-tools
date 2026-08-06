import click

from xi.mount import xi_core as M
from xi.ftable.xi_delete import delete_entry


@click.command('delete')
@click.argument('mount_id', type=int)
@click.option('--keep-strings', is_flag=True, help='Only unregister the model; leave names/key-item.')
@click.option('--force', is_flag=True, help='Allow deleting a retail mount (0-38).')
@click.option('--dry-run', is_flag=True, help='Show the plan without writing.')
def cmd(mount_id, keep_strings, force, dry_run):
    """Remove a custom mount: unregister the model and clear its name/help/key-item.

    Refuses retail ids (0-38) without --force. (delete_entry is itself guarded so
    it will not zero a non-custom FTABLE entry.)
    """
    if not (0 <= mount_id <= M.MODEL_CAP):
        raise click.ClickException(f'mount id must be 0-{M.MODEL_CAP}')
    rec = M.read_record(mount_id)
    if mount_id < M.RETAIL_COUNT and not force:
        raise click.ClickException(
            f'mount {mount_id} is retail ({rec["name_en"] or "reserved"}); pass --force to delete.')
    if not rec['occupied'] and not rec['name_en']:
        click.echo(f'mount {mount_id} is already empty.')
        return

    click.echo(f'mount delete {mount_id} ({rec["name_en"] or "—"})'
               + ('  [DRY RUN]' if dry_run else ''))
    # Model: zero the FTABLE entry (guarded against retail by delete_entry itself).
    if rec['occupied']:
        delete_entry(rec['file_id'], dry_run=dry_run)
    if not keep_strings:
        paths = M.clear_mount_strings(mount_id, dry_run=dry_run)
        click.echo(f"  strings : cleared name/help/key-item across {len(paths)} table(s)")
    click.echo(click.style('  Dry run — nothing written.' if dry_run else '  ✓ Mount removed.',
                           fg='cyan' if dry_run else 'green'))
