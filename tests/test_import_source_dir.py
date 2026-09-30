"""``--source-dir`` on the imports that find their input in the export folder (tex, mesh, gear,
zone, anim; `ui tex si` has its own test in test_ui_tex_si_source_dir.py): the folder given is
searched instead of exports/, for the same file names. Synthetic folders; no install needed."""
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from xi.entity.anim import xi_import as anim_import
from xi.entity.mesh import xi_import as mesh_import
from xi.gear import xi_import as gear_import
from xi.tex import xi_import as tex_import
from xi.ui import xi_simple
from xi.zone import xi_import as zone_import

DAT = Path("ROM/351/102.DAT")   # never opened: its stem names the export


def test_mesh_finds_the_model_in_source_dir(tmp_path):
    (tmp_path / "102.glb").write_bytes(b"")
    assert mesh_import.default_model_path(DAT, tmp_path) == tmp_path / "102.glb"
    (tmp_path / "102.fbx").write_bytes(b"")
    assert mesh_import.default_model_path(DAT, tmp_path) == tmp_path / "102.fbx"   # the export folder's order


def test_mesh_says_where_it_looked(tmp_path):
    dat = tmp_path / "102.DAT"
    dat.write_bytes(b"")
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "other.glb").write_bytes(b"")
    r = CliRunner().invoke(mesh_import.cmd, [str(dat), "--source-dir", str(empty)])
    assert r.exit_code != 0 and f"none found at {empty}" in r.output


def test_gear_finds_the_glb_in_source_dir(tmp_path):
    (tmp_path / "102.gltf").write_bytes(b"")
    assert gear_import.default_gear_model_path("HumeMale", "body", DAT, tmp_path) == tmp_path / "102.gltf"
    assert gear_import.default_gear_model_path("HumeMale", "body", Path("ROM/1/2.DAT"), tmp_path) is None


def test_zone_prefers_the_stem_then_the_newest(tmp_path):
    older, newer = tmp_path / "a.glb", tmp_path / "b.glb"
    older.write_bytes(b"")
    newer.write_bytes(b"")
    os.utime(older, (1, 1))
    assert zone_import.default_model_path(DAT, tmp_path) == newer
    (tmp_path / "102.gltf").write_bytes(b"")
    assert zone_import.default_model_path(DAT, tmp_path) == tmp_path / "102.gltf"


def test_anim_searches_only_source_dir(tmp_path):
    clip = tmp_path / "102_yap" / "yap.gltf"
    clip.parent.mkdir()
    clip.write_bytes(b"")
    matches, roots = anim_import._find_layer_gltf("yap", DAT, tmp_path)
    assert matches == [clip] and roots == [tmp_path]


@pytest.mark.parametrize("cmd", [tex_import.import_cmd, mesh_import.cmd, gear_import.cmd,
                                 zone_import.cmd, anim_import.cmd, xi_simple.simple_import_cmd])
def test_every_import_takes_source_dir(cmd):
    r = CliRunner().invoke(cmd, ["--help"])
    assert r.exit_code == 0 and "--source-dir" in r.output


def test_tex_import_keeps_dir_as_the_older_name():
    png_dir = next(p for p in tex_import.import_cmd.params if p.name == "png_dir")
    assert png_dir.opts == ["--source-dir", "--dir"]
