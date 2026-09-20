"""Small hand-made LandSandBoat trees for the server tests: the three xi_map sources
the weapon-skill widen reads, and a ``scripts/actions`` layout for the Lua stub. Never
a copy of a real server (and never its settings/network.lua)."""
import os
from pathlib import Path

BATTLEUTILS = """#include "battleutils.h"

namespace battleutils
{
    void LoadWeaponSkillsList()
    {
        PWeaponSkill->setType(rset->get<uint8>("type"));
        PWeaponSkill->setSkillLevel(rset->get<uint16>("skilllevel"));
        PWeaponSkill->setElement(rset->get<uint8>("element"));
        PWeaponSkill->setAnimationId(rset->get<uint8>("animation"));
        PWeaponSkill->setAnimationTime(std::chrono::milliseconds(rset->get<uint32>("animationTime")));
        PWeaponSkill->setRange(rset->get<uint8>("range"));
        PWeaponSkill->setAoe(rset->get<uint8>("aoe"));
    }
}
"""

WEAPON_SKILL_H = """#pragma once

class CWeaponSkill
{
public:
    void setAoe(uint8 aoe);
    void setRadius(uint8 radius);
    void setAnimationId(uint8 id);
    void setAnimationTime(timer::duration time);
    void setType(uint8 type);

private:
    uint8                          m_TypeID;
    uint16                         m_Skilllevel;
    uint8                          m_AnimationId;
    timer::duration                m_AnimationTime{};
    uint8                          m_Element;
};
"""

WEAPON_SKILL_CPP = """#include "weapon_skill.h"

void CWeaponSkill::setName(const std::string& name)
{
    m_name = name;
}

void CWeaponSkill::setAnimationId(const uint8 id)
{
    m_AnimationId = id;
}
"""

STOCK = {
    "src/map/utils/battleutils.cpp": BATTLEUTILS,
    "src/map/weapon_skill.h": WEAPON_SKILL_H,
    "src/map/weapon_skill.cpp": WEAPON_SKILL_CPP,
}

BOM = b"\xef\xbb\xbf"


def ws_tree(root: Path, *, files: dict | None = None, bom=True) -> Path:
    """The three sources under ``root`` (LF). ``bom``: True, False, or a set of the
    relative paths that get one."""
    for rel, text in (files or STOCK).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        has = bom if isinstance(bom, bool) else rel in bom
        p.write_bytes((BOM if has else b"") + text.encode("utf-8"))
    return root


def tree_bytes(root: Path) -> dict:
    """Every file under ``root`` with its bytes (to show nothing changed)."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def xi_map(root: Path, *, member=None, exe_time=None, pdb=True, pdb_time=None) -> None:
    """A fake xi_map.exe (+ xi_map.pdb holding one LF_MEMBER m_AnimationId record of
    type 0x20 uint8 / 0x21 uint16 when ``member`` is given)."""
    import struct
    exe = root / "xi_map.exe"
    exe.write_bytes(b"MZ fake")
    if pdb:
        blob = b"\x00" * 64
        if member:
            typ = {"uint8": 0x20, "uint16": 0x21}[member]
            blob += struct.pack("<HHIH", 0x150D, 3, typ, 30) + b"m_AnimationId\x00" + b"\x00" * 16
        (root / "xi_map.pdb").write_bytes(blob)
    elif (root / "xi_map.pdb").exists():
        (root / "xi_map.pdb").unlink()
    if exe_time is not None:
        os.utime(exe, (exe_time, exe_time))
        if pdb:
            t = pdb_time if pdb_time is not None else exe_time
            os.utime(root / "xi_map.pdb", (t, t))


def set_times(root: Path, t: float) -> None:
    for rel in STOCK:
        os.utime(root / rel, (t, t))


SPELL_FIRE = """-----------------------------------
-- Spell: Fire
-----------------------------------
local spellObject = {}

spellObject.onMagicCastingCheck = function(caster, target, spell)
    return 0
end

spellObject.onSpellCast = function(caster, target, spell)
    return xi.spells.damage.useDamageSpell(caster, target, spell)
end

return spellObject
"""


def scripts_tree(root: Path) -> Path:
    """A ``scripts/actions`` layout with a few donors, the spell helper table, and a
    module that uses the name ``hexed``."""
    a = root / "scripts" / "actions"
    for f in ("songs", "black", "blue", "ninjutsu", "summoning", "white", "geomancy", "trust"):
        (a / "spells" / f).mkdir(parents=True, exist_ok=True)
    (a / "abilities" / "pets").mkdir(parents=True, exist_ok=True)
    (a / "weaponskills").mkdir(parents=True, exist_ok=True)
    (a / "spells" / "black" / "fire.lua").write_text(SPELL_FIRE, encoding="utf-8", newline="\n")
    (a / "spells" / "white" / "cure.lua").write_text(SPELL_FIRE.replace("Fire", "Cure"), encoding="utf-8", newline="\n")
    (a / "abilities" / "provoke.lua").write_text(
        "local abilityObject = {}\nabilityObject.onAbilityCheck = function(player, target, ability)\n"
        "    return 0, 0\nend\nabilityObject.onUseAbility = function(player, target, ability)\nend\n"
        "return abilityObject\n", encoding="utf-8", newline="\n")
    (a / "abilities" / "corsairs_roll.lua").write_text(
        "local abilityObject = {}\nabilityObject.onUseAbility = function(caster, target, ability, action)\n"
        "    return xi.job_utils.corsair.useRoll(caster, target, ability, action)\nend\n"
        "return abilityObject\n", encoding="utf-8", newline="\n")
    (a / "abilities" / "box_step.lua").write_text(
        "local abilityObject = {}\nabilityObject.onUseAbility = function(player, target, ability, action)\n"
        "    return xi.job_utils.dancer.useStepAbility(player, target, ability, action)\nend\n"
        "return abilityObject\n", encoding="utf-8", newline="\n")
    (a / "weaponskills" / "fast_blade.lua").write_text(
        "local weaponskillObject = {}\nweaponskillObject.onUseWeaponSkill = function(player, target, wsID, tp, primary, action, taChar)\n"
        "    return 1, 0, false, 10\nend\nreturn weaponskillObject\n", encoding="utf-8", newline="\n")
    g = root / "scripts" / "globals" / "spells"
    g.mkdir(parents=True, exist_ok=True)
    (g / "damage_spell.lua").write_text("xi = xi or {}\nlocal pTable =\n{\n    [144] = { 1 },\n}\n",
                                        encoding="utf-8", newline="\n")
    m = root / "modules" / "x" / "lua"
    m.mkdir(parents=True, exist_ok=True)
    (m / "y.lua").write_text("local m = Module:new('x')\nm:addOverride('xi.actions.spells.black.hexed.onSpellCast', "
                             "function() end)\nreturn m\n", encoding="utf-8", newline="\n")
    return root
