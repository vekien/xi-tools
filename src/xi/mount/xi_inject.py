import os
from pathlib import Path

import click

from xi.mount import xi_core as M

PATCHES_DIR = Path(__file__).resolve().parents[3] / 'patches'


@click.command('inject')
@click.option('--id', 'mount_id', type=int, required=True, help='Mount id (39-62 recommended).')
@click.option('--dat', 'dat_src', required=True,
              help='Mount model .DAT — ROM-relative to FFXI_DIR (e.g. ROM10/10/1.DAT) '
                   'or an absolute/CWD path.')
@click.option('--name', 'name_en', required=True, help='English mount name (the menu label).')
@click.option('--name-jp', default=None, help='Japanese mount name (default: same as EN).')
@click.option('--help-en', default=None, help='English mount help text.')
@click.option('--help-jp', default=None, help='Japanese mount help text.')
@click.option('--ki-name', default=None, help='Key-item name (default: "<name> Companion").')
@click.option('--ki-name-jp', default=None, help='Japanese key-item name.')
@click.option('--ki-desc', default=None, help='English key-item description.')
@click.option('--ki-desc-jp', default=None, help='Japanese key-item description.')
@click.option('--no-keyitem', is_flag=True, help='Skip the key-item name DATs (cosmetic only).')
@click.option('--force', is_flag=True, help='Allow overwriting a retail mount (0-38).')
@click.option('--dry-run', is_flag=True, help='Show the plan without writing.')
def cmd(mount_id, dat_src, name_en, name_jp, help_en, help_jp, ki_name, ki_name_jp,
        ki_desc, ki_desc_jp, no_keyitem, force, dry_run):
    """Inject a brand-new custom mount: model + EN/JP names + key-item text + server bundle."""
    if not (0 <= mount_id <= M.MODEL_CAP):
        raise click.ClickException(f'mount id must be 0-{M.MODEL_CAP}')
    try:
        dat_src = M.resolve_input_dat(dat_src)
    except M.MountError as e:
        raise click.ClickException(str(e))

    rec = M.read_record(mount_id)
    # Retail mounts (0-38) are protected; custom ids (39+) overwrite freely.
    if mount_id < M.RETAIL_COUNT and not force:
        raise click.ClickException(
            f'mount {mount_id} is a retail mount ({rec["name_en"] or "reserved"}); '
            'use `mount import` to reskin it, or --force.')

    name_jp = name_jp or name_en
    ki_name = ki_name or f'{name_en} Companion'
    ki_name_jp = ki_name_jp or ki_name

    click.echo('=' * 64)
    click.echo(f'  xi mount inject — id {mount_id}  "{name_en}"' + ('  [DRY RUN]' if dry_run else ''))
    click.echo('=' * 64)

    if rec['occupied']:
        click.echo(f"  (overwriting mount {mount_id}, currently {rec['model_dat']})")
    try:
        info = M.register_model(mount_id, dat_src, dry_run=dry_run)
    except M.MountError as e:
        raise click.ClickException(str(e))
    click.echo(f"  model   : {os.path.basename(dat_src)} -> {info['rom_rel']}"
               + ('  (in place)' if info['in_place']
                  else '  (copied)' if info['copied']
                  else '  (would copy)' if dry_run else ''))
    click.echo(f"  ftable  : file-id 0x{info['file_id']:05X} = {info['ftval']} (vtable {M.CUSTOM_ROM_IDX}) + base")

    try:
        for lang, txt in (('en', name_en), ('jp', name_jp)):
            M.set_mount_name(mount_id, lang, txt, dry_run=dry_run)
        click.echo(f"  name    : EN {name_en!r}  JP {name_jp!r}  (index {mount_id})")
        for lang, txt in (('en', help_en), ('jp', help_jp)):
            if txt:
                M.set_mount_name(mount_id, lang, txt, help_text=True, dry_run=dry_run)
        if help_en or help_jp:
            click.echo(f"  help    : EN {help_en or '—'!r}  JP {help_jp or '—'!r}")

        if not no_keyitem:
            M.set_key_item(mount_id, 'en', ki_name, desc=ki_desc or '', dry_run=dry_run)
            M.set_key_item(mount_id, 'jp', ki_name_jp, desc=ki_desc_jp or '', dry_run=dry_run)
            click.echo(f"  keyitem : {rec['key_item']}  EN {ki_name!r}  JP {ki_name_jp!r}")
    except M.MountError as e:
        raise click.ClickException(str(e))

    slug = name_en
    bundle = M.server_bundle(mount_id, slug, name_en)
    if not dry_run:
        PATCHES_DIR.mkdir(exist_ok=True)
        out = PATCHES_DIR / f'xi_mount_{mount_id}.lua'
        out.write_text(bundle, encoding='utf-8')
        click.echo(f"  server  : {out}")
    else:
        click.echo("  server  : (bundle would be written to patches/)")

    click.echo('-' * 64)
    if mount_id >= M.MENU_CAP:
        click.echo(click.style(
            f"  ⚠ id {mount_id} >= {M.MENU_CAP}: rideable, but the native mount menu can't "
            "show it without the xidats client patch.", fg='yellow'))
    click.echo(click.style('  Dry run — nothing written.' if dry_run else
                           f'  ✓ Mount {mount_id} injected. Grant key item {rec["key_item"]} to test.',
                           fg='cyan' if dry_run else 'green'))
