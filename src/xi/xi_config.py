import os
import platform
import shutil
import sys
from pathlib import Path

_IS_LINUX = platform.system() == 'Linux'


def _candidate_env_files():
    """Yield ``.env`` locations to try, in priority order (first found wins)."""
    explicit = os.environ.get('XI_ENV_FILE')
    if explicit:
        yield Path(explicit)
    # Next to the executable when run as a frozen/bundled app.
    if getattr(sys, 'frozen', False):
        yield Path(sys.executable).resolve().parent / '.env'
    # Repo root when running from source (src/xi/xi_config.py -> two parents up).
    yield Path(__file__).resolve().parents[2] / '.env'
    # Wherever the command was run from.
    yield Path.cwd() / '.env'


def _load_dotenv() -> None:
    """Load ``KEY=value`` pairs from the first ``.env`` found into ``os.environ``.

    A plain ``KEY=value`` per line; ``#`` starts a comment; surrounding quotes and a
    leading ``export`` are stripped. A blank value (``KEY=``) is ignored so the
    built-in default still applies — matching the ``os.environ.get(key, default)``
    pattern used throughout this module. Real environment variables already set
    ALWAYS win — we never clobber them — so an exported var (or one the launcher set
    before importing xi) takes precedence over the file. The first existing file wins.
    """
    for path in _candidate_env_files():
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[len('export '):].lstrip()
            key, sep, value = line.partition('=')
            if not sep:
                continue
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and value:
                os.environ.setdefault(key, value)
        return  # first file wins


_load_dotenv()

# All paths come from the environment (or .env) — there are no built-in
# defaults. On Linux the FFXI client typically lives under a Wine/Proton
# prefix; point FFXI_DIR at your actual "FINAL FANTASY XI" directory.

# The FFXI game install. Tools READ from here and WRITE edits back in place —
# every edited DAT keeps a pristine `<dat>.base` backup alongside it, which the
# reset commands restore from. REQUIRED — see require_ffxi_dir().
FFXI_DIR = os.environ.get('FFXI_DIR', '')

# Internal write-redirect root — NOT user configuration (no env var). Normally
# None: edits land in place under FFXI_DIR. `xi dats build` temporarily points
# this at a package/target root so build actions write there instead of the
# live install, and always restores it afterwards.
_REDIRECT_DIR: str | None = None

# OPTIONAL DAT staging directory for `xi pivot`. DATs copied here preserve their
# ROM-relative path from FFXI_DIR, e.g. ROM/1/41.DAT. Empty when unset.
FFXI_PIVOT_DIR = os.environ.get('FFXI_PIVOT_DIR', '')

# OPTIONAL HD asset-pack DAT root — mirrors the ROM tree from FFXI_DIR but with
# high-res textures. When set, the zone editor's "Load HD Zone" button serves
# DATs from here and Publish writes back here instead of the standard DAT under
# FFXI_DIR. Empty when unset; hd_path_for() raises if it is needed but unset.
FFXI_HD_DIR = os.environ.get('FFXI_HD_DIR', '')


def apply_env_overrides(values: dict[str, str]) -> None:
    """Push path/config keys into ``os.environ`` and refresh this module's globals.

    Used by the zone-editor bridge after writing ``.env`` so the running process
    picks up FFXI_DIR etc. without a restart. Blank values clear the env var.
    """
    global FFXI_DIR, FFXI_PIVOT_DIR, FFXI_HD_DIR, BLENDER_PATH
    global XI_SERVER_DIR, XI_NAVMESH_DIR
    global DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
    for key, raw in (values or {}).items():
        key = str(key).strip()
        if not key:
            continue
        val = "" if raw is None else str(raw).strip()
        if val:
            os.environ[key] = val
        else:
            os.environ.pop(key, None)
    FFXI_DIR = os.environ.get('FFXI_DIR', '')
    FFXI_PIVOT_DIR = os.environ.get('FFXI_PIVOT_DIR', '')
    FFXI_HD_DIR = os.environ.get('FFXI_HD_DIR', '')
    BLENDER_PATH = os.environ.get('BLENDER_PATH', BLENDER_PATH)
    XI_SERVER_DIR = os.environ.get('XI_SERVER_DIR') or None
    XI_NAVMESH_DIR = os.environ.get('XI_NAVMESH_DIR') or None
    DB_HOST = os.environ.get('XI_DB_HOST', DB_HOST)
    try:
        DB_PORT = int(os.environ.get('XI_DB_PORT', str(DB_PORT)))
    except ValueError:
        pass
    DB_USER = os.environ.get('XI_DB_USER', DB_USER)
    DB_PASSWORD = os.environ.get('XI_DB_PASSWORD', DB_PASSWORD)
    DB_NAME = os.environ.get('XI_DB_NAME', DB_NAME)


def require_ffxi_dir() -> Path:
    """Return FFXI_DIR as a Path, or raise telling the user to set it.

    There is no built-in default: FFXI_DIR must come from the environment or a
    .env file. Called by the DAT read/write helpers below so any command that
    actually touches the install fails with a clear message rather than
    resolving paths against the current working directory.
    """
    if not FFXI_DIR:
        raise RuntimeError(
            "FFXI_DIR is not set. Add it to your .env (or the environment), e.g.\n"
            r'    FFXI_DIR=C:\Program Files (x86)\PlayOnline\SquareEnix\FINAL FANTASY XI')
    path = Path(FFXI_DIR)
    if not path.is_dir():
        raise RuntimeError(f"FFXI_DIR does not point at a directory: {FFXI_DIR}")
    return path


def _in_place() -> bool:
    """True unless a dats-build redirect is active — i.e. the normal case:
    edits are written in place under FFXI_DIR."""
    return (_REDIRECT_DIR is None
            or Path(_REDIRECT_DIR).resolve() == Path(FFXI_DIR).resolve())


def ensure_base(src) -> bool:
    """Snapshot the pristine DAT to <dat>.base if not already done. Returns True
    if a new backup was created. No-op while a dats-build redirect is active
    (the package copy is not the pristine source)."""
    require_ffxi_dir()
    if not _in_place():
        return False
    src = Path(src)
    base = src.with_name(src.name + ".base")
    if base.exists():
        return False
    shutil.copy2(src, base)
    return True


def output_path_for(src) -> Path:
    """Where edits to ``src`` are written: normally ``src`` itself (edits go in
    place under FFXI_DIR). While a dats-build redirect is active, paths under
    FFXI_DIR map into the build's package root instead."""
    base = require_ffxi_dir().resolve()
    src = Path(src).resolve()
    if _in_place():
        return src
    try:
        rel = src.relative_to(base)
    except ValueError:
        return src
    return (Path(_REDIRECT_DIR).resolve() / rel)


def read_path_for(src) -> Path:
    """Preferred location to READ a DAT from: normally ``src`` itself (edits
    live in place). While a dats-build redirect is active, the package copy is
    preferred when it exists so cumulative build edits are visible."""
    out = output_path_for(src)
    return out if out.exists() else Path(src)


def editable_dat(src, *, fresh: bool = True) -> Path:
    """Return the path to READ from and WRITE edits to for a given source DAT.

    Normal (in-place) operation: edits the DAT under FFXI_DIR directly, keeping
    a pristine ``<dat>.base`` backup. ``fresh=True`` (default) restores from
    .base first so the edit starts clean; ``fresh=False`` keeps the current
    bytes so edits layer (e.g. anim import after a mesh import, or a second
    `fx set`).

    While a dats-build redirect is active: mirrors the DAT into the build root
    instead (``fresh`` re-seeds the mirror from the FFXI_DIR copy).
    """
    require_ffxi_dir()
    src = Path(src)
    if _in_place():
        base = src.with_name(src.name + ".base")
        if not base.exists():
            shutil.copy2(src, base)
        elif fresh:
            shutil.copy2(base, src)
        return src
    out = output_path_for(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    if fresh or not out.exists():
        shutil.copy2(src, out)
    return out

def hd_path_for(src) -> Path:
    """Map a DAT path under FFXI_DIR to its location under FFXI_HD_DIR."""
    if not FFXI_HD_DIR:
        raise ValueError("FFXI_HD_DIR is not configured")
    src = Path(src).resolve()
    base = Path(FFXI_DIR).resolve()
    try:
        rel = src.relative_to(base)
    except ValueError:
        rel = Path(src.name)
    return Path(FFXI_HD_DIR).resolve() / rel


def hd_editable_dat(src, *, fresh: bool = False) -> Path:
    """Return the editable HD copy of a DAT.

    The HD DAT is its OWN pristine source — it carries the HD asset pack's
    high-res textures (and any manual edits), which the vanilla FFXI_DIR copy
    does NOT have. So we never overwrite an existing HD DAT with the vanilla
    one; instead we keep a ``<dat>.base`` backup of the HD-pristine bytes (taken
    on first edit) and reset from THAT, exactly like in-place mode.

    fresh=True: restore the HD DAT from its ``.base`` backup (e.g. on Reset /
        Publish-with-reset), so the change-set layers on HD-pristine.
    fresh=False (default): keep the current HD bytes (cumulative edits).

    The vanilla FFXI_DIR copy is only used to SEED a brand-new HD DAT that does
    not exist yet (no HD asset for this zone) — never to overwrite one.
    """
    out = hd_path_for(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        # No HD DAT yet for this zone — seed from the pristine game copy.
        shutil.copy2(src, out)
    base = out.with_name(out.name + ".base")
    if not base.exists():
        # First edit of this HD DAT — snapshot the HD-pristine bytes.
        shutil.copy2(out, base)
    elif fresh:
        # Reset: restore the HD-pristine bytes from the backup.
        shutil.copy2(base, out)
    return out


XI_TOOLS_DIR = os.environ.get('XI_TOOLS_DIR',
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _IS_LINUX else r'D:\\xi-tools')

# `xi dats build` writes DAT output flat under XI_BUILDS_DIR (ROM-relative).
XI_BUILDS_DIR = os.environ.get('XI_BUILDS_DIR', os.path.join(XI_TOOLS_DIR, 'dats', 'builds'))

# Directory containing pre-baked server navmesh files (<ZoneName>.nav).
# Defaults to a sibling xi-server/xiNavmeshes directory if it exists.
XI_NAVMESH_DIR: str | None = os.environ.get(
    'XI_NAVMESH_DIR',
    None if _IS_LINUX else (
        os.path.join(os.path.dirname(XI_TOOLS_DIR), 'xi-server', 'xiNavmeshes')
        if os.path.isdir(os.path.join(os.path.dirname(XI_TOOLS_DIR), 'xi-server', 'xiNavmeshes'))
        else None
    ),
)


# Root of the local dev server checkout (LandSandBoat / fork). Used by
# `xi zone new` to scaffold the per-zone server Lua (scripts/zones/<name>/IDs.lua
# + Zone.lua) the map server requires at startup — without IDs.lua the map logs
# `cannot open scripts/zones/<name>/IDs.lua` for every custom zone.
# Set via XI_SERVER_DIR (env / .env); no auto-detect.
XI_SERVER_DIR: str | None = os.environ.get('XI_SERVER_DIR') or None


def server_zone_scripts_dir() -> Path | None:
    """`scripts/zones` under the dev server checkout, or None if not configured/found."""
    if not XI_SERVER_DIR:
        return None
    p = Path(XI_SERVER_DIR) / 'scripts' / 'zones'
    return p if p.is_dir() else None


def server_zone_command_lua() -> Path | None:
    """`scripts/commands/zone.lua` under the dev server checkout — the `!zone`
    GM command, whose `zoneList` holds each zone's spawn point. None if
    XI_SERVER_DIR is unset or the file isn't there."""
    if not XI_SERVER_DIR:
        return None
    p = Path(XI_SERVER_DIR) / 'scripts' / 'commands' / 'zone.lua'
    return p if p.is_file() else None

TEXCONV_PATH = os.environ.get('TEXCONV_PATH',
    'texconv' if _IS_LINUX else os.path.join(XI_TOOLS_DIR, 'misc', 'texconv.exe'))

BLENDER_PATH = os.environ.get('BLENDER_PATH',
    os.path.join(XI_TOOLS_DIR, 'bin', 'blender-flatpak')
    if _IS_LINUX else r'C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe')

# Hard ceiling for injected texture dimensions (power-of-two). Applies to both
# entity meshes and zone meshes — custom textures are encoded at their SOURCE
# resolution clamped to this. Retail zone textures top out at 512; 2048 gives HD
# asset packs full headroom but is above anything retail ships, so it MAY hit an
# engine texture-buffer limit — lower this if zones crash.
TEXTURE_CLAMP = int(os.environ.get('XI_TEXTURE_CLAMP', '2048'))

# Custom ROM namespace — the ROM directory used for all custom content.
# Override with e.g. CUSTOM_FTABLE=ROM5 if your server uses a different slot.
CUSTOM_ROM     = os.environ.get('CUSTOM_FTABLE', 'ROM10')
CUSTOM_ROM_IDX = int(CUSTOM_ROM[3:])   # 'ROM10' -> 10

# ── Local dev server DB (LandSandBoat — xidb) ───────────────────────────────
# Used by `xi zone new` to auto-apply the generated zone-migration.sql to the
# running dev server's database. Defaults match a stock local LSB setup (MariaDB on
# localhost, db=xidb per the Quick Start Guide). Override per-machine via env, or
# better, point XI_SERVER_DIR at your checkout and let network.lua decide.
DB_HOST     = os.environ.get('XI_DB_HOST', '127.0.0.1')
DB_PORT     = int(os.environ.get('XI_DB_PORT', '3306'))
DB_USER     = os.environ.get('XI_DB_USER', 'root')
DB_PASSWORD = os.environ.get('XI_DB_PASSWORD', 'xi')
DB_NAME     = os.environ.get('XI_DB_NAME', 'xidb')
# Auto-apply migrations on `zone new`. Default (unset/empty) attempts to apply when
# the DB is reachable and skips gracefully otherwise; '0'/'false'/'no'/'off' disables.
DB_AUTOAPPLY = os.environ.get('XI_DB_AUTOAPPLY', '').strip().lower() not in ('0', 'false', 'no', 'off')


def db_creds() -> dict:
    """Keyword args for ``pymysql.connect(**db_creds())`` against the dev server DB."""
    return dict(host=DB_HOST, port=DB_PORT, user=DB_USER,
                password=DB_PASSWORD, database=DB_NAME)

# ── Custom model_id ceilings (the "buffers") ───────────────────────────────
# These are the high-water marks the expand tools provision empty FTABLE/VTABLE
# slots up to. You inject WELL below them — the buffer just reserves high ground
# so the original game's organic content growth never reaches your custom ids.
#
# MAX_ENTITY_MODELID is the single source of truth for the entity↔gear boundary:
# the gear file_id region (CUSTOM_GEAR_BASE in xi.gear.xi_inject) is DERIVED to
# start one slot above the entity ceiling, so the two custom ranges can never
# overlap no matter how you tune this. Bump it and the gear floor slides up with
# it automatically. (Historically these were two hand-maintained constants that
# drifted apart — see the "ceiling mismatch" fix in git history.)
MAX_ENTITY_MODELID = int(os.environ.get('XI_MAX_ENTITY_MODELID', '30000'))

# Highest gear model_id provisioned PER (race, slot) window. The game's gear MId
# is a 12-bit field, so this can't exceed 4095.
MAX_GEAR_MODELID   = int(os.environ.get('XI_MAX_GEAR_MODELID', '4095'))

# Recommended (not enforced) starting model_id for NEW custom gear, well clear of
# the retail per-slot ceilings — leaves a comfortable buffer like the entity
# MODEL_SAFE_START does. `xi ftable info` surfaces this as guidance.
GEAR_RECOMMENDED_START = int(os.environ.get('XI_GEAR_RECOMMENDED_START', '3000'))

# Whether `xi mesh export` writes the <stem>_schema.json dats descriptor by
# default. On unless explicitly disabled; the export's --schema/--no-schema flag
# still overrides per-run.
SCHEMA_GENERATION = os.environ.get('SCHEMA_GENERATION', '').strip().lower() not in ('0', 'false', 'no', 'off')
