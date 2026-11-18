"""``xi database json`` / ``xi database search``: a client table's rows as JSON — the rows the
model viewer's Database shows (``xi mv database`` bakes the same ones), by the same table key.

``mobSkills`` (the monster ability names, ``ROM/27/80`` / ``79``) lists ``{id, name}`` by the
server's ``mob_skill_id``; with ``XI_SERVER_DIR`` set, each row also carries the server's
``mob_skills`` row (``server``: animation, AoE, range, times, flags, skillchains), read from
``sql/mob_skills.sql``, and the server's rows the client has no name for are listed too (``name``
null) — what a new skill needs named. Rows the client doesn't name ('.', '#n') are left out
unless ``--all``. Server ids below 256 are weapon-skill names (the ``abilities`` table).
"""
from __future__ import annotations

import re
from pathlib import Path

import click

_VALUES = re.compile(r"INSERT INTO `(\w+)` VALUES\s*\((.*?)\);\s*$", re.M)
_COLUMN = re.compile(r"^\s*`(\w+)`\s+\w+", re.M)


def _cells(text: str) -> list:
    """One VALUES tuple's cells: quoted text, numbers, NULL."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in " ,":
            i += 1
        elif c == "'":
            j, buf = i + 1, []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                elif text[j] == "'" and j + 1 < n and text[j + 1] == "'":
                    buf.append("'")
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    buf.append(text[j])
                    j += 1
            out.append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and text[j] != ",":
                j += 1
            tok = text[i:j].strip()
            if tok.upper() == "NULL":
                out.append(None)
            else:
                try:
                    out.append(int(tok))
                except ValueError:
                    try:
                        out.append(float(tok))
                    except ValueError:
                        out.append(tok)
            i = j
    return out


def server_rows(sql_name: str, table: str, server_dir: str | None = None) -> dict[int, dict] | None:
    """The rows of ``table`` in the server's ``sql/<sql_name>`` by their first column, as
    ``{column: value}``; None without ``XI_SERVER_DIR`` or the file."""
    if server_dir is None:
        import xi.xi_config as cfg
        server_dir = cfg.XI_SERVER_DIR
    if not server_dir:
        return None
    path = Path(server_dir) / "sql" / sql_name
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    create = re.search(rf"CREATE TABLE[^`]*`{table}`\s*\((.*?)\)\s*ENGINE", text, re.S)
    cols = _COLUMN.findall(create.group(1)) if create else []
    out: dict[int, dict] = {}
    for m in _VALUES.finditer(text):
        if m.group(1) != table:
            continue
        cells = _cells(m.group(2))
        if cells and isinstance(cells[0], int):
            out[cells[0]] = dict(zip(cols, cells)) if len(cols) == len(cells) else {"values": cells}
    return out


def mob_skill_rows(root: Path, lang: str = "en", show_all: bool = False) -> list[dict]:
    """The monster ability names by mob_skill_id, joined with the server's rows."""
    from xi.database import xi_build as DB
    rows = DB.event_rows(root, "mobSkills", lang)
    if rows is None:
        en, jp, _first = DB.C.EVENT_TABLES["mobSkills"]
        raise click.ClickException(f"{jp if lang == 'jp' else en} is not in {root}")
    server = server_rows("mob_skills.sql", "mob_skills")
    out = []
    for r in rows:
        row = {"id": r["id"], "name": r["name"], "row": r["idx"]}
        srv = (server or {}).pop(r["id"], None)
        if srv is not None:
            row["server"] = {k: v for k, v in srv.items() if k != "mob_skill_id"}
        if show_all or srv is not None or not DB.is_placeholder(r["name"]):
            out.append(row)
    for sid, srv in sorted((server or {}).items()):
        if sid >= DB.C.EVENT_TABLES["mobSkills"][2]:            # past the client's table: no name yet
            out.append({"id": sid, "name": None, "row": None,
                        "server": {k: v for k, v in srv.items() if k != "mob_skill_id"}})
    return out


def table_rows(table: str, lang: str = "en", show_all: bool = False, root: Path | None = None) -> list[dict]:
    """The rows of ``table`` (a viewer table key) as the viewer's bake has them."""
    import xi.xi_config as cfg
    from xi.database import xi_core as C
    from xi.mv import xi_database as MV
    game = Path(root or cfg.FFXI_DIR)
    if table == "mobSkills":
        return mob_skill_rows(game, lang, show_all)
    items = {k: (layout, parts) for k, layout, parts in MV.ITEM_TABLES}
    dmsgs = {k: (en, jp) for k, en, jp in MV.DMSG_TABLES}
    menus = {k: (kind, names, helps) for k, kind, names, helps in MV.MENU_TABLES}
    if table in items:
        doc = MV.bake_items(game, table, *items[table], lang)
    elif table in menus:
        doc = MV.bake_menu(game, table, *menus[table], lang)
    elif table in dmsgs:
        doc = MV.bake_dmsg(game, table, *dmsgs[table], lang)
    else:
        raise click.ClickException(f"{table!r} is not a table ({', '.join(MV.ALL_KEYS)})")
    if doc is None:
        raise click.ClickException(f"{table}: its {lang} file is not in {game}")
    return doc["rows"]


def _matches(row: dict, query: str) -> bool:
    """A number (``257``, ``#257``) is an id; anything else is text anywhere in the row."""
    q = query.strip().lower()
    if q.lstrip("#").isdigit():
        n = int(q.lstrip("#"))
        return row.get("id", row.get("idx")) == n
    import json
    return q in json.dumps(row, ensure_ascii=False).lower()


def _emit(rows: list[dict], output: Path | None) -> None:
    from xi.xi_simplified import _write_json
    _write_json(rows, output)


_TABLE_HELP = "A table key: mobSkills, abilities, spells, keyitems, armor, … (xi mv database --help lists them)."


@click.command("json")
@click.argument("table")
@click.option("--search", "query", default=None, help="Only the rows whose id is it or whose text contains it.")
@click.option("--lang", type=click.Choice(["en", "jp"]), default="en", show_default=True)
@click.option("--all", "show_all", is_flag=True, help="mobSkills: include the rows the client leaves unnamed ('.').")
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False), default=None,
              help="Write the JSON here instead of stdout.")
def json_cmd(table: str, query: str | None, lang: str, show_all: bool, output: Path | None):
    """Print TABLE's rows as JSON: the client's record tables, by the viewer's table key.

    \b
    mobSkills is listed by mob_skill_id (the client's ROM/27/80, row = id - 256), joined with
    the server's mob_skills row when XI_SERVER_DIR is set.

    \b
    Examples:
      xi database json mobSkills
      xi database json mobSkills --search "foot kick"
      xi database json mobSkills --all --output mob_skills.json
      xi database json abilities --lang jp
    """
    rows = table_rows(table, lang, show_all)
    if query:
        rows = [r for r in rows if _matches(r, query)]
    _emit(rows, output)


@click.command("search")
@click.argument("table")
@click.argument("query")
@click.option("--lang", type=click.Choice(["en", "jp"]), default="en", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Print the matching rows as JSON instead of a table.")
def search_cmd(table: str, query: str, lang: str, as_json: bool):
    """Find rows of TABLE by id or text: `xi database search mobSkills "breath"`.

    \b
    Examples:
      xi database search mobSkills "foot kick"
      xi database search mobSkills 257
      xi database search mobSkills hell_trap        (the server name matches too)
      xi database search abilities "fast blade" --json
    """
    rows = [r for r in table_rows(table, lang, show_all=True) if _matches(r, query)]
    if as_json:
        _emit(rows, None)
        return
    if not rows:
        click.echo(click.style(f"No {table} row matches {query!r}.", fg="yellow"))
        return
    for r in rows:
        rid = r.get("id", r.get("idx"))
        name = r.get("name")
        if name is None and r.get("subs"):
            name = next((s for s in r["subs"] if isinstance(s, str) and s), "")
        if name is None:
            name = (r.get("raw") or {}).get("strings", [""])[0] if isinstance(r.get("raw"), dict) else ""
        srv = r.get("server") or {}
        extra = f"  [{srv.get('mob_skill_name')} anim {srv.get('mob_anim_id')}]" if srv else ""
        click.echo(f"{rid:>6}  {name if name is not None else '(no client name)'}{extra}")
    click.echo(click.style(f"{len(rows)} row(s).", fg="green"), err=True)
