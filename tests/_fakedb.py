"""A stand-in for a pymysql connection: no network, answers from regex rules, logs
every statement. Used by the server tests (design2 §2.11)."""
import re


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        params = tuple(params or ())
        self.conn.log.append((sql, params))
        for rule in self.conn.rules:
            rx, answer = rule[0], rule[1]
            rowcount = rule[2] if len(rule) > 2 else None
            if re.search(rx, sql, re.I | re.S):
                if isinstance(answer, BaseException):
                    raise answer
                rows = answer(sql, params) if callable(answer) else answer
                if isinstance(rows, int) and not isinstance(rows, bool):   # a write's rowcount
                    self._rows, self.rowcount = [], rows
                    return rows
                self._rows = [tuple(r) for r in (rows or [])]
                self.rowcount = rowcount if rowcount is not None else len(self._rows)
                return self.rowcount
        raise AssertionError(f"FakeConn has no rule for: {sql} {params}")

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """``rules``: ``[(regex, rows | callable(sql, params) | exception[, rowcount])]``,
    first match wins. A callable may return an int: a write's rowcount."""

    def __init__(self, rules=()):
        self.rules = list(rules)
        self.log = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def literal(self, v):
        if v is None:
            return "NULL"
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"

    def close(self):
        self.closed = True

    @property
    def writes(self):
        return [(s, p) for s, p in self.log if not re.match(r"\s*SELECT\b", s, re.I)]


# ── a tiny in-memory LandSandBoat: spell_list / abilities / weapon_skills / mob_pools ──

COLUMNS = {
    "spell_list": [("spellid", "smallint", "smallint(3) unsigned"), ("name", "varchar", "varchar(24)"),
                   ("jobs", "binary", "binary(22)"), ("group", "tinyint", "tinyint(3) unsigned"),
                   ("animation", "smallint", "smallint(5) unsigned"),
                   ("animationTime", "smallint", "smallint(4) unsigned"),
                   ("radius", "smallint", "smallint(3) unsigned"), ("content_tag", "varchar", "varchar(7)")],
    "abilities": [("abilityId", "smallint", "smallint(5) unsigned"), ("name", "tinytext", "tinytext"),
                  ("job", "tinyint", "tinyint(2) unsigned"), ("level", "tinyint", "tinyint(2) unsigned"),
                  ("recastId", "smallint", "smallint(5) unsigned"),
                  ("animation", "smallint", "smallint(5) unsigned"),
                  ("animationTime", "smallint", "smallint(4) unsigned"), ("range", "float", "float(3,1) unsigned"),
                  ("content_tag", "varchar", "varchar(7)")],
    "weapon_skills": [("weaponskillid", "tinyint", "tinyint(3) unsigned"), ("name", "text", "text"),
                      ("jobs", "binary", "binary(22)"), ("skilllevel", "smallint", "smallint(3) unsigned"),
                      ("animation", "tinyint", "tinyint(3) unsigned"),
                      ("animationTime", "smallint", "smallint(4) unsigned"), ("range", "tinyint", "tinyint(2) unsigned")],
}
PKS = {"spell_list": "spellid", "abilities": "abilityId", "weapon_skills": "weaponskillid"}


class FakeServer:
    """Rows per table as dicts keyed by column name; answers the queries xi_db_apply,
    xi_ws_widen and xi_check send, and applies their writes. ``write_rowcount``
    forces a write's rowcount (0 = "the row changed")."""

    def __init__(self, rows=None, *, pools=(), columns=None, write_rowcount=None, fail=None):
        self.cols = {t: list(c) for t, c in (columns or COLUMNS).items()}
        self.rows = {t: [dict(r) for r in (rows or {}).get(t, [])] for t in self.cols}
        self.pools = set(pools)
        self.write_rowcount = write_rowcount
        self.fail = fail                     # regex -> raise on a matching statement
        self.conn = FakeConn([(r".", self.answer)])

    def _pk(self, t):
        return PKS[t]

    def answer(self, sql, params):
        if self.fail and re.search(self.fail[0], sql, re.S):
            raise self.fail[1]
        s = " ".join(sql.split())
        if s.startswith("SELECT VERSION()"):
            return [("10.11.6-MariaDB",)]
        if s.startswith("SELECT @@SESSION.sql_mode"):
            return [("STRICT_TRANS_TABLES",)]
        if "information_schema.TABLES" in s:
            return [(t,) for t in self.cols]
        if "information_schema.COLUMNS" in s:
            if "COLUMN_NAME = 'animation'" in s:
                return [(t, next(c[2] for c in cs if c[0] == "animation")) for t, cs in self.cols.items()]
            if "COLUMN_NAME = %s" in s:
                t, col = params
                return [(c[1], c[2]) for c in self.cols.get(t, []) if c[0] == col]
            return [c for c in self.cols.get(params[0], [])]
        if s.startswith("SELECT poolid FROM mob_pools"):
            lo, hi = params
            return [(p,) for p in sorted(self.pools) if lo <= p <= hi]
        m = re.match(r"SELECT (.+?) FROM `(\w+)` WHERE (.+)$", s)
        if m:
            cols = re.findall(r"`(\w+)`", m.group(1))
            t, where = m.group(2), m.group(3)
            pk = self._pk(t)
            rows = self.rows[t]
            if where == f"`{pk}` = %s":
                hit = [r for r in rows if r[pk] == params[0]]
            elif where == "`name` = %s":
                hit = [r for r in rows if str(r["name"]).lower() == str(params[0]).lower()]
            elif where.startswith("REPLACE("):
                key = params[0].replace("-", "_").lower()
                hit = [r for r in rows if str(r["name"]).replace("-", "_").lower() == key][:5]
            elif where == f"`{pk}` BETWEEN %s AND %s":
                hit = [r for r in rows if params[0] <= r[pk] <= params[1]]
            else:
                raise AssertionError(f"FakeServer: unknown WHERE {where}")
            return [tuple(r.get(c) for c in cols) for r in hit]
        if s.startswith("UPDATE"):
            m = re.match(r"UPDATE `(\w+)` SET `animation` = %s WHERE `(\w+)` = %s AND `animation` = %s$", s)
            assert m, s
            t = m.group(1)
            new, i, cur = params
            hit = [r for r in self.rows[t] if r[self._pk(t)] == i and r["animation"] == cur]
            if self.write_rowcount is not None:
                return self.write_rowcount
            for r in hit:
                r["animation"] = new
            return len(hit)
        if s.startswith("INSERT"):
            m = re.match(r"INSERT INTO `(\w+)` \((.+?)\) SELECT (.+) FROM `\w+` WHERE `(\w+)` = %s$", s)
            assert m, s
            t = m.group(1)
            cols = re.findall(r"`(\w+)`", m.group(2))
            exprs = [e.strip() for e in m.group(3).split(",")]
            assert len(cols) == len(exprs), (cols, exprs)
            donor = [r for r in self.rows[t] if r[self._pk(t)] == params[-1]]
            if self.write_rowcount is not None:
                return self.write_rowcount
            if not donor:
                return 0
            vals = iter(params[:-1])
            new = {}
            for c, e in zip(cols, exprs):
                new[c] = next(vals) if e == "%s" else (None if e == "NULL" else donor[0].get(e.strip("`")))
            if any(r[self._pk(t)] == new[self._pk(t)] for r in self.rows[t]):
                raise Exception("(1062, \"Duplicate entry\")")
            self.rows[t].append(new)
            return 1
        if s.startswith("DELETE"):
            m = re.match(r"DELETE FROM `(\w+)` WHERE `(\w+)` = %s AND `name` = %s AND `animation` = %s$", s)
            assert m, s
            t = m.group(1)
            i, name, anim = params
            keep = [r for r in self.rows[t]
                    if not (r[self._pk(t)] == i and str(r["name"]).lower() == name.lower() and r["animation"] == anim)]
            n = len(self.rows[t]) - len(keep)
            self.rows[t] = keep
            return n
        raise AssertionError(f"FakeServer: no answer for {s}")

    def row(self, t, i):
        return next((r for r in self.rows[t] if r[self._pk(t)] == i), None)
