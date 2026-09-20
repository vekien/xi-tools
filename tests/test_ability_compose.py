"""``xi ability compose`` (xi.ability.xi_compose): the routine writer, section transplanting
with dependency walk, collision renaming, commands of no lane (locks, hits and links added
by hand), and the round trip recipe → DAT → inspect.

Install-backed tests use the ``root`` fixture and skip without FFXI_DIR."""
import copy
import json
import struct
from pathlib import Path

import click
import pytest

from xi.ability import xi_compose as ac
from xi.ability import xi_inspect as ai
from xi.common.xi_section import encode_section_meta
from xi.entity.anim.xi_export import parse_sections


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


def _dat(tmp_path: Path, name: str, routines=()) -> Path:
    """A synthetic DAT: an effect directory, the given routine sections, the end marker.
    It has no clips, so a recipe against it composes PlayClips from the template and
    carries nothing — no install needed."""
    p = tmp_path / name
    p.write_bytes(b"test" + struct.pack("<I", encode_section_meta(32, ai.T_DIR)) + b"\0" * 8
                  + ac._DIR_DATA + b"".join(routines) + ac._END_SECTION)
    return p


def _clip(ref: str, *, delay: int, dur: int, blend=(10, 10), loops: int = 1) -> bytes:
    return ac._stamp(ac._TEMPLATES[0x05], delay=delay, ev={"dur": dur, "blend": list(blend), "loops": loops}, ref=ref)


# The race base's `ssbk` (ROM/27/82): the release, then the follow-through played twice.
_SSBK = ["050a00003a003a006d62313f000000000000803f0000803f0a000000000001000000000000000000",
         "050a00007a003d006d62323f000000000000803f0000803f000000000f0002000000000000000000"]


def test_window_counts_every_cycle_of_a_looped_clip():
    clip = ac._TEMPLATES[0x05]                                   # maxLoops 1
    assert ac._window({"op": 5, "dur": 61}, clip) == 61
    assert ac._window({"op": 5, "dur": 61, "loops": 2}, clip) == 122
    assert ac._window({"op": 5, "dur": 61, "loops": 0}, clip) == 61     # forever: one cycle
    # No `loops` on the event: the command's own maxLoops is what plays.
    assert ac._window({"op": 5, "dur": 61}, bytes.fromhex(_SSBK[1])) == 122
    assert ac._window({"op": 5, "dur": 61, "loops": 1}, bytes.fromhex(_SSBK[1])) == 61
    assert ac._window({"op": "0x05"}, bytes.fromhex(_SSBK[1])) == 0      # no dur, no window
    # Only a clip loops: a generator's word at +30 is not a loop count.
    assert ac._window({"op": 2, "dur": 100, "loops": 3}, bytes(32)) == 100
    # The client plays byte +30 & 0x7F times; +31's high nibble is the start phase, so
    # the race base's @tr0 (0x2001) plays once. An explicit `loops` goes out as those bits.
    tr0 = bytearray(clip)
    struct.pack_into("<H", tr0, 30, 0x2001)
    assert ac._window({"op": 5, "dur": 30}, bytes(tr0)) == 30
    assert ac._window({"op": 5, "dur": 30, "loops": 130}, clip) == 60
    assert ac._window({"op": 5, "dur": 30, "loops": 128}, clip) == 30    # 0: forever, one cycle


def test_composed_routine_waits_out_a_looped_clip(tmp_path: Path):
    # Retail's `ssbk` plays mb1? for 58 then mb2? x2 in a 61-tick window: the last
    # command waits 122 and the routine's totalDelay is 180. A recipe with the same
    # events and no `total` ends there too, not one cycle in.
    spec = str(_dat(tmp_path, "pack.DAT"))
    recipe = {"name": "ss", "sources": {"motion": {"spec": spec, "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": "mb1?", "start": 0, "dur": 58, "blend": [10, 0], "loops": 1},
                         {"from": "motion", "op": 5, "ref": "mb2?", "start": 58, "dur": 61, "blend": [0, 15], "loops": 2}]}
    assert ac.validate_recipe(recipe) == []
    (c,) = ac.compose(recipe)
    assert c.total == 180
    out = tmp_path / "ss.DAT"
    out.write_bytes(c.data)
    m = ai.Model.load(out)
    assert m.routine_total("main") == 180
    assert [e["raw"] for e in ai.flatten(m, "main")] == _SSBK           # byte for byte retail's

    def total(**last):
        ev = {k: v for k, v in {**recipe["events"][1], **last}.items() if v is not None}
        return ac.compose(dict(recipe, events=[recipe["events"][0], ev]))[0].total
    assert total(loops=1) == 119
    assert total(loops=3) == 58 + 61 * 3
    assert total(loops=0) == 119                                        # forever: one cycle, as before
    assert total(raw=_SSBK[1], loops=None) == 180                       # the command's own x2
    # A recipe's own `total` still wins, and a looped clip in the middle only matters
    # when it outlasts what follows.
    assert ac.compose(dict(recipe, total=150))[0].total == 150
    early = dict(recipe, events=[{**recipe["events"][0], "loops": 4}, recipe["events"][1]])
    assert ac.compose(early)[0].total == 58 * 4


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
        assert {"bow0", "bow1"} <= names, (c.race, c.sections)          # parts 0/1 in the body
        assert "bow2" in _clips_of(c.companion), (c.race,)             # part 2 (waist) in the companion
        assert c.warnings == [], (c.race, c.warnings)
    by_race = {c.race: c for c in out}
    assert by_race["HumeFemale"].data != by_race["Galka"].data      # each race's own clips


def test_emote_ws_stows_the_weapon(root: Path):
    # An emote never moves the weapon hand (skeleton ref 127), so a drawn weapon would hang
    # frozen when the client plays it as a weapon skill. The composer stows main+sub at
    # frame 0 with the same 0x75 tags a spell cast runs (ROM/0/0 hwmg), so the pose reads
    # as it does in the mixer preview (which hides them too).
    recipe = {"name": "bow", "sources": {"motion": {"spec": "ROM/37/13.DAT", "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": "bow?", "start": 0, "dur": 60}]}
    for c in ac.compose(recipe):
        assert _vis_hides(c.data) == [(0, 1, 1), (1, 1, 1)], c.race   # main then sub, hidden, ifEngaged


def test_non_emote_motion_keeps_the_weapon(root: Path):
    # A motion that is not an emote or dance is left alone — a race base movement clip here
    # (composed to one DAT, not per race). Only motions that never touch the weapon hand get
    # the hide, so a real weapon-skill motion keeps its weapon shown.
    recipe = {"name": "cm", "sources": {"motion": {"spec": "ROM/32/58.DAT", "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": "cm0?", "start": 0, "dur": 30}]}
    assert _vis_hides(ac.compose(recipe)[0].data) == []


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


def test_stage_labels_by_position():
    from xi.ability.xi_catalog import _stage_labels
    assert _stage_labels(0) == []
    assert _stage_labels(1) == ["Start"]
    assert _stage_labels(2) == ["Start", "End"]
    assert _stage_labels(3) == ["Start", "Middle", "End"]
    assert _stage_labels(4) == ["Start", "Middle 1", "Middle 2", "End"]


def test_base_motion_stages_are_read_from_the_schedules(tmp_path: Path):
    # A synthetic race base: the chant schedule loops mb0?, the finish schedule plays
    # mb1? then mb2? x2 and also a clip of another family, which is not a stage.
    from xi.ability.xi_catalog import base_motion_stages
    link = ac._stamp(ac._TEMPLATES[0x03], delay=0, ev={}, ref="hwmg")
    base = ai.Model.load(_dat(tmp_path, "base.DAT", [
        ac.build_routine("cabk", [link, _clip("mb0?", delay=33, dur=33, blend=(16, 10), loops=63)], 33),
        ac.build_routine("ssbk", [_clip("mb1?", delay=58, dur=58, blend=(10, 0)),
                                  _clip("idl?", delay=0, dur=9),
                                  _clip("mb2?", delay=122, dur=61, blend=(0, 15), loops=2)], 180)]))
    base.clips = {"mb00": {"frames": 14.0}, "mb01": {"frames": 13.0}, "mb10": {"frames": 30.0},
                  "mb20": {"frames": 29.6}, "idl0": {"frames": 56.0}}
    assert base_motion_stages(base, "mb0", ("cabk", "ssbk")) == [
        {"label": "Start", "ref": "mb0?", "frames": 14, "dur": 33, "blend": [16, 10], "loops": 63, "schedule": "cabk"},
        {"label": "Middle", "ref": "mb1?", "frames": 30, "dur": 58, "blend": [10, 0], "loops": 1, "schedule": "ssbk"},
        {"label": "End", "ref": "mb2?", "frames": 30, "dur": 61, "blend": [0, 15], "loops": 2, "schedule": "ssbk"}]
    # A schedule the base lacks, or a clip group it lacks, contributes nothing; the
    # labels follow what is left.
    assert [s["label"] for s in base_motion_stages(base, "mb0", ("cazz", "ssbk"))] == ["Start", "End"]
    del base.clips["mb10"]
    assert [s["ref"] for s in base_motion_stages(base, "mb0", ("cabk", "ssbk"))] == ["mb0?", "mb2?"]
    # `<spec> <routine>` reads a schedule from another DAT (a job ability's main); the
    # clip lengths are still the base's.
    ja = _dat(tmp_path, "ja.DAT", [ac.build_routine("main", [_clip("cm0?", delay=60, dur=60, blend=(0, 0)),
                                                             _clip("cm1?", delay=128, dur=128, blend=(0, 20))], 188)])
    base.clips.update({"cm00": {"frames": 30.0}, "cm10": {"frames": 60.0}})
    assert base_motion_stages(base, "cm0", (f"{ja} main",)) == [
        {"label": "Start", "ref": "cm0?", "frames": 30, "dur": 60, "blend": [0, 0], "loops": 1, "schedule": f"{ja} main"},
        {"label": "End", "ref": "cm1?", "frames": 60, "dur": 128, "blend": [0, 20], "loops": 1, "schedule": f"{ja} main"}]
    assert base_motion_stages(base, "cm0", (f"{tmp_path / 'none.DAT'} main",)) == []


def test_base_motions_carry_retail_stages(root: Path):
    # Every base motion lists the clip groups the game plays for it, in order, with the
    # window / blend / loop count of the race base's own schedule. `clip` is still the
    # first group, so a viewer without stage support picks what it always did.
    from xi.ability import xi_catalog as cat
    by_name = {m["name"]: m for m in cat.build_base_motions()}
    assert set(by_name) == {name for name, *_ in cat.BASE_MOTIONS}
    for m in by_name.values():
        st = m["stages"]
        assert st[0]["ref"] == m["clip"]["ref"] and st[0]["frames"] == m["clip"]["frames"], m["name"]
        assert [s["label"] for s in st] == cat._stage_labels(len(st))
        for s in st:
            assert set(s) == {"label", "ref", "frames", "dur", "blend", "loops", "schedule"}
            assert s["frames"] > 0 and s["dur"] > 0 and s["loops"] >= 1 and len(s["blend"]) == 2
    # A magic cast: the looping chant, the release, the follow-through.
    assert by_name["Black Magic Cast"]["stages"] == [
        {"label": "Start", "ref": "mb0?", "frames": 14, "dur": 33, "blend": [16, 10], "loops": 63, "schedule": "cabk"},
        {"label": "Middle", "ref": "mb1?", "frames": 30, "dur": 58, "blend": [10, 0], "loops": 1, "schedule": "ssbk"},
        {"label": "End", "ref": "mb2?", "frames": 30, "dur": 61, "blend": [0, 15], "loops": 2, "schedule": "ssbk"}]
    assert [(s["label"], s["ref"]) for s in by_name["Item Use"]["stages"]] == [
        ("Start", "mi0?"), ("Middle 1", "mi1?"), ("Middle 2", "mi2?"), ("End", "mi3?")]
    assert [(s["label"], s["ref"], s["schedule"]) for s in by_name["Job Ability"]["stages"]] == [
        ("Start", "cm0?", "ja:0 main"), ("End", "cm1?", "ja:0 main")]
    # Singing has no intro: its hold loops from the start.
    assert [(s["label"], s["ref"], s["loops"] > 1) for s in by_name["Bard: Singing"]["stages"]] == [
        ("Start", "sk1?", True), ("End", "sk2?", False)]
    # The stages laid end to end compose by reference and the routine waits out the x2 End.
    bm = by_name["Black Magic Cast"]
    events, at = [], 0
    for s in bm["stages"][1:]:
        events.append({"from": "motion", "op": 5, "ref": s["ref"], "start": at, "dur": s["dur"],
                       "blend": s["blend"], "loops": s["loops"], "name": s["label"]})
        at += s["dur"] * s["loops"]
    recipe = {"name": "bm", "sources": {"motion": {"spec": bm["spec"], "routine": None}}, "events": events}
    assert ac.validate_recipe(recipe) == []
    (c,) = ac.compose(recipe, kind="spell")
    assert c.total == at == 180 and c.warnings == []
    assert c.sections == ["main(0x07)"]


def test_job_ability_or_spell_bakes_a_race_bound_motion_from_one_race(root: Path):
    # A job ability or spell is one DAT for every race, so a race-bound motion (an emote
    # here) is BAKED from one race's copy into that single DAT rather than composed per race.
    # Experimental: this tests what compose writes, not that a client plays it. The waist
    # sibling (+6) comes along, and the bake race is HumeMale unless compose is given one.
    recipe = {"name": "bow_spell", "target": {"kind": "spell"},
              "sources": {"motion": {"spec": "ROM/37/13.DAT", "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": "bow?", "start": 0, "dur": 60}]}
    assert ac.validate_recipe(recipe) == []
    lane = ac._lanes(recipe)["motion"]
    assert not lane.race_bound and not lane.by_reference     # one DAT, clips carried
    (hume,) = ac.compose(recipe)
    assert hume.race is None
    assert {"bow0", "bow1", "bow2"} <= {s.split("(")[0] for s in hume.sections}, hume.sections
    (mithra,) = ac.compose(recipe, race="Mithra")
    assert mithra.race is None
    assert {"bow0", "bow1", "bow2"} <= {s.split("(")[0] for s in mithra.sections}
    assert mithra.data != hume.data                            # Mithra's own clips
    # Without a kind the same recipe is still race-bound and composes per race.
    assert [c.race for c in ac.compose({**recipe, "target": {}})] == list(ac.RACE_NAMES)


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
    # Asked for a spell, the same emote is baked from one race into the single DAT
    # (experimental), so the kind is honoured, not refused.
    assert ap.infer_kind(emote, "spell") == "spell"
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


# ── Folder layout ────────────────────────────────────────────────────────────────

_RACE_TAGS = ["hm", "hf", "em", "ef", "tr", "tr", "mt", "gl"]       # retail's, in RACE_NAMES order
# Tachi: Enpi's first weapon-trace command (op 0x2C `c3a?`), raw from ws:166:HumeFemale.
_TRACE = ("2c16000000004b006333613f1062bf018a6c80408a6c80000000404000004040000040400000000000004040"
          "0000404000004040000000006f12833a09d7a33c0000a04100000000cdcccc3dcdcccc3d0000000000006400")


def _folders(data: bytes):
    """The DAT's folders, walked from its push (0x01) and pop (0x00) sections: ``(top,
    pops, end)``. ``top["dirs"]`` holds the outermost folders as ``{"name", "payload",
    "items", "dirs"}``, ``items`` being ``(name, type)`` of the sections directly inside;
    ``pops`` counts the 0x00 sections and ``end`` is where the last section ends."""
    top = {"name": None, "payload": None, "items": [], "dirs": []}
    stack, pops, end = [top], 0, 0
    for s in parse_sections(data):
        end = s.start + s.size
        if s.type_code == ai.T_DIR:
            node = {"name": ai._clean(s.name), "payload": data[s.data_start:s.data_start + 16],
                    "items": [], "dirs": []}
            stack[-1]["dirs"].append(node)
            stack.append(node)
        elif s.type_code == ai.T_END:
            pops += 1
            stack.pop()
        else:
            stack[-1]["items"].append((ai._clean(s.name), s.type_code))
    return top, pops, end


def _clips_of(data) -> set:
    """The clip (0x2B) names in a DAT (e.g. a weapon skill's waist companion)."""
    return {ai._clean(s.name) for s in parse_sections(data or b"") if s.type_code == ai.T_CLIP}


def _vis_hides(data) -> list:
    """``(slot, hidden, ifEngaged)`` for every 0x75 ShowHideWeapon tag in the ``main``
    routine, in order — how the composer stows the weapon for an emote weapon skill."""
    out = []
    for s in parse_sections(data or b""):
        if s.type_code != ac.T_ROUTINE or ac._clean(s.name) != "main":
            continue
        body = data[s.data_start:s.start + s.size]
        for off in range(0, len(body) - 16, 4):
            if body[off] == 0x75:
                out.append((struct.unpack_from("<h", body, off + 12)[0],
                            struct.unpack_from("<I", body, off + 8)[0],
                            struct.unpack_from("<h", body, off + 14)[0]))
    return out


def _ws_body(c, effect_dir: str) -> tuple:
    """Check one race's composed weapon-skill body against retail's layout and return its
    ``(root, effect folder, clip folder or None)``: the race root ``<tag>_<c>`` (payload
    all zero) holds the effect folder (``effect_dir``, payload byte 3 = 0x20, everything
    but the motion, `main` last) and a clip folder named like the root (clips and traces
    only); nothing trails the root's end."""
    top, pops, end = _folders(c.data)
    assert top["items"] == [] and len(top["dirs"]) == 1, c.race
    root_dir = top["dirs"][0]
    tag = _RACE_TAGS[ac.RACE_NAMES.index(c.race)]
    assert root_dir["name"] == f"{tag}_{effect_dir[0].lower()}" and root_dir["payload"] == bytes(16), c.race
    assert root_dir["items"] == []
    effect, *rest = root_dir["dirs"]
    assert effect["name"] == effect_dir and effect["dirs"] == []
    assert effect["payload"] == ac._DIR_DATA and effect["payload"][3] == 0x20 and effect["payload"][7] == 0
    assert effect["items"][-1] == ("main", ai.T_ROUTINE)
    assert not any(t in (ai.T_CLIP, ai.T_TRACE) for _n, t in effect["items"]), effect["items"]
    clips = rest[0] if rest else None
    if clips is not None:
        assert len(rest) == 1 and clips["name"] == root_dir["name"] and clips["payload"] == bytes(16)
        assert clips["dirs"] == [] and clips["items"]
        assert all(t in (ai.T_CLIP, ai.T_TRACE) for _n, t in clips["items"]), clips["items"]
    assert pops == (3 if clips else 2) and end == len(c.data), (c.race, pops, end, len(c.data))
    # The report's sections are the output's, in byte order.
    assert [f"{n}(0x{t:02X})" for n, t in effect["items"] + (clips["items"] if clips else [])] == c.sections
    return root_dir, effect, clips


def _body_sections(data: bytes) -> list:
    """Every section but the folders and their ends, as bytes, sorted."""
    return sorted(bytes(data[s.start:s.start + s.size]) for s in parse_sections(data)
                  if s.type_code not in (ai.T_DIR, ai.T_END))


def _section(name: str, type_code: int, body: bytes = bytes(16)) -> bytes:
    return (name.encode("ascii").ljust(4, b"\0") + struct.pack("<I", encode_section_meta(16 + len(body), type_code))
            + b"\0" * 8 + body)


def test_ws_root_name_follows_retail():
    # <race tag>_<the effect folder's first character>: Tachi: Enpi's `1111` sits in
    # `hf_1` (ROM/101/76), Tartarus Torpor's `enst` in `hf_e` (ROM/240/8). Both Tarutaru
    # carry `tr`: retail loads one file for the two.
    assert ac._ws_root("HumeFemale", "1111") == "hf_1"
    assert ac._ws_root("HumeFemale", "enst") == "hf_e"
    assert ac._ws_root("TaruFemale", "LOVE") == "tr_l"
    assert [ac._ws_root(r, "0011") for r in ac.RACE_NAMES] == [f"{t}_0" for t in _RACE_TAGS]
    assert ac._ws_root("mithra", "_abc") == "mt_0"          # not a letter or digit


def test_race_bound_layout_on_synthetic_bytes(tmp_path: Path):
    # Without the install: a source of two clips, a weapon trace and a sound pointer,
    # composed as one race's body and as a single DAT. Both carry the same sections byte
    # for byte and the same routine; only the folders around them differ.
    src = tmp_path / "src.DAT"
    src.write_bytes(ac._dir("test", ac._DIR_DATA) + _section("ab00", ai.T_CLIP) + _section("ab01", ai.T_CLIP)
                    + _section("aba0", ai.T_TRACE) + _section("5123", ai.T_SOUND) + ac._END_SECTION)
    recipe = {"name": "t", "dir": "0011", "sources": {"m": {"spec": str(src), "routine": None}},
              "events": [{"from": "m", "op": 5, "ref": "ab0?", "start": 0, "dur": 30},
                         {"from": "m", "op": 0x2C, "ref": "aba?", "start": 10, "dur": 20, "raw": _TRACE},
                         {"from": "m", "op": 10, "ref": "5123", "start": 12}]}
    assert ac.validate_recipe(recipe) == []
    lanes = ac._lanes(recipe)
    body = ac.compose_once(recipe, lanes, "Galka")
    root_dir, effect, clips = _ws_body(body, "0011")
    assert root_dir["name"] == "gl_0"
    assert effect["items"] == [("5123", ai.T_SOUND), ("main", ai.T_ROUTINE)]
    assert clips["items"] == [("ab00", ai.T_CLIP), ("ab01", ai.T_CLIP), ("aba0", ai.T_TRACE)]
    single = ac.compose_once(recipe, lanes, None)
    top, pops, end = _folders(single.data)
    (folder,) = top["dirs"]
    assert (folder["name"], folder["payload"], folder["dirs"], pops, end) == ("0011", ac._DIR_DATA, [], 1, len(single.data))
    assert single.data[:0x10] == b"0011" + struct.pack("<I", encode_section_meta(32, ai.T_DIR)) + bytes(8)
    assert _body_sections(body.data) == _body_sections(single.data)
    assert (body.timeline, body.total) == (single.timeline, single.total)
    # Nothing to put in a clip folder: the root holds the effect folder alone.
    sound_only = dict(recipe, events=recipe["events"][2:])
    _, _, none = _ws_body(ac.compose_once(sound_only, lanes, "Galka"), "0011")
    assert none is None


def test_ws_body_has_retail_layout(root: Path):
    # Each race's DAT is laid out like a retail weapon-skill body (Tachi: Enpi, ROM/101/76:
    # hf_1 { 1111 {…, main} hf_1 {clips, traces} }): the race root, the effect folder named
    # by `dir` (here the name's default, `bow_`) and a clip folder named like the root.
    recipe = {"name": "bow", "sources": {"motion": {"spec": "ROM/37/13.DAT", "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": "bow?", "start": 0, "dur": 60}]}
    out = ac.compose(recipe)
    assert [c.race for c in out] == list(ac.RACE_NAMES)
    for c in out:
        _root, _effect, clips = _ws_body(c, "bow_")
        # Parts 0/1 in the body, part 2 (waist) in the companion — as retail lays a WS out.
        assert {"bow0", "bow1"} <= {n for n, _t in clips["items"]}, c.race
        assert "bow2" not in {n for n, _t in clips["items"]}, c.race
        assert c.companion is not None, c.race
        comp_clips = _clips_of(c.companion)
        assert "bow2" in comp_clips and "bow0" not in comp_clips, c.race
    assert [c.data[:2].decode() for c in out] == _RACE_TAGS


def test_ws_traces_ride_in_clip_folder(root: Path):
    # Tachi: Enpi's clips and weapon traces (op 0x2C `c3a?` / `c4a?`) composed from its own
    # events: the traces follow the clips in the clip folder, as retail's do, and a `dir`
    # of `1111` gives Enpi's own folder names.
    enpi = ac.recipe_from("ws:166:HumeFemale")
    events = [e for e in enpi["events"] if e["op"] in (0x05, 0x2C)]
    assert {e["ref"] for e in events if e["op"] == 0x2C} == {"c3a?", "c4a?"}
    assert next(e["raw"] for e in events if e["ref"] == "c3a?") == _TRACE
    recipe = {"name": "enpi", "dir": "1111", "sources": {"motion": {"spec": "ws:166"}}, "events": events}
    (c,) = ac.compose(recipe, race="HumeFemale")
    root_dir, _effect, clips = _ws_body(c, "1111")
    assert root_dir["name"] == "hf_1"
    types = [t for _n, t in clips["items"]]
    assert types == [ai.T_CLIP] * (len(types) - 2) + [ai.T_TRACE] * 2
    assert [n for n, t in clips["items"] if t == ai.T_TRACE] == ["c3a0", "c4a0"]


def test_single_dat_layout_unchanged(root: Path):
    # A job ability or spell stays one effect folder holding everything, as retail's are
    # (Cure III, ROM/10/11: `care`, payload 00000020…), a baked motion's clips included.
    bow = {"name": "bow", "sources": {"motion": {"spec": "ROM/37/13.DAT", "routine": None}},
           "events": [{"from": "motion", "op": 5, "ref": "bow?", "start": 0, "dur": 60}]}
    for recipe, kind, name in ((ac.recipe_from("spell:144"), None, "spel"), (bow, "spell", "bow_")):
        (c,) = ac.compose(recipe, kind=kind)
        assert c.race is None
        top, pops, end = _folders(c.data)
        (folder,) = top["dirs"]
        assert folder["name"] == name and folder["payload"] == ac._DIR_DATA and folder["dirs"] == []
        assert pops == 1 and end == len(c.data) and c.data[:4] == name.encode()
        assert [f"{n}(0x{t:02X})" for n, t in folder["items"]] == c.sections
    assert {"bow0", "bow1", "bow2"} <= {s.split("(")[0] for s in c.sections}


def test_dir_payload_matches_retail(root: Path):
    # The client reads the high nibble of a directory payload's byte 3: 2 on an effect
    # folder (Cure III's root `care`, Tachi: Enpi's `1111`), 0 on a weapon skill's race
    # root and clip folder (Enpi's two `hf_1`).
    cure3 = (root / "ROM/10/11.DAT").read_bytes()
    assert cure3[:4] == b"care" and cure3[0x10:0x20] == ac._DIR_DATA
    (hf_1,) = _folders((root / "ROM/101/76.DAT").read_bytes())[0]["dirs"]
    effect, clips = hf_1["dirs"]
    assert (hf_1["name"], effect["name"], clips["name"]) == ("hf_1", "1111", "hf_1")
    assert effect["payload"] == ac._DIR_DATA
    assert hf_1["payload"] == clips["payload"] == ac._DIR_PLAIN


def test_race_bound_round_trip_reads_through_the_folders(root: Path, tmp_path: Path):
    # Fast Blade composed per race from its own events, then read back: the nested folders
    # hide nothing from the inspector. Every race's DAT has the source's timeline and
    # total, and that race's clips and trace.
    src = ai.inspect_target(ai.resolve_targets("ws:1:HumeMale")[0])
    recipe = ac.recipe_from("ws:1")
    out = ac.compose(recipe)
    assert [c.race for c in out] == list(ac.RACE_NAMES)
    for c in out:
        _ws_body(c, "ws1_")
        p = tmp_path / ac.output_name(recipe, c)
        p.write_bytes(c.data)
        rebuilt = ai.inspect_target(ai.Target(p.stem, p, p.name))
        assert _timeline_key(rebuilt) == _timeline_key(src), c.race
        assert rebuilt["total"] == src["total"]
        assert set(rebuilt["clips"]) == {"b000", "b001", "b020", "b021"}, c.race
        assert rebuilt["traces"] == ["b0a0"]
        assert set(rebuilt["generators"]) == set(src["generators"])
        assert [n["name"] for n in rebuilt["tree"]] == [c.data[:4].decode()]


# ── Commands of no lane: locks, hits and links added by hand ─────────────────────

_LINK_MDAM = "03040000000000006d64616d00000000"          # 0x03 LinkRoutine `mdam`, retail's hit
EXAMPLES = json.loads((Path(__file__).resolve().parents[1] / "schema" / "ability_recipe.json")
                      .read_text(encoding="utf-8"))["examples"]


def _named(op: int, ref: str) -> bytes:
    """A command naming ``ref`` at +8, delay 0: the op's template, else the 16-byte link body."""
    body = bytearray(ac._TEMPLATES.get(op) or ac._TEMPLATES[0x03])
    body[0] = op
    return ac._stamp(bytes(body), delay=0, ev={}, ref=ref)


def _main_refs(data: bytes, tmp_path: Path) -> list:
    """``(start, op, ref)`` of each command of the composed ``main`` itself (not of what it links)."""
    p = tmp_path / "out.DAT"
    p.write_bytes(data)
    return [(e["start"], e["op"], e["ref"]) for e in ai.flatten(ai.Model.load(p), "main") if not e["via"]]


def test_a_shared_link_keeps_its_name_where_the_lane_renames_a_section_of_it(tmp_path: Path):
    # Lane b's sound pointer `mdam` is not lane a's, so b's goes out as `mda0` and what b
    # plays by that sound follows it. b's links to the shared routine `mdam` (ROM/0/0's
    # hit) do not: not the recipe's own, not the one inside b's carried `tgt0`, and not a
    # command of no lane, which no lane's rename reaches.
    a, b = tmp_path / "a.DAT", tmp_path / "b.DAT"
    a.write_bytes(ac._dir("aaaa", ac._DIR_DATA) + _section("mdam", ai.T_SOUND) + ac._END_SECTION)
    tgt0 = ac.build_routine("tgt0", [_named(0x0A, "mdam"), _named(0x03, "mdam")], 0)
    b.write_bytes(ac._dir("bbbb", ac._DIR_DATA) + _section("mdam", ai.T_SOUND, b"\1" * 16) + tgt0 + ac._END_SECTION)
    recipe = {"name": "t", "sources": {"a": {"spec": str(a), "routine": None}, "b": {"spec": str(b), "routine": None}},
              "events": [{"from": "a", "op": 0x0A, "ref": "mdam", "start": 0},
                         {"from": "b", "op": 0x03, "ref": "tgt0", "start": 5},
                         {"from": "b", "op": 0x0A, "ref": "mdam", "start": 8},
                         {"from": "b", "op": 0x03, "ref": "mdam", "start": 10},
                         {"op": 0x03, "ref": "mdam", "start": 12, "raw": _LINK_MDAM}]}
    assert ac.validate_recipe(recipe) == []
    (c,) = ac.compose(recipe)
    assert c.renames == {"b:mdam": "mda0"}
    assert [(t["op"], t["ref"], t["from"]) for t in c.timeline] == [
        (0x0A, "mdam", "a"), (0x03, "tgt0", "b"), (0x0A, "mda0", "b"), (0x03, "mdam", "b"), (0x03, "mdam", None)]
    assert _main_refs(c.data, tmp_path) == [(0, 0x0A, "mdam"), (5, 0x03, "tgt0"), (8, 0x0A, "mda0"),
                                            (10, 0x03, "mdam"), (12, 0x03, "mdam")]
    assert [(e["op"], e["ref"]) for e in ai.flatten(ai.Model.load(tmp_path / "out.DAT"), "tgt0")] == [
        (0x0A, "mda0"), (0x03, "mdam")]
    assert c.warnings == []
    # Both lanes' own `tgt0` routines differ, so b's is renamed: b's link to its own
    # `tgt0` follows it, while b's link to the shared `mdam` and a command of no lane
    # naming `tgt0` keep their names.
    a.write_bytes(ac._dir("aaaa", ac._DIR_DATA) + ac.build_routine("tgt0", [_named(0x03, "proc")], 0) + ac._END_SECTION)
    both = dict(recipe, events=[{"from": "a", "op": 0x03, "ref": "tgt0", "start": 0},
                                {"from": "b", "op": 0x03, "ref": "tgt0", "start": 5},
                                {"from": "b", "op": 0x03, "ref": "mdam", "start": 10},
                                {"op": 0x03, "ref": "tgt0", "start": 12, "raw": _named(0x03, "tgt0").hex()}])
    (c,) = ac.compose(both)
    new = c.renames["b:tgt0"]
    assert new != "tgt0" and [t["ref"] for t in c.timeline] == ["tgt0", new, "mdam", "tgt0"]


def test_a_link_the_game_will_not_find_is_written_with_a_warning(tmp_path: Path, monkeypatch):
    # Where the client looks depends on the op: 0x03 / 0x3B / 0x5F in this DAT, then
    # ROM/0/0, never on an actor; 0x3C among the caster's own routines only; 0x09 on the
    # target and 0x57 / 0x85 on the caster, then ROM/0/0. ROM/0/0 stands in as {mdam, wash}.
    monkeypatch.setattr(ac, "_sys_routines", lambda: frozenset({"mdam", "wash"}))
    src = _dat(tmp_path, "src.DAT", [ac.build_routine("tgt0", [_named(0x03, "mdam")], 0)])

    def link(op, ref, start):
        return {"op": op, "ref": ref, "start": start, "raw": _named(op, ref).hex()}
    recipe = {"name": "t", "sources": {"m": {"spec": str(src), "routine": None}},
              "events": [{"from": "m", "op": 0x03, "ref": "tgt0", "start": 0},     # this DAT's
                         link(0x03, "mdam", 1), link(0x09, "mdam", 2),             # ROM/0/0's
                         link(0x3C, "shbk", 3), link(0x57, "shbk", 4),             # the caster's release
                         link(0x03, "zzzz", 5), link(0x03, "shbk", 6), link(0x3C, "wash", 7),
                         link(0x09, "damg", 8), link(0x5F, "ner9", 9)]}
    assert ac.validate_recipe(recipe) == []
    (c,) = ac.compose(recipe)
    assert c.warnings == [
        "link zzzz (0x03) at frame 5 resolves nowhere: zzzz is not a routine of this DAT or ROM/0/0, nor a race "
        "schedule; the game skips it",
        "link shbk (0x03) at frame 6: 0x03 looks in this DAT and ROM/0/0, never on the caster, so the race "
        "schedule shbk is not found; retail links it with 0x3C",
        "link wash (0x3C) at frame 7: 0x3C looks only among the caster's own routines, and wash is ROM/0/0's; "
        "link it with 0x03, or 0x3B to wait for it",
        "link damg (0x09) at frame 8 resolves nowhere: damg is not a routine of this DAT or ROM/0/0, nor a race "
        "schedule; the game skips it unless the target has a routine of that name",
        "link ner9 (0x5F) at frame 9 resolves nowhere: ner9 is not a routine of this DAT or ROM/0/0, nor a race "
        "schedule; the game skips it"]
    # The commands are written all the same, and without an install to read ROM/0/0 from
    # nothing is said.
    assert len(c.timeline) == 10
    monkeypatch.setattr(ac, "_sys_routines", lambda: None)
    assert ac.compose(recipe)[0].warnings == []


def test_a_link_on_an_actor_to_a_routine_of_this_dat_is_written_with_a_warning(tmp_path: Path, monkeypatch):
    # 0x09 looks among the target's routines and 0x3C among the caster's own, never in
    # this DAT; 0x57 finds this DAT's routine only when the DAT is attached to the caster,
    # as a race-bound weapon-skill body is. 0x3B looks in this DAT. ROM/0/0 stands in as
    # {mdam, wash}.
    monkeypatch.setattr(ac, "_sys_routines", lambda: frozenset({"mdam", "wash"}))
    src = _dat(tmp_path, "src.DAT", [ac.build_routine("tgt0", [_named(0x03, "mdam")], 0)])

    def link(op, start):
        return {"op": op, "ref": "tgt0", "start": start, "raw": _named(op, "tgt0").hex()}
    recipe = {"name": "t", "dir": "0011", "sources": {"m": {"spec": str(src), "routine": None}},
              "events": [{"from": "m", "op": 0x03, "ref": "tgt0", "start": 0},     # carries tgt0
                         link(0x09, 1), link(0x3C, 2), link(0x57, 3), link(0x3B, 4)]}
    assert ac.validate_recipe(recipe) == []
    fix = "never in this DAT, so this DAT's tgt0 does not play; link it with 0x03, or 0x3B to wait for it"
    on_target = f"link tgt0 (0x09) at frame 1: 0x09 looks among the target's own routines and then ROM/0/0, {fix}"
    on_caster = f"link tgt0 (0x3C) at frame 2: 0x3C looks among the caster's own routines, {fix}"
    attached = f"link tgt0 (0x57) at frame 3: 0x57 looks among the caster's own routines and then ROM/0/0, {fix}"
    (c,) = ac.compose(recipe)
    assert c.warnings == [on_target, on_caster, attached]
    # One race's body is attached to the caster: 0x57 finds tgt0 there, 0x09 and 0x3C still do not.
    assert ac.compose_once(recipe, ac._lanes(recipe), "Galka").warnings == [on_target, on_caster]
    # Where the op looks does not depend on ROM/0/0, so the install is not needed to say it.
    monkeypatch.setattr(ac, "_sys_routines", lambda: None)
    assert ac.compose(recipe)[0].warnings == [on_target, on_caster, attached]


def test_hand_added_locks_and_hit_compose_at_their_frames(root: Path, tmp_path: Path):
    # Fast Blade's own recipe, plus a target-status lock, a control lock and an `mdam` hit
    # added by hand as commands of no lane: the rebuilt DAT reads as Fast Blade with the
    # three at the frames they were given.
    src = ai.inspect_target(ai.resolve_targets("ws:1:HumeMale")[0])
    recipe = ac.recipe_from("ws:1:HumeMale")
    recipe["events"] += [{"op": 0x20, "start": 5, "dur": 50, "raw": "200300000000320000000000", "name": "LockActorStatus"},
                         {"op": 0x2E, "start": 5, "dur": 60, "raw": "2e02000000003c00"},
                         {"op": 0x03, "ref": "mdam", "start": 90, "raw": _LINK_MDAM}]
    assert ac.validate_recipe(recipe) == []
    (c,) = ac.compose(recipe)
    assert [t for t in c.timeline if t["from"] is None] == [
        {"start": 5, "op": 0x20, "ref": None, "dur": 50, "from": None},
        {"start": 5, "op": 0x2E, "ref": None, "dur": 60, "from": None},
        {"start": 90, "op": 0x03, "ref": "mdam", "dur": None, "from": None}]
    assert c.warnings == [] and c.renames == {}
    p = tmp_path / "fb.DAT"
    p.write_bytes(c.data)
    rebuilt = ai.inspect_target(ai.Target("fb", p, "fb.DAT"))
    added = [(5, 0x20, None, 50), (5, 0x2E, None, 60), (90, 0x03, "mdam", 0)]
    assert _timeline_key(rebuilt) == sorted(_timeline_key(src) + added, key=lambda e: e[0])
    assert rebuilt["total"] == src["total"]


def test_fast_blades_own_locks_and_hit_need_no_lane(root: Path):
    # Fast Blade's target lock, control lock and `mdam` link with their lane taken away
    # compose to the same bytes: the locks are their own bytes and the link names a shared
    # routine. The same holds on a race-bound lane, where each race's DAT has them.
    for spec, race in (("ws:1:HumeMale", None), ("ws:1", "Galka")):
        recipe = ac.recipe_from(spec)
        by_hand = copy.deepcopy(recipe)
        moved = [ev for ev in by_hand["events"] if ev["op"] in (0x20, 0x2E) or ev["ref"] == "mdam"]
        for ev in moved:
            del ev["from"]
        assert [(ev["start"], ev["op"]) for ev in moved] == [(0, 0x20), (0, 0x2E), (80, 0x03)]
        assert ac.validate_recipe(by_hand) == []
        (want,), (got,) = ac.compose(recipe, race), ac.compose(by_hand, race)
        assert got.race == race and got.data == want.data
        assert [(t["start"], t["op"], t["ref"]) for t in got.timeline if t["from"] is None] == [
            (0, 0x20, None), (0, 0x2E, None), (80, 0x03, "mdam")]


def test_a_command_of_no_lane_goes_into_a_race_built_without_its_lane(root: Path):
    # HumeF Variations is Hume Female's alone: Galka's DAT is built without that lane's
    # events, but a command of no lane belongs to no lane and is in every race's DAT.
    recipe = {"name": "var", "sources": {"motion": {"spec": "ROM/173/48.DAT", "routine": None}},
              "events": [{"from": "motion", "op": 5, "ref": "ol6?", "start": 0, "dur": 30},
                         {"op": 0x03, "ref": "mdam", "start": 20, "raw": _LINK_MDAM}]}
    (hf,) = ac.compose(recipe, "HumeFemale")
    (galka,) = ac.compose(recipe, "Galka")
    assert [(t["op"], t["from"]) for t in hf.timeline] == [(0x05, "motion"), (0x03, None)]
    assert [(t["start"], t["op"], t["ref"], t["from"]) for t in galka.timeline] == [(20, 0x03, "mdam", None)]
    assert len(galka.warnings) == 1 and "HumeFemale only" in galka.warnings[0], galka.warnings


def test_schema_example_adds_fast_blades_locks_by_hand(root: Path, tmp_path: Path):
    # The schema's fourth example: Fast Blade's swings, trace, sparks and sounds from its
    # own DAT, its locks, flash, hit and added effect by hand. Every event reads back at its
    # frame, as retail's Fast Blade has it, and the local hit routines come along.
    example = EXAMPLES[3]
    assert ac.validate_recipe(example) == []
    (c,) = ac.compose(example, "HumeMale")
    assert c.warnings == [] and c.renames == {}
    assert {"hit1(0x07)", "hit2(0x07)"} <= set(c.sections)
    assert [(t["start"], t["op"], t["ref"]) for t in c.timeline if t["from"] is None] == [
        (0, 0x20, None), (0, 0x59, None), (0, 0x2E, None), (10, 0x03, "eis1"), (80, 0x03, "mdam"), (100, 0x03, "proc")]
    got = _main_refs(c.data, tmp_path)
    assert got == [(ev["start"], ev["op"], ev.get("ref")) for ev in example["events"]]
    retail = ai.inspect_target(ai.resolve_targets("ws:1:HumeMale")[0])["timeline"]
    assert set(got) <= {(e["start"], e["op"], e["ref"]) for e in retail if not e["via"]}
