import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import click

# ── Credentials ───────────────────────────────────────────────────────────────

# Last-resort fallback, used only when no server checkout is configured and no XI_DB_*
# override is set — in which case any guess is likely to fail anyway. "xidb" is the
# name LandSandBoat's Quick Start Guide creates; "tpzdb" was the legacy Topaz name.
# There is no meaningful cross-platform default for user/password (the guide uses
# root on Windows and xi/password on Linux), so network.lua is the real source.
_DEFAULTS = dict(host="127.0.0.1", port=3306, user="root", password="", database="xidb")

#: ``XI_DB_*`` env var per credential field, for :func:`_env_creds`.
_ENV_KEYS = {
    "host": "XI_DB_HOST", "port": "XI_DB_PORT", "user": "XI_DB_USER",
    "password": "XI_DB_PASSWORD", "database": "XI_DB_NAME",
}


def lua_config_path() -> Path | None:
    """``<XI_SERVER_DIR>/settings/network.lua``, or ``None`` when unconfigured.

    Resolved per call rather than at import: the zone-editor setup writes ``.env`` and
    hot-reloads :mod:`xi.xi_config` in the running bridge, so a module-level constant
    would pin whatever XI_SERVER_DIR happened to be at startup."""
    from xi.xi_config import XI_SERVER_DIR
    return Path(XI_SERVER_DIR) / "settings" / "network.lua" if XI_SERVER_DIR else None


def _env_creds() -> dict:
    """Explicit ``XI_DB_*`` overrides. Blank/unset keys are omitted, not defaulted —
    otherwise an unset password would masquerade as a deliberate choice and shadow
    network.lua."""
    import os
    out: dict = {}
    for field, env in _ENV_KEYS.items():
        raw = (os.environ.get(env) or "").strip()
        if not raw:
            continue
        if field == "port":
            try:
                out[field] = int(raw)
            except ValueError:
                continue
        else:
            out[field] = raw
    return out

# Lua accepts either quote style and real checkouts mix them (some ship
# SQL_HOST = '127.0.0.1' single-quoted next to double-quoted SQL_LOGIN), so matching
# only `"` silently dropped fields and fell back to the defaults.
_LUA_PATTERNS = {
    "host":     r"""SQL_HOST\s*=\s*['"]([^'"]+)['"]""",
    "port":     r"""SQL_PORT\s*=\s*(\d+)""",
    "user":     r"""SQL_LOGIN\s*=\s*['"]([^'"]+)['"]""",
    # `*` not `+`: an empty SQL_PASSWORD is a legitimate dev setup, and it must win
    # over the default rather than being treated as "not found".
    "password": r"""SQL_PASSWORD\s*=\s*['"]([^'"]*)['"]""",
    "database": r"""SQL_DATABASE\s*=\s*['"]([^'"]+)['"]""",
}


def _read_lua_creds(path: Path | None) -> dict:
    if path is None:                       # XI_SERVER_DIR not configured
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    out = {}
    for key, pat in _LUA_PATTERNS.items():
        m = re.search(pat, text)
        if m:
            out[key] = int(m.group(1)) if key == "port" else m.group(1)
    return out


def _resolve(host, port, user, password, database) -> tuple:
    """Resolve DB credentials. Precedence, last wins:

    hardcoded defaults → ``<XI_SERVER_DIR>/settings/network.lua`` → ``XI_DB_*`` env
    → explicit arguments.

    network.lua sits above the defaults because a server checkout is the authoritative
    source for its own database; ``XI_DB_*`` sits above network.lua because setting it
    is a deliberate act (the zone-editor setup writes it only when you fill the field)."""
    creds = {**_DEFAULTS, **_read_lua_creds(lua_config_path()), **_env_creds()}
    return (
        host     or creds["host"],
        port     or creds["port"],
        user     or creds["user"],
        password or creds["password"],
        database or creds["database"],
    )


def resolved_creds() -> dict:
    """``{host, port, user, database, source}`` for the setup UI — no password.

    ``source`` names where the effective values came from, so the wizard can say "read
    from network.lua" rather than showing defaults that may not work.

    An ``XI_DB_*`` value only counts as an override when it actually *differs* from
    what network.lua declares. ``.env`` keys are loaded with ``os.environ.setdefault``
    and the shipped ``.env.sample`` pre-fills XI_DB_*, so presence alone would label
    every install "custom" and hide the fact that network.lua is really in charge."""
    lua = _read_lua_creds(lua_config_path())
    env = _env_creds()
    differs = {k: v for k, v in env.items() if not lua or lua.get(k) != v}
    h, p, u, _pw, db = _resolve(None, None, None, None, None)
    source = "override" if differs else ("network.lua" if lua else "default")
    return {"host": h, "port": p, "user": u, "database": db, "source": source,
            "luaPath": str(lua_config_path() or ""),
            "hasOverride": bool(differs),
            "overriddenFields": sorted(differs)}


def _connect(host, port, user, password, database):
    try:
        import pymysql
    except ImportError:
        click.echo("pymysql missing — run: uv pip install pymysql", err=True)
        sys.exit(1)
    return pymysql.connect(
        host=host, port=port, user=user, password=password,
        database=database, charset="utf8mb4", autocommit=True,
    )


# ── Output helpers ─────────────────────────────────────────────────────────────

def _fmt_table(columns: list, rows: list):
    if not rows:
        click.echo("(0 rows)")
        return
    widths = [len(c) for c in columns]
    for row in rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len("NULL" if v is None else str(v)))
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    click.echo(sep)
    click.echo("|" + "|".join(f" {c:<{w}} " for c, w in zip(columns, widths)) + "|")
    click.echo(sep)
    for row in rows:
        vals = ["NULL" if v is None else str(v) for v in row]
        click.echo("|" + "|".join(f" {v:<{w}} " for v, w in zip(vals, widths)) + "|")
    click.echo(sep)
    n = len(rows)
    click.echo(f"({n} row{'s' if n != 1 else ''})")


# ── Shared credential options ──────────────────────────────────────────────────

def _cred_opts(f):
    for opt in reversed([
        click.option("--host",     default=None, help="DB host  [default: from network.lua]"),
        click.option("--port",     default=None, type=int, help="DB port  [default: 3306]"),
        click.option("--user", "-u", default=None, help="DB user  [default: root]"),
        click.option("--password", "-p", default=None, help="DB password"),
        click.option("--database", "--db", default=None, help="DB schema  [default: from network.lua, else xidb]"),
    ]):
        f = opt(f)
    return f


# ── Commands ───────────────────────────────────────────────────────────────────

@click.command("db")
@click.argument("sql")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON array.")
@click.option("--csv",  "as_csv",  is_flag=True, help="Output as CSV.")
@_cred_opts
def db_cmd(sql, as_json, as_csv, host, port, user, password, database):
    """Execute a SQL query against the server database.

    \b
    Examples:
      xi server db "SELECT charname, pos_zone FROM chars LIMIT 10"
      xi server db --json "SELECT * FROM zone_settings WHERE zoneid=230"
      xi server db "UPDATE chars SET pos_zone=230 WHERE charname='Josh'"
    """
    h, p, u, pw, db = _resolve(host, port, user, password, database)
    conn = _connect(h, p, u, pw, db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description:
                columns = [d[0] for d in cur.description]
                rows = cur.fetchall()
                if as_json:
                    click.echo(json.dumps(
                        [dict(zip(columns, row)) for row in rows],
                        default=str, indent=2,
                    ))
                elif as_csv:
                    w = csv.writer(sys.stdout)
                    w.writerow(columns)
                    w.writerows(rows)
                else:
                    _fmt_table(columns, rows)
            else:
                click.echo(f"OK — {cur.rowcount} row(s) affected.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


_XI_PROCS = ["xi_world.exe", "xi_connect.exe", "xi_search.exe", "xi_map.exe"]


@click.command("status")
def status_cmd():
    """Show which FFXI server processes are currently running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        running = set()
        for line in result.stdout.splitlines():
            parts = line.strip().strip('"').split('","')
            if parts:
                running.add(parts[0].lower())
    except Exception as e:
        click.echo(f"Error reading process list: {e}", err=True)
        sys.exit(1)

    click.echo("FFXI Server Status")
    click.echo("─" * 28)
    all_up = True
    for proc in _XI_PROCS:
        up = proc.lower() in running
        icon = click.style("✓", fg="green") if up else click.style("✗", fg="red")
        click.echo(f"  {icon}  {proc}")
        if not up:
            all_up = False
    click.echo()
    if all_up:
        click.echo(click.style("All processes running.", fg="green"))
    else:
        click.echo(click.style("Some processes are not running.", fg="yellow"))


@click.command("npc-snapshot")
@click.option("--sql", "sql_path", type=click.Path(path_type=Path), default=None,
              help="npc_list mysqldump  [default: <XI_SERVER_DIR>/sql/npc_list.sql]")
@click.option("--out", "out_path", type=click.Path(path_type=Path), default=None,
              help="Output file  [default: the bundled src/xi/server/data/npc_list.json.gz]")
@click.option("--source", "source", default=None,
              help="Provenance label recorded in the file  [default: the dump path]")
def npc_snapshot_cmd(sql_path, out_path, source):
    """Rebuild the bundled npc_list snapshot from a server checkout's SQL dump.

    The editor resolves a cutscene NPC's model from its npc_list row, which normally
    needs a running server database. This bakes the six columns the preview needs
    (npcid, name, look, pos_*) into a ~460 KB gzipped-JSON file that ships inside the
    package, so cutscene NPCs render with no server at all. A reachable database still
    wins. Inspect the result with: gzip -dc npc_list.json.gz | jq

    Parses the dump directly — no database connection required.
    """
    from xi.server import xi_npc_snapshot as snap

    if sql_path is None:
        from xi.xi_config import XI_SERVER_DIR
        if not XI_SERVER_DIR:
            click.echo("XI_SERVER_DIR is not set — pass --sql /path/to/npc_list.sql", err=True)
            sys.exit(1)
        sql_path = Path(XI_SERVER_DIR) / "sql" / "npc_list.sql"
    if not sql_path.is_file():
        click.echo(f"Dump not found: {sql_path}", err=True)
        sys.exit(1)

    out_path = out_path or snap.default_path()

    click.echo(f"Reading {sql_path} ({sql_path.stat().st_size / 1048576:.1f} MiB)")
    stats: dict = {}
    try:
        rows = snap.parse_dump_file(sql_path, stats)
    except ValueError as e:
        click.echo(f"Could not parse dump: {e}", err=True)
        sys.exit(1)
    if not rows:
        click.echo("No npc_list rows found in that dump — nothing written.", err=True)
        sys.exit(1)

    # Report what was dropped rather than quietly shipping a short table.
    if stats.get("commented"):
        click.echo(f"  skipped     : {stats['commented']} commented-out INSERT(s)")
    if stats.get("duplicate"):
        click.echo(f"  duplicates  : {stats['duplicate']} repeated npcid(s), last one wins")
    if stats.get("malformed"):
        click.echo(click.style(
            f"  malformed   : {stats['malformed']} row(s) with an unexpected column count "
            f"— skipped", fg="yellow"))

    modelled = sum(1 for r in rows if any(r["look"]))
    meta = {
        "source": source or str(sql_path),
        "generated": _utc_stamp(),
    }
    blob = snap.build(rows, meta)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(blob)

    click.echo(f"  rows        : {len(rows)}")
    click.echo(f"  with a look : {modelled}")
    click.echo(f"  written     : {out_path}  ({len(blob) / 1024:.0f} KiB)")

    # Read it straight back — a snapshot that fails to parse would degrade silently at
    # runtime (the loader swallows errors by design), so catch it here instead.
    check = snap.load(out_path)
    if check is None or len(check) != len(rows):
        click.echo("Verification FAILED — the file did not read back cleanly.", err=True)
        sys.exit(1)
    click.echo(click.style(f"  verified    : {len(check)} rows readable", fg="green"))


def _utc_stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
