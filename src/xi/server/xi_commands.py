import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import click

# ── Credentials ───────────────────────────────────────────────────────────────

from xi.xi_config import XI_SERVER_DIR

# Server checkout comes from XI_SERVER_DIR (env / .env) — no hardcoded default.
_LUA_CONFIG = Path(XI_SERVER_DIR) / "settings" / "network.lua" if XI_SERVER_DIR else None

_DEFAULTS = dict(host="127.0.0.1", port=3306, user="root", password="xi", database="tpzdb")

_LUA_PATTERNS = {
    "host":     r'SQL_HOST\s*=\s*"([^"]+)"',
    "port":     r'SQL_PORT\s*=\s*(\d+)',
    "user":     r'SQL_LOGIN\s*=\s*"([^"]+)"',
    "password": r'SQL_PASSWORD\s*=\s*"([^"]+)"',
    "database": r'SQL_DATABASE\s*=\s*"([^"]+)"',
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
    creds = {**_DEFAULTS, **_read_lua_creds(_LUA_CONFIG)}
    return (
        host     or creds["host"],
        port     or creds["port"],
        user     or creds["user"],
        password or creds["password"],
        database or creds["database"],
    )


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
        click.option("--database", "--db", default=None, help="DB schema  [default: tpzdb]"),
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
