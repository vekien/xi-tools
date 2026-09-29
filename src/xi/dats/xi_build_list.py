"""A build list (schema/build_list.json): the projects ``xi dats build --list`` builds, first
to last. With ``--reset`` every table any of them edits in place is reset once, before the
first project, to the install's untouched copy (its ``.base``, else the file).

Projects that share a table (two that edit ``ROM/118/114.DAT``, a zone editor project and a
project that edits the same zone's dialog) then layer the same way on every build: each
starts from the tables as the ones before it left them. The file records, per target, the
tables the projects edit, so a ``--reset`` resets a table a project dropped (or a project
taken off the list) too. Resetting is here as well: :func:`reset_tables`, which a single
project's ``--reset`` uses.
"""
from __future__ import annotations

import json
from pathlib import Path

LIST_SCHEMA = "xi.dats.build_list.v1"
DEFAULT_PATH = Path("projects") / "build_list.json"
TARGETS = ("dir", "pivot")
_KEYS = {"schema", "description", "projects", "result"}


class ListError(ValueError):
    pass


def validate(doc) -> list[str]:
    """Problems with a build list, each naming the field; ``[]`` when it is valid.
    Mirrors schema/build_list.json."""
    if not isinstance(doc, dict):
        return ["the build list must be an object"]
    errs = [f"unknown key {k!r}" for k in doc if k not in _KEYS]
    if doc.get("schema") != LIST_SCHEMA:
        errs.append(f'schema must be "{LIST_SCHEMA}"')
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


def is_list(path: Path) -> bool:
    """``path`` is a build list (by its ``schema``), not a project."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("schema") == LIST_SCHEMA
    except (OSError, ValueError, AttributeError):
        return False


def list_path(arg) -> Path:
    """The build list ``xi dats build --list [LIST]`` names: a bare name is
    ``projects/<name>.json``, anything else a path; none is ``projects/build_list.json``."""
    if not arg:
        return DEFAULT_PATH
    p = Path(arg)
    return Path("projects") / f"{p.name}.json" if p.parent == Path(".") and p.suffix == "" else p


def load(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ListError(f"no build list at {path} (see schema/build_list.json: "
                        f'{{"schema": "{LIST_SCHEMA}", "projects": ["main", …]}})')
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ListError(f"{path}: not valid JSON ({e})") from None
    errs = validate(doc)
    if errs:
        raise ListError(f"{path}: " + "; ".join(errs))
    return doc


def save(path: Path, doc: dict) -> None:
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def project_path(entry: str, list_file: Path) -> Path:
    """A project of the list: a bare name is ``projects/<name>.json``; anything else is a
    path, relative to the list file."""
    p = Path(entry)
    if p.parent == Path(".") and p.suffix == "":
        return Path("projects") / f"{p.name}.json"
    return p if p.is_absolute() else Path(list_file).parent / p


def recorded(doc: dict, target: str) -> list[str]:
    return list((((doc.get("result") or {}).get("files") or {}).get(target)) or [])


def record(doc: dict, target: str, rels) -> None:
    doc.setdefault("result", {}).setdefault("files", {})[target] = sorted(set(rels))


def reset_tables(root: Path, rels, dry_run: bool) -> dict[str, list[str]]:
    """Reset each table ``rels`` names in ``root`` to the install's untouched copy
    (:func:`xi.xi_config.pristine`: its ``.base``, else the file itself):
    ``{"restored": [...], "none": [...]}``. In the install that is its ``.base``; ``none``
    there has no ``.base`` (never written through xi-tools, so already untouched). Into the
    pivot folder the install's copy is written over the pivot's; ``none`` is a table the
    install doesn't have. A dry run holds the reset tables for the builds after it."""
    import xi.xi_config as cfg
    from xi.dats import xi_stage
    from xi.menu.xi_menu_table import target_path
    out: dict[str, list[str]] = {"restored": [], "none": []}
    install = Path(cfg.FFXI_DIR)
    in_place = Path(root).resolve() == install.resolve()
    for rel in rels:
        src = install / Path(*rel.split("/"))
        source = cfg.pristine(src)
        if source is None or (in_place and source == src):
            out["none"].append(rel)
            continue
        if dry_run:
            xi_stage.write(root, rel, source.read_bytes(), True)
        else:
            p = target_path(root, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(source.read_bytes())
        out["restored"].append(rel)
    return out
