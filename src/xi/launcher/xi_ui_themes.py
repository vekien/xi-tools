import shutil
import zipfile
from pathlib import Path

import click

from xi.xi_config import XI_TOOLS_DIR, FFXI_DIR, read_path_for

# The 8 window-skin DATs (win0): ROM/0/14..21
WIN0_THEME_IDS = list(range(14, 22))


@click.command('ui-themes')
@click.argument('name')
@click.option('--no-zip', is_flag=True, help='Skip creating the .zip archive.')
@click.option('--force', is_flag=True, help='Overwrite an existing folder/zip of the same name.')
def cmd(name: str, no_zip: bool, force: bool):
    """Package the current window-skin DATs (ROM/0/14..21) as a named theme.

    Copies the 8 skin DATs into launcher/ui-themes/<NAME>/ROM/0/ and zips the
    folder to launcher/ui-themes/<NAME>.zip — ready to release as a variation.
    """
    rom0 = Path(FFXI_DIR) / 'ROM' / '0'
    out_root = Path(XI_TOOLS_DIR) / 'launcher' / 'ui-themes' / name
    dest_rom0 = out_root / 'ROM' / '0'
    zip_path = out_root.with_suffix('.zip')

    if out_root.exists() or zip_path.exists():
        if not force:
            raise click.ClickException(
                f"'{name}' already exists ({out_root} / {zip_path}). Use --force to overwrite."
            )
        if out_root.exists():
            shutil.rmtree(out_root)
        if zip_path.exists():
            zip_path.unlink()

    dest_rom0.mkdir(parents=True, exist_ok=True)

    copied = 0
    missing = []
    for theme_id in WIN0_THEME_IDS:
        # Package the edited skin if one exists in the output mirror, else stock.
        src = Path(read_path_for(rom0 / f'{theme_id}.DAT'))
        if not src.is_file():
            missing.append(src.name)
            continue
        shutil.copy2(src, dest_rom0 / f'{theme_id}.DAT')
        click.echo(f'  copied {src} -> {dest_rom0 / f"{theme_id}.DAT"}')
        copied += 1

    if copied == 0:
        shutil.rmtree(out_root, ignore_errors=True)
        raise click.ClickException(f'No window-skin DATs found under {rom0}')

    click.echo(f'Packaged {copied} theme DAT(s) into {out_root}')
    if missing:
        click.echo(f'Missing (skipped): {", ".join(missing)}')

    if not no_zip:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(out_root.rglob('*')):
                if f.is_file():
                    zf.write(f, f.relative_to(out_root))
        click.echo(f'Zipped -> {zip_path}')
