"""Names and categories for entity models, read out of the server's SQL dumps.

The client DATs know a model's *shape* but not its *name* — the name lives on the
server. ``mob_pools`` and ``npc_list`` both carry a 20-byte ``look`` blob whose
first two fields are ``u16 lookType`` and ``u16 modelId``; when ``lookType`` is 0
the entity wears a monster/NPC model and ``modelId`` is the id that
``entity.xi_core.modelid_to_file_id`` turns into a file_id.

``mob_family_system.ecosystem`` supplies the grouping the curated ``npcs.json``
already uses (Amorphs, Aquans, Beastmen, …), so auto-added mobs land in the same
buckets a human would have picked.

Everything degrades to empty when ``XI_SERVER_DIR`` is unset — the model list
still builds, entries just come through unnamed.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# ecosystem (mob_family_system) → the category name npcs.json already uses.
ECOSYSTEM_CATEGORY = {
    "Amorph": "Amorphs",
    "Aquan": "Aquans",
    "Arcana": "Arcana",
    "ArchaicMachine": "Gears",
    "Avatar": "Avatars",
    "Beast": "Beasts",
    "Beastmen": "Beastmen",
    "Bird": "Birds",
    "Demon": "Demons",
    "Dragon": "Dragons",
    "Elemental": "Elementals",
    "Empty": "Promyvion",
    "Fairy": "Unclassified",
    "Humanoid": "Primary NPC",
    "Lizard": "Lizards",
    "Luminian": "Naakuals",
    "Luminion": "Naakuals",
    "Obstacle": "Rare Objects and Furnishings",
    "Plantoid": "Plantoids",
    "Unclassified": "Unclassified",
    "Undead": "Undead",
    "Vermin": "Vermin",
    "Voragean": "Unclassified",
}

# Where auto-added entries go when the server has no name for the model.
UNNAMED_CATEGORY = "Unsorted Models"
# …and when only npc_list (not mob_pools) names it, so there is no ecosystem.
NPC_CATEGORY = "Unsorted NPCs"


def _sql_dir() -> Path | None:
    from xi.xi_config import XI_SERVER_DIR
    if not XI_SERVER_DIR:
        return None
    d = Path(XI_SERVER_DIR) / "sql"
    return d if d.is_dir() else None


def _read(name: str) -> str:
    d = _sql_dir()
    if d is None:
        return ""
    p = d / name
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def _look_model(blob: str) -> tuple[int, int] | None:
    """``0x0000320000…`` → ``(lookType, modelId)``; None if the blob is too short."""
    raw = blob[2:] if blob[:2].lower() == "0x" else blob
    if len(raw) < 8:
        return None
    b = bytes.fromhex(raw[:8])
    return int.from_bytes(b[0:2], "little"), int.from_bytes(b[2:4], "little")


def _title(name: str) -> str:
    return " ".join(p.capitalize() for p in (name or "").replace("_", " ").split())


# Placeholder rows the server keeps around: they name a slot, not a model.
_JUNK_NAME = re.compile(r"^(blank|dummy|none|npc(\[\d+\])?|\?+|-+)$", re.I)


def _real_name(*candidates: str) -> str | None:
    """First candidate that is an actual name rather than a placeholder."""
    for c in candidates:
        c = (c or "").strip()
        if c and not _JUNK_NAME.match(c):
            return c
    return None


@lru_cache(maxsize=1)
def family_ecosystem() -> dict[int, str]:
    """``familyID → ecosystem`` from mob_family_system.sql."""
    text = _read("mob_family_system.sql")
    rows = re.findall(
        r"INSERT INTO `mob_family_system` VALUES \((\d+),'[^']*',\d+,'[^']*',\d+,'([^']*)'",
        text, re.I,
    )
    return {int(fam): eco for fam, eco in rows}


@lru_cache(maxsize=1)
def model_names() -> dict[int, dict]:
    """``modelId → {name, category, source}`` merged from mob_pools then npc_list.

    mob_pools wins: a model used by a real monster gets that monster's name and
    its family's ecosystem. npc_list only fills in models no mob uses.
    """
    eco_by_family = family_ecosystem()
    out: dict[int, dict] = {}

    mobs = re.findall(
        r"INSERT INTO `mob_pools` VALUES \(\d+,'([^']*)','([^']*)',(\d+),(0x[0-9A-Fa-f]+),",
        _read("mob_pools.sql"), re.I,
    )
    for name, packet_name, family, blob in mobs:
        look = _look_model(blob)
        if look is None or look[0] != 0 or look[1] == 0:
            continue
        model_id = look[1]
        if model_id in out:
            continue
        label = _real_name(packet_name, name)
        if label is None:
            continue
        eco = eco_by_family.get(int(family))
        out[model_id] = {
            "name": _title(label),
            "category": ECOSYSTEM_CATEGORY.get(eco or "", UNNAMED_CATEGORY),
            "source": "mob_pools",
        }

    npcs = re.findall(
        r"INSERT INTO `npc_list` VALUES \(\d+,'([^']*)','([^']*)',"
        r"[^)]*?,(0x[0-9A-Fa-f]{40}),",
        _read("npc_list.sql"), re.I,
    )
    for name, polutils_name, blob in npcs:
        look = _look_model(blob)
        if look is None or look[0] != 0 or look[1] == 0:
            continue
        model_id = look[1]
        if model_id in out:
            continue
        label = _real_name(polutils_name, _title(name))
        if label is None:
            continue
        out[model_id] = {"name": label, "category": NPC_CATEGORY, "source": "npc_list"}

    return out


@lru_cache(maxsize=1)
def weapon_skill_anims() -> list[tuple[int, str, int]]:
    """``(weaponskillid, name, animation)`` from weapon_skills.sql."""
    rows = re.findall(
        r"INSERT INTO `weapon_skills` VALUES \((\d+),'([^']*)',0x[0-9A-Fa-f]+,\d+,\d+,\d+,(\d+),",
        _read("weapon_skills.sql"), re.I,
    )
    return [(int(i), _title(n), int(a)) for i, n, a in rows]
