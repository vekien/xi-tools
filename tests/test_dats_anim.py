"""The ``anim`` action (xi.entity.anim.xi_build, schema/anim.json): a clip and the routine
that plays it put into a model DAT, in the install, the pivot folder and, with
``xi dats build --hd``, the HD folder's own copy — on a synthetic goblin model, a gesture
bank and an HD copy of the model, in the synthetic game folder of test_dats_database_build."""
import base64
import hashlib
import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from _minischema import errors, load
from test_dats_database_build import game  # noqa: F401 — the fixture
from xi.common.xi_section import encode_section_meta
from xi.entity.anim import xi_build as AB
from xi.entity.anim.xi_import import _encode_animation_section

MODEL, BANK = "ROM/3/59.DAT", "ROM/322/95.DAT"


# ── synthetic DATs ───────────────────────────────────────────────────────────

def section(name: bytes, tc: int, body: bytes) -> bytes:
    size = (16 + len(body) + 15) & ~15
    out = name.ljust(4, b"\x00") + struct.pack("<I", encode_section_meta(size, tc)) + b"\x00" * 8 + body
    return out + b"\x00" * (size - len(out))


def skeleton(n: int = 2) -> bytes:
    body = b"\x00\x00" + bytes([n]) + b"\x00"
    for i in range(n):
        body += bytes([max(0, i - 1), 0]) + struct.pack("<4f", 0, 0, 0, 1) + struct.pack("<3f", 0, float(i), 0)
    return section(b"gob_", 0x29, body)


def clip(name: str, frames: int = 4, joints=(0, 1), turn: float = 0.0) -> bytes:
    entries = []
    for j in joints:
        rots = [(0.0, turn * f / frames, 0.0, 1.0) for f in range(frames)]
        entries.append((j, rots, [(0.0, 0.0, 0.0)] * frames, [(1.0, 1.0, 1.0)] * frames))
    return _encode_animation_section(name, 0, frames, 0.5, entries)


def routine(tag: bytes, ref: bytes, dur: int = 20) -> bytes:
    """A clean single-clip 0x07 routine: sec2 (section-relative 0x30) = one 0x05 play + end."""
    body = bytearray(0x20)
    struct.pack_into("<4I", body, 0x10, 0x20, 0x30, 0, 0)
    play = bytearray(32)
    play[0], play[1] = 0x05, 8
    struct.pack_into("<H", play, 6, dur)
    play[8:12] = ref
    body += play + bytes([0x00, 0x01, 0x00, 0x00])
    return section(tag, 0x07, bytes(body))


def model(extra: bytes = b"") -> bytes:
    return skeleton() + clip("idl0") + clip("wlk0") + routine(b"corp", b"idl?") + extra


def plays_routine(tag: bytes, refs=(b"wlk?", b"idl?")) -> bytes:
    """A routine of markers and plays only, as the goblin's (ROM/3/59 @tl0): a start marker,
    40-byte plays back to back, the end; sec1/sec2/sec3 = 64/80/176 and totalDelay the sum of
    the delays, as measured there."""
    marker = bytes([0x01, 0x02, 0, 0, 0, 0, 0, 0])
    plays = b""
    for i, ref in enumerate(refs):
        e = bytearray(40)
        e[0], e[1] = 0x05, 0x0A
        struct.pack_into("<HH", e, 4, 15 if i == 0 else 80, 80)
        e[8:12] = ref
        struct.pack_into("<2f", e, 16, 1.0, 1.0)
        struct.pack_into("<HHHH", e, 24, 30, 0, 30, 0x1001 if i == 0 else 1)
        plays += e
    sec3 = 0x50 + len(marker) + len(plays) + 8
    body = bytearray(0x40)
    struct.pack_into("<4I", body, 0x10, 64, 80, sec3, 15 + 80 * (len(refs) - 1))
    body += marker + plays + bytes([0x00, 0x02, 0, 0, 0, 0, 0, 0]) + bytes([1]) + bytes(15)
    return section(tag, 0x07, bytes(body))


def bank() -> bytes:
    return clip("q4s0", 6, turn=0.5) + clip("q161", 8, turn=0.25) + clip("big0", 3, joints=(0, 5))


def put(root: Path, rel: str, data: bytes) -> Path:
    p = root / Path(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def names(data: bytes, tc: int) -> list[str]:
    from xi.entity.anim.xi_export import parse_sections
    return [AB._label(s.name) for s in parse_sections(data) if s.type_code == tc]


def sec(data: bytes, tc: int, name: str) -> bytes:
    s = AB._find(data, tc, name)
    assert s is not None, f"no {name!r} ({tc:#x})"
    return data[s.start:s.start + s.size]


def play_cmd(data: bytes, tag: str) -> dict:
    from xi.event.xi_event import _routine_sec2_commands
    return next(c for c in _routine_sec2_commands(data, tag) if c["op"] == 0x05)


HD_TEX = section(b"tex_", 0x20, b"HD pack texture" * 4)     # what makes the HD copy the HD pack's


@pytest.fixture
def gob(game: Path, tmp_path: Path, monkeypatch):
    """The install's goblin + bank, and an HD folder holding its own goblin (with an extra
    section the install's lacks). FFXI_HD_DIR always points into tmp_path."""
    import xi.xi_config as cfg
    put(game, MODEL, model())
    put(game, BANK, bank())
    hd = tmp_path / "hd"
    put(hd, MODEL, model(HD_TEX))
    monkeypatch.setattr(cfg, "FFXI_HD_DIR", str(hd))
    return {"game": game, "hd": hd, "model": game / Path(*MODEL.split("/")), "hd_model": hd / Path(*MODEL.split("/"))}


def run(*args):
    from xi.dats.xi_dats import group
    return CliRunner().invoke(group, list(args), catch_exceptions=False)


def action(project="goblin") -> dict:
    from xi.dats.xi_dats import _read_manifest
    return _read_manifest(Path(f"projects/{project}.json"))["actions"][0]


def prepare_bank(*extra, project="goblin"):
    r = run("prepare", "ROM/322/95", "--type", "anim", "--source-clip", "q4s0", "--target", "ROM/3/59",
            "--clip", "gbw0", "--project", project, *extra)
    assert r.exit_code == 0, r.output
    return r


# ── schema and validation ────────────────────────────────────────────────────

def test_schema_examples_validate_and_package_lists_the_type():
    schema = load("anim.json")
    for example in schema["examples"]:
        assert AB.validate_action(example) == [] and errors(schema, example) == []
    assert any(e.get("$ref") == "anim.json" for e in load("package.json")["$defs"]["entry"]["oneOf"])


def test_validation_names_the_field():
    base = {"id": "anim.x", "type": "anim", "target": {"dat": "ROM/3/59.DAT", "animation": "gbw0"},
            "source": {"dat": "ROM/322/95.DAT", "clip": "q4s0"}}
    assert AB.validate_action(base) == []
    both = {**base, "resources": {"animation": "a.gltf"}}
    assert any("either" in e for e in AB.validate_action(both))
    assert AB.validate_action({**base, "target": {"dat": "ROM/3/59.DAT", "animation": "toolong"}}) == [
        "target.animation must be the clip's name in the model: 1-4 letters, digits or '_' "
        "(gbw0; a digit-less name is slot 0)"]
    assert AB.validate_action({**base, "schedule": {"loops": -1}}) == [
        "schedule.loops must be 0 (loop until something else plays) to 65535"]
    assert "a .glb isn't read" in AB.validate_action(
        {k: v for k, v in base.items() if k != "source"} | {"resources": {"animation": "a.glb"}})[0]
    assert AB.rom_rel("rom\\3\\59") == "ROM/3/59.DAT" and AB.rom_rel("ROM10/2/0.dat") == "ROM10/2/0.DAT"
    assert AB.rom_rel("exports/x.gltf") is None


# ── build / rebuild / undo ───────────────────────────────────────────────────

def test_a_bank_clip_goes_into_the_install_with_a_routine_and_undo_takes_it_out(gob):
    r = prepare_bank()
    a = action()
    assert a == {"id": "anim.rom_3_59.gbw0", "type": "anim", "target": {"dat": MODEL, "animation": "gbw0"},
                 "source": {"dat": BANK, "clip": "q4s0"}}
    retail = gob["model"].read_bytes()
    r = run("build", "goblin")
    assert r.exit_code == 0, r.output
    data = gob["model"].read_bytes()
    assert gob["model"].with_name("59.DAT.base").read_bytes() == retail
    assert names(data, 0x2B) == ["idl0", "wlk0", "gbw0"]
    assert sec(data, 0x2B, "gbw0")[4:] == sec(bank(), 0x2B, "q4s0")[4:]       # the bank's bytes, renamed
    cmd = play_cmd(data, "gbw0")
    assert cmd["ref"] == "gbw?" and cmd["maxLoops"] == 0 and cmd["transIn"] == cmd["transOut"] == 15
    assert cmd["dur"] == 20                       # twice the clip's (6 - 1) / 0.5 frames: natural speed
    # The HD folder has its own goblin, which the client loads instead: the build says so.
    assert "has its own ROM/3/59.DAT" in r.output and "--hd" in r.output
    res = action()["result"]
    assert res["targets"] == ["dir"]
    recs = res["roots"]["dir"]
    assert [(e["kind"], e["name"], e["retail"]) for e in recs] == [("clip", "gbw0", False), ("routine", "gbw0", False)]
    assert recs[0]["sha1"] == hashlib.sha1(sec(data, 0x2B, "gbw0")).hexdigest()
    assert recs[1]["plays"] == "gbw0" and recs[1]["loops"] == 0
    # The HD copy is untouched without --hd.
    assert gob["hd_model"].read_bytes() == model(HD_TEX)
    r = run("undo", "goblin", "--yes", "--keep-json")
    assert r.exit_code == 0, r.output
    assert gob["model"].read_bytes() == retail


def test_hd_puts_the_clip_into_the_hd_copy_and_keeps_its_own_bytes(gob):
    prepare_bank()
    r = run("build", "goblin", "--hd")
    assert r.exit_code == 0, r.output
    assert "HD overlay ->" in r.output and "--hd to change it too" not in r.output
    hd = gob["hd_model"].read_bytes()
    assert sec(hd, 0x20, "tex_") == HD_TEX                       # still the HD pack's DAT
    assert names(hd, 0x2B) == ["idl0", "wlk0", "gbw0"] and play_cmd(hd, "gbw0")["ref"] == "gbw?"
    assert gob["hd_model"].with_name("59.DAT.base").read_bytes() == model(HD_TEX)
    assert "gbw0" in names(gob["model"].read_bytes(), 0x2B)
    res = action()["result"]
    assert res["targets"] == ["dir", "hd"] and set(res["roots"]) == {"dir", "hd"}
    r = run("undo", "goblin", "--yes")
    assert r.exit_code == 0, r.output
    assert gob["hd_model"].read_bytes() == model(HD_TEX) and gob["model"].read_bytes() == model()
    assert not Path("projects/goblin.json").exists()


def test_a_model_the_hd_folder_has_no_copy_of_is_left_alone_there(gob):
    gob["hd_model"].unlink()
    prepare_bank()
    r = run("build", "goblin", "--hd")
    assert r.exit_code == 0, r.output
    assert "left alone" in r.output and not gob["hd_model"].exists()
    assert action()["result"]["targets"] == ["dir"]


def test_dry_run_writes_nothing_and_previews_each_target(gob):
    prepare_bank()
    r = run("build", "goblin", "--hd", "--dry-run")
    assert r.exit_code == 0, r.output
    assert r.output.count("anim.rom_3_59.gbw0 (anim) [") == 2 and "Dry run" in r.output
    assert gob["model"].read_bytes() == model() and gob["hd_model"].read_bytes() == model(HD_TEX)
    assert not gob["model"].with_name("59.DAT.base").exists()
    assert "result" not in action()


def test_a_rebuild_on_top_changes_nothing_and_a_rename_leaves_nothing_behind(gob):
    prepare_bank()
    assert run("build", "goblin", "--hd").exit_code == 0
    once, hd_once = gob["model"].read_bytes(), gob["hd_model"].read_bytes()
    r = run("build", "goblin", "--hd")
    assert r.exit_code == 0 and "already built" in r.output
    assert gob["model"].read_bytes() == once and gob["hd_model"].read_bytes() == hd_once
    # Re-prepared under a new name: the result survives, and the next build takes the old clip out.
    r = run("prepare", "ROM/322/95", "--type", "anim", "--source-clip", "q161", "--id", "anim.rom_3_59.gbw0",
            "--clip", "gch0", "--loops", "1", "--project", "goblin", "--replace")
    assert r.exit_code == 0, r.output
    a = action()
    assert a["target"] == {"dat": MODEL, "animation": "gch0"} and a["schedule"] == {"loops": 1}
    assert set(a["result"]["roots"]) == {"dir", "hd"}
    assert run("build", "goblin", "--hd").exit_code == 0
    for p in (gob["model"], gob["hd_model"]):
        data = p.read_bytes()
        assert names(data, 0x2B) == ["idl0", "wlk0", "gch0"] and names(data, 0x07) == ["corp", "gch0"]
        assert play_cmd(data, "gch0")["maxLoops"] == 1
    assert run("undo", "goblin", "--yes").exit_code == 0
    assert gob["model"].read_bytes() == model() and gob["hd_model"].read_bytes() == model(HD_TEX)


def test_reset_puts_each_copy_back_to_its_own_base_first(gob):
    prepare_bank()
    assert run("build", "goblin", "--hd").exit_code == 0
    # An edit made outside the project, in both copies: --reset takes it away.
    for p in (gob["model"], gob["hd_model"]):
        data = p.read_bytes()
        s = AB._find(data, 0x2B, "wlk0")
        p.write_bytes(data[:s.start] + clip("wlk0", 9) + data[s.start + s.size:])
    r = run("build", "goblin", "--hd", "--reset")
    assert r.exit_code == 0, r.output
    assert "in the HD folder from its own .base" in r.output
    for p, orig in ((gob["model"], model()), (gob["hd_model"], model(HD_TEX))):
        data = p.read_bytes()
        assert sec(data, 0x2B, "wlk0") == sec(orig, 0x2B, "wlk0") and "gbw0" in names(data, 0x2B)
    assert sec(gob["hd_model"].read_bytes(), 0x20, "tex_") == HD_TEX


def test_a_pivot_build_seeds_its_copy_and_undo_removes_it(gob, tmp_path, monkeypatch):
    import xi.xi_config as cfg
    pivot = tmp_path / "pivot"
    pivot.mkdir()
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    prepare_bank()
    r = run("build", "goblin", "--pivot")
    assert r.exit_code == 0, r.output
    placed = pivot / "ROM" / "3" / "59.DAT"
    assert "gbw0" in names(placed.read_bytes(), 0x2B) and gob["model"].read_bytes() == model()
    assert all(e.get("seeded") for e in action()["result"]["roots"]["pivot"])
    assert run("undo", "goblin", "--yes").exit_code == 0
    assert not placed.exists()


def test_a_routine_tag_and_no_routine(gob):
    prepare_bank("--tag", "wave", "--blend", "5")
    assert run("build", "goblin").exit_code == 0
    data = gob["model"].read_bytes()
    assert names(data, 0x07) == ["corp", "wave"] and play_cmd(data, "wave")["transIn"] == 5
    prepare_bank("--no-schedule", "--replace")
    assert action()["schedule"] is False
    assert run("build", "goblin").exit_code == 0
    assert names(gob["model"].read_bytes(), 0x07) == ["corp"]


def test_replacing_a_retail_clip_grows_its_routine_and_undo_puts_both_back(gob):
    r = run("prepare", "ROM/322/95", "--type", "anim", "--source-clip", "q161", "--target", "ROM/3/59",
            "--clip", "idl0", "--project", "goblin")
    assert r.exit_code == 0, r.output
    r = run("build", "goblin")
    assert r.exit_code == 0, r.output
    data = gob["model"].read_bytes()
    assert names(data, 0x07) == ["corp"]                       # a replaced clip gets no routine of its own
    assert play_cmd(data, "corp")["dur"] == 28                 # grown to 2 x (8 - 1) / 0.5
    assert "replaces the model's own clip 'idl0'" in r.output
    recs = action()["result"]["roots"]["dir"]
    assert [(e["kind"], e["name"], e["retail"]) for e in recs] == [("clip", "idl0", True), ("routine", "corp", True)]
    assert run("undo", "goblin", "--yes").exit_code == 0
    assert gob["model"].read_bytes() == model()


def test_a_section_changed_since_the_build_is_left_by_undo(gob):
    prepare_bank()
    assert run("build", "goblin").exit_code == 0
    data = gob["model"].read_bytes()
    s = AB._find(data, 0x2B, "gbw0")
    gob["model"].write_bytes(data[:s.start] + clip("gbw0", 5) + data[s.start + s.size:])
    r = run("undo", "goblin", "--yes")
    assert r.exit_code == 0
    assert "the clip 'gbw0' changed since the last build; left as it is" in r.output
    data = gob["model"].read_bytes()
    assert "gbw0" in names(data, 0x2B) and names(data, 0x07) == ["corp"]


def test_errors_say_what_to_change(gob):
    r = run("prepare", "ROM/322/95", "--type", "anim", "--target", "ROM/3/59", "--project", "goblin")
    assert r.exit_code != 0 and "holds 3 clips (q4s0, q161, big0): pass --source-clip" in r.output
    r = run("prepare", "ROM/322/95", "--type", "anim", "--source-clip", "q4s0", "--project", "goblin")
    assert r.exit_code != 0 and "pass --target" in r.output
    r = run("prepare", "ROM/322/95", "--type", "anim", "--source-clip", "big0", "--target", "ROM/3/59",
            "--project", "goblin")
    assert r.exit_code == 0
    r = run("build", "goblin")
    assert r.exit_code != 0 and "drives joint 5, but ROM/3/59.DAT's skeleton has 2 joints" in r.output
    assert gob["model"].read_bytes() == model()
    r = run("prepare", "nowhere.json", "--project", "goblin")
    assert r.exit_code != 0 and "does not exist" in r.output


# ── a glTF source ────────────────────────────────────────────────────────────

def gltf(path: Path, name: str = "wave") -> Path:
    """Two bone nodes and one animation turning bone0001 over a second."""
    times = [0.0, 0.5, 1.0]
    quats = [(0.0, 0.0, 0.0, 1.0), (0.0, 0.3826834, 0.0, 0.9238795), (0.0, 0.7071068, 0.0, 0.7071068)]
    blob = struct.pack("<3f", *times) + b"".join(struct.pack("<4f", *q) for q in quats)
    doc = {"asset": {"version": "2.0"},
           "nodes": [{"name": "bone0000", "children": [1]}, {"name": "bone0001", "translation": [0, 1, 0]}],
           "buffers": [{"byteLength": len(blob),
                        "uri": "data:application/octet-stream;base64," + base64.b64encode(blob).decode()}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 12},
                           {"buffer": 0, "byteOffset": 12, "byteLength": 48}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "SCALAR",
                          "min": [0.0], "max": [1.0]},
                         {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC4"}],
           "animations": [{"name": name, "samplers": [{"input": 0, "output": 1, "interpolation": "LINEAR"}],
                           "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}}]}]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_a_gltf_clip_is_built_into_each_copy(gob, tmp_path):
    src = gltf(tmp_path / "exports" / "anim" / "59_wave0.gltf")
    r = run("prepare", str(src), "--target", "ROM/3/59", "--project", "goblin")     # .gltf -> anim
    assert r.exit_code == 0, r.output
    a = action()
    assert a["target"] == {"dat": MODEL, "animation": "wave"}           # 59_wave0 -> its last part, 4 chars
    assert a["resources"] == {"animation": "../exports/anim/59_wave0.gltf"}
    r = run("build", "goblin", "--hd")
    assert r.exit_code == 0, r.output
    for p in (gob["model"], gob["hd_model"]):
        data = p.read_bytes()
        assert names(data, 0x2B) == ["idl0", "wlk0", "wave"] and names(data, 0x07) == ["corp", "wave"]
    hd = gob["hd_model"].read_bytes()
    assert sec(hd, 0x20, "tex_") == HD_TEX
    # The same clip in both copies: built from the same glTF on the same rig, 1 s at 15 fps.
    assert sec(hd, 0x2B, "wave") == sec(gob["model"].read_bytes(), 0x2B, "wave")
    assert action()["result"]["roots"]["dir"][0]["frames"] == 16


def test_a_model_with_only_a_plays_routine_gets_one_cut_down_to_its_clip(gob):
    """The goblin's case: no single-clip routine to clone, only @tl0 (wlk? then idl?). The new
    routine is that one with its run of plays swapped for one play of the new clip."""
    from xi.event.xi_event import _routine_sec2_commands
    goblin = skeleton() + clip("idl0") + clip("wlk0") + plays_routine(b"@tl0")
    gob["model"].write_bytes(goblin)
    assert [c["op"] for c in _routine_sec2_commands(goblin, "@tl0")] == [0x01, 0x05, 0x05, 0x00]
    prepare_bank("--loops", "1")
    r = run("build", "goblin")
    assert r.exit_code == 0, r.output
    data = gob["model"].read_bytes()
    assert names(data, 0x07) == ["@tl0", "gbw0"] and sec(data, 0x07, "@tl0") == sec(goblin, 0x07, "@tl0")
    cmds = _routine_sec2_commands(data, "gbw0")
    assert [(c["op"], c["ref"]) for c in cmds] == [(0x01, None), (0x05, "gbw?"), (0x00, None)]
    play = cmds[1]
    assert (play["delay"], play["dur"], play["maxLoops"], play["transIn"], play["transOut"]) == (0, 20, 1, 15, 15)
    new, old = sec(data, 0x07, "gbw0"), sec(goblin, 0x07, "@tl0")
    assert struct.unpack_from("<4I", new, 0x20) == (64, 80, 176 - 40, 0)
    assert len(new) == 160 and new[-24:-8] == old[-16:]            # sec3 kept, after the one play
    assert run("undo", "goblin", "--yes").exit_code == 0
    assert gob["model"].read_bytes() == goblin
