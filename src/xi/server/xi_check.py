"""``xi server check``: what the local server setup looks like to xi-tools, read-only
(design2 §2.7, with design2-overrides O3/O4).

Credentials come from xi-tools' ``.env`` only (``XI_DB_*``; the server's network.lua
is never read). The report never holds the password. ``--json`` prints one
``xi.server-check.v1`` document and always exits 0 (the model viewer throws on a
non-zero exit); text mode exits 0 too.
"""
from __future__ import annotations

import json
from pathlib import Path

import click

from xi.server.xi_commands import db_configured, field_sources, resolved_creds
from xi.server.xi_step import current_server_dir

SCHEMA = "xi.server-check.v1"
TABLES = ("spell_list", "abilities", "weapon_skills")


def server_check(db: bool = True, binary: bool = True) -> dict:
    """Build ``xi.server-check.v1``. ``db=False`` makes no connection attempt;
    ``binary=False`` skips the xi_map PDB probe and the process check."""
    from xi.server import xi_db_apply as D
    from xi.server import xi_ws_widen as W

    rc = resolved_creds()
    conf = db_configured()
    server_dir = current_server_dir()
    sd = Path(server_dir) if server_dir else None
    doc = {
        "schema": SCHEMA,
        "ok": False,
        "configured": conf,
        "source": rc["source"],
        "overriddenFields": list(rc["overriddenFields"]),
        "fieldSources": field_sources(),
        "serverDir": str(sd) if sd else None,
        "serverDirValid": bool(sd and sd.is_dir() and (sd / "scripts" / "actions").is_dir()),
        "host": rc["host"], "port": int(rc["port"]), "user": rc["user"], "database": rc["database"],
        "connected": None, "version": None, "sqlMode": None, "tables": None,
        "weaponSkills": None,
        "error": None,
    }
    conn = None
    try:
        if db:
            if not conf:
                doc["error"] = D.NOT_CONFIGURED
            else:
                try:
                    conn = D.connect()
                except D.DbUnavailable as e:
                    doc["connected"] = False
                    doc["error"] = str(e)
        if conn is not None:
            try:
                doc["version"] = str((D._q(conn, "SELECT VERSION()") or [[None]])[0][0])
                doc["sqlMode"] = str((D._q(conn, "SELECT @@SESSION.sql_mode") or [[""]])[0][0])
                present = {str(r[0]).lower() for r in D._q(
                    conn, "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                          "AND TABLE_NAME IN ('spell_list', 'abilities', 'weapon_skills')")}
                cols = {str(r[0]).lower(): str(r[1]) for r in D._q(
                    conn, "SELECT TABLE_NAME, COLUMN_TYPE FROM information_schema.COLUMNS WHERE "
                          "TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = 'animation' "
                          "AND TABLE_NAME IN ('spell_list', 'abilities', 'weapon_skills')")}
                doc["tables"] = {t: {"present": t in present, "animation": cols.get(t)} for t in TABLES}
                doc["connected"] = True
            except Exception as e:                  # noqa: BLE001
                from xi.server.xi_commands import friendly_db_error
                doc["connected"] = True
                doc["error"] = friendly_db_error(e)
        try:
            doc["weaponSkills"] = W.ws_state(conn if doc["connected"] else None, server_dir, binary=binary)
        except Exception as e:                      # noqa: BLE001
            doc["weaponSkills"] = W.ws_state(None, server_dir, binary=binary)
            doc["error"] = doc["error"] or f"weapon_skills: {e}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                       # noqa: BLE001
                pass
    tables_ok = doc["tables"] is None or all(v["present"] for v in doc["tables"].values())
    doc["ok"] = bool(conf and doc["connected"] is not False and tables_ok
                     and not (db and conf and doc["error"]))
    return doc


def _sources_text(fs: dict) -> str:
    groups: dict = {}
    for k, v in fs.items():
        groups.setdefault(v, []).append(k)
    if len(groups) == 1:
        (src, _), = groups.items()
        return "all from xi-tools .env" if src == "env" else "all defaults"
    label = {"env": "xi-tools .env", "default": "default"}
    return " · ".join(f"{', '.join(ks)}: {label.get(src, src)}" for src, ks in groups.items())


def _ws_text(ws: dict | None) -> str:
    if not ws:
        return "not checked"
    st = ws["source"]["state"]
    if st == "no-server-dir":
        return "no server folder — set XI_SERVER_DIR to check"
    if st == "stock":
        return "stock — 0–255 only (xi server ws-widen writes the C++ patch and SQL)"
    if st in ("partial", "unrecognised"):
        first = next((e for e in ws["source"]["edits"] if e["state"] != "wide"), None)
        where = f" ({first['file']}:{first['line'] or '?'})" if first else ""
        return f"{st}{where} — finish the 4 lines by hand (xi server ws-widen --print shows them)"
    if ws["ready"]:
        return "ready (16-bit)"
    parts = ["source widened"]
    if ws["columnWide"] is False:
        parts.append(f"the column is still {ws['column']} — run the SQL from xi server ws-widen")
    elif ws["columnWide"] is None:
        parts.append("database column not checked")
    b = ws["binary"]
    if b["rebuilt"] is False:
        parts.append(f"rebuild xi_map ({ws['buildCommand'] or 'cmake --build … --target xi_map'}), then restart it")
    elif b["rebuilt"] is None and b["method"] != "skipped":
        parts.append("can't tell whether xi_map was rebuilt (no xi_map.pdb)")
    return " · ".join(parts)


def check_lines(doc: dict) -> list:
    """The text-mode report."""
    folder = doc["serverDir"] or "(not set: XI_SERVER_DIR)"
    if doc["serverDir"]:
        folder += "  (scripts/actions found)" if doc["serverDirValid"] else "  (no scripts/actions — not a LandSandBoat checkout?)"
    who = f"{doc['database']}@{doc['host']}:{doc['port']} as {doc['user']}"
    lines = ["Local server",
             f"  folder:        {folder}",
             f"  database:      {who}  ({_sources_text(doc['fieldSources'])})"
             + ("" if doc["configured"] else " — not configured")]
    if doc["connected"]:
        lines.append(f"  connected:     {doc['version']} · sql_mode {doc['sqlMode'] or '(empty)'}")
    elif doc["connected"] is False:
        lines.append(f"  connected:     no — {doc['error']}")
    else:
        err = doc["error"]
        lines.append("  connected:     not checked" + (f" — {err}" if err else ""))
    if doc["tables"]:
        lines.append("  tables:        " + " · ".join(
            f"{t}.animation {v['animation']}" if v["present"] else f"{t} missing" for t, v in doc["tables"].items()))
    lines.append(f"  weapon skills: {_ws_text(doc['weaponSkills'])}")
    return lines


@click.command("check")
@click.option("--json", "as_json", is_flag=True, help="Print xi.server-check.v1 JSON (always exits 0).")
@click.option("--no-db", is_flag=True, help="Make no database connection attempt.")
@click.option("--no-binary", is_flag=True, help="Skip the xi_map.pdb probe and the process check.")
def check_cmd(as_json, no_db, no_binary):
    """Show the local server setup: folder, database, tables, weapon-skill widening.

    Read-only. Credentials come from XI_DB_* in xi-tools' .env (Settings › Local
    Server in the model viewer); the password is never shown.

    \b
    Examples:
      xi server check
      xi server check --json --no-db
    """
    doc = server_check(db=not no_db, binary=not no_binary)
    if as_json:
        click.echo(json.dumps(doc, ensure_ascii=False, indent=2))
        return
    for line in check_lines(doc):
        click.echo(line)
