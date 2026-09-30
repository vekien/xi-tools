"""`xi ui tex si --dir DIR` (xi.ui.xi_simple): the PNGs of another folder imported into a UI DAT,
from a temporary copy seeded with the DAT's own DDS, so the folder is never written to. On a
copy of the install's title screen sheet (ROM/119/50.DAT); skips without FFXI_DIR."""
import shutil
from pathlib import Path

from click.testing import CliRunner

from xi.ui import xi_simple as S
from xi.ui.xi_core import compression_name, parse_textures

SHEET = "ROM/119/50.DAT"


def _copy_sheet(root: Path, tmp_path: Path) -> Path:
    game = tmp_path / "game"
    (game / "ROM" / "119").mkdir(parents=True)
    src = root / SHEET
    base = src.with_name(src.name + ".base")
    shutil.copy2(base if base.is_file() else src, game / SHEET)
    return game


def test_dir_imports_from_a_folder_it_leaves_alone(root, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    game = _copy_sheet(root, tmp_path)
    before = parse_textures(bytearray((game / SHEET).read_bytes()))
    r = CliRunner().invoke(S.simple_extract_cmd, [SHEET, "--ffxi", str(game)], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    title = tmp_path / "title"
    title.mkdir()
    for p in (tmp_path / "exports" / "ui" / "119" / "50").iterdir():
        if p.suffix == ".png" or p.name == S.ALPHA_SIDECAR:
            shutil.copy2(p, title / p.name)
    shutil.rmtree(tmp_path / "exports")
    listing = sorted(p.name for p in title.iterdir())

    r = CliRunner().invoke(S.simple_import_cmd, [SHEET, "--ffxi", str(game), "--dir", str(title)],
                           catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert f"patched {len(before)} texture(s)" in r.output
    assert sorted(p.name for p in title.iterdir()) == listing          # no DDS, no .alpha left behind
    assert not (tmp_path / "exports").exists()                          # nor in the working folder
    after = parse_textures(bytearray((game / SHEET).read_bytes()))
    assert [(t.name, t.width, t.height, compression_name(t)) for t in after] == \
           [(t.name, t.width, t.height, compression_name(t)) for t in before]   # formats kept

    once = (game / SHEET).read_bytes()
    r = CliRunner().invoke(S.simple_import_cmd, [SHEET, "--ffxi", str(game), "--dir", str(title)],
                           catch_exceptions=False)
    assert r.exit_code == 0 and (game / SHEET).read_bytes() == once     # the same PNGs, the same DAT


def test_dir_needs_pngs_and_not_all_themes(root, tmp_path):
    game = _copy_sheet(root, tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    r = CliRunner().invoke(S.simple_import_cmd, [SHEET, "--ffxi", str(game), "--dir", str(empty)])
    assert r.exit_code != 0 and "No .png files found" in r.output
    r = CliRunner().invoke(S.simple_import_cmd,
                           [SHEET, "--ffxi", str(game), "--dir", str(empty), "--all-themes"])
    assert r.exit_code != 0 and "cannot be used together" in r.output
