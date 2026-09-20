"""Lua Stub: the server script for a spell, job ability or weapon skill a mix created
(design2 §2.5; ``round2/lua-stub.md``).

The server finds an action's script by the row's name —
``scripts/actions/spells/<group folder>/<name>.lua``, ``…/abilities/<name>.lua``,
``…/weaponskills/<name>.lua`` — so a new row needs one. The stub hands every call to
the donor's script at run time (``xi.actions.<kind>.<donor>``): the script is **what
the ability does** (copied from Clone from); the published DAT is only **how it
looks**. A spell stub also lends the new spell id the donor's rows in the spell
helpers' private per-id tables (untested in game; every spell write says so).

Only :func:`write_stub` and :func:`remove_stub` touch files, and only under
``<XI_SERVER_DIR>/scripts/actions``. A file is written in place (the server's file
watcher ignores a rename), never over a file without the marker line, never over a
stub edited by hand, and no folder is ever created.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from xi.server.xi_step import DASH, Step, current_server_dir, now_iso

MARKER = "-- xi: mixer lua stub (xi dats build --lua-stub); delete this line to keep your own edits"
_STUB_RX = re.compile(r"^-- xi-stub: v=(\d+) project=(\S+) action=(\S+) kind=(\S+) donor=(\S+) "
                      r"body=sha256:([0-9a-f]{64})[ \t]*$", re.M)
RULE = "-----------------------------------"

KIND_DIRS = {"spell": "spells", "ja": "abilities", "ws": "weaponskills"}
TITLES = {"spell": "Spell", "ja": "Ability", "ws": "Weapon Skill"}
#: spell_list.group -> folder, for the groups a stub may be written in (spell.cpp).
STUB_GROUP_FOLDERS = {1: "songs", 2: "black", 4: "ninjutsu", 5: "summoning", 6: "white", 7: "geomancy"}
SPELL_FOLDERS = ("songs", "black", "blue", "ninjutsu", "summoning", "white", "geomancy", "trust")
#: A script named like a path segment overwrites that whole table (luautils.cpp:892).
SEGMENT_NAMES = frozenset({"actions", "spells", "abilities", "pets", "weaponskills", "songs", "black",
                           "blue", "ninjutsu", "summoning", "white", "geomancy", "trust"})
NAME_RX = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DONOR_RX = re.compile(r"^[a-z0-9_-]+$")
_HELPER_RX = re.compile(r"^local (pTable|absorbStatData|absorbPointsData|indiData|geoData) =", re.M)

UNTESTED = ("the donor-row borrowing in spell stubs hasn't been tried in game yet — cast {name} once; "
            "\"attempt to index a nil value\" in the map server log means it failed (then clone a "
            "self-contained spell: cure, raise, meteor or a summon)")


# ── the stub text ─────────────────────────────────────────────────────────────

_SPELL_BODY = """---@type TSpell
local spellObject = {{}}

local DONOR_GROUP = '{group}'
local DONOR_NAME  = '{donor}'
local DONOR_ID    = {donor_id}

-- The spell helpers keep per-spell tuning in private tables keyed by spell id
-- (damage_spell.lua pTable, …). Give this spell's id the donor's rows; never overwrite one.
local BORROW = {{ pTable = true, absorbStatData = true, absorbPointsData = true, indiData = true, geoData = true }}

local function borrowDonorRows(spell)
    local newId = spell:getID()
    if newId == DONOR_ID then
        return
    end

    local namespaces =
    {{
        xi.spells and xi.spells.damage,
        xi.spells and xi.spells.enfeebling,
        xi.spells and xi.spells.enhancing,
        xi.spells and xi.spells.absorb,
        xi.job_utils and xi.job_utils.geomancer,
    }}

    for _, ns in pairs(namespaces) do
        if type(ns) == 'table' then
            for _, fn in pairs(ns) do
                if type(fn) == 'function' then
                    local i = 1
                    while true do
                        local upName, upValue = debug.getupvalue(fn, i)
                        if upName == nil then
                            break
                        end

                        if
                            BORROW[upName] and
                            type(upValue) == 'table' and
                            upValue[newId] == nil and
                            upValue[DONOR_ID] ~= nil
                        then
                            upValue[newId] = upValue[DONOR_ID]
                        end

                        i = i + 1
                    end
                end
            end
        end
    end
end

local function donor()
    local spells = xi.actions and xi.actions.spells
    local group  = spells and spells[DONOR_GROUP]
    return group and group[DONOR_NAME]
end

spellObject.onMagicCastingCheck = function(caster, target, spell)
    local d = donor()
    if not d or not d.onMagicCastingCheck then
        return 47 -- what the server returns for a spell with no script (luautils.cpp:4188)
    end

    borrowDonorRows(spell)
    return d.onMagicCastingCheck(caster, target, spell)
end

spellObject.onSpellCast = function(caster, target, spell)
    local d = donor()
    if not d or not d.onSpellCast then
        return 0
    end

    borrowDonorRows(spell)
    return d.onSpellCast(caster, target, spell)
end

return spellObject
"""

_JA_BODY = """---@type TAbility
local abilityObject = {{}}

local DONOR_NAME = '{donor}'

local function donor()
    local abilities = xi.actions and xi.actions.abilities
    return abilities and abilities[DONOR_NAME]
end

abilityObject.onAbilityCheck = function(...)
    local d = donor()
    if d and d.onAbilityCheck then
        return d.onAbilityCheck(...)
    end

    return 0, 0
end

abilityObject.onUseAbility = function(...)
    local d = donor()
    if d and d.onUseAbility then
        return d.onUseAbility(...)
    end
end

return abilityObject
"""

_WS_BODY = """---@type TWeaponSkill
local weaponskillObject = {{}}

local DONOR_NAME = '{donor}'

local function donor()
    local ws = xi.actions and xi.actions.weaponskills
    return ws and ws[DONOR_NAME]
end

weaponskillObject.onUseWeaponSkill = function(...)
    local d = donor()
    if d and d.onUseWeaponSkill then
        return d.onUseWeaponSkill(...)
    end

    return 0, 0, false, 0
end

return weaponskillObject
"""


def _token(s) -> str:
    """A header token (no whitespace)."""
    return re.sub(r"\s+", "_", str(s).strip()) or "-"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def stub_text(kind: str, name: str, *, project: str, action: str, donor: str, donor_id: int,
              group: str | None = None) -> str:
    """The whole stub file (UTF-8, LF, no BOM): the header with its marker and
    ``xi-stub:`` line, then the body the header's hash covers."""
    if kind == "spell":
        body_src = _SPELL_BODY.format(group=group, donor=donor, donor_id=int(donor_id))
        label = f"{group}/{donor}"
    else:
        body_src = (_JA_BODY if kind == "ja" else _WS_BODY).format(donor=donor)
        label = donor
    body = f"\n{RULE}\n{body_src}"
    head = (f"{RULE}\n-- {TITLES[kind]}: {name}\n{MARKER}\n"
            f"-- xi-stub: v=1 project={_token(project)} action={_token(action)} kind={kind} "
            f"donor={label}#{int(donor_id)} body=sha256:{_sha(body.encode('utf-8'))}")
    return head + body


@dataclass
class StubInfo:
    """A stub file as it is on disk."""
    marker: bool
    project: str | None = None
    action: str | None = None
    kind: str | None = None
    donor: str | None = None
    body_ok: bool = False
    data: bytes = b""


def read_stub(path: Path) -> StubInfo | None:
    """``None`` when the file can't be read; else whether it carries the marker, what
    its ``xi-stub:`` line says, and whether its body still hashes to it."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    has_marker = any(line.rstrip() == MARKER for line in text.split("\n"))
    info = StubInfo(has_marker, data=data)
    m = _STUB_RX.search(text)
    if m:
        info.project, info.action, info.kind, info.donor = m.group(2), m.group(3), m.group(4), m.group(5)
        info.body_ok = _sha(text[m.end():].encode("utf-8")) == m.group(6)
    return info


# ── plan / write / remove ─────────────────────────────────────────────────────

@dataclass
class StubCtx:
    """One ability action's Lua Stub inputs. ``db`` is ``result.db`` as it stands after
    this build's database step (``created``, ``like``, ``group``)."""
    kind: str                           # spell | ja | ws
    name: str                           # the recipe name; the script is name.lower()
    db: dict | None
    project: str = ""
    action: str = ""                    # the action id, e.g. ability.love
    prev_lua: dict | None = None        # the recorded result.lua
    server_dir: str | None = None       # default: XI_SERVER_DIR now
    created_now: bool = False           # this build inserted the row


@dataclass
class StubPlan:
    """What the stub step does: ``write``, ``rewrite``, ``unchanged``, ``kept``,
    ``skip``, ``refused`` or ``error``. ``path`` is relative to the server folder,
    always with ``/``."""
    op: str
    kind: str
    path: str | None = None
    reason: str | None = None
    donor: str | None = None            # "black/fire#144"
    donor_call: str | None = None       # "black/fire #144" (the write line)
    content: bytes | None = None
    remove_old: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    server_dir: str | None = None
    prev_lua: dict | None = None
    executed: bool = False

    def step(self, would: bool = False) -> Step:
        """The ``lua:`` line (design2 §1.2.2)."""
        if self.op == "write":
            text = f"{self.path} (calls {self.donor_call} at run time)"
        elif self.op == "rewrite":
            text = f"{self.path} (donor or template changed)"
        elif self.op == "unchanged":
            text = f"{self.path}"
        elif self.op == "kept":
            text = (f"{self.path} {DASH} edited by hand; delete its 'xi:' line to own it, "
                    "or delete the file to regenerate")
        else:
            text = f"{DASH} {self.reason or ''}".rstrip()
        return Step(self.op, text, would, list(self.warnings))

    def result_lua(self, at: str | None = None) -> dict | None:
        """The ``result.lua`` to record (JSON only): the stub after a write that ran or
        an unchanged one; the previous record for everything else."""
        if (self.op in ("write", "rewrite") and self.executed) or self.op == "unchanged":
            return {"path": self.path, "sha256": _sha(self.content), "donor": self.donor,
                    "kind": self.kind, "at": at or now_iso()}
        return self.prev_lua


def _skip(p: StubPlan, why: str) -> StubPlan:
    p.op, p.reason = "skip", why
    return p


def _refuse(p: StubPlan, why: str) -> StubPlan:
    p.op, p.reason = "refused", why
    return p


def _safe_rel(rel) -> str | None:
    """``rel`` when it is a plain ``scripts/actions/…/<x>.lua`` path, else ``None``."""
    if not rel or not isinstance(rel, str):
        return None
    pp = PurePosixPath(rel.replace("\\", "/"))
    if pp.is_absolute() or ".." in pp.parts or pp.parts[:2] != ("scripts", "actions") or pp.suffix != ".lua":
        return None
    return pp.as_posix()


def _same_file(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _module_ref(root: Path, kind: str, name: str) -> str | None:
    """The first ``modules/**/*.lua`` that refers to ``name`` as an action of ``kind``
    (heuristic: the dotted path, or the quoted name in a file that names the kind's
    ``xi.actions`` table). Relative, with ``/``."""
    mods = root / "modules"
    if not mods.is_dir():
        return None
    n = re.escape(name)
    end = r"(?![\w-])"
    dotted = {"spell": rf"xi\.actions\.spells\.\w+\.{n}{end}",
              "ja": rf"xi\.actions\.abilities\.(?:pets\.)?{n}{end}",
              "ws": rf"xi\.actions\.weaponskills\.{n}{end}"}[kind]
    table = f"xi.actions.{KIND_DIRS[kind]}"
    quoted = re.compile(rf"""(['"]){n}\1""")
    rx = re.compile(dotted)
    for f in sorted(mods.rglob("*.lua")):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name not in txt:
            continue
        if rx.search(txt) or (table in txt and quoted.search(txt)):
            return f.relative_to(root).as_posix()
    return None


def helper_tables_present(server_dir) -> bool:
    """Whether the spell helpers still keep the per-id tables the spell stub borrows
    from (``local pTable =`` …) under ``scripts/globals``."""
    root = Path(server_dir)
    files = list((root / "scripts" / "globals" / "spells").rglob("*.lua"))
    files.append(root / "scripts" / "globals" / "job_utils" / "geomancer.lua")
    for f in files:
        try:
            if _HELPER_RX.search(f.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def _own(info: StubInfo | None, project: str, action: str) -> bool:
    return bool(info and info.marker and info.project == _token(project) and info.action == _token(action))


def plan_stub(ctx: StubCtx) -> StubPlan:
    """Decide the stub step (design2 §2.5.1-§2.5.4). Reads files only."""
    kind = ctx.kind
    p = StubPlan("skip", kind, prev_lua=ctx.prev_lua)
    db = ctx.db or {}
    if not db.get("created"):
        return _skip(p, "Lua Stub writes a script only for a row this mix created "
                        "(turn on Database Update)")
    server_dir = ctx.server_dir if ctx.server_dir is not None else current_server_dir()
    if not server_dir:
        return _skip(p, "no server folder (Settings › Local Server)")
    root = Path(server_dir)
    p.server_dir = str(root)
    kd = KIND_DIRS[kind]
    kind_dir = root / "scripts" / "actions" / kd
    if not kind_dir.is_dir():
        return _refuse(p, f"scripts/actions/{kd} not found in the server folder; "
                          "is XI_SERVER_DIR a LandSandBoat checkout?")
    name = str(ctx.name).lower()
    like = db.get("like") or {}
    if like.get("id") is None or not like.get("name"):
        return _skip(p, "the row's donor isn't recorded (result.db.like)")
    donor = str(like["name"]).lower()
    donor_id = int(like["id"])
    if not NAME_RX.match(name):
        return _refuse(p, f"'{name}' can't be a script name (a-z, 0-9, _ and -, starting with a letter or digit)")
    if name in SEGMENT_NAMES:
        return _refuse(p, f"'{name}' is a folder name of the server's script loader; a script can't take it")
    if not _DONOR_RX.match(donor):
        return _refuse(p, f"the donor's name '{donor}' can't be used in a script")

    group = None
    if kind == "spell":
        g = db.get("group")
        group = STUB_GROUP_FOLDERS.get(int(g)) if g is not None else None
        if not group:
            return _refuse(p, f"spell group {g} has no script folder")
        if not (kind_dir / group).is_dir():
            return _refuse(p, f"scripts/actions/spells/{group} not found in the server folder; "
                              "is XI_SERVER_DIR a LandSandBoat checkout?")
        rel_dir = f"scripts/actions/spells/{group}"
        p.donor = f"{group}/{donor}#{donor_id}"
        p.donor_call = f"{group}/{donor} #{donor_id}"
    else:
        rel_dir = f"scripts/actions/{kd}"
        p.donor = f"{donor}#{donor_id}"
        p.donor_call = f"{donor} #{donor_id}"
    donor_file = root / PurePosixPath(rel_dir) / f"{donor}.lua"
    if not donor_file.is_file():
        return _refuse(p, f"{donor} has no script in scripts/actions (a module-defined donor isn't supported)")
    if kind == "ja":
        dtxt = donor_file.read_text(encoding="utf-8", errors="replace")
        if donor.endswith("_roll") or "job_utils.corsair" in dtxt or "job_utils.dancer" in dtxt:
            return _refuse(p, f"{donor} looks its tuning up by ability id and sets its own animation "
                              "(corsair rolls, dancer waltzes/steps/flourishes)")

    rel = (PurePosixPath(rel_dir) / f"{name}.lua").as_posix()
    target = root / PurePosixPath(rel)
    p.path = rel
    text = stub_text(kind, name, project=ctx.project, action=ctx.action, donor=donor,
                     donor_id=donor_id, group=group)
    p.content = text.encode("utf-8")

    if target.exists():                               # case-insensitive on Windows
        info = read_stub(target)
        if info is None or not info.marker:
            return _skip(p, f"{rel} exists (not written by xi)")
        if info.project is not None and not _own(info, ctx.project, ctx.action):
            return _refuse(p, f"{rel} is {info.project}/{info.action}'s stub")
        if info.project is None or not info.body_ok:
            p.op = "kept"
            return p
        p.op = "unchanged" if info.data.replace(b"\r\n", b"\n") == p.content else "rewrite"
    else:
        p.op = "write"
    if p.op == "unchanged":
        return p

    # A script of that name in any other folder of the kind (this mix's own stub excepted).
    others = []
    if kind == "spell":
        others = [kind_dir / f / f"{name}.lua" for f in SPELL_FOLDERS]
    elif kind == "ja":
        others = [kind_dir / f"{name}.lua", kind_dir / "pets" / f"{name}.lua"]
    for other in others:
        if _same_file(other, target) or not other.is_file():
            continue
        orel = other.relative_to(root).as_posix()
        info = read_stub(other)
        if _own(info, ctx.project, ctx.action):
            _old_stub(p, orel, info)
        else:
            return _refuse(p, f"{name} already has a script: {orel}")
    prev_rel = _safe_rel((ctx.prev_lua or {}).get("path"))
    if prev_rel and prev_rel != rel and prev_rel not in p.remove_old:
        old = root / PurePosixPath(prev_rel)
        if old.is_file() and not _same_file(old, target):
            info = read_stub(old)
            if _own(info, ctx.project, ctx.action) or (info and not info.marker):
                _old_stub(p, prev_rel, info)

    ref = _module_ref(root, kind, name)
    if ref:
        return _refuse(p, f"a server module uses the name '{name}' ({ref}); a script here would clash with it")

    if kind == "spell":
        p.warnings.append(UNTESTED.format(name=name))
        if not helper_tables_present(root):
            p.warnings.append("the spell helpers no longer keep the per-spell tables this stub borrows from; "
                              "a clone of an id-keyed donor may error when cast")
    p.warnings.append("restart the map server (xi_map) to load it with the new row" if ctx.created_now
                      else "the server reloads changed scripts on its own (restart it if the change doesn't show)")
    return p


def _will_remove(rel: str) -> str:
    return f"the old stub {rel} is removed when this one is written"


def _old_stub(p: StubPlan, rel: str, info: StubInfo | None) -> None:
    """This mix's stub at a path it no longer uses: removed when written if unedited."""
    if info and info.marker and info.body_ok:
        p.remove_old.append(rel)
        p.warnings.append(_will_remove(rel))
    else:
        p.warnings.append(f"the old stub {rel} was edited by hand (or lost its 'xi:' line); "
                          "left it — delete it yourself if it's no longer wanted")


def write_stub(plan: StubPlan) -> StubPlan:
    """Write a ``write``/``rewrite`` plan's file in place (``Path.write_bytes``), then
    remove this mix's old unedited stubs it replaced. Other ops are returned as they are."""
    if plan.op not in ("write", "rewrite") or plan.content is None or not plan.server_dir:
        return plan
    root = Path(plan.server_dir)
    target = root / PurePosixPath(plan.path)
    try:
        target.write_bytes(plan.content)
    except OSError as e:
        plan.op, plan.reason = "error", f"cannot write {plan.path} ({e.strerror or e})"
        return plan
    plan.executed = True
    done = {}
    for rel in plan.remove_old:
        try:
            (root / PurePosixPath(rel)).unlink()
            done[_will_remove(rel)] = f"removed the old stub {rel}; it stays loaded until the map server restarts"
        except OSError as e:
            done[_will_remove(rel)] = f"couldn't remove the old stub {rel} ({e.strerror or e})"
    plan.warnings = [done.get(w, w) for w in plan.warnings]
    return plan


def remove_stub(server_dir, lua: dict | None, *, project: str | None = None, action: str | None = None,
                dry_run: bool = False) -> tuple:
    """Undo a recorded ``result.lua`` (``dats undo --apply-db``): ``("removed", None)``
    when the file still has the marker and its body hash matches (and, when given, names
    this project and action); ``("missing", None)`` when it is gone; else
    ``("left", why)``."""
    if not server_dir:
        return ("left", "no server folder")
    rel = _safe_rel((lua or {}).get("path"))
    if not rel:
        return ("left", "not a scripts/actions path")
    path = Path(server_dir) / PurePosixPath(rel)
    if not path.is_file():
        return ("missing", None)
    info = read_stub(path)
    if info is None:
        return ("left", "can't read it")
    if not info.marker:
        return ("left", "it lost its 'xi:' line (yours now)")
    if info.project is None or not info.body_ok:
        return ("left", "edited by hand")
    if project is not None and action is not None and not _own(info, project, action):
        return ("left", f"it is {info.project}/{info.action}'s stub")
    if not dry_run:
        try:
            path.unlink()
        except OSError as e:
            return ("left", f"couldn't delete it ({e.strerror or e})")
    return ("removed", None)
