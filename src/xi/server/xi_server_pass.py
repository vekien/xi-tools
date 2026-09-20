"""The server side of an ability build: Database Update, Client Menu Record and Lua Stub
(design2 §2.2-§2.5 — the ``--apply-db``, ``--menu-record`` and ``--lua-stub`` options of
``xi dats build`` and ``xi ability publish``).

``dats build`` places the DATs and records them in the manifest first; then :func:`run`
takes each ability action it built and, per action:

0. refuses every enabled step while the action still owns a row, a menu record or a
   stub of another Type (:func:`xi_db_apply.type_change_refusal`);
a. finds the mix's server row (SELECT only) and decides the database op;
b. chooses one id for a new row and its menu record — the same number on both sides:
   a blank retail placeholder (or this mix's own record) in the client, free on the
   server, and used by no other project (:meth:`_Pass.choose_id`);
c. plans the menu record and the stub;
d. writes the menu record, e. the database row, f. the stub, g. ``<slug>_<anim>.applied.sql``;
h. records ``result.db`` / ``result.menu`` / ``result.lua`` on the action (JSON only).

A dry run stops after c and prints every step with ``would``. Nothing here raises: a
step that fails is ``<step>: error — …`` and the build still exits 0 (its DATs are placed).
The menu step never reads ``--force``: it writes only over a retail placeholder or over
the record it wrote itself, and never grows a menu section.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from xi.menu import xi_menu_table as MT
from xi.server import xi_db_apply as D
from xi.server import xi_lua_stub as L
from xi.server.xi_step import DASH, Step, current_server_dir, now_iso, q

#: The client record a publish kind's menu entry is (design2 §2.4.2).
RECORD_KIND = {"spell": "spell", "ja": "command", "ws": "command"}
#: Record indexes a donor of each kind may be: spells any; job abilities comm 528–1023;
#: weapon skills comm 1–255.
DONOR_BAND = {"spell": (0, 1024), "ja": (528, 1024), "ws": (1, 256)}

WS_MENU_SKIP = ("a weapon-skill menu record is placed only with Database Update "
                "(a weapon_skills row at the same id)")
NO_DONOR = "no donor record to copy the menu entry from"
RESTART_CLIENT = "restart the game client to load it (114.DAT is read at start-up)"
PIVOT_SHADOW = ("the pivot folder has its own 114.DAT, which a client with PIVOT on reads instead "
                f"{DASH} turn on Use Pivot Folder")
PROVISIONAL = (f"the id is provisional {DASH} the database was not checked; the next publish with "
               "Database Update keeps it if it is free")
_CTRL_RX = re.compile("[\x00-\x1f\x7f\x85  ]")


def record_index(kind: str, server_id: int) -> int:
    """The client record of a server id: spell N → ``mgc_[N]``, job ability A →
    ``comm[A + 512]``, weapon skill W → ``comm[W]``."""
    return server_id + 512 if kind == "ja" else server_id


def server_of_record(kind: str, idx: int) -> int:
    return idx - 512 if kind == "ja" else idx


@dataclass
class ServerOpts:
    """The build options of the server pass (all off / empty by default)."""
    apply_db: bool = False
    db_row: int | None = None
    clone_from: str | None = None
    server_id: int | None = None
    menu_record: bool = False
    menu_name: str | None = None
    lua_stub: bool = False

    def any(self) -> bool:
        return bool(self.apply_db or self.menu_record or self.lua_stub)

    def enabled(self) -> list:
        return [k for k, on in (("db", self.apply_db), ("menu", self.menu_record), ("lua", self.lua_stub)) if on]


def _err(e: Exception) -> str:
    text = str(e).strip()
    return text or type(e).__name__


def _keep_at(new, old):
    """``old`` when ``new`` says the same thing apart from its ``at`` time (so an
    unchanged step does not rewrite the manifest), else ``new``."""
    if isinstance(new, dict) and isinstance(old, dict):
        if {k: v for k, v in new.items() if k != "at"} == {k: v for k, v in old.items() if k != "at"}:
            return old
    return new


def _dump(v) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


class _Client:
    """The menu table (and names) of one record kind as the build root sees it."""

    def __init__(self, root, record_kind: str):
        self.root, self.kind = root, record_kind
        self.menu = None
        self.names = None
        self.error = None

    def load(self):
        if self.menu is None and self.error is None:
            try:
                self.menu = MT.load_menu(self.root)
                self.names = MT.row_names(self.kind, self.root)
            except Exception as e:                   # noqa: BLE001 — a missing / unreadable table
                self.error = f"can't read the client's menu table in {self.root} ({_err(e)})"
        return self.menu

    def count(self) -> int:
        return self.menu.count(self.kind) if self.load() is not None else 0

    def state(self, idx: int, own_hex: str | None = None) -> tuple:
        if self.load() is None:
            return ("error", self.error)
        return MT.row_state(self.kind, self.root, idx, own_hex, menu=self.menu, names=self.names)

    def record(self, idx: int):
        return MT.record_at(self.menu, self.kind, idx) if self.load() is not None else None

    def name(self, idx: int, lang: str = "en") -> str:
        if self.load() is None:
            return ""
        names = self.names[0 if lang == "en" else 1]
        return names[idx] if idx < len(names) else ""


# ── other projects' ids (guard (b) of the id picker) ──────────────────────────

def _ids_in(actions, kind: str, who: str, skip_id: str | None, out: dict) -> None:
    table = D.KIND_TABLES[kind].name
    for a in actions or []:
        if not isinstance(a, dict) or (skip_id is not None and a.get("id") == skip_id):
            continue
        res = a.get("result")
        if not isinstance(res, dict):
            continue
        label = f"{who}: {a.get('id')}"
        typ = a.get("type")
        if typ == "ability":
            db, menu = res.get("db"), res.get("menu")
            if isinstance(db, dict) and db.get("table") == table and isinstance(db.get("id"), int):
                out.setdefault(db["id"], label)
            if isinstance(menu, dict) and menu.get("kind") == kind and isinstance(menu.get("server_id"), int):
                out.setdefault(menu["server_id"], label)
        elif typ in ("spell", "command"):
            rid = res.get("record_id")
            if not isinstance(rid, int):
                continue
            if typ == "spell" and kind == "spell":
                out.setdefault(rid, label)
            elif typ == "command" and kind == "ja" and 512 <= rid < 1024:
                out.setdefault(rid - 512, label)
            elif typ == "command" and kind == "ws" and 1 <= rid < 256:
                out.setdefault(rid, label)


def other_ids(kind: str, manifest_path, manifest: dict | None, action_id: str | None) -> dict:
    """``{server id: "project: action id"}`` for every id of ``kind`` another action
    records: an ability's ``result.db.id`` / ``result.menu.server_id``, or a spell /
    command record action's ``result.record_id`` mapped back to a server id. This
    manifest is read from ``manifest`` (in memory) with ``action_id`` left out; the
    other ``projects/*.json`` from disk."""
    out: dict = {}
    me = Path(manifest_path).resolve() if manifest_path else None
    if manifest is not None:
        _ids_in(manifest.get("actions"), kind, Path(manifest_path).stem if manifest_path else "this project",
                action_id, out)
    projects = Path("projects")
    if projects.is_dir():
        for mf in sorted(projects.glob("*.json")):
            if me is not None and mf.resolve() == me:
                continue
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                _ids_in(data.get("actions"), kind, mf.stem, None, out)
    return out


# ── the pass ──────────────────────────────────────────────────────────────────

class _Pass:
    def __init__(self, opts: ServerOpts, *, root, target: str, dry_run: bool, manifest_path, manifest,
                 project: str | None):
        self.opts, self.root, self.target, self.dry_run = opts, Path(root), target, dry_run
        self.manifest_path, self.manifest, self.project = manifest_path, manifest, project
        self.server_dir = current_server_dir()
        self.at = now_iso()
        self.conn = None
        self.db_error = None
        self._tried = False
        self.claimed = {k: set() for k in D.KIND_TABLES}

    # ── connection ──
    def connection(self):
        """One connection per build, opened on first need and only when a database is
        configured; ``None`` when it isn't or it can't be reached (``db_error``)."""
        if not self._tried:
            self._tried = True
            if D.configured() is not None:
                try:
                    self.conn = D.connect()
                except D.DbUnavailable as e:
                    self.db_error = str(e)
        return self.conn

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:                    # noqa: BLE001
                pass

    # ── ids ──
    def accept_fn(self, kind: str, client: _Client, own_hex_for, others: dict):
        """Guards (a) and (b) of the id picker, plus ids this build already gave out."""
        rk = RECORD_KIND[kind]

        def accept(i: int) -> str | None:
            idx = record_index(kind, i)
            st, nm = client.state(idx, own_hex_for(idx))
            if st == "error":
                return nm
            if st == "past-end":
                return f"{self.target}'s 114.DAT has only {client.count()} {rk} records; {idx} is past its end"
            if st == "taken":
                return (f"the client's {rk} {idx} holds {q(nm)}" if not MT._blank(nm)
                        else f"the client's {rk} {idx} is a reserved retail row")
            if i in others:
                return f"{others[i]} uses it"
            if i in self.claimed[kind]:
                return "another ability in this build takes it"
            return None
        accept.client = client
        return accept

    def choose_id(self, conn, kind: str, prev: dict, accept) -> D.IdChoice:
        """design2 §2.4.3 steps 3-6 (steps 1-2 — the mix's own row, a created row that is
        gone — are the caller's): ``--server-id``; else the id of this mix's menu record
        while it still passes (else it is re-picked: it was never bound to a server
        row); else the highest id that passes every guard. Every id needs a blank client
        record at its index, even without Client Menu Record, so one can be added later."""
        client = getattr(accept, "client", None)
        if client is not None and client.load() is None:
            return D.IdChoice(None, conn is None, "pick", client.error)
        if self.opts.server_id is not None:
            return D.pick_server_id(conn, kind, int(self.opts.server_id), accept=accept, server_dir=self.server_dir)
        pm = prev.get("menu")
        if isinstance(pm, dict) and pm.get("kind") == kind and isinstance(pm.get("server_id"), int):
            c = D.pick_server_id(conn, kind, pm["server_id"], accept=accept, server_dir=self.server_dir)
            if c.server_id is not None:
                c.source = "menu"
                return c
        return D.pick_server_id(conn, kind, None, accept=accept, server_dir=self.server_dir)

    # ── one action ──
    def do(self, action: dict, result: dict) -> bool:
        opts, would = self.opts, self.dry_run
        sp = {"db": None, "menu": None, "lua": None, "rows_written": [], "wrote": {}}
        result["server_pass"] = sp
        kind = result.get("kind")
        aid = str(action.get("id") or "")
        name = str(result.get("name") or aid.split(".")[-1])
        anim = int(result.get("animation"))
        # In a dry run action["result"] is the last build's record; in a real build it is
        # this build's, with db / menu / lua carried over (xi_dats._ability_result).
        prev = action.get("result") if isinstance(action.get("result"), dict) else {}
        before = {k: _dump(prev.get(k)) for k in ("db", "menu", "lua")}
        if kind not in D.KIND_TABLES:
            for key in opts.enabled():
                sp[key] = Step.skip(f"{kind!r} is not a spell, job ability or weapon skill", would=would)
            return False
        why = D.type_change_refusal(prev, kind, self.project)
        if why:
            for key in opts.enabled():
                sp[key] = Step.refused(why, would=would)
            return False

        t = D.KIND_TABLES[kind]
        conn = self.connection() if (opts.apply_db or opts.menu_record) else None
        client = _Client(self.root, RECORD_KIND[kind])
        prev_menu = prev.get("menu") if isinstance(prev.get("menu"), dict) and prev["menu"].get("kind") == kind else None
        prev_root = ((prev_menu or {}).get("roots") or {}).get(self.target)
        prev_root = prev_root if isinstance(prev_root, dict) else None

        def own_hex_for(idx: int):
            if prev_root and prev_root.get("record_id") == idx:
                return prev_root.get("written")
            return None

        accept = self.accept_fn(kind, client, own_hex_for, other_ids(kind, self.manifest_path, self.manifest, aid))

        # a/b. the database row (or, menu-only, a read-only lookup of it)
        plan = lookup = None
        lookup_error = None
        if opts.apply_db:
            if D.configured() is None:
                sp["db"] = Step.skip(D.NOT_CONFIGURED, would=would)
            elif conn is None:
                sp["db"] = Step.error(self.db_error or "the database could not be reached", would=would)
            else:
                try:
                    ctx = D.DbCtx(kind, name, anim, prev=prev, db_row=opts.db_row, clone_from=opts.clone_from,
                                  server_id=opts.server_id, server_dir=self.server_dir, project=self.project,
                                  menu_record=opts.menu_record, lua_stub=opts.lua_stub)
                    plan = D.plan(conn, ctx, choose_id=lambda _p: self.choose_id(conn, kind, prev, accept))
                except Exception as e:           # noqa: BLE001
                    sp["db"] = Step.error(D.friendly_db_error(e), would=would)
        elif opts.menu_record and conn is not None:
            try:
                pr = D.probe(conn, t)
                if pr is None:
                    lookup = D.Located("refused", None, f"no {t.name} table in the database")
                else:
                    lookup = D.locate(conn, pr, name.lower(), prev.get("db"))
            except Exception as e:               # noqa: BLE001
                lookup_error = D.friendly_db_error(e)
        planned_op = plan.op if plan is not None else None
        if plan is not None and planned_op in ("insert", "update"):
            self.claimed[kind].add(plan.id)

        # c/d. the menu record
        new_menu = None
        if opts.menu_record:
            try:
                sp["menu"], new_menu = self.menu_step(kind, name, prev, plan, lookup, lookup_error, conn, client,
                                                      accept, prev_menu, prev_root, own_hex_for, sp)
            except Exception as e:               # noqa: BLE001
                sp["menu"] = Step.error(_err(e), would=would)

        # e. the database write
        if plan is not None and plan.writes and not self.dry_run:
            try:
                D.execute(conn, plan)
            except Exception as e:               # noqa: BLE001
                plan.op, plan.reason = "error", D.friendly_db_error(e)
            if plan.executed:
                sp["rows_written"].append(f"{plan.table.name} #{plan.id}")
                sp["wrote"]["db"] = ("inserted" if plan.op == "insert" else "updated", plan.table.name, plan.id)

        new_db = prev.get("db")
        if opts.apply_db and plan is not None and not self.dry_run:
            new_db = plan.result_db(self.at)

        # f. the stub
        new_lua = prev.get("lua")
        if opts.lua_stub:
            try:
                sp["lua"], new_lua = self.lua_step(kind, name, aid, prev, plan, planned_op, new_db, sp)
            except Exception as e:               # noqa: BLE001
                sp["lua"] = Step.error(_err(e), would=would)
                new_lua = prev.get("lua")
            lua_op = sp["lua"].op if sp["lua"] else None
            if (plan is not None and planned_op == "insert" and kind == "spell"
                    and lua_op not in ("write", "rewrite", "unchanged", "kept")):
                folder = D.SPELL_GROUP_FOLDERS.get(int((plan.like or {}).get("group") or 0), "<group>")
                plan.warnings.append(f"no script yet {DASH} turn on Lua Stub, or write scripts/actions/spells/"
                                     f"{folder}/{plan.name}.lua (without one it cannot be cast)")

        # g. what ran, in the publish folder
        if plan is not None and plan.executed:
            self.write_applied(plan, result, anim, conn)
        if plan is not None:
            sp["db"] = plan.step(would=would)

        # h. record (JSON only)
        if self.dry_run:
            return False
        res = action.get("result")
        if not isinstance(res, dict):
            res = action["result"] = {}
        for key, new in (("db", new_db), ("menu", new_menu if new_menu is not None else prev.get("menu")),
                         ("lua", new_lua)):
            new = _keep_at(new, prev.get(key))
            if new is None:
                res.pop(key, None)
            else:
                res[key] = new
        return any(_dump(res.get(k)) != before[k] for k in ("db", "menu", "lua"))

    # ── the menu step (design2 §2.4.4) ──
    def menu_step(self, kind, name, prev, plan, lookup, lookup_error, conn, client, accept, prev_menu,
                  prev_root, own_hex_for, sp) -> tuple:
        opts, would = self.opts, self.dry_run
        label, rk, t = D.KIND_LABEL[kind], RECORD_KIND[kind], D.KIND_TABLES[kind]
        prev_db = prev.get("db") if isinstance(prev.get("db"), dict) else None
        created_prev = bool(prev_db and prev_db.get("created") and prev_db.get("table") == t.name
                            and isinstance(prev_db.get("id"), int))
        warnings: list = []

        def skip(why, **kw):
            return Step.skip(why, would=would, warnings=warnings + kw.get("extra", [])), None

        def refused(why, extra=()):
            return Step.refused(why, would=would, warnings=warnings + list(extra)), None

        # 1. the id
        sid = source = None
        provisional = False
        if opts.apply_db and plan is not None:
            op = plan.op
            if op == "insert":
                sid, source = plan.id, ("bound" if plan.state == "gone" else "insert")
            elif op in ("update", "unchanged") and (plan.state == "ours" or plan.confirmed):
                sid, source = plan.id, "row"
            elif op == "unchanged":
                return skip(f"row #{plan.id} was not made by this mix")
            elif op == "needs-confirm":
                return skip("waiting for the database row to be confirmed")
            elif op == "refused":
                return skip("the database step was refused (see db:)")
            elif op == "skip":
                return skip(NO_DONOR)
            else:
                return skip("the database step failed (see db:)")
        elif lookup is not None:
            if kind == "ws":
                return skip(WS_MENU_SKIP)
            if lookup.state == "refused":
                return refused(lookup.reason)
            if lookup.state in ("ours", "foreign"):
                sid, source = int(lookup.row["id"]), "row"
                if lookup.state == "foreign":
                    warnings.append(f"#{sid} {q(lookup.row['name'])} was not made by this mix; its menu record "
                                    "goes at its id (nothing on the server changes)")
                if opts.server_id is not None and int(opts.server_id) != sid:
                    warnings.append(f"#{sid} is the row named after this mix; Server id {opts.server_id} is ignored")
            elif lookup.state == "gone" and created_prev:
                sid, source = int(prev_db["id"]), "bound"
            else:
                c = self.choose_id(conn, kind, prev, accept)
                if c.server_id is None:
                    return refused(c.reason)
                sid, source, provisional = c.server_id, c.source, c.provisional
        else:
            # The database is not reachable: not configured, or it could not be reached.
            if kind == "ws":
                return skip(WS_MENU_SKIP)
            msg = lookup_error or (self.db_error if not opts.apply_db else None)
            if msg:
                warnings.append(f"couldn't check the database ({msg}); the id is provisional")
            if created_prev:
                sid, source = int(prev_db["id"]), "bound"
            else:
                c = self.choose_id(None, kind, prev, accept)
                if c.server_id is None:
                    return refused(c.reason)
                sid, source, provisional = c.server_id, c.source, c.provisional

        # 2. the client row at that id
        idx = record_index(kind, sid)
        own_hex = own_hex_for(idx)
        st, en = client.state(idx, own_hex)
        if st == "error":
            return Step.error(en, would=would, warnings=warnings), None
        if st == "past-end":
            return refused(f"{self.target}'s 114.DAT has only {client.count()} {rk} records; {idx} is past its end")
        if st == "taken":
            if source == "row":
                return skip(f"{label} {sid} already has a client record ({q(en)})")
            extra = (["choose another id with Server id in Manage (--server-id), or free that record"]
                     if source == "bound" else [])
            return refused(f"{label} {sid} now holds {q(en)} (a retail update?); not overwritten", extra)
        self.claimed[kind].add(sid)

        # 3. the donor
        created_like = None
        if plan is not None and plan.op == "insert" and plan.like:
            created_like = plan.like
        elif created_prev and isinstance((prev_db.get("like") or {}).get("id"), int):
            created_like = prev_db["like"]
        # Priority: the DB insert's donor, then an explicit --clone-from, then this mix's own
        # record's donor, then the kind's default (Cure / Berserk / Fast Blade) so a menu
        # record clones a working record with no typing.
        explicit = str(opts.clone_from).strip() if opts.clone_from else ""
        reuse_own = (st == "own" and prev_menu and prev_menu.get("server_id") == sid
                     and isinstance((prev_menu.get("like") or {}).get("server_id"), int))
        clone = explicit or ("" if reuse_own else D.DEFAULT_DONOR.get(kind, ""))
        if created_like:
            donor_sid, donor_name = int(created_like["id"]), created_like.get("name")
            x = donor_name or str(donor_sid)
        elif clone:
            x = clone
            if conn is not None:
                pr = D.probe(conn, t)
                row, why = D.resolve_donor(conn, pr, x) if pr else (None, f"no {t.name} table in the database")
                if why:
                    return refused(why)
                donor_sid, donor_name = int(row["id"]), row.get("name")
            else:
                row, why = D.resolve_donor_offline(self.server_dir, kind, x)
                if why:
                    return skip(why)
                donor_sid, donor_name = int(row["id"]), row.get("name")
        elif reuse_own:
            # Only to rewrite this mix's own record at the same id.
            donor_sid, donor_name = int(prev_menu["like"]["server_id"]), None
            x = str(donor_sid)
        else:
            return skip(NO_DONOR)
        like_idx = record_index(kind, donor_sid)
        lo, hi = DONOR_BAND[kind]
        drec = client.record(like_idx) if lo <= like_idx < hi else None
        if drec is None or client.state(like_idx)[0] != "taken":
            return refused(f"Clone from {q(x)} is not a {label} record in {self.target}")

        # 4. the menu name (checked before anything is written; never printed when refused)
        raw = opts.menu_name or ""
        if _CTRL_RX.search(raw):
            return refused("the menu name has a control character or a line break")
        mname = raw.strip() or str(name).replace("_", " ")
        if _CTRL_RX.search(mname):
            return refused("the menu name has a control character or a line break")
        problem = MT._text_problem(rk, "name_en", mname)
        if problem:
            return refused(problem.replace("text.name_en", "the menu name"))

        # 5. the spell's menu index
        mi = None
        if rk == "spell":
            recs = client.menu.records("spell")
            if prev_root and prev_root.get("record_id") == idx and isinstance(prev_root.get("menu_index"), int):
                mi = prev_root["menu_index"]
            else:
                mi = MT.next_menu_index([r for i, r in enumerate(recs) if i != idx])
            if mi >= 0x400:
                return refused(f"menu index {mi} is past the 1024 the game lists without cexislots")
            off = MT.SPELL_FIELDS["menu_index"][0]
            dup = next((i for i, r in enumerate(recs)
                        if i != idx and not MT.is_empty(r) and struct.unpack_from("<H", r, off)[0] == mi), None)
            if dup is not None:
                return refused(f"menu index {mi} is already used by spell {dup}")

        # 6. place (or find it unchanged)
        d = {"schema": "xi.spell.v1" if rk == "spell" else "xi.command.v1", "name": name, "like": like_idx,
             "text": {"name_en": mname}}
        new_rec = MT.build_record(rk, client.menu, d, idx, mi)
        unchanged = (st == "own" and new_rec.hex() == own_hex
                     and client.name(idx, "en") == mname and client.name(idx, "jp") == mname)
        if unchanged:
            placed = {"record_id": idx, "menu_index": mi, "replaced": (prev_root or {}).get("replaced"),
                      "written": own_hex, "left": None}
        else:
            try:
                placed = MT.place_record(rk, self.root, idx, d, menu_index=mi, prev_root=prev_root,
                                         dry_run=self.dry_run)
            except OSError as e:
                path = getattr(e, "filename", None) or MT.MENU_DAT
                return (Step.error(f"cannot write {path} (is the game running? close it and publish again)",
                                   would=would, warnings=warnings), None)
            except MT.MenuError as e:
                return refused(str(e))
            if not self.dry_run:
                sp["wrote"]["menu"] = (label, sid)
        left = placed.get("left")
        if left:
            warnings.append(f"{label} {server_of_record(kind, left['record_id'])} in {self.target} now holds "
                            f"{q(left.get('name') or '')}; left as it is")

        # 7. warnings and the line
        if not unchanged:
            warnings.append(RESTART_CLIENT)
        import xi.xi_config as cfg
        pivot_dir = getattr(cfg, "FFXI_PIVOT_DIR", None)
        if self.target != "pivot" and pivot_dir and (Path(pivot_dir) / "ROM" / "118" / "114.DAT").is_file():
            warnings.append(PIVOT_SHADOW)
        if kind == "ja":
            recast = struct.unpack_from("<H", drec, 0x08)[0]
            who = donor_name or f"job ability {donor_sid}"
            warnings.append(f"shares {who}'s recast timer on the client (recast id {recast}, "
                            "comm +0x08 can't be changed yet)")
        if provisional:
            warnings.append(PROVISIONAL)
        if unchanged:
            step = Step("unchanged", f"{label} {sid} {q(mname)} in {self.target}", would, warnings)
        else:
            why = {"own": "rewrites its own record", "placeholder": "replaces the blank placeholder",
                   "empty": "fills an empty row"}.get(st, "replaces the blank placeholder")
            if kind == "spell":
                text = f"spell {sid} {q(mname)} like spell {donor_sid}, menu index {mi}, in {self.target} ({why})"
            elif kind == "ja":
                text = (f"job ability {sid} {q(mname)} at command {idx} like job ability {donor_sid} "
                        f"(command {like_idx}), in {self.target} ({why})")
            else:
                text = (f"weapon skill {sid} {q(mname)} at command {idx} like weapon skill {donor_sid}, "
                        f"in {self.target} ({why})")
            if provisional:
                text += f" · provisional {DASH} database not checked"
            step = Step("place", text, would, warnings)

        roots = dict((prev_menu or {}).get("roots") or {})
        roots[self.target] = {"record_id": idx, "menu_index": placed.get("menu_index"),
                              "replaced": placed.get("replaced"), "written": placed.get("written")}
        new_menu = {"kind": kind, "record_kind": rk, "server_id": sid, "name": mname,
                    "like": {"server_id": donor_sid, "record_id": like_idx},
                    "provisional": bool(provisional), "roots": roots, "at": self.at}
        return step, (None if self.dry_run else new_menu)

    # ── the stub (design2 §2.5) ──
    def lua_step(self, kind, name, aid, prev, plan, planned_op, new_db, sp) -> tuple:
        would = self.dry_run
        if planned_op == "insert" and not self.dry_run and not (plan and plan.executed):
            return Step.skip("no database row (the insert failed)", would=would), prev.get("lua")
        db = new_db
        if self.dry_run and plan is not None and planned_op == "insert" and plan.like:
            db = {"table": plan.table.name, "id": plan.id, "name": plan.name, "created": True,
                  "like": {"id": plan.like["id"], "name": plan.like["name"]}, "group": plan.like.get("group")}
        ctx = L.StubCtx(kind, name, db if isinstance(db, dict) else None, project=self.project or "",
                        action=aid, prev_lua=prev.get("lua"), server_dir=self.server_dir,
                        created_now=planned_op == "insert")
        sp_plan = L.plan_stub(ctx)
        if not self.dry_run:
            L.write_stub(sp_plan)
            if sp_plan.executed:
                sp["wrote"]["lua"] = sp_plan.path
        return sp_plan.step(would=would), (prev.get("lua") if self.dry_run else sp_plan.result_lua(self.at))

    # ── the applied SQL (design2 §2.3.8) ──
    def write_applied(self, plan, result, anim, conn) -> None:
        from xi.ability import xi_publish as AP
        path = None
        try:
            folder = Path(result.get("folder")) if result.get("folder") else AP.publish_folder(str(result.get("id")))
            path = folder / f"{folder.name}_{anim}.applied.sql"
            text = D.applied_sql(conn, [plan], at=self.at, server=plan.server)
            if text:
                folder.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text.encode("utf-8"))
        except Exception as e:                   # noqa: BLE001
            plan.warnings.append(f"couldn't write {path or 'the applied SQL'} ({_err(e)})")


def run(pairs, opts: ServerOpts, *, root, target: str, dry_run: bool, manifest_path=None, manifest=None,
        project: str | None = None) -> bool:
    """The server pass over ``pairs`` (``[(action, build result)]``, ability actions
    only). Attaches ``result["server_pass"] = {"db", "menu", "lua": Step|None,
    "rows_written": [...], "wrote": {...}}`` to each build result and, in a real build,
    stores the JSON ``db`` / ``menu`` / ``lua`` on ``action["result"]``. Returns True
    when any of those changed. Never raises (``KeyboardInterrupt`` aside)."""
    p = _Pass(opts, root=root, target=target, dry_run=dry_run, manifest_path=manifest_path,
              manifest=manifest, project=project)
    changed = False
    try:
        for action, result in pairs:
            try:
                changed = p.do(action, result) or changed
            except Exception as e:               # noqa: BLE001
                sp = result.setdefault("server_pass", {"db": None, "menu": None, "lua": None,
                                                       "rows_written": [], "wrote": {}})
                for key in opts.enabled():
                    if sp.get(key) is None:
                        sp[key] = Step.error(_err(e), would=dry_run)
    finally:
        p.close()
    return changed


def rows_written(results) -> list:
    return [row for r in results or [] if isinstance(r, dict)
            for row in ((r.get("server_pass") or {}).get("rows_written") or [])]


def database_line(results) -> str | None:
    """``database: 1 row written (spell_list #1023) — restart the map server …`` or None."""
    rows = rows_written(results)
    if not rows:
        return None
    n = len(rows)
    return (f"database: {n} row{'s' if n != 1 else ''} written ({', '.join(rows)}) {DASH} "
            "restart the map server (xi_map) to load it")


def warn_unrecorded(results, manifest_path, exc: Exception) -> None:
    """The manifest could not be written after the pass wrote to the server: say, on each
    step that wrote something, what the next publish or undo will not know."""
    where = str(manifest_path)
    e = _err(exc)
    for r in results or []:
        sp = r.get("server_pass") if isinstance(r, dict) else None
        if not sp:
            continue
        wrote = sp.get("wrote") or {}
        if "db" in wrote and sp.get("db") is not None:
            verb, table, i = wrote["db"]
            tail = (f"the next publish will treat #{i} as a row this mix didn't make" if verb == "inserted"
                    else "undo won't know the animation it had before")
            sp["db"].warnings.append(f"{verb} {table} #{i} but couldn't record it in {where} ({e}); {tail}")
        if "menu" in wrote and sp.get("menu") is not None:
            label, sid = wrote["menu"]
            sp["menu"].warnings.append(f"placed {label} {sid} but couldn't record it in {where} ({e}); "
                                       "undo won't put the placeholder back")
        if "lua" in wrote and sp.get("lua") is not None:
            sp["lua"].warnings.append(f"wrote {wrote['lua']} but couldn't record it in {where} ({e})")


# ── dats undo ─────────────────────────────────────────────────────────────────

def menu_record_label(menu: dict, root_name: str) -> str:
    """``the spell 1023 menu record in dir`` (undo's ``manifest kept:`` line)."""
    kind = menu.get("kind")
    label = D.KIND_LABEL.get(kind, menu.get("record_kind") or "record")
    return f"the {label} {menu.get('server_id')} menu record in {root_name}"


def undo_menu(menu: dict, root_name: str, root) -> tuple:
    """Put back an ability's menu record in one root: ``("restored", None)`` while the row
    still holds the bytes the build wrote; ``("done", None)`` when it already holds what
    was replaced, or ``root`` has no 114.DAT of its own any more (the record went with
    it); ``("left", warning)`` when someone else's record is there now (left alone); and
    ``("error", warning)`` when the files can't be read or written (a locked 114.DAT
    while the game runs) — the record is still ours, so undo keeps it to try again.
    Safe to run again: never raises."""
    rs = (menu.get("roots") or {}).get(root_name) or {}
    kind = menu.get("kind")
    rk = menu.get("record_kind") or RECORD_KIND.get(kind, "spell")
    label = D.KIND_LABEL.get(kind, rk)
    idx = rs.get("record_id")
    try:
        if not MT.target_path(root, MT.MENU_DAT).exists():
            return ("done", None)
        m = MT.load_menu(root)
        names = MT.row_names(rk, root)
        st, nm = MT.row_state(rk, root, idx, rs.get("written"), menu=m, names=names)
        if st == "own":
            MT.restore_record(rk, root, idx, rs.get("replaced"))
            return ("restored", None)
        if MT.row_is_restored(rk, idx, rs.get("replaced"), menu=m, names=names):
            return ("done", None)
    except Exception as e:                   # noqa: BLE001 — reported, and retried by the next undo
        sid = server_of_record(kind, idx) if isinstance(idx, int) else menu.get("server_id")
        hint = " (is the game running? close it and undo again)" if isinstance(e, OSError) else ""
        return ("error", f"couldn't put back {label} {sid} in {root_name}: {_err(e)}{hint}")
    return ("left", f"{label} {server_of_record(kind, idx)} in {root_name} now holds {q(nm)}; left as it is")


def describe_db(db: dict) -> str:
    """``spell_list #1023 'love' (created — deleted with --apply-db)`` for undo's summary."""
    head = f"{db.get('table')} #{db.get('id')} {q(db.get('name') or '')}"
    if db.get("created"):
        return f"{head} (created {DASH} deleted with --apply-db)"
    before = (db.get("before") or {}).get("animation")
    if before is not None:
        return f"{head} (animation {before} -> {db.get('animation')} {DASH} put back with --apply-db)"
    return f"{head} (nothing to put back)"


def db_needs_undo(db) -> bool:
    """Whether a recorded ``result.db`` has something to revert (a created row, or an
    animation it changed)."""
    if not isinstance(db, dict):
        return False
    try:
        return D.revert_statement(db) is not None
    except (ValueError, KeyError, TypeError):
        return False
