"""``xi ability compose`` (xi.ability.xi_compose): the routine writer, section transplanting
with dependency walk, collision renaming, and the round trip recipe → DAT → inspect.

Install-backed tests use the ``root`` fixture and skip without FFXI_DIR."""
import struct
from pathlib import Path

import click
import pytest

from xi.ability import xi_compose as ac
from xi.ability import xi_inspect as ai


def test_build_routine_layout_matches_retail():
    gen = bytes.fromhex("02040000000000006730303000000000")
    sec = ac.build_routine("main", [gen, gen], total=42)
    assert sec[:4] == b"main"
    meta = struct.unpack_from("<I", sec, 4)[0]
    assert meta & 0x7F == 0x07 and ((meta >> 7) & 0x7FFFF) * 16 == len(sec)
    s1, s2, s3, total = struct.unpack_from("<4I", sec, 0x20)
    assert (s1, s2, total) == (0x40, 0x50, 42)
    assert sec[s1:s1 + 4] == bytes.fromhex("00010000")
    assert sec[s2:s2 + 8] == ac._ROUTINE_START
    assert sec[s3:s3 + 4] == bytes.fromhex("00010000")
    assert len(sec) % 16 == 0


def test_stamp_patches_delay_dur_ref_and_clip_fields():
    clip = bytes.fromhex(ac._TEMPLATES[0x05].hex())
    out = ac._stamp(clip, delay=7, ev={"dur": 33, "blend": [4, 9], "loops": 2}, ref="ab0?")
    assert struct.unpack_from("<HH", out, 4) == (7, 33)
    assert out[8:12] == b"ab0?"
    assert struct.unpack_from("<H", out, 24)[0] == 4
    assert struct.unpack_from("<H", out, 28)[0] == 9
    assert struct.unpack_from("<H", out, 30)[0] == 2


def test_fresh_name_avoids_used():
    assert ac._fresh_name("g000", {"g000", "g001"}) == "g002"
    assert ac._fresh_name("g000", {"g00" + c for c in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"}).startswith("x00")


def _timeline_key(info):
    return [(e["start"], e["op"], e["ref"], e["dur"]) for e in info["timeline"]]


def test_round_trip_fast_blade(root: Path, tmp_path: Path):
    src = ai.resolve_targets("ws:1:HumeMale")[0]
    original = ai.inspect_target(src)
    recipe = ac.recipe_from("ws:1:HumeMale")
    (composed,) = ac.compose(recipe)
    p = tmp_path / "fb.DAT"
    p.write_bytes(composed.data)
    rebuilt = ai.inspect_target(ai.Target("fb", p, "fb.DAT"))
    assert _timeline_key(rebuilt) == _timeline_key(original)
    assert rebuilt["total"] == original["total"]
    assert set(rebuilt["sounds"]) == set(original["sounds"])
    assert set(rebuilt["generators"]) == set(original["generators"])
    # every referenced clip came along; unreferenced 1-frame stubs did not
    assert set(rebuilt["clips"]) == {"b000", "b001", "b020", "b021"}
    assert composed.renames == {}


def test_mixed_recipe_transplants_and_renames(root: Path):
    recipe = {
        "name": "mixtest",
        "sources": {"motion": {"spec": "ws:1:HumeMale"}, "vfx": {"spec": "spell:144"},
                    "vfx2": {"spec": "spell:145"}},        # Fire II: same g00N names as Fire
        "events": [
            {"from": "motion", "op": 5, "ref": "b00?", "start": 10, "dur": 35},
            {"from": "vfx", "op": 2, "ref": "g003", "start": 60, "dur": 80},
            {"from": "vfx2", "op": 2, "ref": "g003", "start": 60, "dur": 80},
            {"from": "motion", "op": 10, "ref": "8050", "start": 60},
        ],
    }
    (c,) = ac.compose(recipe)
    assert c.race is None
    assert any(k.startswith("vfx2:g003") for k in c.renames), c.renames
    refs = [t["ref"] for t in c.timeline if t["op"] == 2]
    assert "g003" in refs and c.renames["vfx2:g003"] in refs
    names = {s.split("(")[0] for s in c.sections}
    assert {"b000", "b001", "8050", "g003", "main"} <= names
    assert any(s.endswith("(0x20)") for s in c.sections)      # a texture dependency came along


def test_mesh_textures_come_along(root: Path):
    # Cure III's `ob02` generator draws the `ob2` ParticleMesh (0x1F), whose texture
    # is `obi`; `pk00` draws the `shp1` sprite sheet (0x21), whose texture is `shu`.
    # The generators never name the textures. The walk used to follow only a
    # ZoneMesh to its texture, so both were dropped and the composed ability drew
    # white quads until a retail play had cached them in the viewer.
    recipe = {"name": "cure3", "sources": {"vfx": {"spec": "spell:3"}},
              "events": [{"from": "vfx", "op": 2, "ref": "ob02", "start": 0, "dur": 100},
                         {"from": "vfx", "op": 2, "ref": "pk00", "start": 0, "dur": 100}]}
    (c,) = ac.compose(recipe)
    names = {s for s in c.sections if s.endswith("(0x20)")}
    assert {"obi(0x20)", "shu(0x20)"} <= names, names


def test_clip_pack_lane_without_routine(root: Path):
    # An emote file (ROM/37/13, Hume Female) is a clip pack: bow0/bow1 and friends, no
    # `main`. A lane with `routine: null` composes a PlayClip from the template and
    # carries the clips.
    recipe = {"name": "bow", "sources": {"motion": {"spec": "ROM/37/13.DAT", "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": "bow?", "start": 0, "dur": 30}]}
    assert ac.validate_recipe(recipe) == []
    (c,) = ac.compose(recipe, race="HumeFemale")
    names = {s.split("(")[0] for s in c.sections}
    assert "bow0" in names and "main" in names, c.sections
    assert [t["ref"] for t in c.timeline if t["op"] == 5] == ["bow?"]
    assert c.total == 30      # the routine ends where its lone clip's window does, not at 0


def test_emote_lane_maps_to_every_race(root: Path):
    # Hume Female's emote file is a slot in the client's per-race emote table: every race
    # composes its own bow, the waist part (bow2, in the +6 sibling) included.
    recipe = {"name": "bow", "sources": {"motion": {"spec": "ROM/37/13.DAT", "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": "bow?", "start": 0, "dur": 60}]}
    lane = ac._lanes(recipe)["motion"]
    assert lane.race_bound and lane.slot.category == "emote"
    out = ac.compose(recipe)
    assert [c.race for c in out] == list(ac.RACE_NAMES)
    for c in out:
        names = {s.split("(")[0] for s in c.sections}
        assert {"bow0", "bow1", "bow2"} <= names, (c.race, c.sections)
        assert c.warnings == [], (c.race, c.warnings)
    by_race = {c.race: c for c in out}
    assert by_race["HumeFemale"].data != by_race["Galka"].data      # each race's own clips


def test_race_base_lane_names_clips_without_carrying(root: Path):
    # The race base (movement table, +0) is always loaded on a character, so its clips
    # are named, not carried: one DAT serves every race, and a job ability can use it.
    recipe = {"name": "cm", "sources": {"motion": {"spec": "ROM/32/58.DAT", "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": "cm0?", "start": 0, "dur": 30}]}
    lane = ac._lanes(recipe)["motion"]
    assert lane.by_reference and not lane.race_bound
    (c,) = ac.compose(recipe)
    assert not any(s.endswith("(0x2B)") for s in c.sections), c.sections
    assert [t["ref"] for t in c.timeline if t["op"] == 5] == ["cm0?"]
    assert c.warnings == []


def test_base_motions_are_by_reference_casts(root: Path):
    # The curated base-motion list the mixer offers for a job ability or spell: real clips
    # from the always-loaded race base, one row per cast/ability family, each with a base
    # DAT per race for preview. A pick composes by-reference — one race-agnostic DAT that
    # names the clip, so the motion plays from every race's own pool.
    from xi.ability import xi_catalog as cat
    motions = cat.build_base_motions()
    assert motions, "no base motions built (needs the game dir)"
    by_name = {m["name"]: m for m in motions}
    assert {"Black Magic Cast", "Job Ability"} <= set(by_name)
    bm = by_name["Black Magic Cast"]
    assert bm["kind"] == "spell" and bm["clip"]["ref"] == "mb0?" and bm["clip"]["frames"] > 0
    assert {"HumeMale", "Mithra"} <= set(bm["paths"])      # a base DAT per race for preview
    recipe = {"name": "bm", "sources": {"motion": {"spec": bm["spec"], "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": bm["clip"]["ref"],
                          "start": 0, "dur": bm["clip"]["frames"] * 2}]}
    assert ac.validate_recipe(recipe) == []
    lane = ac._lanes(recipe)["motion"]
    assert lane.by_reference and not lane.race_bound       # one DAT for every race
    (c,) = ac.compose(recipe)
    assert not any(s.endswith("(0x2B)") for s in c.sections), c.sections   # clip named, not carried
    assert [t["ref"] for t in c.timeline if t["op"] == 5] == [bm["clip"]["ref"]]
    # v1 exposes only always-loaded motions, so nothing here forces a per-race (ws) bake.
    assert all(m["kind"] in ("ja", "spell") for m in motions)


def test_one_races_own_motion_is_built_for_that_race_only(root: Path):
    # HumeF Variations (ROM/173/48) is outside the motion tables and the character list
    # gives it to Hume Female alone: every race composes, only Hume Female carries the
    # clips, and the other races say they were built without them.
    recipe = {"name": "var", "sources": {"motion": {"spec": "ROM/173/48.DAT", "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": "ol6?", "start": 0, "dur": 30}]}
    lane = ac._lanes(recipe)["motion"]
    assert lane.slot is None and lane.owners == frozenset({"HumeFemale"}) and lane.race_bound
    by_race = {c.race: c for c in ac.compose(recipe)}
    assert "ol60" in {s.split("(")[0] for s in by_race["HumeFemale"].sections}
    assert by_race["HumeFemale"].warnings == []
    galka = by_race["Galka"]
    assert not any(s.endswith("(0x2B)") for s in galka.sections), galka.sections
    assert len(galka.warnings) == 1 and "HumeFemale only" in galka.warnings[0], galka.warnings


def test_per_race_motion_publishes_as_a_weapon_skill(root: Path):
    from xi.ability import xi_publish as ap
    emote = {"name": "bow", "sources": {"motion": {"spec": "ROM/37/13.DAT", "routine": None}},
             "events": [{"from": "motion", "op": 5, "ref": "bow?", "start": 0, "dur": 60}]}
    assert ap.infer_kind(emote) == "ws"
    with pytest.raises(click.ClickException):
        ap.infer_kind(emote, "spell")
    assert ap._source_ws_animation(emote) is None          # waist clips ride in the body DAT
    base = {"name": "cm", "sources": {"motion": {"spec": "ROM/32/58.DAT", "routine": None}},
            "events": [{"from": "motion", "op": 5, "ref": "cm0?", "start": 0, "dur": 30}]}
    assert ap.infer_kind(base) == "ja"


def test_race_bound_recipe_composes_per_race(root: Path):
    recipe = {"name": "rb", "sources": {"motion": {"spec": "ws:1"}},
              "events": [{"from": "motion", "op": 5, "ref": "b00?", "start": 0, "dur": 35}]}
    out = ac.compose(recipe, race="Mithra")
    assert [c.race for c in out] == ["Mithra"]
    assert ac.output_name(recipe, out[0]) == "rb.Mithra.DAT"
