"""Database Update: the server row an ability mix publishes its animation to
(design2 §2.3, with the credential rules of design2-overrides O3).

- :func:`plan` decides what to do and runs only ``SELECT`` / ``information_schema``
  queries, so a dry run (Check) or a menu-only lookup never writes.
- :func:`execute` runs exactly one guarded, parameterised statement keyed by primary
  key (an ``UPDATE … AND animation = <current>`` or an ``INSERT … SELECT`` cloned from
  a donor row). :func:`revert` undoes one, with the same guards.
- Every query goes through :func:`_q`, which refuses anything but a ``SELECT`` unless
  the caller passes ``write=True`` — only :func:`execute` and :func:`revert` do.

Ownership: a row this mix created, or one the user confirmed once (``--db-row``), is
"ours" and is updated silently; any other row named after the mix is ``needs-confirm``.
No transactions (the tables are Aria ``TRANSACTIONAL=0``). Credentials come from
:func:`xi.server.xi_commands._resolve` (xi-tools' ``.env``) and are never returned,
printed or recorded: a result names the server as ``database@host:port`` only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from xi.server.xi_commands import _resolve, db_configured, friendly_db_error, resolved_creds
from xi.server.xi_step import DASH, Step, current_server_dir, now_iso, q


class DbUnavailable(Exception):
    """The database could not be reached; the message is the friendly text."""


@dataclass(frozen=True)
class Table:
    name: str
    pk: str
    first: int
    last: int
    skip: tuple = ()


#: Where each publish kind lives on the server, and the ids a new row may take.
#: spell ids stop at 1023 (MAX_SPELL_ID 1024, spell.h); players only get job abilities
#: below 512, never 55 (charutils), and 353–355 are pet abilities (ability.cpp);
#: weapon skills are below 256 (battleutils.cpp). Tests monkeypatch the ranges.
KIND_TABLES = {
    "spell": Table("spell_list", "spellid", 1, 1023),
    "ja": Table("abilities", "abilityId", 16, 511, (55, 353, 354, 355)),
    "ws": Table("weapon_skills", "weaponskillid", 1, 255),
}
#: The canonical table names (never taken from a manifest into SQL).
TABLE_NAMES = {"spell_list": ("spell", "spellid"), "abilities": ("ja", "abilityId"),
               "weapon_skills": ("ws", "weaponskillid")}
KIND_LABEL = {"spell": "spell", "ja": "job ability", "ws": "weapon skill"}
#: The donor an insert clones when none is named — a working row that carries the animation
#: so it plays and can be used; a dev fixes its real stats afterwards. `--clone-from` overrides.
DEFAULT_DONOR = {"spell": "cure", "ja": "berserk", "ws": "fast_blade"}

SPELL_ID_CAP = 1024                 # spell ids are < 1024 whatever KIND_TABLES says
TRUST_FIRST_SPELL = 896             # trustutils: spell id + 5000 is a trust's pool id
TRUST_POOLS = (5896, 6023)
JA_DONOR_RANGE = (16, 511)
JA_PET_IDS = (353, 354, 355)

#: spell_list.group -> its script folder (spell.cpp); 0 has none, 3 BLUE and 8 TRUST
#: are refused as donors.
SPELL_GROUP_FOLDERS = {1: "songs", 2: "black", 3: "blue", 4: "ninjutsu", 5: "summoning",
                       6: "white", 7: "geomancy", 8: "trust"}
SPELL_GROUP_REFUSED = {3: "blue magic", 8: "a trust"}

JOB_NAMES = {1: "WAR", 2: "MNK", 3: "WHM", 4: "BLM", 5: "RDM", 6: "THF", 7: "PLD", 8: "DRK",
             9: "BST", 10: "BRD", 11: "RNG", 12: "SAM", 13: "NIN", 14: "DRG", 15: "SMN",
             16: "BLU", 17: "COR", 18: "PUP", 19: "DNC", 20: "SCH", 21: "GEO", 22: "RUN"}

#: A server name an insert may write (and a Lua file may be named).
NAME_RX = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_COL_RX = re.compile(r"^\w+$")

_INT_BITS = {"tinyint": 8, "smallint": 16, "mediumint": 24, "int": 32, "integer": 32, "bigint": 64}
_TEXT_MAX = {"tinytext": 255, "text": 65535, "mediumtext": 16777215, "longtext": 4294967295}

NOT_CONFIGURED = ("no database configured (Settings › Local Server in the model viewer, "
                  "or XI_DB_* in xi-tools' .env)")


# ── connection ────────────────────────────────────────────────────────────────

def configured() -> dict | None:
    """:func:`resolved_creds` (no password) when a database is configured — at least one
    of ``XI_DB_HOST``/``XI_DB_USER``/``XI_DB_NAME`` set — else ``None``."""
    return resolved_creds() if db_configured() else None


def server_label() -> str:
    """``database@host:port`` — never the user or the password."""
    h, p, _u, _pw, db = _resolve(None, None, None, None, None)
    return f"{db}@{h}:{p}"


def connect():
    """An autocommit pymysql connection with xi-tools' credentials (short timeouts).
    Raises :class:`DbUnavailable` with the friendly text; never exits."""
    try:
        import pymysql
    except ImportError:
        raise DbUnavailable("pymysql is not installed (uv pip install pymysql)") from None
    h, p, u, pw, db = _resolve(None, None, None, None, None)
    try:
        return pymysql.connect(host=h, port=int(p), user=u, password=pw, database=db,
                               charset="utf8mb4", autocommit=True, connect_timeout=5,
                               read_timeout=10, write_timeout=10)
    except Exception as e:                       # noqa: BLE001 — every failure is "unavailable"
        raise DbUnavailable(friendly_db_error(e)) from None


def _q(conn, sql: str, params=(), *, write: bool = False):
    """Run one statement. Read side: asserts it is a ``SELECT`` and returns its rows
    (tuples). ``write=True`` (only :func:`execute`/:func:`revert`): returns rowcount."""
    if not write and not re.match(r"\s*SELECT\b", sql, re.I):
        raise AssertionError(f"read-only query expected, got: {sql.split(None, 1)[0] if sql.strip() else sql!r}")
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        if write:
            return cur.rowcount
        return list(cur.fetchall() or [])


# ── probe ─────────────────────────────────────────────────────────────────────

@dataclass
class Probe:
    """A table's columns as ``information_schema`` lists them (the INSERT copies this
    list, so schema drift — a missing ``radius`` — is fine)."""
    table: Table
    columns: list
    types: dict                       # lower(col) -> (DATA_TYPE, COLUMN_TYPE)
    anim_max: int | None
    name_max: int | None

    def col(self, name: str) -> str | None:
        """The column's real spelling, or ``None`` when the table has none."""
        low = name.lower()
        return next((c for c in self.columns if c.lower() == low), None)

    @property
    def anim_type(self) -> tuple:
        return self.types.get("animation", (None, None))


def _int_max(data_type: str, column_type: str) -> int | None:
    bits = _INT_BITS.get((data_type or "").lower())
    if not bits:
        return None
    return (1 << bits) - 1 if "unsigned" in (column_type or "").lower() else (1 << (bits - 1)) - 1


def _text_max(data_type: str, column_type: str) -> int | None:
    dt = (data_type or "").lower()
    if dt in _TEXT_MAX:
        return _TEXT_MAX[dt]
    m = re.search(r"\((\d+)\)", column_type or "")
    return int(m.group(1)) if m and dt in ("varchar", "char") else None


def probe(conn, table: Table) -> Probe | None:
    """The table's columns, or ``None`` when it doesn't exist."""
    rows = _q(conn, "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
              (table.name,))
    if not rows:
        return None
    cols = [str(r[0]) for r in rows]
    bad = [c for c in cols if not _COL_RX.match(c)]
    if bad:
        raise ValueError(f"{table.name} has a column name xi can't quote safely: {bad[0]!r}")
    types = {str(r[0]).lower(): (str(r[1]).lower(), str(r[2])) for r in rows}
    at = types.get("animation", (None, None))
    nt = types.get("name", (None, None))
    return Probe(table, cols, types, _int_max(*at), _text_max(*nt))


# ── rows ──────────────────────────────────────────────────────────────────────

_EXTRA = ("animationTime", "group", "job", "level", "recastId", "skilllevel")


def _select(conn, pr: Probe, where: str, params) -> list:
    """Rows as dicts ``{id, name, animation, animationTime?, group?, job?, level?,
    recastId?, skilllevel?}`` (the optional keys only when the table has them)."""
    t = pr.table
    keys = ["id", "name", "animation"]
    cols = [f"`{pr.col(t.pk) or t.pk}`", "`name`", "`animation`"]
    for extra in _EXTRA:
        real = pr.col(extra)
        if real:
            keys.append(extra)
            cols.append(f"`{real}`")
    rows = _q(conn, f"SELECT {', '.join(cols)} FROM `{t.name}` WHERE {where}", params)
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["id"] = int(d["id"])
        d["name"] = "" if d["name"] is None else str(d["name"])
        d["animation"] = None if d["animation"] is None else int(d["animation"])
        out.append(d)
    return out


def _by_id(conn, pr: Probe, i: int) -> list:
    return _select(conn, pr, f"`{pr.col(pr.table.pk) or pr.table.pk}` = %s", (int(i),))


def _by_name(conn, pr: Probe, name: str) -> list:
    return _select(conn, pr, "`name` = %s", (name,))


def _ids(rows) -> str:
    return ", ".join(f"#{r['id']}" for r in rows)


def _owned(db: dict | None) -> bool:
    """A recorded binding this mix owns: it created the row, the user confirmed it, or
    it changed it (``before``)."""
    return bool(db) and bool(db.get("created") or db.get("confirmed") or db.get("before"))


@dataclass
class Located:
    """Where the mix's row stands: ``ours`` / ``foreign`` (one row named after the mix),
    ``gone`` (a row this mix created is missing), ``none`` (no row), or ``refused``."""
    state: str
    row: dict | None = None
    reason: str | None = None
    hint: str = ""


def locate(conn, pr: Probe, name: str, prev_db: dict | None) -> Located:
    """Find the mix's row (design2 §2.3.4) — SELECTs only. ``name`` is the row name
    (lower case). A binding (``prev_db``) this mix owns is checked by primary key first."""
    t = pr.table
    if prev_db and prev_db.get("table") == t.name and isinstance(prev_db.get("id"), int):
        rows = _by_id(conn, pr, prev_db["id"])
        if rows:
            r = rows[0]
            if r["name"].lower() == name.lower():
                if _owned(prev_db):
                    return Located("ours", r)
            elif _owned(prev_db):
                return Located("refused", r, f"{t.name} #{r['id']} is now named {q(r['name'])}, not {q(name)}")
        elif prev_db.get("created"):
            return Located("gone")
    rows = _by_name(conn, pr, name)
    if len(rows) > 1:
        return Located("refused", None, f"{len(rows)} {t.name} rows are named {q(name)} ({_ids(rows)})")
    if len(rows) == 1:
        r = rows[0]
        mine = (bool(prev_db) and prev_db.get("table") == t.name and prev_db.get("id") == r["id"]
                and bool(prev_db.get("created") or prev_db.get("confirmed")))
        return Located("ours" if mine else "foreign", r)
    return Located("none")


def _near_miss(conn, pr: Probe, name: str) -> str:
    t = pr.table
    rows = _q(conn, f"SELECT `{pr.col(t.pk) or t.pk}`, `name` FROM `{t.name}` "
                    "WHERE REPLACE(`name`, '-', '_') = REPLACE(%s, '-', '_') LIMIT 5", (name,))
    hits = [f"#{int(i)} {q(n)}" for i, n in rows]
    return f"; did you mean {' or '.join(hits)}?" if hits else ""


def resolve_donor(conn, pr: Probe, clone_from) -> tuple:
    """``(row, None)`` for ``--clone-from`` (an id, or a name: case ignored, runs of
    whitespace become ``_``), or ``(None, reason)``."""
    s = str(clone_from).strip()
    rows = _by_id(conn, pr, int(s)) if s.isdigit() else _by_name(conn, pr, re.sub(r"\s+", "_", s))
    if not rows:
        return None, f"Clone from {q(s)} matches no {pr.table.name} row"
    if len(rows) > 1:
        return None, f"Clone from {q(s)} matches {_ids(rows)}"
    return rows[0], None


def resolve_donor_offline(server_dir, kind: str, clone_from) -> tuple:
    """``--clone-from`` without a database (the menu step's offline case): digits are
    taken as the id as they are (name ``None`` unless the server's SQL has it); a name is
    looked up in ``<server_dir>/sql/<table>.sql`` (``INSERT INTO `t` VALUES (id,'name'``).
    ``({"id", "name"}, None)`` or ``(None, reason)``."""
    s = str(clone_from).strip()
    t = KIND_TABLES[kind]
    names: dict = {}
    f = Path(server_dir) / "sql" / f"{t.name}.sql" if server_dir else None
    if f is not None and f.is_file():
        rx = re.compile(r"^INSERT INTO `?%s`? VALUES \((\d+),'([^']*)'" % re.escape(t.name))
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = rx.match(line.strip())
            if m:
                names[int(m.group(1))] = m.group(2)
    if s.isdigit():
        return {"id": int(s), "name": names.get(int(s))}, None
    want = re.sub(r"\s+", "_", s).lower()
    hits = [i for i, n in names.items() if n.lower() == want]
    if len(hits) == 1:
        return {"id": hits[0], "name": names[hits[0]]}, None
    if len(hits) > 1:
        return None, f"Clone from {q(s)} matches {', '.join(f'#{i}' for i in sorted(hits))}"
    return None, f"can't resolve Clone from {q(s)} without the database; give its id"


def donor_problem(kind: str, donor: dict, clone_from=None) -> str | None:
    """Why ``donor`` can't be cloned for ``kind``, or ``None``."""
    label = q(clone_from if clone_from is not None else donor.get("name"))
    if kind == "spell":
        g = donor.get("group")
        g = int(g) if g is not None else None
        if g == 0 or g is None:
            return (f"Clone from {label} has spell group 0, which has no script folder; "
                    "pick a spell that has one")
        if g in SPELL_GROUP_REFUSED:
            return f"Clone from {label} is {SPELL_GROUP_REFUSED[g]} spell (group {g}), which can't be cloned"
        if g not in SPELL_GROUP_FOLDERS:
            return f"Clone from {label} has spell group {g}, which has no script folder"
    elif kind == "ja":
        i = donor["id"]
        if not (JA_DONOR_RANGE[0] <= i <= JA_DONOR_RANGE[1]) or i in JA_PET_IDS:
            return (f"Clone from {label} is #{i}, not a player job ability "
                    f"({JA_DONOR_RANGE[0]}–{JA_DONOR_RANGE[1]}, except the pet abilities 353–355)")
    return None


# ── ids ───────────────────────────────────────────────────────────────────────

@dataclass
class IdChoice:
    """An id for an insert (and the menu record at the same id): ``server_id``, or a
    refusal ``reason``. ``provisional`` when the database wasn't checked."""
    server_id: int | None
    provisional: bool = False
    source: str = ""                # "server-id" | "pick" | "bound" | …
    reason: str | None = None


def id_problem(kind: str, i: int) -> str | None:
    """Why ``i`` can never be a new ``kind`` row (range, reserved, the spell cap)."""
    t = KIND_TABLES[kind]
    if kind == "spell" and i >= SPELL_ID_CAP:
        return f"{i} is past the server's spell limit (ids below {SPELL_ID_CAP})"
    if not (t.first <= i <= t.last):
        return f"{i} is outside {t.name}'s range for new rows ({t.first}–{t.last})"
    if i in t.skip:
        return f"{i} is reserved on the server ({t.name} skips {', '.join(map(str, t.skip))})"
    return None


def candidate_ids(kind: str):
    """Ids a new ``kind`` row may take, highest first."""
    t = KIND_TABLES[kind]
    for i in range(t.last, t.first - 1, -1):
        if id_problem(kind, i) is None:
            yield i


def taken_ids(conn, kind: str) -> set:
    """Ids of ``kind``'s table within its range (one SELECT)."""
    t = KIND_TABLES[kind]
    rows = _q(conn, f"SELECT `{t.pk}` FROM `{t.name}` WHERE `{t.pk}` BETWEEN %s AND %s", (t.first, t.last))
    return {int(r[0]) for r in rows}


def trust_pools(conn) -> set:
    """``mob_pools`` ids in the trust band (5896–6023): a spell id ≥ 896 with a pool at
    id + 5000 is a trust's, and is never used."""
    rows = _q(conn, "SELECT poolid FROM mob_pools WHERE poolid BETWEEN %s AND %s", TRUST_POOLS)
    return {int(r[0]) for r in rows}


def _trust_clash(kind: str, i: int, pools: set) -> bool:
    return kind == "spell" and i >= TRUST_FIRST_SPELL and (i + 5000) in pools


_SQL_INSERT_RX = r"^INSERT INTO `?{t}`? VALUES \((\d+),"


def server_ids_offline(server_dir, kind: str) -> set | None:
    """Ids a text scan of the server's SQL finds for ``kind``'s table: ``sql/<table>.sql``
    plus every ``modules/**/*.sql`` INSERT/REPLACE/UPDATE/DELETE naming one. ``None``
    without a server folder. Used when there is no database (the id is provisional)."""
    if not server_dir or not Path(server_dir).is_dir():
        return None
    t = KIND_TABLES[kind]
    root = Path(server_dir)
    ids: set = set()
    rx = re.compile(_SQL_INSERT_RX.format(t=re.escape(t.name)))
    f = root / "sql" / f"{t.name}.sql"
    if f.is_file():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = rx.match(line.strip())
            if m:
                ids.add(int(m.group(1)))
    rx_ins = re.compile(r"(?:INSERT|REPLACE)\s+(?:IGNORE\s+)?INTO\s+`?%s`?[^;]*?VALUES\s*\((\d+)" % re.escape(t.name), re.I | re.S)
    rx_multi = re.compile(r"\),\s*\((\d+),")
    rx_head = re.compile(r"(INSERT|REPLACE)\s+(IGNORE\s+)?INTO\s+`?%s`?" % re.escape(t.name), re.I)
    rx_upd = re.compile(r"(UPDATE\s+`?%s`?|DELETE\s+FROM\s+`?%s`?)" % (re.escape(t.name), re.escape(t.name)), re.I)
    rx_pk = re.compile(r"`?%s`?\s*=\s*(\d+)" % re.escape(t.pk), re.I)
    mods = root / "modules"
    if mods.is_dir():
        for sqlf in mods.rglob("*.sql"):
            try:
                txt = sqlf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if t.name not in txt:
                continue
            for stmt in txt.split(";"):
                if t.name not in stmt:
                    continue
                ids.update(int(m.group(1)) for m in rx_ins.finditer(stmt))
                if rx_head.search(stmt):
                    ids.update(int(m.group(1)) for m in rx_multi.finditer(stmt))
                if rx_upd.search(stmt):
                    ids.update(int(m.group(1)) for m in rx_pk.finditer(stmt))
    return ids


def trust_pools_offline(server_dir) -> set:
    """``mob_pools`` ids from ``sql/mob_pools.sql`` (text scan); empty when absent."""
    out: set = set()
    if not server_dir:
        return out
    f = Path(server_dir) / "sql" / "mob_pools.sql"
    if f.is_file():
        rx = re.compile(_SQL_INSERT_RX.format(t="mob_pools"))
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = rx.match(line.strip())
            if m:
                out.add(int(m.group(1)))
    return out


def server_id_problem(conn, kind: str, i: int, *, server_dir=None) -> str | None:
    """Guard (c) of the id picker for one id: ``None`` when the server side is free —
    the id is valid, no row holds it, and (spells ≥ 896) no trust pool is at id + 5000.
    With ``conn=None`` the server folder's SQL is scanned instead (provisional)."""
    why = id_problem(kind, i)
    if why:
        return why
    t = KIND_TABLES[kind]
    if conn is not None:
        rows = _q(conn, f"SELECT `{t.pk}`, `name` FROM `{t.name}` WHERE `{t.pk}` = %s", (int(i),))
        if rows:
            return f"{t.name} #{i} already exists ({q(rows[0][1])})"
        if kind == "spell" and i >= TRUST_FIRST_SPELL and _trust_clash(kind, i, trust_pools(conn)):
            return f"spell {i} is a trust's (mob_pools {i + 5000})"
        return None
    ids = server_ids_offline(server_dir, kind)
    if ids and i in ids:
        return f"the server's SQL has a {t.name} row #{i}"
    if _trust_clash(kind, i, trust_pools_offline(server_dir)):
        return f"spell {i} is a trust's (mob_pools {i + 5000})"
    return None


def pick_server_id(conn, kind: str, server_id: int | None = None, *, accept=None,
                   server_dir=None) -> IdChoice:
    """The shared id picker's server side (design2 §2.4.3 steps 3, 5 and 6).

    ``server_id`` (``--server-id``) is validated; otherwise ids are tried from the top
    of the range down. ``accept(i) -> str | None`` adds the caller's own guards (the
    client record is blank, no other project uses it) and returns why an id fails.
    Without ``conn`` the server's SQL files are scanned and the choice is provisional."""
    provisional = conn is None
    if conn is not None:
        taken = taken_ids(conn, kind)
        t = KIND_TABLES[kind]
        pools = trust_pools(conn) if kind == "spell" and t.last >= TRUST_FIRST_SPELL else set()
    else:
        taken = server_ids_offline(server_dir, kind) or set()
        pools = trust_pools_offline(server_dir) if kind == "spell" else set()

    def why(i: int) -> str | None:
        p = id_problem(kind, i)
        if p:
            return p
        if i in taken:
            return f"{KIND_TABLES[kind].name} #{i} already exists"
        if _trust_clash(kind, i, pools):
            return f"spell {i} is a trust's (mob_pools {i + 5000})"
        return accept(i) if accept else None

    if server_id is not None:
        w = why(int(server_id))
        if w:
            return IdChoice(None, provisional, "server-id", f"Server id {server_id} (--server-id): {w}")
        return IdChoice(int(server_id), provisional, "server-id")
    for i in candidate_ids(kind):
        if why(i) is None:
            return IdChoice(i, provisional, "pick")
    t = KIND_TABLES[kind]
    return IdChoice(None, provisional, "pick",
                    f"no id is both blank in the client and free on the server ({kind}: {t.first}–{t.last})")


# ── the Type-change guard ─────────────────────────────────────────────────────

def type_change_refusal(prev: dict | None, kind: str, project: str | None = None) -> str | None:
    """design2 §2.3.4a: the refusal every enabled step gets while this action's
    previous result still owns something of another kind, else ``None``. A binding that
    only records an unchanged foreign row owns nothing and never trips it."""
    prev = prev or {}
    table = KIND_TABLES[kind].name
    undo = f"xi dats undo {project} --apply-db" if project else "xi dats undo --apply-db"
    db = prev.get("db")
    if isinstance(db, dict) and db.get("table") and db.get("table") != table and (db.get("created") or db.get("before")):
        old = TABLE_NAMES.get(db["table"], (None,))[0]
        verb = "created" if db.get("created") else "changed"
        return (f"this mix {verb} {db['table']} #{db.get('id')} when its Type was "
                f"{KIND_LABEL.get(old, old)}; set the Type back, or undo it first ({undo})")
    menu = prev.get("menu")
    if isinstance(menu, dict) and menu.get("kind") and menu.get("kind") != kind and menu.get("roots"):
        old = menu["kind"]
        return (f"this mix placed the {KIND_LABEL.get(old, old)} {menu.get('server_id')} menu record when "
                f"its Type was {KIND_LABEL.get(old, old)}; set the Type back, or undo it first ({undo})")
    lua = prev.get("lua")
    if isinstance(lua, dict) and lua.get("kind") and lua.get("kind") != kind:
        old = lua["kind"]
        return (f"this mix wrote {lua.get('path')} when its Type was {KIND_LABEL.get(old, old)}; "
                f"set the Type back, or undo it first ({undo})")
    return None


# ── the plan ──────────────────────────────────────────────────────────────────

@dataclass
class DbCtx:
    """One ability action's Database Update inputs."""
    kind: str                           # spell | ja | ws
    name: str                           # the recipe name; the row name is name.lower()
    animation: int
    prev: dict | None = None            # the action's previous ``result`` (reads .db/.menu/.lua)
    db_row: int | None = None           # --db-row
    clone_from: str | None = None       # --clone-from
    server_id: int | None = None        # --server-id
    server_dir: str | None = None       # default: XI_SERVER_DIR now
    project: str | None = None          # for the undo hint
    menu_record: bool = False           # --menu-record (insert warning)
    lua_stub: bool = False              # --lua-stub (insert warning)


@dataclass
class DbPlan:
    """What the database step does. ``op``: ``update``, ``insert``, ``unchanged``,
    ``needs-confirm``, ``skip``, ``refused`` or ``error``."""
    op: str
    kind: str
    name: str                           # the row name (lower case)
    animation: int
    table: Table | None = None
    id: int | None = None
    row: dict | None = None             # the located row as it is now
    state: str | None = None            # ours | foreign | gone | none (from locate)
    like: dict | None = None            # the donor of an insert {id, name, group?, …}
    confirmed: bool = False             # --db-row matched a foreign row
    reason: str | None = None           # skip / refused / error / needs-confirm
    warnings: list = field(default_factory=list)
    server: str | None = None
    prev_db: dict | None = None
    id_choice: IdChoice | None = None
    statement: tuple | None = None      # (sql, params) execute() runs
    insert_parts: tuple | None = None   # (columns, exprs, params, donor_id) for the applied SQL
    executed: bool = False
    rowcount: int | None = None

    @property
    def writes(self) -> bool:
        return self.op in ("update", "insert")

    def step(self, would: bool = False) -> Step:
        """The ``db:`` line (design2 §1.2.2)."""
        t = self.table.name if self.table else "?"
        srv = f" ({self.server})" if self.server else ""
        rn = (self.row or {}).get("name") or self.name
        if self.op == "update":
            text = f"{t} #{self.id} {q(rn)} animation {self.row['animation']} -> {self.animation}{srv}"
        elif self.op == "insert":
            text = (f"{t} #{self.id} {q(self.name)} like #{self.like['id']} {q(self.like['name'])} "
                    f"animation {self.animation}{srv}")
        elif self.op == "unchanged":
            text = f"{t} #{self.id} {q(rn)} animation {self.animation}{srv}"
        elif self.op == "needs-confirm":
            text = (f"{t} #{self.id} {q(rn)} animation {self.row['animation']} -> {self.animation} "
                    f"{DASH} {self.reason}")
        else:
            return Step(self.op, f"{DASH} {self.reason or ''}".rstrip(), would, list(self.warnings))
        return Step(self.op, text, would, list(self.warnings))

    def result_db(self, at: str | None = None) -> dict | None:
        """The ``result.db`` to record after this step (JSON types only): the new binding
        after a write that ran, or an unchanged row that is ours / confirmed now; the
        previous binding as it was for everything else."""
        prev = self.prev_db
        at = at or now_iso()
        t = self.table.name if self.table else None
        same = bool(prev) and prev.get("table") == t and prev.get("id") == self.id
        if self.op == "insert" and self.executed:
            out = {"table": t, "id": self.id, "name": self.name, "created": True, "confirmed": False,
                   "like": {"id": self.like["id"], "name": self.like["name"]}}
            if self.kind == "spell":
                out["group"] = self.like.get("group")
            out.update(animation=self.animation, before=None, op="insert", server=self.server, at=at)
            return out
        if (self.op == "update" and self.executed) or (self.op == "unchanged" and (self.state == "ours" or self.confirmed)):
            base = dict(prev) if same else {}
            created = bool(base.get("created"))
            out = {"table": t, "id": self.id, "name": (self.row or {}).get("name") or self.name,
                   "created": created, "confirmed": bool(base.get("confirmed") or self.confirmed),
                   "like": base.get("like")}
            if self.kind == "spell":
                out["group"] = base.get("group", (self.row or {}).get("group"))
            before = base.get("before")
            if self.op == "update" and not created and not before:
                before = {"animation": self.row["animation"], "animationTime": self.row.get("animationTime")}
            out.update(animation=self.animation, before=before, op=self.op, server=self.server, at=at)
            return out
        if self.op == "unchanged":                  # a foreign row, not confirmed: owns nothing
            return prev if _owned(prev) else None
        return prev


def _refuse(plan: DbPlan, reason: str, op: str = "refused") -> DbPlan:
    plan.op, plan.reason = op, reason
    return plan


def ws_gate(pr: Probe | None, n: int, server_dir) -> tuple:
    """design2 §2.3.7 (O4 wording): ``(refusal, warnings)`` for a weapon-skill animation
    above 255 — the column must be wide, the source widened and xi_map rebuilt with
    16-bit reads (PDB), or, with no readable PDB, not known to be stale (then a ⚠)."""
    from xi.server import xi_ws_widen as W
    dt = (pr.anim_type[0] if pr else None) or ""
    if dt not in W.WIDE_TYPES:
        return ("weapon_skills.animation holds 0–255; widen it first (run the SQL from "
                "xi server ws-widen — Settings › Local Server › Weapon skills)", [])
    src = W.source_state(server_dir)
    if src["state"] == "no-server-dir":
        return ("set the Server folder so the build can check xi_map reads 16 bits", [])
    if src["state"] != "widened":
        first = next((e for e in src["edits"] if e["state"] != "wide"), src["edits"][0])
        where = f"{first['file']}:{first['line']}" if first.get("line") else first["file"]
        return (f"xi_map reads weapon-skill animations as 8 bits ({where} …); apply the C++ patch "
                "from xi server ws-widen, then rebuild xi_map", [])
    b = W.binary_state(server_dir, src["state"])
    if b["rebuilt"] is False:
        cmd = W.build_command(server_dir) or "rebuild xi_map"
        return (f"xi_map has not been rebuilt since the widen (it would play {n % 256}); {cmd}, "
                "then restart it", [])
    if b["rebuilt"] is None:
        # No xi_map.exe in the server folder to check against the widened source.
        return (None, ["can't check whether xi_map reads 16-bit weapon-skill animations — "
                       "no xi_map.exe in the server folder. If it plays wrong, rebuild and restart xi_map"])
    # rebuilt is True: the PDB shows a 16-bit m_AnimationId, or xi_map.exe is newer than the
    # widened source (built after the widen). Either way it reads 16 bits — no warning.
    return (None, [])


def _ja_sets_own_animation(server_dir, name: str) -> bool:
    if not server_dir:
        return False
    p = Path(server_dir) / "scripts" / "actions" / "abilities" / f"{name}.lua"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "setAnimation" in text or "job_utils.corsair" in text or "job_utils.dancer" in text


def _restart(table: str) -> str:
    return f"restart the map server (xi_map): it reads {table} only at startup"


def _insert_warnings(plan: DbPlan, ctx: DbCtx) -> list:
    t, d, i = plan.table.name, plan.like, plan.id
    w = []
    if plan.kind == "spell":
        w.append(f"learn it in game with !addspell {i}")
        w.append(_restart(t))
        if not ctx.lua_stub:
            folder = SPELL_GROUP_FOLDERS.get(int(d.get("group") or 0), "<group>")
            w.append(f"no script yet — turn on Lua Stub, or write scripts/actions/spells/{folder}/"
                     f"{plan.name}.lua (without one it cannot be cast)")
    elif plan.kind == "ja":
        job = d.get("job")
        job = JOB_NAMES.get(int(job), f"job {job}") if job is not None else "job"
        w.append(f"every {job} of level ≥ {d.get('level', '?')} gets it after the restart (cloned from {d['name']})")
        w.append(f"shares {d['name']}'s recast timer (recastId {d.get('recastId', '?')}) on the server and the client")
        w.append(_restart(t))
    else:
        w.append(f"every job that can use {d['name']} gets it (skill ≥ {d.get('skilllevel', '?')}) after the restart")
        w.append(_restart(t))
    if not ctx.menu_record:
        w.append(f"no client menu record at {KIND_LABEL[plan.kind]} {i} — turn on Client Menu Record "
                 "so players can use it from the menu")
    return w


def _build_insert(plan: DbPlan, pr: Probe, new_id: int, donor_id: int) -> None:
    t = pr.table
    exprs, params = [], []
    for c in pr.columns:
        low = c.lower()
        if low == t.pk.lower():
            exprs.append("%s"); params.append(int(new_id))         # noqa: E702
        elif low == "name":
            exprs.append("%s"); params.append(plan.name)            # noqa: E702
        elif low == "animation":
            exprs.append("%s"); params.append(int(plan.animation))  # noqa: E702
        elif low == "content_tag":
            exprs.append("NULL")
        else:
            exprs.append(f"`{c}`")
    cols = ", ".join(f"`{c}`" for c in pr.columns)
    pk = pr.col(t.pk) or t.pk
    sql = f"INSERT INTO `{t.name}` ({cols}) SELECT {', '.join(exprs)} FROM `{t.name}` WHERE `{pk}` = %s"
    plan.statement = (sql, tuple(params) + (int(donor_id),))
    plan.insert_parts = (list(pr.columns), exprs, tuple(params), int(donor_id))


def _check_clone_from(conn, pr: Probe, ctx: DbCtx, like: dict | None, row_id: int, plan: DbPlan) -> None:
    """A created row keeps its donor: a different --clone-from only warns."""
    if not ctx.clone_from or not like:
        return
    donor, _why = resolve_donor(conn, pr, ctx.clone_from)
    if donor and donor["id"] != like.get("id"):
        x = str(ctx.clone_from).strip()
        plan.warnings.append(f"#{row_id} was cloned from #{like.get('id')} {q(like.get('name'))}; "
                             f"Clone from {q(x)} is ignored (undo, then publish again, to clone it from {q(x)})")


def plan(conn, ctx: DbCtx, *, choose_id=None) -> DbPlan:
    """Decide the database step for one ability action — SELECT / information_schema
    only (design2 §2.3.4-§2.3.7). Never raises for a database error: that is an
    ``error`` plan with the friendly text.

    ``choose_id(plan) -> IdChoice`` supplies the id of an insert (the shared picker in
    the build pass, which adds the client-record and other-project guards); without it
    :func:`pick_server_id` picks with the server-side guards only. Whatever it returns
    is re-checked here: range, reserved ids, the spell cap, a free primary key and the
    trust pools."""
    name = str(ctx.name).lower()
    t = KIND_TABLES[ctx.kind]
    p = DbPlan("skip", ctx.kind, name, int(ctx.animation), table=t,
               prev_db=(ctx.prev or {}).get("db"), server=server_label())
    server_dir = ctx.server_dir if ctx.server_dir is not None else current_server_dir()
    try:
        why = type_change_refusal(ctx.prev, ctx.kind, ctx.project)
        if why:
            return _refuse(p, why)
        pr = probe(conn, t)
        if pr is None:
            db = _resolve(None, None, None, None, None)[4]
            return _refuse(p, f"no {t.name} table in {db}")
        for need in (t.pk, "name", "animation"):
            if not pr.col(need):
                return _refuse(p, f"{t.name} has no `{need}` column")
        n = p.animation
        gate_needed = ctx.kind == "ws" and n > 255

        loc = locate(conn, pr, name, p.prev_db)
        p.state, p.row = loc.state, loc.row
        if loc.state == "refused":
            return _refuse(p, loc.reason)

        if gate_needed:
            refusal, warns = ws_gate(pr, n, server_dir)
            if refusal:
                return _refuse(p, refusal)
            p.warnings.extend(warns)
        if n < 0:
            return _refuse(p, f"animation {n} is negative")
        if pr.anim_max is not None and n > pr.anim_max:
            return _refuse(p, f"animation {n} is more than {t.name}.animation holds "
                              f"({pr.anim_type[1]}, up to {pr.anim_max})")

        if loc.state in ("ours", "foreign"):
            r = loc.row
            p.id = r["id"]
            prev = p.prev_db or {}
            if loc.state == "ours" and prev.get("created"):
                _check_clone_from(conn, pr, ctx, prev.get("like"), r["id"], p)
                if ctx.server_id is not None and int(ctx.server_id) != r["id"]:
                    p.warnings.append(f"#{r['id']} is this mix's row; Server id {ctx.server_id} is ignored")
            if r["animation"] == n:
                p.op = "unchanged"
                if loc.state == "foreign" and ctx.db_row is not None and int(ctx.db_row) == r["id"]:
                    p.confirmed = True
                return p
            if loc.state == "ours":
                p.op = "update"
            elif ctx.db_row is not None and int(ctx.db_row) == r["id"]:
                p.op, p.confirmed = "update", True
            else:
                p.op = "needs-confirm"
                mismatch = (f" (--db-row {ctx.db_row} does not match #{r['id']})"
                            if ctx.db_row is not None else "")
                p.reason = (f"this mix did not create that row{mismatch}; confirm it once "
                            f"(Confirm in Manage, or --db-row {r['id']})")
                return p
            pk = pr.col(t.pk) or t.pk
            p.statement = (f"UPDATE `{t.name}` SET `animation` = %s WHERE `{pk}` = %s AND `animation` = %s",
                           (n, r["id"], r["animation"]))
            p.warnings.append(_restart(t.name))
            if ctx.kind == "ja" and _ja_sets_own_animation(server_dir, name):
                p.warnings.append(f"{name}'s script sets its own animation; the new number will not show")
            return p

        # ── insert: a created row that is gone, or no row named after the mix ──
        prev = p.prev_db or {}
        if loc.state == "gone":
            like = prev.get("like") or {}
            if like.get("id") is None:
                return _refuse(p, f"#{prev.get('id')}, which this mix created, is missing and its donor "
                                  "isn't recorded; undo it (xi dats undo --apply-db) and publish again")
            rows = _by_id(conn, pr, like["id"])
            if not rows:
                return _refuse(p, f"#{prev.get('id')}, which this mix created, is missing, and its donor "
                                  f"#{like['id']} {q(like.get('name'))} is gone too")
            donor = rows[0]
            if ctx.server_id is not None and int(ctx.server_id) != prev.get("id"):
                return _refuse(p, f"this mix already created #{prev.get('id')}; undo it first to move it "
                                  "(xi dats undo --apply-db), or clear Server id (--server-id)")
            _check_clone_from(conn, pr, ctx, like, prev.get("id"), p)
            p.warnings.append(f"#{prev.get('id')} {q(name)}, which this mix created, was missing (a dbtool "
                              f"re-import?); re-inserted it from #{donor['id']} {q(donor['name'])} as before")
            choice = IdChoice(int(prev["id"]), False, "bound")
        else:
            # No row named after the mix: insert one, cloned from --clone-from or, with none,
            # the kind's default donor (cure / berserk / fast_blade). It carries the animation
            # so the new one plays and can be used; a dev sets its real stats afterwards.
            clone = ctx.clone_from or DEFAULT_DONOR.get(ctx.kind)
            if not clone:
                p.op = "skip"
                p.reason = f"no {t.name} row named {q(name)} and no donor to clone" + _near_miss(conn, pr, name)
                return p
            donor, why = resolve_donor(conn, pr, clone)
            if why:
                if not ctx.clone_from:                 # the default donor is missing from this database
                    return _refuse(p, f"no {t.name} row named {q(name)}; its default donor {q(clone)} isn't in "
                                      f"{t.name} either — name one with --clone-from")
                return _refuse(p, why)
            choice = None
        why = donor_problem(ctx.kind, donor, ctx.clone_from if loc.state != "gone" else None)
        if why:
            return _refuse(p, why)
        if not NAME_RX.match(name):
            return _refuse(p, "the name must start with a letter or digit and use only a-z, 0-9, _ and -")
        if pr.name_max is not None and len(name) > pr.name_max:
            return _refuse(p, f"the name {q(name)} is longer than {t.name}.name holds ({pr.name_max})")
        p.like = dict(donor)
        p.op = "insert"
        if choice is None:
            choice = choose_id(p) if choose_id else pick_server_id(conn, ctx.kind, ctx.server_id, server_dir=server_dir)
        p.id_choice = choice
        if choice is None or choice.server_id is None:
            return _refuse(p, (choice.reason if choice else None) or "no id was chosen")
        new_id = int(choice.server_id)
        guard = server_id_problem(conn, ctx.kind, new_id)
        if guard:
            return _refuse(p, f"Server id {new_id} (--server-id): {guard}" if choice.source == "server-id"
                           else f"#{new_id}: {guard}")
        p.id = new_id
        _build_insert(p, pr, new_id, donor["id"])
        p.warnings.extend(_insert_warnings(p, ctx))
        return p
    except AssertionError:
        raise
    except Exception as e:                        # noqa: BLE001
        return _refuse(p, friendly_db_error(e), "error")


# ── writes ────────────────────────────────────────────────────────────────────

def execute(conn, plan: DbPlan) -> DbPlan:
    """Run the plan's one statement (update or insert only). It must affect exactly one
    row; anything else turns the plan into ``error — the row changed while publishing``.
    A database error becomes ``error — <friendly text>``. Returns ``plan``."""
    if not plan.writes or not plan.statement:
        return plan
    sql, params = plan.statement
    try:
        n = _q(conn, sql, params, write=True)
    except AssertionError:
        raise
    except Exception as e:                        # noqa: BLE001
        return _refuse(plan, friendly_db_error(e), "error")
    plan.rowcount = n
    if n != 1:
        return _refuse(plan, "the row changed while publishing; publish again", "error")
    plan.executed = True
    return plan


def _table_of(db: dict) -> Table:
    name = db.get("table")
    if name not in TABLE_NAMES:
        raise ValueError(f"unknown table {name!r}")
    kind, pk = TABLE_NAMES[name]
    return Table(name, pk, 0, 0)


def revert_statement(db: dict) -> tuple | None:
    """The guarded statement that undoes a recorded ``result.db``: a DELETE of a created
    row (by id, name and our animation), or an UPDATE back to ``before.animation`` (while
    the row still holds ours). ``None`` when there is nothing to undo."""
    t = _table_of(db)
    i = int(db["id"])
    if db.get("created"):
        return (f"DELETE FROM `{t.name}` WHERE `{t.pk}` = %s AND `name` = %s AND `animation` = %s",
                (i, str(db["name"]), int(db["animation"])))
    before = db.get("before") or {}
    if before.get("animation") is not None:
        return (f"UPDATE `{t.name}` SET `animation` = %s WHERE `{t.pk}` = %s AND `animation` = %s",
                (int(before["animation"]), i, int(db["animation"])))
    return None


def current_row(conn, db: dict) -> dict | None:
    """``{id, name, animation}`` of the row at a recorded ``result.db``'s primary key as
    it is now, or ``None`` when there is none (SELECT only)."""
    t = _table_of(db)
    rows = _q(conn, f"SELECT `{t.pk}`, `name`, `animation` FROM `{t.name}` WHERE `{t.pk}` = %s",
              (int(db["id"]),))
    if not rows:
        return None
    i, name, anim = rows[0][:3]
    return {"id": int(i), "name": "" if name is None else str(name),
            "animation": None if anim is None else int(anim)}


#: :func:`revert` outcomes after which nothing of this mix is left in the database.
REVERT_DONE = frozenset({"deleted", "reverted", "gone", "already", "other", "none"})


def revert(conn, db: dict) -> tuple:
    """Undo a recorded ``result.db`` (``dats undo --apply-db``). Returns ``(outcome, row)``:

    - ``"deleted"`` / ``"reverted"``: the guarded DELETE / UPDATE changed the row now;
    - ``"gone"``: no row has that id any more (a dbtool re-import, a hand delete);
    - ``"already"``: a row this mix changed holds its ``before`` animation again;
    - ``"other"``: a created row's id now holds a row with another name (not ours);
    - ``"changed"``: the row is still there with other values — left as it is;
    - ``"none"``: nothing to undo.

    Every outcome but ``"changed"`` is done (:data:`REVERT_DONE`). ``row`` is what the id
    holds now (:func:`current_row`, read back only when the guarded statement matched
    nothing), else ``None``."""
    st = revert_statement(db)
    if st is None:
        return ("none", None)
    if _q(conn, st[0], st[1], write=True):
        return ("deleted" if db.get("created") else "reverted", None)
    # 0 rows: the guard didn't match. Look (SELECT only) before calling it "changed", or
    # undo could never finish once the row is gone or already put back.
    now = current_row(conn, db)
    if now is None:
        return ("gone", None)
    if db.get("created"):
        if now["name"].lower() != str(db.get("name") or "").lower():
            return ("other", now)
        return ("changed", now)
    if now["animation"] == (db.get("before") or {}).get("animation"):
        return ("already", now)
    return ("changed", now)


# ── the applied SQL ───────────────────────────────────────────────────────────

def _lit(conn, v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    lit = getattr(conn, "literal", None) if conn is not None else None
    if lit is not None:
        out = lit(v)
        return out.decode() if isinstance(out, bytes) else str(out)
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def _render(conn, sql: str, params) -> str:
    parts = sql.split("%s")
    if len(parts) - 1 != len(params):
        raise ValueError("placeholder count mismatch")
    out = [parts[0]]
    for v, rest in zip(params, parts[1:]):
        out.append(_lit(conn, v))
        out.append(rest)
    return "".join(out)


def plan_sql(conn, plan: DbPlan) -> list:
    """The statements of an executed plan as they go in ``.applied.sql``, keyed by
    primary key so the file is safe to run again: an update without its
    ``AND animation =`` guard; an insert as ``DELETE … AND name = …`` then the
    ``INSERT … SELECT`` with literals."""
    if not plan.executed:
        return []
    t = plan.table
    pk = t.pk
    if plan.op == "update":
        return [_render(conn, f"UPDATE `{t.name}` SET `animation` = %s WHERE `{pk}` = %s;",
                        (plan.animation, plan.id))]
    cols, exprs, params, donor_id = plan.insert_parts
    sel = _render(conn, ", ".join(exprs), params)
    return [
        _render(conn, f"DELETE FROM `{t.name}` WHERE `{pk}` = %s AND `name` = %s;", (plan.id, plan.name)),
        f"INSERT INTO `{t.name}` ({', '.join(f'`{c}`' for c in cols)}) SELECT {sel} FROM `{t.name}` "
        f"WHERE `{pk}` = {int(donor_id)};",
    ]


def applied_sql(conn, plans, *, at: str | None = None, server: str | None = None) -> str | None:
    """The ``<slug>_<anim>.applied.sql`` text for the plans that ran (LF), or ``None``
    when none did. Holds no credentials."""
    stmts = [s for p in plans for s in plan_sql(conn, p)]
    if not stmts:
        return None
    server = server or next((p.server for p in plans if p.server), None) or server_label()
    head = [f"-- xi dats build --apply-db · {at or now_iso()} · {server}",
            "-- what ran against the database; safe to run again (keyed by primary key)"]
    return "\n".join(head + stmts) + "\n"


def revert_sql(db: dict, conn=None) -> str | None:
    """The revert statement of a recorded ``result.db`` with literals (for undo's
    "left as it is" notice), or ``None``."""
    st = revert_statement(db)
    return _render(conn, st[0], st[1]) + ";" if st else None


def not_configured_step() -> Step:
    """``db: skip — no database configured (…)``."""
    return Step.skip(NOT_CONFIGURED)
