"""``xi ftable lookup --modelid`` follows the client's four modelid bands
(xi.entity.xi_core.modelid_to_file_id), so a retail monster such as modelid 1 resolves to
file_id 1301, not modelid + 98239."""
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from xi.ftable.xi_lookup import cmd

SIZE = 120_000


def _write_pair(root: Path, entries: dict[int, tuple[int, int]]) -> Path:
    """A base FTABLE/VTABLE pair holding ``{file_id: (subdir, file_idx)}``."""
    fdata = bytearray(SIZE * 2)
    vdata = bytearray(SIZE)
    for fid, (subdir, file_idx) in entries.items():
        struct.pack_into("<H", fdata, fid * 2, (subdir << 7) | file_idx)
        vdata[fid] = 1
    ft = root / "FTABLE.DAT"
    ft.write_bytes(bytes(fdata))
    (root / "VTABLE.DAT").write_bytes(bytes(vdata))
    return ft


@pytest.mark.parametrize(
    "modelid, file_id",
    [
        (1, 1301),          # 0-1499: + 1300
        (1499, 2799),
        (1500, 51795),      # 1500-2999: + 50295
        (3000, 99907),      # 3000-3499: + 96907
        (3500, 101739),     # 3500+: + 98239
        (15000, 113239),    # custom entity range
    ],
)
def test_modelid_uses_the_client_bands(tmp_path: Path, modelid: int, file_id: int):
    ft = _write_pair(tmp_path, {file_id: (5, 3)})
    result = CliRunner().invoke(cmd, [str(ft), "--modelid", str(modelid)])
    assert result.exit_code == 0, result.output
    assert f"file_id      : {file_id}  (= {modelid} + {file_id - modelid})" in result.output
    assert "DAT path     : ROM/5/3.DAT" in result.output


def test_file_id_is_looked_up_as_given(tmp_path: Path):
    ft = _write_pair(tmp_path, {1301: (2, 7)})
    result = CliRunner().invoke(cmd, [str(ft), "--file-id", "1301"])
    assert result.exit_code == 0, result.output
    assert "DAT path     : ROM/2/7.DAT" in result.output
