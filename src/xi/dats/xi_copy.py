"""The ``copy`` action (schema/copy.json): a file put into the target verbatim.

For content that is kept as the finished file itself, not as edits: a UI sheet redrawn
with ``xi ui tex``, a replaced music track. The build writes the file at ``target.path``
in the install or the pivot folder; nothing is registered, so the path must be one the
client already reads (a DAT the FTABLE already points at, a ``sound*/…`` track). A new
DAT that needs a file id goes through a type that registers one (mount, entity, gear …).

In the install the file it replaces is kept as ``<file>.base`` first (the in-place
contract), and ``undo`` puts that back; in a pivot folder ``undo`` deletes the copy, so
the client reads the install's file again.
"""
import re
import shutil
from pathlib import Path, PurePosixPath

ACTION_KEYS = {"id", "type", "enabled", "description", "depends_on", "outputs",
               "target", "resources", "result"}
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_ROM = re.compile(r"^ROM([0-9]*)$", re.I)
_SOUND = re.compile(r"^sound[0-9]*$", re.I)


def rel_path(value) -> str | None:
    """``value`` as a path inside the game folder (forward slashes, a ROM DAT with its
    ``.DAT``), or None when it isn't one: absolute, a drive, ``..``, or empty."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().replace("\\", "/")
    if s.startswith("/") or ":" in s:
        return None
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    if len(parts) == 3 and _ROM.match(parts[0]) and parts[1].isdigit() and parts[2].isdigit():
        parts[2] += ".DAT"
    return "/".join(parts)


def guess_target(source: Path) -> str | None:
    """The game path a source file stands for, read off its own path: the ``ROM*/n/n.DAT``
    or ``sound*/…`` it ends with (``rom/ROM/119/51.DAT`` → ``ROM/119/51.DAT``)."""
    parts = PurePosixPath(Path(source).as_posix()).parts
    for i, part in enumerate(parts):
        rest = parts[i:]
        if _ROM.match(part) and len(rest) == 3 and re.match(r"^[0-9]+$", rest[1]) \
                and re.match(r"^[0-9]+\.dat$", rest[2], re.I):
            return f"{part.upper()}/{rest[1]}/{rest[2].split('.')[0]}.DAT"
        if _SOUND.match(part) and len(rest) > 1:
            return "/".join(rest)
    return None


def validate_action(action: dict) -> list[str]:
    """What's wrong with a copy action, one line per problem (empty when it's fine)."""
    errs = [f"unknown key {k!r}" for k in action if k not in ACTION_KEYS]
    if not isinstance(action.get("id"), str) or not _ID.match(action["id"]):
        errs.append("id must be lowercase letters, digits, '_', '.' or '-'")
    if action.get("type") != "copy":
        errs.append("type must be 'copy'")
    target = action.get("target")
    if not isinstance(target, dict) or set(target) - {"path"}:
        errs.append("target must be {\"path\": <path in the game folder>}")
    elif rel_path(target.get("path")) is None:
        errs.append(f"target.path {target.get('path')!r} must be a path inside the game folder "
                    "(like ROM/119/51.DAT or sound9/win/music/data/music067.bgw)")
    res = action.get("resources")
    if not isinstance(res, dict) or set(res) - {"file"}:
        errs.append("resources must be {\"file\": <the file to copy>}")
    elif not isinstance(res.get("file"), str) or not res["file"].strip():
        errs.append("resources.file must name the file to copy")
    return errs


def _find(root: Path, rel: str) -> Path:
    """``root/rel``, matching an existing file's case part by part (a pivot folder may hold
    ``119/51.dat`` for ``ROM/119/51.DAT``); the path as written when there is none."""
    p = root
    for i, part in enumerate(rel.split("/")):
        exact = p / part
        if exact.exists() or not p.is_dir():
            p = exact
            continue
        hit = next((c for c in p.iterdir() if c.name.lower() == part.lower()), None)
        if hit is None:
            return p.joinpath(*rel.split("/")[i:])
        p = hit
    return p


def place(action: dict, src: Path, root: Path, *, in_install: bool, dry_run: bool = False) -> dict:
    """Write ``src`` at the action's ``target.path`` under ``root``. ``state`` says what
    happened: ``copied``, ``unchanged`` (the target already holds these bytes) or
    ``source`` (the target *is* the source file: a build into the folder it lives in)."""
    rel = rel_path(action["target"]["path"])
    out = _find(root, rel)
    blob = src.read_bytes()
    if out.exists() and out.resolve() == src.resolve():
        state = "source"
    elif out.is_file() and out.read_bytes() == blob:
        state = "unchanged"
    else:
        state = "copied"
        if not dry_run:
            if in_install and out.is_file():
                base = out.with_name(out.name + ".base")
                if not base.exists():
                    shutil.copy2(out, base)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(blob)
    return {"id": action["id"], "type": "copy", "path": rel, "output": str(out),
            "source": str(src.resolve()), "bytes": len(blob), "state": state}


def undo(action: dict, root: Path, in_install: bool, src: Path | None = None) -> tuple[int, list[str]]:
    """Take a copy back out of ``root``: in the install the file it replaced goes back
    (its ``.base``; a file that was new is deleted), in a pivot folder the copy is deleted.
    ``src`` (the resolved source) is never deleted. ``(1 when something changed, warnings)``."""
    rel = (action.get("result") or {}).get("path") or rel_path((action.get("target") or {}).get("path"))
    if not rel:
        return 0, []
    out = _find(root, rel)
    if not out.is_file():
        return 0, []
    if src is not None and src.exists() and src.resolve() == out.resolve():
        return 0, [f"{rel} is {action['id']}'s own source file: left alone"]
    base = out.with_name(out.name + ".base")
    if in_install and base.is_file() and base.stat().st_size:
        shutil.copy2(base, out)
    else:
        out.unlink()
    return 1, []
