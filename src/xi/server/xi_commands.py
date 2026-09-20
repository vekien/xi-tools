import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import click

# ── Credentials ───────────────────────────────────────────────────────────────

# Last-resort fallback for any field no XI_DB_* key sets — in which case a guess is
# likely to fail anyway. "xidb" is the name LandSandBoat's Quick Start Guide creates;
# "tpzdb" was the legacy Topaz name. There is no meaningful cross-platform default
# for user/password (the guide uses root on Windows and xi/password on Linux), so the
# XI_DB_* keys in xi-tools' .env (Settings › Local Server in the model viewer, or the
# zone editor's setup) are the real source. The server's settings/network.lua is
# never read for credentials: only the zone editor's setup offers it as a pre-fill.
_DEFAULTS = dict(host="127.0.0.1", port=3306, user="root", password="", database="xidb")

#: ``XI_DB_*`` env var per credential field, for :func:`_env_creds`.
_ENV_KEYS = {
    "host": "XI_DB_HOST", "port": "XI_DB_PORT", "user": "XI_DB_USER",
    "password": "XI_DB_PASSWORD", "database": "XI_DB_NAME",
}


def lua_config_path() -> Path | None:
    """``<XI_SERVER_DIR>/settings/network.lua``, or ``None`` when unconfigured.

    Only the zone editor's setup wizard reads it (``env.serverCreds``, a pre-fill the
    user asks for); no credential resolver does.

    Resolved per call rather than at import: the zone-editor setup writes ``.env`` and
    hot-reloads :mod:`xi.xi_config` in the running bridge, so a module-level constant
    would pin whatever XI_SERVER_DIR happened to be at startup."""
    from xi.xi_config import XI_SERVER_DIR
    return Path(XI_SERVER_DIR) / "settings" / "network.lua" if XI_SERVER_DIR else None


def _env_creds() -> dict:
    """The ``XI_DB_*`` values that are set (the real environment, which includes what
    xi-tools' ``.env`` loaded with ``setdefault``). Blank/unset keys are omitted, not
    defaulted, so a field nobody set falls back to :data:`_DEFAULTS`."""
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
    """The SQL_* settings of a server's ``settings/network.lua``. Used only by the
    zone editor's setup wizard to pre-fill its fields (``env.serverCreds``); the
    resolvers below never call it."""
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

    hardcoded :data:`_DEFAULTS` → non-blank ``XI_DB_*`` (the environment, which holds
    what xi-tools' ``.env`` set) → explicit arguments.

    The server's ``settings/network.lua`` is not a layer: xi-tools' ``.env`` is the one
    place credentials live (Settings › Local Server in the model viewer edits it)."""
    creds = {**_DEFAULTS, **_env_creds()}
    return (
        host     or creds["host"],
        port     or creds["port"],
        user     or creds["user"],
        password or creds["password"],
        database or creds["database"],
    )


def field_sources() -> dict:
    """Where each credential field's effective value comes from: ``"env"`` when its
    ``XI_DB_*`` key is set (from the shell, the viewer or xi-tools' ``.env``), else
    ``"default"``. Keys: host, port, user, password, database. Never a value."""
    env = _env_creds()
    return {k: ("env" if k in env else "default") for k in _ENV_KEYS}


def db_configured() -> bool:
    """True when a database is set up: at least one of ``XI_DB_HOST``, ``XI_DB_USER``
    or ``XI_DB_NAME`` is set non-blank. A port or password alone is not a server."""
    env = _env_creds()
    return any(k in env for k in ("host", "user", "database"))


def resolved_creds() -> dict:
    """``{host, port, user, database, source, …}`` for setup UIs — no password.

    ``source`` is ``"env"`` when any ``XI_DB_*`` key is set non-blank, else
    ``"default"``. The other keys keep the names the zone editor's bridge reads:
    ``hasOverride`` (source is env), ``overriddenFields`` (the fields an ``XI_DB_*``
    key sets) and ``luaPath`` (always ``None``: network.lua is not read)."""
    env = _env_creds()
    h, p, u, _pw, db = _resolve(None, None, None, None, None)
    source = "env" if env else "default"
    return {"host": h, "port": p, "user": u, "database": db, "source": source,
            "luaPath": None,
            "hasOverride": source == "env",
            "overriddenFields": sorted(env)}


def friendly_db_error(exc: Exception) -> str:
    """Translate pymysql connection failures into something a user can act on.

    MariaDB answers a wrong username/password with ``auth_gssapi_client not
    configured`` rather than "access denied" (it avoids confirming whether an account
    exists). Taken at face value that reads like a missing Kerberos dependency, which
    is exactly the wrong thing to go fix."""
    msg = str(exc)
    if "auth_gssapi_client" in msg:
        return ("Wrong username or password. (MariaDB reports a failed login as an "
                "'auth_gssapi_client' plugin error rather than access denied.)")
    if "Access denied" in msg:
        return "Access denied — check the username and password."
    if "Unknown database" in msg:
        return "That database does not exist on the server."
    if any(s in msg for s in ("Can't connect", "Connection refused", "timed out", "WinError 10061")):
        return "Could not reach the server — is it running, and are the host and port right?"
    return msg


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
        click.option("--host",     default=None, help="DB host  [default: XI_DB_HOST, else 127.0.0.1]"),
        click.option("--port",     default=None, type=int, help="DB port  [default: XI_DB_PORT, else 3306]"),
        click.option("--user", "-u", default=None, help="DB user  [default: XI_DB_USER, else root]"),
        click.option("--password", "-p", default=None, help="DB password  [default: XI_DB_PASSWORD]"),
        click.option("--database", "--db", default=None, help="DB schema  [default: XI_DB_NAME, else xidb]"),
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
