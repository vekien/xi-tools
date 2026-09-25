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
    _refresh_db_globals()


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

# ── Custom animation bands ──────────────────────────────────────────────────
# The client turns an animation number into a DAT file id with fixed arithmetic
# per kind, and the file ids that arithmetic reaches run out long before the
# 12-bit number space does: 92 spell animations are free, 161 job ability, and 8
# weapon skill (`xi ability publish` reports which). A client-side plugin can
# patch that arithmetic so numbers at or above a threshold resolve into a
# reserved region instead, and these say where that region is so the publisher
# can allocate into it.
#
#   file_id = BASE + animation                           spells, job abilities
#   file_id = WS_BASE + (animation - WS_FIRST) * 24      weapon skills, then
#             + block * 8 + race                         body / waist A / waist B
#
# For spells and job abilities the number is added whole: FIRST decides only
# whether the custom band applies, and is never subtracted. Each base therefore
# reserves 4096 file ids, one for every value the 12-bit animation field can
# hold, and the numbers below FIRST simply address the unused front of the block.
# Offsetting by FIRST instead allocates below where the client will look.
#
# Weapon-skill numbers cost 24 file ids each because the client resolves them
# through per-race motion banks, and there the threshold *is* subtracted: the
# reserved count is what bounds that band, not the number space. Grouping those
# ids by number rather than by race keeps WS_SLOTS out of the arithmetic, so
# raising it later moves nothing already published.
#
# A threshold has to clear every number the client is *sent* in that category,
# not just the table's last entry. Job abilities are the trap: the abilities
# table ends at 338, but category 6 is also how a server reaches the warp and
# teleport effects, at 596..656 (`injectActionPacket(id, 6, 600, ...)`), and
# those must keep the retail arithmetic. A band at 500 broke every scripted
# teleport in the field; 1024 clears them and leaves 3,072 custom numbers.
# Spells are fine at 1612: the core's own spell-category numbers stop at 847.
#
# Unset, each takes cexislots' value (cexidats src/cexislots/sites.h), so the
# publisher assumes a client running that plugin. The numbers a stock client can
# load are still handed out first; the band is only reached once they are used
# up, and the build says when a number needs the plugin. Set a band's FIRST to 0
# to switch it off for a stock client (the model viewer sends 0 when Settings ›
# XI Tools › Custom animation bands is off); a different plugin sets its own.
def _band(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default

FX_SPELL_BAND_FIRST = _band('FX_SPELL_BAND_FIRST', 1612)
FX_SPELL_BAND_BASE  = _band('FX_SPELL_BAND_BASE', 423152)
FX_JA_BAND_FIRST    = _band('FX_JA_BAND_FIRST', 1024)
FX_JA_BAND_BASE     = _band('FX_JA_BAND_BASE', 427248)
FX_WS_BAND_FIRST    = _band('FX_WS_BAND_FIRST', 272)
FX_WS_BAND_BASE     = _band('FX_WS_BAND_BASE', 431344)
FX_WS_BAND_SLOTS    = _band('FX_WS_BAND_SLOTS', 256)


def fx_band_ends() -> list:
    """``(first file id, one past the last)`` of every custom band that is on."""
    ends = []
    for first, base in ((FX_SPELL_BAND_FIRST, FX_SPELL_BAND_BASE), (FX_JA_BAND_FIRST, FX_JA_BAND_BASE)):
        if first and base:
            ends.append((base, base + 4096))
    if FX_WS_BAND_FIRST and FX_WS_BAND_BASE and FX_WS_BAND_SLOTS:
        ends.append((FX_WS_BAND_BASE, FX_WS_BAND_BASE + 24 * FX_WS_BAND_SLOTS))
    return ends


def fx_band_floor() -> int:
    """The first file id the custom bands reserve (0 when every band is off). They sit
    past the end of the expanded tables, so a table longer than this is a DAT overlay's
    ROM pair carrying band registrations, not a table out of step with its peers."""
    return min((lo for lo, _hi in fx_band_ends()), default=0)


def fx_band_ceiling() -> int:
    """Table entries a DAT overlay's ROM pair needs to register any band file id —
    cexislots' FX_CEILING (437,488) with its values. The plugin grows the client's one
    table to this in memory and merges the overlay's pair into it, so only the overlay
    grows on disk; the install's tables stay as they are."""
    return max((hi for _lo, hi in fx_band_ends()), default=0)

# ── Local dev server DB (LandSandBoat — xidb) ───────────────────────────────
# Used by `xi zone new` to auto-apply the generated zone-migration.sql to the
# running dev server's database. Defaults match a stock local LSB setup (MariaDB on
# localhost, db=xidb per the Quick Start Guide). Set XI_DB_* in .env (Settings ›
# Local Server in the model viewer, or the zone editor's setup) — the one place
# credentials live; the server's network.lua is never read. These are the same keys,
# blank rule and defaults (an empty password) as xi.server.xi_commands._resolve, so
# `zone new` and `dats build --apply-db` log in the same way.
def _db_env(key: str, default: str) -> str:
    """A non-blank ``XI_DB_*`` value (trimmed), else ``default``."""
    return (os.environ.get(key) or '').strip() or default


def _refresh_db_globals() -> None:
    global DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
    DB_HOST = _db_env('XI_DB_HOST', '127.0.0.1')
    try:
        DB_PORT = int(_db_env('XI_DB_PORT', '3306'))
    except ValueError:
        DB_PORT = 3306
    DB_USER = _db_env('XI_DB_USER', 'root')
    DB_PASSWORD = _db_env('XI_DB_PASSWORD', '')
    DB_NAME = _db_env('XI_DB_NAME', 'xidb')


_refresh_db_globals()
# Auto-apply migrations on `zone new`. Default (unset/empty) attempts to apply when
# the DB is reachable and skips gracefully otherwise; '0'/'false'/'no'/'off' disables.
DB_AUTOAPPLY = os.environ.get('XI_DB_AUTOAPPLY', '').strip().lower() not in ('0', 'false', 'no', 'off')


def db_creds() -> dict:
    """Keyword args for ``pymysql.connect(**db_creds())`` against the dev server DB.

    Resolved at call time by :func:`xi.server.xi_commands._resolve`, so this and every
    other database user (``xi server db``/``check``, ``dats build --apply-db``) get
    identical credentials: non-blank ``XI_DB_*``, else the defaults."""
    from xi.server.xi_commands import _resolve
    host, port, user, password, database = _resolve(None, None, None, None, None)
    return dict(host=host, port=int(port), user=user, password=password, database=database)

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
