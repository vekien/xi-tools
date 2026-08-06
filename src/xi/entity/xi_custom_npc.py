"""Custom NPC registry — register an already-placed entity model as a zone NPC.

A "custom NPC" is a fixed-model (monster/entity/object) NPC that reuses a model id
already injected into the FTABLE (via ``xi dats`` / ``xi entity inject``). This
module never touches DATs: it only

  * confirms a model id resolves in the FTABLE,
  * allocates a zone-local ``npcid`` and builds the 20-byte ``look`` blob,
  * persists the record in a per-project registry (``custom-npcs.json``), and
  * generates the ``npc_list`` SQL that makes the model appear in-game.

The registry is the single source of truth consumed by the editor's Asset Browser
(Custom NPCs section), the Package wizard (bundled SQL) and the cutscene author
(Custom NPCs actor group + on-stage preview via [[parse_look]]).

Registry file: ``custom-npcs.json`` at the active project workspace root::

    {"npcs": [{npcid, npcidHex, name, modelid, fileId, datRel, zoneId, zoneName,
               look, pos:[x, y, z], rot, created}]}

``npcid`` is the FFXI server entity id: ``0x01000000 | (zoneId << 12) | localIndex``.
"""

import json
import struct
import time
from pathlib import Path

from xi.entity.xi_core import modelid_to_file_id

# Valid zone-local targid band for a STATIC NPC. The FFXI entity id reserves bit 0x800 as
# the "dynamic entity" flag (server reinterprets id & 0x800 → runtime pool, see CatsEyeXI
# zoneutils.cpp:133), and 0x0E entity_update packets are only valid for targids 0..0x3FF and
# 0x700..0x8FF — with 0x400..0x6FF being the PLAYER range (0x0D char_update) and 0x700..0x8FF
# the dynamic pool. So a static npc_list NPC must live in 0x001..0x3FF. Retail data agrees:
# the busiest zone tops out at 0x3F5. We allocate just above a zone's existing NPCs/mobs.
NPC_STATIC_TARGID_MIN = 0x001
NPC_STATIC_TARGID_MAX = 0x3FF

# Reserved CatsEyeXI custom-NPC band: customs are allocated from here upward, never from
# just-above-retail (which is exactly where new upstream content lands). Same convention as
# MODEL_SAFE_START for entity model ids — a fixed, documented base keeps custom ids stable
# and self-identifying (local >= this ⇒ ours).
#
# Chosen from the retail data: NPC locals are dense to ~0x300 and thin out fast above it
# (>=0x300: 2679 npcs/35 zones · >=0x380: 393/13 · >=0x3C0: 89/4), the global peak is 0x3F5,
# and mobs — which share this per-zone targid space — never exceed 0x364. So 0x380..0x3FF is
# free of mobs entirely and empty in ~97% of zones, giving 128 custom NPCs per zone. The 13
# zones with retail up here are handled by allocating the first FREE slot, not base+n.
CUSTOM_NPC_LOCAL_START = 0x380

# ``npc_list`` column defaults for a fixed-model CUTSCENE NPC. Mirrors how retail stages
# its cutscene cast — e.g. Qufim's Lion (0x0107E203) and Iroha (0x0107E202): status 6,
# entityFlags 27, pos (0,0,0).
#
# ☠ status 6 = CUTSCENE_ONLY is deliberate and is what makes the NPC invisible in the zone
# but visible in the cutscene. Two delivery paths exist and only one is status-gated:
#   * CZoneEntities::SpawnNPCs — push, per tick, gated on NORMAL/UPDATE. status 6 is skipped,
#     so the NPC never stands around in the zone.
#   * GP_CLI_COMMAND_CHARREQ (packet 0x016) — pull, *ignoring status*. The client sends it
#     from event-init for every actor whose event-DAT block lists the running event id (the
#     involvement mini-blocks xi_compile step 5b writes) — NOT merely because an opcode
#     references the targid. Without the block the entity is never requested and 0x4E/0xBA
#     silently no-op.
# So the cutscene's own opcodes reveal (0x4E) and position (0xBA) the actor (hence retail's
# pos 0,0,0). Setting status 0 makes it a permanently-standing zone NPC instead — not what a
# cutscene actor wants. New rows only spawn at zone boot: the server must be restarted.
NPC_DEFAULTS = {
    "flag": 1, "speed": 50, "speedsub": 50, "animation": 0, "animationsub": 0,
    "namevis": 0, "status": 6, "entityFlags": 27, "name_prefix": 0, "widescan": 1,
}

# ``npc_list.status`` (CatsEyeXI / LSB STATUS_TYPE). Editable from the Asset Browser.
NPC_STATUS_NORMAL = 0
NPC_STATUS_DISAPPEAR = 2
NPC_STATUS_INVISIBLE = 3
NPC_STATUS_CUTSCENE_ONLY = 6
NPC_STATUS_CHOICES = (
    (NPC_STATUS_NORMAL, "Normal (visible)"),
    (NPC_STATUS_DISAPPEAR, "Disappear (hidden)"),
    (NPC_STATUS_INVISIBLE, "Invisible"),
    (NPC_STATUS_CUTSCENE_ONLY, "Cutscene only"),
)
NPC_STATUS_VALUES = {v for v, _ in NPC_STATUS_CHOICES}


def normalize_status(value, default: int = NPC_STATUS_NORMAL) -> int:
    """Coerce a user/registry status to a known STATUS_TYPE (falls back to ``default``)."""
    try:
        s = int(value)
    except (TypeError, ValueError):
        return int(default)
    return s if s in NPC_STATUS_VALUES else int(default)

# Full column list (declaration order) for generated / live npc_list writes.
NPC_COLUMNS = (
    "npcid", "name", "polutils_name", "pos_rot", "pos_x", "pos_y", "pos_z",
    "flag", "speed", "speedsub", "animation", "animationsub", "namevis",
    "status", "entityFlags", "look", "name_prefix", "content_tag", "widescan",
)


# ---------------------------------------------------------------------------
# npcid / look helpers
# ---------------------------------------------------------------------------

def zone_of(npcid: int) -> int:
    """Zone id embedded in a server entity id."""
    return (npcid >> 12) & 0xFFF

def local_of(npcid: int) -> int:
    """Zone-local index embedded in a server entity id."""
    return npcid & 0xFFF

def make_npcid(zone_id: int, local: int) -> int:
    return 0x01000000 | ((zone_id & 0xFFF) << 12) | (local & 0xFFF)

def look_bytes(modelid: int) -> bytes:
    """20-byte fixed-model ``look``: ``size=0`` (u16) + ``modelid`` (u16 LE) + 16 zero
    bytes — the monster/object appearance format decoded by [[parse_look]]."""
    return struct.pack("<HH", 0, modelid & 0xFFFF) + bytes(16)


def resolve_model(modelid: int):
    """Confirm ``modelid`` is registered in the FTABLE. Returns ``(file_id, dat_rel)``
    (``dat_rel`` may be ``None`` if the FTABLE has the slot but no path). Raises
    ``ValueError`` when the model isn't placed — the caller surfaces it to the user."""
    from xi.ftable.xi_core import scan_file_ids
    file_id = modelid_to_file_id(int(modelid))
    hits = scan_file_ids([file_id])
    if not hits:
        raise ValueError(
            f"model {modelid} (file {file_id}) is not registered in the FTABLE — "
            f"place its DAT first with `xi dats` / `xi entity inject`.")
    return file_id, hits[0].get("dat")


# ---------------------------------------------------------------------------
# Registry load / save / mutate
# ---------------------------------------------------------------------------

def load_registry(path: Path) -> dict:
    """Read the ``custom-npcs.json`` registry (empty ``{"npcs": []}`` if absent)."""
    reg: dict = {}
    if path.exists():
        try:
            reg = json.loads(path.read_text(encoding="utf-8")) or {}
        except (ValueError, OSError):
            reg = {}
    if not isinstance(reg.get("npcs"), list):
        reg["npcs"] = []
    return reg

def save_registry(path: Path, reg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def alloc_local(zone_id: int, reg: dict, used=None) -> int:
    """First FREE zone-local targid in the reserved custom band for ``zone_id``.

    Scans :data:`CUSTOM_NPC_LOCAL_START`..:data:`NPC_STATIC_TARGID_MAX` and returns the
    first slot nothing else claims — so a custom id sits far above where retail grows
    rather than immediately after it, and the 13 zones with retail NPCs up here are
    skipped over instead of collided with.

    ``used`` = locals already claimed in this zone from every source that can own a
    targid: live ``npc_list`` + ``mob_spawn_points`` (mobs share this space) and Event DAT
    actors (retail NPCs the DB may have no row for — colliding with one makes the cutscene
    picker show the retail NPC for the same option value). Registry entries for the zone
    are folded in automatically. Raises ``ValueError`` when the band is exhausted, so we
    never hand back an id in the player (0x400-0x6FF) or dynamic (0x800 bit) ranges.
    """
    occupied = {int(x) for x in (used or []) if 0 < int(x) <= NPC_STATIC_TARGID_MAX}
    occupied |= {local_of(int(n["npcid"])) for n in reg.get("npcs", [])
                 if zone_of(int(n["npcid"])) == zone_id}
    for local in range(CUSTOM_NPC_LOCAL_START, NPC_STATIC_TARGID_MAX + 1):
        if local not in occupied:
            return local
    raise ValueError(
        f"zone {zone_id}: the custom-NPC band "
        f"(0x{CUSTOM_NPC_LOCAL_START:X}-0x{NPC_STATIC_TARGID_MAX:X}, "
        f"{NPC_STATIC_TARGID_MAX - CUSTOM_NPC_LOCAL_START + 1} slots) is full")


def make_record(zone_id: int, zone_name: str, name: str, modelid: int,
                npcid: int, file_id: int, dat_rel, pos=None, rot: int = 0,
                status: int = NPC_STATUS_CUTSCENE_ONLY) -> dict:
    """Assemble a registry record for one custom NPC.

    Defaults match retail cutscene cast (Qufim Lion/Iroha): status 6 CUTSCENE_ONLY,
    pos (0,0,0). SpawnNPCs skips status 6 so the NPC is invisible in the zone until an
    event CHARREQs it; the cutscene's 0xBA then stages it.
    """
    st = normalize_status(status, NPC_STATUS_CUTSCENE_ONLY)
    # Cutscene-only NPCs always stage at origin in npc_list (retail Lion/Iroha). The
    # event's 0xBA owns the on-stage position — a non-zero DB pos makes them pop at
    # that world spot if status is ever wrong, and confuses CHARREQ spawn.
    if st == NPC_STATUS_CUTSCENE_ONLY:
        pos = [0.0, 0.0, 0.0]
    return {
        "npcid": int(npcid),
        "npcidHex": f"0x{int(npcid):08X}",
        "name": name,
        "modelid": int(modelid),
        "fileId": int(file_id),
        "datRel": dat_rel,
        "zoneId": int(zone_id),
        "zoneName": zone_name or "",
        "look": look_bytes(modelid).hex(),
        "pos": [float((pos or [0, 0, 0])[0]), float((pos or [0, 0, 0])[1]),
                float((pos or [0, 0, 0])[2])],
        "rot": int(rot or 0) & 0xFF,
        "status": st,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def upsert(reg: dict, record: dict) -> dict:
    """Insert/replace a record by ``npcid``. Returns the registry (mutated in place)."""
    npcs = reg.setdefault("npcs", [])
    for i, n in enumerate(npcs):
        if int(n.get("npcid", -1)) == int(record["npcid"]):
            npcs[i] = record
            return reg
    npcs.append(record)
    return reg


def remove(reg: dict, npcid: int) -> bool:
    """Drop the record for ``npcid``. Returns True if one was removed."""
    npcs = reg.get("npcs", [])
    before = len(npcs)
    reg["npcs"] = [n for n in npcs if int(n.get("npcid", -1)) != int(npcid)]
    return len(reg["npcs"]) < before


def for_zone(reg: dict, zone_id: int) -> list:
    """Registry records that belong to ``zone_id`` (or all if ``zone_id`` is falsy)."""
    npcs = reg.get("npcs", [])
    if not zone_id:
        return list(npcs)
    return [n for n in npcs if zone_of(int(n.get("npcid", 0))) == int(zone_id)]


# ---------------------------------------------------------------------------
# SQL generation (packaged / applied to the CatsEyeXI DB)
# ---------------------------------------------------------------------------

def _sql_str(s: str) -> str:
    """Single-quoted SQL string literal (escapes quotes/backslashes)."""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "''") + "'"

def _sql_binhex(b: bytes) -> str:
    """``0x…`` binary literal for varbinary/binary columns (``0x00`` when empty)."""
    return ("0x" + b.hex().upper()) if b else "0x00"


def row_values(record: dict) -> list:
    """Ordered value list for one npc_list row, matching :data:`NPC_COLUMNS`.
    ``name`` (varbinary) is returned as raw bytes; ``look`` as raw bytes."""
    name = (record.get("name") or "")
    pos = record.get("pos") or [0, 0, 0]
    d = NPC_DEFAULTS
    status = normalize_status(record.get("status"), d["status"])
    return [
        int(record["npcid"]),
        name.encode("utf-8")[:24],                 # name  (varbinary 24)
        name[:50],                                 # polutils_name (char 50)
        int(record.get("rot") or 0) & 0xFF,        # pos_rot
        float(pos[0]), float(pos[1]), float(pos[2]),
        d["flag"], d["speed"], d["speedsub"], d["animation"], d["animationsub"],
        int(record.get("namevis", d["namevis"])),  # per-record override (cutscene "hide names")
        status, d["entityFlags"],
        bytes.fromhex(record["look"]),             # look  (binary 20)
        d["name_prefix"], None,                    # name_prefix, content_tag
        d["widescan"],
    ]


def inject_name_record(name_dat: bytes, sid: int, name: str) -> bytes:
    """Insert/replace a client name-table record for ``sid`` in a zone's NPC name DAT
    (``ROM/27/…``). Each record is 32 bytes: 28-byte cp932 name (NUL-padded) + u32 sid LE.

    The table is a list of ``(name, sid)`` the client looks up by sid for widescan/labels —
    without an entry the client shows "NPC". Records run in ascending sid order; we keep that
    (insert at the sorted position, or overwrite an existing record with the same sid), so a
    binary-search client still finds it."""
    data = bytearray(name_dat)
    n = len(data) // 32
    rec = bytearray(32)
    enc = name.encode("cp932", "replace")[:28]
    rec[0:len(enc)] = enc
    rec[28:32] = struct.pack("<I", sid & 0xFFFFFFFF)
    # Find insert/replace position by sid order.
    pos = n
    for i in range(n):
        rsid = struct.unpack_from("<I", data, i * 32 + 28)[0]
        if rsid == sid:
            data[i * 32:(i + 1) * 32] = rec       # replace in place
            return bytes(data)
        if rsid > sid:
            pos = i
            break
    data[pos * 32:pos * 32] = rec                 # insert (or append when pos == n)
    return bytes(data)


def remove_name_record(name_dat: bytes, sid: int) -> bytes:
    """Drop the name-table record for ``sid`` (inverse of :func:`inject_name_record`).
    Returns the DAT unchanged if no record matches."""
    data = bytearray(name_dat)
    n = len(data) // 32
    for i in range(n):
        if struct.unpack_from("<I", data, i * 32 + 28)[0] == sid:
            del data[i * 32:(i + 1) * 32]
            return bytes(data)
    return bytes(data)


def generate_sql(reg: dict, zone_id: int = 0) -> str:
    """``REPLACE INTO npc_list`` statements for the registry's custom NPCs (idempotent).
    Restricted to ``zone_id`` when given, else every registered NPC."""
    records = for_zone(reg, zone_id)
    cols = ", ".join(f"`{c}`" for c in NPC_COLUMNS)
    lines = [
        "-- Custom NPCs — generated by the xi level editor.",
        "-- Apply to your server's game database; REPLACE makes it idempotent.",
        "-- Restart the affected zone (or the server) so the NPC spawns.",
        "",
    ]
    for rec in records:
        vals = row_values(rec)
        rendered = []
        for v in vals:
            if v is None:
                rendered.append("NULL")
            elif isinstance(v, (bytes, bytearray)):
                rendered.append(_sql_binhex(bytes(v)))
            elif isinstance(v, float):
                rendered.append(f"{v:.3f}")
            elif isinstance(v, int):
                rendered.append(str(v))
            else:
                rendered.append(_sql_str(v))
        name = rec.get("name") or ""
        lines.append(f"-- {name} · model {rec.get('modelid')} · zone {rec.get('zoneName') or zone_of(int(rec['npcid']))}")
        lines.append(f"REPLACE INTO `npc_list` ({cols}) VALUES ({', '.join(rendered)});")
        lines.append("")
    return "\n".join(lines)
