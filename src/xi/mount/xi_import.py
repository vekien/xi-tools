import os
import click

from xi.mount import xi_core as M


@click.command('import')
@click.argument('mount_id', type=int)
@click.option('--dat', 'dat_src', required=True,
              help='Replacement model .DAT — ROM-relative to FFXI_DIR or an absolute/CWD path.')
@click.option('--name', 'name_en', default=None, help='Also set the English name.')
@click.option('--name-jp', default=None, help='Also set the Japanese name.')
@click.option('--force', is_flag=True, help='Allow reskinning a retail mount (0-38).')
@click.option('--dry-run', is_flag=True, help='Show the plan without writing.')
def cmd(mount_id, dat_src, name_en, name_jp, force, dry_run):
    """Override an EXISTING mount's model (and optionally its name).

    Registers your DAT at the mount's file-id, replacing what it points at. Use
    this to reskin a mount in place; use `mount inject` for a brand-new id.
    """
    if not (0 <= mount_id <= M.MODEL_CAP):
        raise click.ClickException(f'mount id must be 0-{M.MODEL_CAP}')
    try:
        dat_src = M.resolve_input_dat(dat_src)
    except M.MountError as e:
        raise click.ClickException(str(e))
    rec = M.read_record(mount_id)
    if mount_id < M.RETAIL_COUNT and not force:
        raise click.ClickException(
            f'mount {mount_id} is retail ({rec["name_en"] or "reserved"}); pass --force to reskin it.')

    click.echo(f'mount import {mount_id} ({rec["name_en"] or "—"})'
               + ('  [DRY RUN]' if dry_run else ''))
    info = M.register_model(mount_id, dat_src, dry_run=dry_run)
    click.echo(f"  model : {os.path.basename(dat_src)} -> {info['rom_rel']}  "
               f"(file-id 0x{info['file_id']:05X})")
    try:
        if name_en:
            M.set_mount_name(mount_id, 'en', name_en, dry_run=dry_run)
            M.set_mount_name(mount_id, 'jp', name_jp or name_en, dry_run=dry_run)
            click.echo(f"  name  : EN {name_en!r}  JP {(name_jp or name_en)!r}")
    except M.MountError as e:
        raise click.ClickException(str(e))
    click.echo(click.style('  Dry run — nothing written.' if dry_run else '  ✓ Model replaced.',
                           fg='cyan' if dry_run else 'green'))
