"""``xi mv database`` — bake the model viewer's Database tables to JSON.

The viewer's Assets > Database page decodes the client's record DATs (item
tables, quest/mission/key-item d_msg tables) in the browser. That means
reading 12–20 MB item DATs on every fresh session. This command does the same
decode once and writes ``mv/db/<table>.<lang>.json`` files the viewer prefers
when they exist (it falls back to the DATs otherwise).

The decoders mirror ``ui/js/database.js`` in xi-model-viewer — the table
registry, block layouts and JSON row shape must stay in step with it:

* item rows: ``{idx, part, raw}`` where ``raw`` is the typed header plus
  ``strings``/``stringOffset`` (real item records) or ``{id, block}`` with the
  decoded block base64-encoded (the non-item DATs the viewer decodes itself).
* d_msg rows: ``{idx, offset, length, subs}`` — ``subs`` is a list of text or,
  for a non-text sub-string, its integer marker.
"""

from __future__ import annotations

import base64
import json
import struct
import time
from pathlib import Path

import click

from xi.xi_config import FFXI_DIR, XI_TOOLS_DIR

# ── registry (keep in step with ui/js/database.js) ───────────────────────────

ITEM_TABLES = [
    ("general", "general", [("ROM/118/106.DAT", "ROM/0/4.DAT"), ("ROM/301/115.DAT", "ROM/301/114.DAT")]),
    ("usable", "usable", [("ROM/118/107.DAT", "ROM/0/5.DAT")]),
    ("puppet", "puppet", [("ROM/118/110.DAT", "ROM/0/8.DAT")]),
    ("armor", "armor", [("ROM/118/109.DAT", "ROM/0/7.DAT"), ("ROM/286/73.DAT", "ROM/286/72.DAT")]),
    ("weapons", "weapon", [("ROM/118/108.DAT", "ROM/0/6.DAT")]),
    ("maze", "maze", [("ROM/217/21.DAT", "ROM/217/20.DAT")]),
    ("monst1", "instinct", [("ROM/288/80.DAT", "ROM/288/79.DAT")]),
    ("roeObj", "roe", [("ROM/307/16.DAT", "ROM/307/15.DAT")]),
    ("items3", "instinctList", [("ROM/314/89.DAT", "ROM/314/89.DAT")]),
    ("monst2", "species", [("ROM/288/67.DAT", "ROM/288/66.DAT")]),
    ("roeCat", "roeCat", [("ROM/307/24.DAT", "ROM/307/23.DAT")]),
    ("items4", "positions", [("ROM/320/26.DAT", "ROM/320/26.DAT")]),
    ("items5", "idlist", [("ROM/332/49.DAT", "ROM/332/47.DAT")]),
    ("items6", "general", [("ROM/332/48.DAT", "ROM/332/46.DAT")]),
    ("gil", "general", [("ROM/174/48.DAT", "ROM/0/9.DAT")]),
]

DMSG_TABLES = [
    # quests
    ("q_sandoria", "ROM/176/60.DAT", "ROM/176/46.DAT"),
    ("q_bastok", "ROM/176/61.DAT", "ROM/176/47.DAT"),
    ("q_windurst", "ROM/176/62.DAT", "ROM/176/48.DAT"),
    ("q_jeuno", "ROM/176/63.DAT", "ROM/176/49.DAT"),
    ("q_other", "ROM/176/64.DAT", "ROM/176/50.DAT"),
    ("q_toau", "ROM/176/66.DAT", "ROM/176/52.DAT"),
    ("q_wotg", "ROM/196/6.DAT", "ROM/196/3.DAT"),
    ("q_abyssea", "ROM/242/64.DAT", "ROM/242/63.DAT"),
    ("q_assault", "ROM/176/72.DAT", "ROM/176/58.DAT"),
    ("q_campaign", "ROM/196/8.DAT", "ROM/196/5.DAT"),
    ("q_adoulin", "ROM/293/70.DAT", "ROM/293/67.DAT"),
    ("q_coalition", "ROM/293/71.DAT", "ROM/293/68.DAT"),
    # missions
    ("m_sandoria", "ROM/176/67.DAT", "ROM/176/53.DAT"),
    ("m_bastok", "ROM/176/68.DAT", "ROM/176/54.DAT"),
    ("m_windurst", "ROM/176/69.DAT", "ROM/176/55.DAT"),
    ("m_zilart", "ROM/176/70.DAT", "ROM/176/56.DAT"),
    ("m_cop", "ROM/176/71.DAT", "ROM/176/57.DAT"),
    ("m_toau", "ROM/176/73.DAT", "ROM/176/59.DAT"),
    ("m_wotg", "ROM/196/7.DAT", "ROM/196/4.DAT"),
    ("m_acp", "ROM/222/18.DAT", "ROM/222/17.DAT"),
    ("m_amk", "ROM/223/12.DAT", "ROM/223/10.DAT"),
    ("m_asa", "ROM/223/13.DAT", "ROM/223/11.DAT"),
    ("m_adoulin", "ROM/293/69.DAT", "ROM/293/66.DAT"),
    ("m_rov", "ROM/333/4.DAT", "ROM/333/3.DAT"),
    # names & text
    ("keyitems", "ROM/175/35.DAT", "ROM/175/34.DAT"),
    ("titles", "ROM/180/78.DAT", "ROM/180/77.DAT"),
    ("jobs", "ROM/165/86.DAT", "ROM/165/86.DAT"),
    ("spells", "ROM/181/73.DAT", "ROM/181/69.DAT"),
    ("spellHelp", "ROM/181/75.DAT", "ROM/181/71.DAT"),
    ("abilities", "ROM/181/72.DAT", "ROM/181/68.DAT"),
    ("abilityHelp", "ROM/181/74.DAT", "ROM/181/70.DAT"),
    ("bluHelp", "ROM/166/116.DAT", "ROM/166/115.DAT"),
    ("status", "ROM/180/102.DAT", "ROM/180/101.DAT"),
    ("mounts", "ROM/351/84.DAT", "ROM/351/82.DAT"),
    ("mountHelp", "ROM/351/85.DAT", "ROM/351/83.DAT"),
    ("monsterFamilies", "ROM/188/38.DAT", "ROM/188/37.DAT"),
    ("slots", "ROM/175/33.DAT", "ROM/175/32.DAT"),
    ("augments", "ROM/220/58.DAT", "ROM/220/57.DAT"),
    ("merits", "ROM/169/75.DAT", "ROM/169/74.DAT"),
    ("jobPoints", "ROM/314/62.DAT", "ROM/314/61.DAT"),
    ("jobGifts", "ROM/324/59.DAT", "ROM/324/58.DAT"),
    ("soulplates", "ROM/187/70.DAT", "ROM/187/67.DAT"),
    ("trust", "ROM/311/74.DAT", "ROM/311/73.DAT"),
    ("emoteHelp", "ROM/327/124.DAT", "ROM/327/123.DAT"),
    ("chatHelp", "ROM/173/89.DAT", "ROM/173/88.DAT"),
    ("mazeRunes", "ROM/219/86.DAT", "ROM/219/85.DAT"),
    ("headings", "ROM/165/81.DAT", "ROM/165/67.DAT"),
    ("servers", "ROM/333/34.DAT", "ROM/333/33.DAT"),
]

ALL_KEYS = [t[0] for t in ITEM_TABLES] + [t[0] for t in DMSG_TABLES]
LANGS = ("en", "jp")

ITEM_BLOCK = 0xC00
ICON_OFFSET = 0x280
ITEM_LAYOUTS = {"general", "usable", "puppet", "armor", "weapon", "maze", "instinct", "roe"}
EN_SUBS = ["name", "article", "logName", "logPlural", "description"]
JP_SUBS = ["name", "description"]
ELEMENT_GLYPHS = ["Fire", "Ice", "Wind", "Earth", "Lightning", "Water", "Light", "Dark"]

_ROT = bytes(((b << 3) | (b >> 5)) & 0xFF for b in range(256))


def decode_block(raw: bytes) -> bytes:
    """Rotate every byte left by 3 (the client's item "encryption")."""
    return raw.translate(_ROT)


def decode_text(b: bytes) -> str:
    """cp932 with the client's element glyphs (0xEF 0x1F..0x26) named and
    auto-translate brackets (0xFD + 4 bytes) dropped — same as the viewer."""
    out = []
    run = 0
    i = 0
    n = len(b)
    while i < n:
        c = b[i]
        if c == 0xEF and i + 1 < n:
            out.append(b[run:i].decode("cp932", "replace"))
            k = b[i + 1] - 0x1F
            if 0 <= k < len(ELEMENT_GLYPHS):
                out.append(ELEMENT_GLYPHS[k])
            i += 2
            run = i
            continue
        if c == 0xFD and i + 1 < n:
            out.append(b[run:i].decode("cp932", "replace"))
            i += 1 + min(4, n - 1 - i)
            run = i
            continue
        i += 1
    out.append(b[run:].decode("cp932", "replace"))
    return "".join(out)


def cstr(block: bytes, at: int) -> bytes:
    end = block.find(b"\x00", at)
    return block[at:end if end >= 0 else len(block)]


def u16(b, o): return struct.unpack_from("<H", b, o)[0]
def u32(b, o): return struct.unpack_from("<I", b, o)[0]


def find_string_block(block: bytes):
    for off in range(0x08, 0x82, 2):
        n = u32(block, off)
        if not 1 <= n <= 8:
            continue
        first = u32(block, off + 4)
        if first != 4 + n * 8:
            continue
        ok = True
        for i in range(n):
            o = u32(block, off + 4 + i * 8)
            f = u32(block, off + 8 + i * 8)
            if o < first or off + o + 4 > ICON_OFFSET or f > 1:
                ok = False
                break
        if ok:
            return off, n
    return None


def read_header(block: bytes, layout: str) -> dict:
    h = {
        "id": u32(block, 0x00), "flags": u16(block, 0x04), "stack": u16(block, 0x06),
        "type": u16(block, 0x08), "resourceId": u16(block, 0x0A), "targets": u16(block, 0x0C),
    }
    if layout in ("armor", "weapon"):
        h.update(level=u16(block, 0x0E), slots=u16(block, 0x10), races=u16(block, 0x12),
                 jobs=u32(block, 0x14), superiorLevel=u16(block, 0x18))
    if layout == "armor":
        h.update(shieldSize=u16(block, 0x1A), maxCharges=u16(block, 0x1C), castTime=u16(block, 0x1E),
                 useDelay=u16(block, 0x20), reuseDelay=u16(block, 0x22), itemLevel=u16(block, 0x26))
    elif layout == "weapon":
        h.update(damage=u16(block, 0x1C), delay=u16(block, 0x1E), dps=u16(block, 0x20),
                 skill=block[0x22], jugSize=block[0x23], maxCharges=u16(block, 0x28),
                 castTime=u16(block, 0x2A), useDelay=u16(block, 0x2C), reuseDelay=u16(block, 0x2E),
                 baseItemId=u16(block, 0x30), itemLevel=u16(block, 0x32))
    elif layout == "usable":
        h["castTime"] = u16(block, 0x0E)
    elif layout == "puppet":
        h.update(puppetSlot=u16(block, 0x0E), elementCharge=u32(block, 0x10))
    elif layout == "instinct":
        h.update(level=u16(block, 0x0E), instinctCost=u16(block, 0x18))
    return h


def raw_item_row(block: bytes, layout: str, lang: str):
    if layout in ITEM_LAYOUTS:
        h = read_header(block, layout)
        sb = find_string_block(block)
        if not sb:
            return None
        off, n = sb
        names = JP_SUBS if lang == "jp" else EN_SUBS
        strings = {}
        for i in range(n):
            o = u32(block, off + 4 + i * 8)
            f = u32(block, off + 8 + i * 8)
            key = names[i] if i < len(names) else f"sub{i}"
            strings[key] = decode_text(cstr(block, off + o + 0x1C)) if f == 0 else u32(block, off + o)
        name = strings.get("name")
        if not isinstance(name, str) or not name or set(name.strip()) == {"."}:
            return None
        h["strings"] = strings
        h["stringOffset"] = off
        return h
    item_id = u32(block, 0)
    if not item_id:
        return None
    return {"id": item_id, "block": base64.b64encode(block).decode("ascii")}


def bake_items(game: Path, key: str, layout: str, parts, lang: str):
    rows = []
    blocks = 0
    files = []
    for part, (en, jp) in enumerate(parts):
        rel = jp if lang == "jp" else en
        path = game / rel
        files.append(rel)
        if not path.is_file():
            continue
        data = path.read_bytes()
        count = len(data) // ITEM_BLOCK
        blocks += count
        for idx in range(count):
            block = decode_block(data[idx * ITEM_BLOCK:(idx + 1) * ITEM_BLOCK])
            raw = raw_item_row(block, layout, lang)
            if raw is not None:
                rows.append({"idx": idx, "part": part, "raw": raw})
    return {"kind": "items", "key": key, "lang": lang, "layout": layout, "files": files,
            "blocks": blocks, "rows": rows}


def dmsg_subs(blk: bytes):
    if len(blk) < 4:
        return []
    n = u32(blk, 0)
    if not 0 < n <= 64:
        return []
    subs = []
    for i in range(n):
        eo = 4 + i * 8
        if eo + 8 > len(blk):
            break
        off = u32(blk, eo)
        if off < 4 or off + 4 > len(blk):
            subs.append(None)
            continue
        marker = u32(blk, off)
        subs.append(decode_text(cstr(blk, off + 4 + 0x18)) if marker == 1 else marker)
    return subs


def bake_dmsg(game: Path, key: str, en: str, jp: str, lang: str):
    rel = jp if lang == "jp" else en
    path = game / rel
    if not path.is_file():
        return None
    data = path.read_bytes()
    if data[:5] != b"d_msg":
        raise click.ClickException(f"{rel} is not a d_msg table")
    xor = 0xFF if data[0x0A] else 0
    file_size = min(u32(data, 0x14) or len(data), len(data))
    table_offset = u32(data, 0x18)
    table_size = u32(data, 0x1C)
    stride = u32(data, 0x20)
    num = u32(data, 0x28)
    body = data[table_offset:file_size]
    if xor:
        body = bytes(b ^ xor for b in body)
    rows = []
    if table_size == 0:
        actual = min(num, len(body) // stride) if stride else 0
        for i in range(actual):
            blk = body[i * stride:(i + 1) * stride]
            rows.append({"idx": i, "offset": table_offset + i * stride, "length": len(blk), "subs": dmsg_subs(blk)})
    else:
        actual = min(num, table_size // 8)
        for i in range(actual):
            off = u32(body, i * 8)
            ln = u32(body, i * 8 + 4)
            s = table_size + off
            blk = body[s:s + ln] if s + ln <= len(body) else b""
            rows.append({"idx": i, "offset": table_offset + s, "length": len(blk), "subs": dmsg_subs(blk)})
    return {"kind": "dmsg", "key": key, "lang": lang, "files": [rel],
            "variant": "fixed" if table_size == 0 else "variable", "stride": stride, "xor": xor,
            "num": num, "tableOffset": table_offset, "rows": rows}


def default_out_dir() -> Path:
    return Path(XI_TOOLS_DIR) / "mv" / "db"


@click.command("database")
@click.option("--only", default=None, metavar="KEYS",
              help=f"Comma-separated subset of tables (default: all). Choices: {', '.join(ALL_KEYS)}")
@click.option("--lang", "langs", default="en,jp", show_default=True, help="Client languages to bake (en, jp).")
@click.option("--out", "out_dir", type=click.Path(path_type=Path, file_okay=False), default=None,
              help="Output directory  [default: mv/db under XI_TOOLS_DIR].")
@click.option("--game", "game_dir", type=click.Path(path_type=Path, file_okay=False, exists=True), default=None,
              help="FINAL FANTASY XI install  [default: FFXI_DIR].")
def cmd(only: str | None, langs: str, out_dir: Path | None, game_dir: Path | None):
    """Bake the model viewer's Database tables (items, quests, missions, …) to JSON.

    \b
    Writes mv/db/<table>.<lang>.json plus manifest.json. The viewer's
    Assets > Database page loads these instead of decoding the 12–20 MB
    item DATs in the browser. Examples:
      xi mv database
      xi mv database --only armor,weapons --lang en
    """
    game = game_dir or (Path(FFXI_DIR) if FFXI_DIR else None)
    if not game or not game.is_dir():
        raise click.ClickException("FFXI_DIR is not set (or --game is not a directory)")
    out = out_dir or default_out_dir()
    out.mkdir(parents=True, exist_ok=True)
    keys = [k.strip() for k in only.split(",")] if only else ALL_KEYS
    unknown = [k for k in keys if k not in ALL_KEYS]
    if unknown:
        raise click.ClickException(f"unknown table(s): {', '.join(unknown)}")
    lang_list = [l.strip() for l in langs.split(",") if l.strip()]
    bad = [l for l in lang_list if l not in LANGS]
    if bad:
        raise click.ClickException(f"unknown language(s): {', '.join(bad)}")

    stamp = time.strftime("%Y-%m-%d %H:%M")
    manifest_path = out / "manifest.json"
    manifest = {"generated": stamp, "game": str(game), "tables": {}}
    if manifest_path.is_file():
        try:
            manifest["tables"] = json.loads(manifest_path.read_text("utf-8")).get("tables", {})
        except (OSError, ValueError):
            pass

    items = {k: (layout, parts) for k, layout, parts in ITEM_TABLES}
    dmsgs = {k: (en, jp) for k, en, jp in DMSG_TABLES}
    t0 = time.time()
    for key in keys:
        for lang in lang_list:
            if key in items:
                layout, parts = items[key]
                doc = bake_items(game, key, layout, parts, lang)
            else:
                en, jp = dmsgs[key]
                doc = bake_dmsg(game, key, en, jp, lang)
                if doc is None:
                    click.echo(f"  {key:16} {lang}  missing — skipped")
                    continue
            doc["generated"] = stamp
            target = out / f"{key}.{lang}.json"
            target.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), "utf-8")
            manifest["tables"][f"{key}.{lang}"] = {
                "file": target.name, "rows": len(doc["rows"]), "files": doc["files"], "generated": stamp,
            }
            click.echo(f"  {key:16} {lang}  {len(doc['rows']):>6} rows  {target.stat().st_size / 1e6:6.2f} MB")
    manifest_path.write_text(json.dumps(manifest, indent=2), "utf-8")
    click.echo(f"wrote {out} in {time.time() - t0:.1f}s")
