"""The build order (schema/build_order.json): the projects ``xi dats build --all`` builds,
first to last, after every table any of them edits in place is reset from its ``.base``.

Projects that share a table (two that edit ``ROM/118/114.DAT``, a zone editor project and
a project that edits the same zone's dialog) then layer the same way on every build: each
starts from the tables as the ones before it left them, and nothing a project dropped
lingers. The file records, per target, the tables the last ``--all`` left edited, so the
next one resets those too.
"""
from __future__ import annotations

import json
from pathlib import Path

ORDER_SCHEMA = "xi.dats.build_order.v1"
DEFAULT_PATH = Path("projects") / "build_order.json"
TARGETS = ("dir", "pivot")
_KEYS = {"schema", "description", "projects", "result"}


class OrderError(ValueError):
    pass


def validate(doc) -> list[str]:
    """Problems with a build order, each naming the field; ``[]`` when it is valid.
    Mirrors schema/build_order.json."""
    if not isinstance(doc, dict):
        return ["the build order must be an object"]
    errs = [f"unknown key {k!r}" for k in doc if k not in _KEYS]
    if doc.get("schema") != ORDER_SCHEMA:
        errs.append(f'schema must be "{ORDER_SCHEMA}"')
    if "description" in doc and not isinstance(doc["description"], str):
        errs.append("description must be a string")
    projects = doc.get("projects")
    if not isinstance(projects, list) or not projects:
        errs.append("projects must be a non-empty list of project names or paths")
    else:
        seen = set()
        for i, p in enumerate(projects):
            if not isinstance(p, str) or not p.strip():
                errs.append(f"projects[{i}] must be a project name or a path to a project file")
            elif p in seen:
                errs.append(f"projects[{i}]: {p!r} is listed twice")
            else:
                seen.add(p)
    result = doc.get("result")
    if result is not None:
        files = result.get("files") if isinstance(result, dict) else None
        if not isinstance(result, dict) or set(result) - {"files"} or not isinstance(files or {}, dict):
            errs.append("result must be {\"files\": {target: [ROM paths]}}")
        else:
            for t, rels in (files or {}).items():
                if t not in TARGETS or not (isinstance(rels, list) and all(isinstance(r, str) for r in rels)):
                    errs.append(f"result.files.{t} must be a list of ROM paths (targets: dir, pivot)")
    return errs


def load(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise OrderError(f"no build order at {path} (see schema/build_order.json: "
                         f'{{"schema": "{ORDER_SCHEMA}", "projects": ["main", …]}})')
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise OrderError(f"{path}: not valid JSON ({e})") from None
    errs = validate(doc)
    if errs:
        raise OrderError(f"{path}: " + "; ".join(errs))
    return doc


def save(path: Path, doc: dict) -> None:
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def project_path(entry: str, order_path: Path) -> Path:
    """A project of the order: a bare name is ``projects/<name>.json``; anything else is a
    path, relative to the order file."""
    p = Path(entry)
    if p.parent == Path(".") and p.suffix == "":
        return Path("projects") / f"{p.name}.json"
    return p if p.is_absolute() else Path(order_path).parent / p


def recorded(doc: dict, target: str) -> list[str]:
    return list((((doc.get("result") or {}).get("files") or {}).get(target)) or [])


def record(doc: dict, target: str, rels) -> None:
    doc.setdefault("result", {}).setdefault("files", {})[target] = sorted(set(rels))


def reset_tables(root: Path, rels, dry_run: bool) -> dict[str, list[str]]:
    """Put each table ``rels`` names in ``root`` back to its ``.base``:
    ``{"restored": [...], "removed": [...], "none": [...]}`` — ``removed`` is a pivot copy a
    build made, ``none`` has no ``.base`` (never written through xi-tools, or before it kept
    one in the pivot folder). A dry run holds the reset tables for the builds after it."""
    import xi.xi_config as cfg
    from xi.dats import xi_stage
    from xi.menu.xi_menu_table import target_path
    out: dict[str, list[str]] = {"restored": [], "removed": [], "none": []}
    for rel in rels:
        p = target_path(root, rel)
        base = p.with_name(p.name + ".base")
        if not dry_run:
            out[cfg.reset_to_base(p)].append(rel)
        elif not base.is_file():
            out["none"].append(rel)
        elif base.stat().st_size == 0:
            # Without the pivot copy a read falls back to the install's.
            install = Path(cfg.FFXI_DIR) / Path(*rel.split("/"))
            if install.is_file():
                xi_stage.write(root, rel, install.read_bytes(), True)
            out["removed"].append(rel)
        else:
            xi_stage.write(root, rel, base.read_bytes(), True)
            out["restored"].append(rel)
    return out
