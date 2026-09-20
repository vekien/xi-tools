"""The spell / command menu tables of ROM/118/114.DAT (xi.menu.xi_menu_table):
container parse/serialize, record fields, growth past the retail counts, id
allocation, definitions and the string-table writes — all on synthetic bytes."""
import json
import struct
from pathlib import Path

import pytest

from xi.common import xi_dmsg as D
from xi.common.xi_menu_records import encode_record
from xi.menu import xi_menu_table as MT

REPO = Path(__file__).resolve().parents[1]
SPELL_EXAMPLE = json.loads((REPO / "schema" / "spell_definition.json").read_text(encoding="utf-8"))["examples"][0]
COMMAND_EXAMPLE = json.loads((REPO / "schema" / "command_definition.json").read_text(encoding="utf-8"))["examples"][0]


# ── synthetic data ────────────────────────────────────────────────────────────

def spell_record(idx: int, **fields) -> bytes:
    """A decoded mgc_ record shaped like retail's: id, a kind, every job 0xFFFF
    except the ones given, menu index = id."""
    rec = bytearray(0x64)
    struct.pack_into("<24H", rec, MT.SPELL_LEVELS, *([MT.NO_LEVEL] * 24))
    rec = MT.write_fields("spell", bytes(rec), {"id": idx, "kind": 2, "element": 0, "skill": 36,
                                                "mp": 7, "cast": 2, "recast": 8, "menu_index": idx,
                                                "icon": 8, "levels": {"BLM": 13}, **fields})
    return rec


def command_record(idx: int, **fields) -> bytes:
    rec = bytearray(0x30)
    return MT.write_fields("command", bytes(rec), {"id": idx, "type": 1, "icon": 41, "targets": 32,
                                                   "tp": 0xFFFF, "level": 5, "range": 11, **fields})


def menu_dat(n_spells: int = 8, n_commands: int = 6) -> bytes:
    """A 114.DAT with real-looking headers and small mgc_ / comm sections."""
    head = b"menu\x01\x01" + b"\0" * (MT.FILE_HEADER - 6)
    m = MT.MenuDat(head)
    # a filler section first, like retail's mnc2, to prove unknown sections survive
    m.sections.append(MT.Section(b"levc", 4, bytearray(b"\x11" * 0x30)))
    m.sections.append(MT.Section(b"mgc_", 0x49, bytearray(b"".join(
        encode_record(spell_record(i)) for i in range(n_spells)))))
    m.sections.append(MT.Section(b"comm", 0x53, bytearray(b"".join(
        encode_record(command_record(i)) for i in range(n_commands)))))
    return m.serialize()


def dmsg_table(texts: list, stride: int) -> bytes:
    """A plain fixed-stride d_msg table with sub[0] = text per block."""
    subs = [{"flag": 0, "marker": 1, "raw": struct.pack("<I", 1) + b"\0" * D.META_LEN + D._encode_body(t)}
            for t in texts]
    blocks = [bytearray(D._assemble_block([s], stride)) for s in subs]
    prefix = bytearray(D.HEADER_SIZE)
    prefix[:5] = b"d_msg"
    struct.pack_into("<8I", prefix, 0x10, 0, 0, D.HEADER_SIZE, 0, stride, 0, len(blocks), 0)
    return D.serialize(D.DmsgTable(prefix, blocks, 0, stride, D.HEADER_SIZE))


def install(root: Path, n_spells: int = 8, n_commands: int = 6) -> Path:
    """A fake FFXI_DIR holding 114.DAT and the six name/help tables. Writes
    straight under ``root`` — never through the library's path resolution, which
    would follow a configured pivot overlay into the real install."""
    p = root / Path(*MT.MENU_DAT.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(menu_dat(n_spells, n_commands))
    for kind, n in (("spell", n_spells), ("command", n_commands)):
        k = MT.KINDS[kind]
        for lang in MT.LANGS:
            for rom, stride, label in ((k.names[lang], 80, "Name"), (k.help[lang], 256, "Help")):
                path = root / Path(*rom.split("/"))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(dmsg_table([f"{label} {i}" for i in range(n)], stride))
    return root


# ── container ─────────────────────────────────────────────────────────────────

def test_parse_serialize_roundtrip_and_headers():
    data = menu_dat()
    assert MT.verify_roundtrip(data)
    m = MT.parse(data)
    assert [s.tag for s in m.sections] == [b"levc", b"mgc_", b"comm"]
    # section header: tag + u32 (len16 << 7 | type), 8 zero bytes; end section last
    off = MT.FILE_HEADER
    v = struct.unpack_from("<I", data, off + 4)[0]
    assert v & 0x7F == 4 and (v >> 7) * 16 == 0x10 + 0x30
    assert data[-16:] == b"end\0" + struct.pack("<I", 1 << 7) + b"\0" * 8
    assert m.count("spell") == 8 and m.count("command") == 6
    assert MT.read_fields("spell", m.records("spell")[3])["id"] == 3


def test_bad_container_is_rejected():
    with pytest.raises(MT.MenuError):
        MT.parse(b"not a menu")
    with pytest.raises(MT.MenuError):
        MT.parse(menu_dat()[:-16])       # no end section


def test_fields_roundtrip_and_named_values():
    rec = spell_record(5, element="fire", kind="black", levels={"BLM": 20, "RDM": 25})
    f = MT.read_fields("spell", rec)
    assert f["element"] == 0 and f["kind"] == 2 and f["levels"] == {"BLM": 20, "RDM": 25}
    assert MT.read_fields("command", command_record(2, level=30))["level"] == 30
    with pytest.raises(MT.MenuError):
        MT.write_fields("spell", rec, {"nope": 1})
    with pytest.raises(MT.MenuError):
        MT.write_fields("spell", rec, {"levels": {"XYZ": 1}})
    with pytest.raises(MT.MenuError):
        MT.write_fields("spell", rec, {"cast": 300})


def test_grow_keeps_every_retail_record():
    data = menu_dat()
    m = MT.parse(data)
    before = m.records("spell")
    m.set_record("spell", 4095, spell_record(4095))
    out = m.serialize()
    m2 = MT.parse(out)
    assert m2.count("spell") == 4096
    assert m2.records("spell")[:8] == before
    assert MT.is_empty(m2.records("spell")[100])
    assert MT.read_fields("spell", m2.records("spell")[4095])["id"] == 4095
    assert m2.section("comm").body == m.section("comm").body
    assert m2.section("levc").body == b"\x11" * 0x30
    with pytest.raises(MT.MenuError):
        m.set_record("spell", 4096, spell_record(0))


def test_free_ids_and_pick_top_down():
    m = MT.parse(menu_dat())
    assert MT.pick_id("spell", m) == 4095
    assert MT.pick_id("command", m) == 4095
    assert MT.pick_id("spell", m, taken={4095}) == 4094
    m.set_record("spell", 4095, spell_record(4095))
    assert MT.pick_id("spell", m) == 4094
    assert MT.free_ids("spell", m, 0, 9) == [8, 9]
    assert MT.next_menu_index(m.records("spell")) == 4096   # the new record's index


# ── definitions ───────────────────────────────────────────────────────────────

def test_schema_examples_validate():
    assert MT.validate_definition(SPELL_EXAMPLE) == []
    assert MT.validate_definition(COMMAND_EXAMPLE) == []
    assert MT.definition_kind(SPELL_EXAMPLE) == "spell"
    assert MT.definition_kind(COMMAND_EXAMPLE) == "command"


def test_validator_names_the_field():
    bad = dict(SPELL_EXAMPLE, extra=1)
    assert any("unknown key 'extra'" in e for e in MT.validate_definition(bad))
    bad = dict(SPELL_EXAMPLE, fields={"tp": 1})
    assert any("fields.tp is not a spell field" in e for e in MT.validate_definition(bad))
    bad = dict(SPELL_EXAMPLE, text={"name_jp": "x"})
    assert any("text.name_en is required" in e for e in MT.validate_definition(bad))
    bad = dict(SPELL_EXAMPLE, id=-1)
    assert any("id must be" in e for e in MT.validate_definition(bad))
    assert MT.validate_definition({"schema": "xi.nope.v1"})[0].startswith("schema must be")


def test_validator_checks_text_fits_its_block():
    # command names hold 39 bytes, spell names 99, help 215 — a Japanese character counts 2
    ok = dict(COMMAND_EXAMPLE, text={"name_en": "x" * 39, "help_en": "y" * 215})
    assert MT.validate_definition(ok) == []
    long_name = dict(COMMAND_EXAMPLE, text={"name_en": "x" * 40})
    assert any("text.name_en is 40 bytes; a command name holds 39" in e for e in MT.validate_definition(long_name))
    assert MT.validate_definition(dict(SPELL_EXAMPLE, text={"name_en": "x" * 99})) == []
    assert MT.validate_definition(dict(SPELL_EXAMPLE, text={"name_en": "x", "help_en": "y" * 216}))
    jp = dict(COMMAND_EXAMPLE, text={"name_en": "Ok", "name_jp": "あ" * 20})    # 40 bytes in cp932
    assert any("text.name_jp is 40 bytes" in e for e in MT.validate_definition(jp))
    emoji = dict(SPELL_EXAMPLE, text={"name_en": "Fire \U0001F525"})
    assert any("cannot show" in e for e in MT.validate_definition(emoji))


def test_client_warning_names_the_plugin():
    assert MT.client_warning("spell", 1023) is None
    assert "cexislots" in MT.client_warning("spell", 1024) and "spells 0-1023" in MT.client_warning("spell", 1024)
    assert MT.client_warning("command", 1791) is None
    assert "mounts" in MT.client_warning("command", 1792)                   # /ja stops at 0x700
    assert "commands 0-2815" in MT.client_warning("command", 2816)
    assert MT.client_warning("spell").startswith("auto takes an id past what the game reads")


def test_load_definition_rejects_bad_file(tmp_path: Path):
    p = tmp_path / "x.spell.json"
    p.write_text(json.dumps(dict(SPELL_EXAMPLE, like="four")), encoding="utf-8")
    with pytest.raises(MT.MenuError) as e:
        MT.load_definition(p)
    assert "spell_definition.json" in str(e.value) and "like" in str(e.value)


def test_build_record_clones_the_donor():
    m = MT.parse(menu_dat())
    d = dict(SPELL_EXAMPLE, like=3)
    rec = MT.build_record("spell", m, d, 4095, None)
    f = MT.read_fields("spell", rec)
    assert f["id"] == 4095 and f["mp"] == 12 and f["cast"] == 8 and f["recast"] == 40
    assert f["element"] == 0 and f["levels"] == {"BLM": 20, "RDM": 25}
    assert f["menu_index"] == 8            # one past the retail rows
    assert f["icon"] == 8 and f["skill"] == 36   # kept from the donor
    assert MT.read_fields("spell", MT.build_record("spell", m, d, 4095, 500))["menu_index"] == 500
    with pytest.raises(MT.MenuError):
        MT.build_record("spell", m, dict(d, like=7000), 4095, None)
    c = MT.build_record("command", m, dict(COMMAND_EXAMPLE, like=2), 4000, None)
    assert MT.read_fields("command", c)["level"] == 30 and MT.read_fields("command", c)["id"] == 4000


def test_server_snippet_mentions_ids_and_overrides():
    sql = MT.server_snippet("spell", dict(SPELL_EXAMPLE, like=144), 1024, 144)
    assert "INSERT INTO `spell_list`" in sql and "SELECT 1024, 'testspell'" in sql
    assert "12, 2000, 10000" in sql and "`spellid` = 144" in sql
    sql = MT.server_snippet("command", COMMAND_EXAMPLE, 2816, 547)
    assert "INSERT INTO `abilities`" in sql and "SELECT 2304, 'war_cry', `job`, 30" in sql and "`abilityId` = 35" in sql


# ── files in a DAT root ────────────────────────────────────────────────────────

def test_strings_grow_and_clear(tmp_path: Path, monkeypatch):
    import xi.xi_config as cfg
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)
    root = install(tmp_path / "game")
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    assert MT.read_names("spell", root)[3] == "Name 3"
    assert MT.menu_path(root) == root / "ROM" / "118" / "114.DAT"
    written = MT.set_texts("spell", root, 4095, {"name_en": "Testspell", "help_en": "Burns."})
    assert len(written) == 4
    names = MT.read_names("spell", root)
    assert len(names) == 4096 and names[4095] == "Testspell" and names[100] == "." and names[3] == "Name 3"
    assert MT.read_names("spell", root, "jp")[4095] == "Testspell"      # JP falls back to EN
    help_en = D.parse((root / "ROM/181/75.DAT").read_bytes())
    assert D.get_text(help_en.blocks[4095], 0) == "Burns."
    assert (root / "ROM/181/73.DAT.base").exists()                        # first edit backed up
    # dry-run touches nothing
    MT.set_texts("spell", root, 4094, {"name_en": "Nope"}, dry_run=True)
    assert MT.read_names("spell", root)[4094] == "."
    # a record + its strings can be cleared again
    m = MT.load_menu(root)
    m.set_record("spell", 4095, spell_record(4095))
    MT.save_menu(root, m)
    assert not MT.is_empty(MT.load_menu(root).records("spell")[4095])
    MT.restore_record("spell", root, 4095)
    assert MT.is_empty(MT.load_menu(root).records("spell")[4095])
    assert MT.read_names("spell", root)[4095] == "."
    assert (MT.menu_path(root).with_name("114.DAT.base")).exists()


def test_text_that_does_not_fit_writes_nothing(tmp_path: Path, monkeypatch):
    import xi.xi_config as cfg
    root = install(tmp_path / "game")                    # the synthetic name tables are 80-byte blocks
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    before = {rom: (root / rom).read_bytes() for rom in MT.string_tables("spell")}
    with pytest.raises(MT.MenuError) as e:
        MT.set_texts("spell", root, 4095, {"name_en": "x" * 40, "help_en": "fits"})
    assert "40 bytes; block 4095 holds 39" in str(e.value)
    assert {rom: (root / rom).read_bytes() for rom in MT.string_tables("spell")} == before
    assert not list(root.rglob("*.base"))
    assert MT.text_capacity(D.parse(before["ROM/181/75.DAT"]).blocks[0]) == 215


def test_capture_and_restore_put_back_a_replaced_row(tmp_path: Path, monkeypatch):
    import xi.xi_config as cfg
    root = install(tmp_path / "game")
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    original = MT.load_menu(root).records("spell")[5]
    replaced = MT.capture_record("spell", root, 5)
    assert replaced["record"] == original.hex() and set(replaced["blocks"]) == set(MT.string_tables("spell"))
    assert MT.capture_record("spell", root, 4095) is None            # nothing there to keep
    m = MT.load_menu(root)
    m.set_record("spell", 5, spell_record(5, mp=99))
    MT.save_menu(root, m)
    MT.set_texts("spell", root, 5, {"name_en": "Testspell"})
    MT.restore_record("spell", root, 5, replaced)
    assert MT.load_menu(root).records("spell")[5] == original
    assert MT.read_names("spell", root)[5] == "Name 5" and MT.read_names("spell", root, "jp")[5] == "Name 5"


def test_pivot_root_is_used_only_when_asked(tmp_path: Path, monkeypatch):
    """Reads and writes go to the root they are given. A configured FFXI_PIVOT_DIR
    changes nothing by itself; given as the root, its own copy is read and written,
    and a table it lacks reads the install's and is copied in (without .base) on write."""
    import xi.xi_config as cfg
    root = install(tmp_path / "game")
    pivot = tmp_path / "pivot"
    (pivot / "ROM" / "118").mkdir(parents=True)
    (pivot / "ROM" / "118" / "114.DAT").write_bytes(menu_dat(9, 6))     # one more spell than the install
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot), raising=False)
    assert MT.menu_path(root) == root / "ROM" / "118" / "114.DAT"
    assert MT.load_menu(root).count("spell") == 8
    assert MT.menu_path(pivot) == pivot / "ROM" / "118" / "114.DAT"
    assert MT.load_menu(pivot).count("spell") == 9
    assert MT.dat_path(pivot, "ROM/181/73.DAT") == root / "ROM" / "181" / "73.DAT"   # reads fall back
    m = MT.load_menu(pivot)
    m.set_record("spell", 4095, spell_record(4095))
    assert MT.save_menu(pivot, m) == pivot / "ROM" / "118" / "114.DAT"
    assert MT.load_menu(pivot).count("spell") == 4096
    assert MT.load_menu(root).count("spell") == 8                               # install untouched
    written = MT.set_string(pivot, "ROM/181/73.DAT", 4095, "Testspell")
    assert written == pivot / "ROM" / "181" / "73.DAT"
    assert MT.read_names("spell", pivot)[4095] == "Testspell"
    assert len(D.parse((root / "ROM" / "181" / "73.DAT").read_bytes()).blocks) == 8
    assert not list(pivot.rglob("*.base")) and not list(root.rglob("*.base"))
    # undo in a root leaves tables it has no copy of alone instead of copying them in
    MT.restore_record("spell", pivot, 4095)
    assert MT.is_empty(MT.load_menu(pivot).records("spell")[4095])
    assert MT.read_names("spell", pivot)[4095] == "."
    assert not (pivot / "ROM" / "181" / "69.DAT").exists()


# ── retail placeholders (the ability build's Client Menu Record) ──────────────

def _ph_spell(i: int) -> bytes:
    return spell_record(i, kind=8, mp=0, skill=0, icon2=0xFFFF, levels={}, menu_index=0)


def _ph_ja(i: int) -> bytes:
    return command_record(i, type=1, icon=46, icon2=46, range=0, tp=0xFFFF, level=0)


def _ph_ws(i: int) -> bytes:
    return command_record(i, type=3, icon=46, icon2=46, aoe=0, radius=0, range=0)


def test_is_placeholder_takes_only_the_canonical_blank_rows():
    assert MT.is_placeholder("spell", 1023, _ph_spell(1023), ".", ".")
    assert MT.is_placeholder("command", 1023, _ph_ja(1023), ".", "")
    assert MT.is_placeholder("command", 237, _ph_ws(237), ".", ".")
    assert not MT.is_placeholder("spell", 1023, _ph_spell(1023), "Cornelia", ".")          # named
    assert not MT.is_placeholder("spell", 1002, _ph_spell(1002), ".", "\u30b3\u30fc\u30cd\u30ea\u30a2")  # a JP name
    assert not MT.is_placeholder("spell", 1023, MT.write_fields("spell", _ph_spell(1023), {"levels": {"WHM": 1}}),
                                 ".", ".")                                                  # a job can learn it
    assert not MT.is_placeholder("spell", 600, spell_record(600, kind=6, skill=43), ".", ".")  # unnamed blue magic
    recast = bytearray(_ph_ja(900))
    recast[0x08] = 7
    for bad in (MT.write_fields("command", _ph_ja(900), {"range": 12}), bytes(recast),
                MT.write_fields("command", _ph_ja(900), {"icon2": 12})):
        assert not MT.is_placeholder("command", 900, bad, ".", ".")
    assert not MT.is_placeholder("command", 211, MT.write_fields("command", _ph_ws(211), {"aoe": 1}), ".", ".")
    assert not MT.is_placeholder("command", 1500, _ph_ja(1500), ".", ".")     # outside the ja / ws bands
    assert not MT.is_placeholder("spell", 5, bytes(0x64), ".", ".")           # an empty row is not one


def _install_with(tmp_path: Path, monkeypatch, rows: dict, names: dict) -> Path:
    import xi.xi_config as cfg
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)
    root = install(tmp_path / "game")
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    m = MT.load_menu(root)
    for i, rec in rows.items():
        m.set_record("spell", i, rec)
    MT.save_menu(root, m)
    for i, text in names.items():
        for rom in ("ROM/181/73.DAT", "ROM/181/69.DAT"):
            MT.set_string(root, rom, i, text)
    return root


def test_row_state(tmp_path: Path, monkeypatch):
    root = _install_with(tmp_path, monkeypatch, {7: _ph_spell(7), 6: bytes(0x64)}, {7: ".", 6: "."})
    assert MT.row_state("spell", root, 8) == ("past-end", "")
    assert MT.row_state("spell", root, 7) == ("placeholder", ".")
    assert MT.row_state("spell", root, 6)[0] == "empty"
    assert MT.row_state("spell", root, 3) == ("taken", "Name 3")
    own = MT.load_menu(root).records("spell")[3].hex()
    assert MT.row_state("spell", root, 3, own) == ("own", "Name 3")
    assert MT.row_state("spell", root, 7, own)[0] == "placeholder"            # bytes elsewhere are not "own"


def test_place_record_captures_rewrites_and_moves(tmp_path: Path, monkeypatch):
    root = _install_with(tmp_path, monkeypatch, {5: _ph_spell(5), 6: _ph_spell(6)}, {5: ".", 6: "."})
    d = {"schema": "xi.spell.v1", "name": "love", "like": 3, "text": {"name_en": "Love"}}
    first = MT.place_record("spell", root, 6, d, menu_index=50)
    assert first["replaced"]["record"] == _ph_spell(6).hex() and first["moved_from"] is None
    assert MT.row_state("spell", root, 6, first["written"]) == ("own", "Love")
    assert MT.read_fields("spell", MT.load_menu(root).records("spell")[6])["menu_index"] == 50
    # a rewrite of its own record keeps what it replaced the first time
    again = MT.place_record("spell", root, 6, dict(d, text={"name_en": "Love Two"}), menu_index=50, prev_root=first)
    assert again["replaced"] == first["replaced"] and MT.read_names("spell", root)[6] == "Love Two"
    # a move puts the old row back while it still holds what was written there
    moved = MT.place_record("spell", root, 5, d, menu_index=50, prev_root=again)
    assert moved["moved_from"] == 6 and moved["left"] is None
    assert MT.load_menu(root).records("spell")[6] == _ph_spell(6) and MT.read_names("spell", root, "jp")[6] == "."
    # ... and leaves one that is no longer ours
    m = MT.load_menu(root)
    m.set_record("spell", 5, spell_record(5, mp=77))
    MT.save_menu(root, m)
    MT.set_string(root, "ROM/181/73.DAT", 5, "Retail")
    left = MT.place_record("spell", root, 6, d, menu_index=50, prev_root=moved)
    assert left["left"] == {"record_id": 5, "name": "Retail"} and left["moved_from"] is None
    assert MT.read_names("spell", root)[5] == "Retail"
    assert MT.row_is_restored("spell", 6, None, menu=MT.load_menu(root), names=MT.row_names("spell", root)) is False


def test_server_snippet_names_a_weapon_skill_command_below_512():
    sql = MT.server_snippet("command", COMMAND_EXAMPLE, 237, 32)
    assert sql.startswith("-- command 237 is a weapon skill (weapon_skills.weaponskillid 237), not an abilities row")
    assert "INSERT" not in sql
