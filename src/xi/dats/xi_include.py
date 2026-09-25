"""Include files in a ``xi dats`` project.

An ``actions`` entry that is a string is an include: the path, relative to the file it
appears in, of a ``xi.dats.include.v1`` file (schema/include.json) whose own ``actions``
are spliced in at that spot. Includes nest.

:func:`flatten` turns a project into the flat action list every builder sees and records
where each included action came from; :func:`documents` does the reverse for a write, so a
build's ``result`` lands in the file that holds its action and the include strings stay
where they were written. Included actions are matched back by ``id``, which they must
have; an action added at run time (``dats new`` / ``prepare``) goes in the project file.
"""
from __future__ import annotations

import json
from pathlib import Path

INCLUDE_SCHEMA = "xi.dats.include.v1"
LAYOUT_KEY = "_include_layout"   # private to a read manifest; never written or printed


class IncludeError(ValueError):
    pass


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise IncludeError(f"include not found: {path}") from None
    except (OSError, ValueError) as exc:
        raise IncludeError(f"invalid JSON in {path}: {exc}") from None
    if not isinstance(data, dict) or data.get("schema") != INCLUDE_SCHEMA \
            or not isinstance(data.get("actions"), list):
        raise IncludeError(f'{path}: an include file needs "schema": "{INCLUDE_SCHEMA}" and an "actions" list')
    return data


def _resolve(entry: str, owner: Path) -> Path:
    if not entry.lower().endswith(".json"):
        raise IncludeError(f"{owner}: include {entry!r} is not a .json path")
    return (owner.parent / entry).resolve()


def flatten(path: Path, actions: list) -> tuple[list, dict]:
    """``(flat actions, layout)`` for a project whose ``actions`` may hold includes.
    ``layout`` is plain JSON: the entries of every file read (an include's path, or an
    action's id) and, per included action id, the file it lives in."""
    root = Path(path).resolve()
    layout = {"root": str(root), "files": {}, "owners": {}}
    flat: list = []
    seen: dict = {}

    def walk(file: Path, items: list, chain: tuple) -> None:
        entries = []
        for item in items:
            if isinstance(item, str):
                target = _resolve(item, file)
                if str(target) in chain:
                    loop = " -> ".join(Path(p).name for p in (*chain, str(target)))
                    raise IncludeError(f"include cycle: {loop}")
                walk(target, _load(target)["actions"], (*chain, str(target)))
                entries.append({"include": item, "path": str(target)})
                continue
            aid = item.get("id") if isinstance(item, dict) else None
            if file != root and not aid:
                raise IncludeError(f"{file}: an included action needs an id")
            if aid and aid in seen and (file != root or seen[aid] != str(root)):
                raise IncludeError(f"action id {aid!r} is in both {seen[aid]} and {file}")
            if aid:
                seen[aid] = str(file)
                if file != root:
                    layout["owners"][aid] = str(file)
            flat.append(item)
            entries.append({"action": aid})
        layout["files"][str(file)] = {"entries": entries}

    walk(root, actions, (str(root),))
    return flat, layout


def _with_includes(entries: list, mine: list) -> list:
    """This file's current actions with its include strings put back. An include goes
    before the first of the actions that followed it when read that is still here (at the
    end when none is); actions the file didn't have when read go after everything."""
    ids = lambda a: a.get("id") if isinstance(a, dict) else None
    known = {e["action"] for e in entries if e.get("action")}
    present = {ids(a) for a in mine} - {None}
    pending = []
    for i, e in enumerate(entries):
        if "include" in e:
            anchor = next((x["action"] for x in entries[i + 1:] if x.get("action") in present), None)
            pending.append([anchor, e["include"]])
    old = [a for a in mine if not ids(a) or ids(a) in known]
    new = [a for a in mine if ids(a) and ids(a) not in known]
    out = []
    for action in old:
        for p in pending:
            if p[0] is not None and p[0] == ids(action):
                out.append(p[1])
                p[0] = p[1] = None
        out.append(action)
    out.extend(p[1] for p in pending if p[1] is not None)
    return out + new


def documents(path: Path, manifest: dict) -> dict[Path, dict]:
    """``{file: document}`` to write for ``manifest``: the project file, plus every include
    file it was read with, each holding its own actions again."""
    doc = {k: v for k, v in manifest.items() if k != LAYOUT_KEY}
    layout = manifest.get(LAYOUT_KEY)
    if not layout:
        return {Path(path): doc}
    root = layout["root"]
    buckets: dict[str, list] = {f: [] for f in layout["files"]}
    for action in manifest.get("actions") or []:
        aid = action.get("id") if isinstance(action, dict) else None
        owner = layout["owners"].get(aid) if aid else None
        buckets[owner if owner in buckets else root].append(action)
    out = {Path(path): {**doc, "actions": _with_includes(layout["files"][root]["entries"], buckets[root])}}
    for file, info in layout["files"].items():
        if file == root:
            continue
        p = Path(file)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError
        except (OSError, ValueError):
            data = {"schema": INCLUDE_SCHEMA}
        data["actions"] = _with_includes(info["entries"], buckets[file])
        out[p] = data
    return out


def project_actions(path: Path) -> list:
    """The flat action list of the project file at ``path`` (includes expanded), or ``[]``
    when it isn't a readable project. For code that scans ``projects/*.json``."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("schema") == INCLUDE_SCHEMA:
        return []
    actions = data.get("actions") or []
    if not any(isinstance(a, str) for a in actions):
        return actions
    try:
        return flatten(Path(path), actions)[0]
    except IncludeError:
        return [a for a in actions if isinstance(a, dict)]
