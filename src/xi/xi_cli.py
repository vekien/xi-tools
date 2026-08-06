import sys

import click

# Force UTF-8 stdout/stderr so help text and echoes containing non-ASCII
# (—, →, etc.) never raise UnicodeEncodeError on a legacy Windows cp1252 console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from xi.audio    import xi_music          as audio_music
from xi.audio    import xi_sfx            as audio_sfx
from xi.audio    import xi_decode         as audio_decode
from xi.audio    import xi_refs           as audio_refs
from xi.audio    import xi_encode         as audio_encode
from xi.batch    import xi_batch         as batch_cmds
from xi.ftable   import xi_expand as expand, xi_lookup as lookup, xi_range_scan as range_scan
from xi.ftable   import xi_delete as ftable_delete
from xi.ftable   import xi_set    as ftable_set
from xi.ftable   import xi_list   as ftable_list
from xi.ftable   import xi_tables as ftable_tables
from xi.ftable   import xi_compare as ftable_compare
from xi.gear     import xi_inject as gear_inject   # table ops now live under `ftable`
from xi.entity   import xi_inject as inject, xi_list as list_, xi_recommend as recommend
from xi.entity.anim    import xi_export as anim_export
from xi.entity.anim    import xi_import as anim_import
from xi.entity.anim    import xi_schedule as anim_schedule
from xi.entity.mesh    import xi_export as mesh_export
from xi.entity.mesh    import xi_import as mesh_import
from xi.ffximain import xi_geargroups as ffximain_geargroups
from xi.ffximain import xi_gear_patch as ffximain_gear_patch
from xi.ffximain import xi_text_dump as ffximain_text_dump
from xi.ffximain import xi_unpack   as ffximain_unpack
from xi.gear     import xi_list    as gear_list
from xi.gear     import xi_export  as gear_export
from xi.gear     import xi_import  as gear_import
from xi.gear     import xi_character as gear_character
from xi.launcher import xi_ui_themes as launcher_ui_themes
from xi.mount    import xi_list   as mount_list
from xi.mount    import xi_export as mount_export
from xi.mount    import xi_inject as mount_inject
from xi.mount    import xi_import as mount_import
from xi.mount    import xi_delete as mount_delete
from xi.zone     import xi_export         as zone_export
from xi.zone     import xi_navmesh        as zone_navmesh
from xi.zone     import xi_import         as zone_import
from xi.zone     import xi_add_object     as zone_add_object
from xi.zone     import xi_list           as zone_list
from xi.zone     import xi_apply_changes  as zone_apply_changes
from xi.zone     import xi_delete         as zone_delete
from xi.zone     import xi_object         as zone_object
from xi.zone     import xi_objects        as zone_objects
from xi.zone     import xi_reset          as zone_reset
from xi.zone     import xi_inject        as zone_inject
from xi.zone     import xi_assemble      as zone_assemble
from xi.zone     import xi_new           as zone_new
from xi.zone     import xi_make_template as zone_make_template
from xi.zone     import xi_zone_delete   as zone_drop
from xi.zone     import xi_footsteps     as zone_footsteps
from xi.fx       import xi_list       as fx_list
from xi.fx       import xi_dump         as fx_dump
from xi.fx       import xi_delete       as fx_delete
from xi.fx       import xi_set         as fx_set
from xi.fx       import xi_copy         as fx_copy
from xi.fx       import xi_export       as fx_export
from xi.tex      import xi_list       as tex_list
from xi.tex      import xi_export      as tex_export
from xi.tex      import xi_import     as tex_import
from xi.ui       import xi_extract   as ui_export
from xi.ui       import xi_list      as ui_list
from xi.ui       import xi_damv_pos  as ui_damv_pos
from xi.ui       import xi_import    as ui_import
from xi.ui       import xi_menu_pos  as ui_menu_pos
from xi.ui       import xi_mnc2_pos  as ui_mnc2_pos
from xi.ui       import xi_sel_pos   as ui_sel_pos
from xi.ui       import xi_simple    as ui_simple
from xi.ui.strings import xi_strings as ui_strings
from xi.ui.items   import xi_items   as ui_items
from xi.ui.spells  import xi_spells  as ui_spells
from xi.utils    import xi_dds2png as dds2png, xi_png2dds as png2dds
from xi.dialog   import xi_commands as dialog_cmds
from xi.event    import xi_commands as event_cmds
from xi.server   import xi_commands as server_cmds
from xi.misc import xi_scan    as misc_scan
from xi.misc import xi_trace   as misc_trace
from xi.misc import xi_preview as misc_preview
from xi.misc import xi_stage   as misc_stage
from xi.misc import xi_orphans as misc_orphans
from xi.misc import xi_navmesh as misc_navmesh
from xi import xi_simplified
from xi.dats import xi_dats


def _require_ffxi_dir() -> None:
    """Abort every command up front when the configured FFXI_DIR folder doesn't
    exist — all DAT reads resolve against it, so failing here beats a confusing
    mid-command error. Skipped for help output so `--help` always works."""
    if "--help" in sys.argv[1:]:
        return
    # `xi bridge` starts the zone-editor WebSocket server; it can come up before
    # FFXI_DIR is configured (the editor wizard sets it). Individual bridge
    # methods still fail clearly if a DAT path is needed.
    if len(sys.argv) > 1 and sys.argv[1] == "bridge":
        return
    from pathlib import Path
    from xi.xi_config import FFXI_DIR
    if FFXI_DIR and Path(FFXI_DIR).is_dir():
        return
    raise click.ClickException(
        f"FFXI_DIR folder not found: {FFXI_DIR or '(empty)'}\n\n"
        "Please click \"Settings\" or update your .env file to point to the "
        "correct 'FINAL FANTASY XI' install.")


@click.group(context_settings={"max_content_width": 110})
def cli():
    """xi - FFXI private server DAT tools."""
    _require_ffxi_dir()


from xi.misc import xi_bridge_server as misc_bridge
cli.add_command(misc_bridge.cmd, 'bridge')
cli.add_command(batch_cmds.batch, 'batch')
cli.add_command(xi_dats.group, 'dats')


# ── audio (FFXI music .bgw + sound effects .spw → WAV) ────────────────────────

@cli.group()
def audio():
    """Decode FFXI audio to WAV (music .bgw / sound effects .spw)."""
    pass

@audio.group('music')
def audio_music_group():
    """FFXI music tracks (.bgw)."""
    pass

audio_music_group.add_command(audio_music.list_cmd,   'list')
audio_music_group.add_command(audio_music.export_cmd, 'export')
audio_music_group.hidden = True

@audio.group('sfx')
def audio_sfx_group():
    """FFXI sound effects (.spw)."""
    pass

audio_sfx_group.add_command(audio_sfx.list_cmd,   'list')
audio_sfx_group.add_command(audio_sfx.export_cmd, 'export')
audio_sfx_group.hidden = True

audio.add_command(xi_simplified.audio_search_cmd, 'search')
audio.add_command(xi_simplified.audio_json_cmd,   'json')
audio.add_command(xi_simplified.audio_export_cmd, 'export')
audio.add_command(audio_decode.decode_cmd, 'decode')
audio.add_command(audio_decode.info_cmd,   'info')
audio.add_command(audio_refs.refs_cmd,     'refs')
audio.add_command(audio_encode.import_cmd, 'import')
audio.add_command(audio_encode.install_cmd, 'install')


# ── ftable ────────────────────────────────────────────────────────────────

@cli.group()
def ftable():
    """FTABLE/VTABLE operations."""
    pass

@ftable.group('expand', invoke_without_command=True)
@click.option('--no-gear', is_flag=True,
              help='Provision only the entity buffer (skip gear windows + DLL check).')
@click.option('--no-backup', is_flag=True, help='Skip the backup step.')
@click.option('--dry-run', is_flag=True, help='Show plan without writing.')
@click.option('--debug', '-v', is_flag=True,
              help='Timed per-step diagnostics (which file/operation is slow).')
@click.option('--force', is_flag=True,
              help='Re-expand gear even if already expanded (restore tables first).')
@click.option('--pivot/--no-pivot', default=True, show_default=True,
              help='Also grow the pivot/override pack tables (FFXI_PIVOT_DIR) to the same size — the '
                   'client merges them with the base install, so a size mismatch crashes it.')
@click.pass_context
def ftable_expand(ctx, no_gear, no_backup, dry_run, debug, force, pivot):
    """Expand FTABLE/VTABLE buffers for custom entity AND gear models.

    \b
    With NO subcommand, provisions BOTH in one pass — the entity buffer (below the
    gear floor) plus the gear windows — using the ceilings configured in xi_config
    (MAX_ENTITY_MODELID / MAX_GEAR_MODELID). Use a subcommand to do just one.

    \b
    Examples:
      xi ftable expand              # both, config defaults (entity 30000 + gear 4095)
      xi ftable expand --no-gear    # entity buffer only (skips the gear DLL check)
      xi ftable expand --dry-run -v # preview the unified plan with timings
      xi ftable expand entity 30000 # entity only, explicit modelid ceiling
      xi ftable expand gear 4000    # gear only, explicit per-slot max
    """
    if ctx.invoked_subcommand is not None:
        return
    gear_inject.expand_all(do_gear=not no_gear, do_backup=not no_backup,
                           dry_run=dry_run, debug=debug, force=force, pivot=pivot)

ftable_expand.add_command(expand.entity_cmd,            'entity')
ftable_expand.add_command(gear_inject.gear_expand_cmd,  'gear')

ftable.add_command(gear_inject.reset_cmd, 'reset')
ftable.add_command(xi_simplified.ftable_json_cmd, 'json')
ftable.add_command(lookup.cmd,          'lookup')
ftable.add_command(range_scan.cmd,      'range-scan')
ftable.add_command(ftable_delete.cmd,   'delete')
ftable.add_command(ftable_set.cmd,      'set')
ftable.add_command(ftable_list.list_cmd,'list')
ftable.add_command(ftable_compare.cmd, 'compare')
ftable.add_command(ftable_tables.tables_cmd, 'tables')
ftable.add_command(ftable_tables.info_cmd,   'info')
ftable_list.list_cmd.hidden = True
ftable_tables.tables_cmd.hidden = True
ftable_tables.info_cmd.hidden = True


# ── entity ───────────────────────────────────────────────────────────────

@cli.group()
def entity():
    """Entity model operations (monsters, NPCs, objects)."""
    pass

entity.add_command(inject.cmd,          'inject')
entity.add_command(list_.cmd,           'list')
entity.add_command(recommend.cmd,       'recommend')

from xi.entity import xi_recolor as entity_recolor
entity.add_command(entity_recolor.cmd,  'recolor')
entity.add_command(gear_character.look_cmd, 'look')   # decode an NPC 'look' appearance blob
entity.hidden = True


# ── model / mesh / anim (clean replacements for the old entity namespace) ────

@cli.group()
def model():
    """Model-id and FTABLE registry inspection."""
    pass

model.add_command(xi_simplified.model_search_cmd, 'search')
model.add_command(xi_simplified.model_json_cmd,   'json')


@cli.group('anim')
def anim_top():
    """Animation export/import and JSON inspection."""
    pass

anim_top.add_command(anim_export.cmd,       'export')
anim_top.add_command(anim_export.list_cmd,  'list')
anim_top.add_command(anim_import.cmd,       'import')
anim_top.add_command(xi_simplified.anim_json_cmd, 'json')
anim_top.add_command(anim_schedule.group,   'schedule')


@entity.group()
def mesh():
    """Entity mesh export (skeleton + mesh + textures, no animation)."""
    pass

mesh.add_command(mesh_export.cmd,       'export')
mesh.add_command(mesh_import.cmd,       'import')


@cli.group('mesh')
def mesh_top():
    """Mesh export/import and JSON inspection."""
    pass

mesh_top.add_command(mesh_export.cmd,       'export')
mesh_top.add_command(mesh_import.cmd,       'import')
mesh_top.add_command(xi_simplified.mesh_json_cmd, 'json')


# ── gear ──────────────────────────────────────────────────────────────────

@cli.group()
def gear():
    """Gear model operations."""
    pass

gear.add_command(gear_list.cmd,          'list')
gear.add_command(xi_simplified.gear_search_cmd, 'search')
gear.add_command(xi_simplified.gear_json_cmd,   'json')
gear.add_command(gear_export.cmd,        'export')
gear.add_command(gear_character.character_cmd, 'character')   # assemble a full NPC model from its 'look'

import copy as _copy
from xi.gear import xi_edit as gear_edit
gear.add_command(gear_edit.cmd,          'edit')
gear.add_command(gear_inject.inject_cmd, 'inject')
gear.add_command(gear_inject.apply_config_cmd, 'import-json')
gear.add_command(gear_import.cmd,        'import')
gear_list.cmd.hidden = True
gear_inject.inject_cmd.hidden = True
# Table prep + reset moved to `xi ftable expand gear` / `xi ftable reset`.

# Back-compat: `gear recolor` is the old name for `gear edit` (hidden alias).
_gear_recolor_alias = _copy.copy(gear_edit.cmd)
_gear_recolor_alias.name = 'recolor'
_gear_recolor_alias.hidden = True
gear.add_command(_gear_recolor_alias,    'recolor')


# ── ffximain ──────────────────────────────────────────────────────────────

@cli.group()
def ffximain():
    """FFXiMain.dll POL1 decompression tools."""
    pass

ffximain.add_command(ffximain_geargroups.cmd, 'gear-groups')
ffximain.add_command(ffximain_gear_patch.cmd, 'gear-patch')
ffximain.add_command(ffximain_text_dump.cmd,  'text-dump')
ffximain.add_command(ffximain_unpack.cmd,     'unpack')


# ── ui ────────────────────────────────────────────────────────────────────────

@cli.group()
def ui():
    """FFXI UI DAT operations — textures, layout, items, spells, and string tables."""
    pass


@ui.group('tex')
def ui_tex():
    """UI texture operations — extract and re-import DXT textures from lobb/menu DATs."""
    pass

ui_tex.add_command(ui_export.cmd,               'export')
ui_tex.add_command(ui_import.cmd,               'import')
ui_tex.add_command(ui_list.list_cmd,            'list')
ui_tex.add_command(ui_simple.simple_extract_cmd,'sx')
ui_tex.add_command(ui_simple.simple_import_cmd, 'si')


@ui.group('layout')
def ui_layout():
    """UI layout inspection and patching — menu positions, animation curves, keyframes."""
    pass

ui_layout.add_command(ui_damv_pos.cmd, 'damv-pos')
ui_layout.add_command(ui_menu_pos.cmd, 'menu-pos')
ui_layout.add_command(ui_mnc2_pos.cmd, 'mnc2-pos')
ui_layout.add_command(ui_sel_pos.cmd,  'sel-pos')


ui.add_command(ui_strings.group, 'strings')
ui.add_command(ui_items.group,   'items')
ui.add_command(ui_spells.group,  'spells')


# ── mount ─────────────────────────────────────────────────────────────────────

@cli.group()
def mount():
    """Custom mount operations (model + EN/JP names + key item)."""
    pass

mount.add_command(mount_list.cmd,   'list')
mount.add_command(xi_simplified.mount_search_cmd, 'search')
mount.add_command(xi_simplified.mount_json_cmd,   'json')
mount.add_command(mount_export.cmd, 'export')
mount.add_command(mount_inject.cmd, 'inject')
mount.add_command(mount_import.cmd, 'import')
mount.add_command(mount_delete.cmd, 'delete')
mount_list.cmd.hidden = True
mount_inject.cmd.hidden = True


# ── zone ──────────────────────────────────────────────────────────────────────

@cli.group()
def zone():
    """Zone model operations (static area geometry)."""
    pass

zone.add_command(zone_export.cmd,              'export')
zone.add_command(zone_export.tree_cmd,         'tree')
zone.add_command(xi_simplified.zone_search_cmd, 'search')
zone.add_command(xi_simplified.zone_json_cmd,   'json')
zone.add_command(zone_import.cmd,              'import')
zone.add_command(zone_list.cmd,                'list')
zone.add_command(zone_apply_changes.apply_changes_cmd, 'import-json')
zone.add_command(zone_reset.cmd,               'reset')
zone.add_command(zone_inject.cmd,             'inject')
zone.add_command(zone_assemble.cmd,           'build-from-manifest')
zone.add_command(zone_navmesh.cmd,            'navmesh')
zone.add_command(zone_navmesh.info_cmd,       'navmesh-info')
zone.add_command(zone_new.cmd,                'new')
zone.add_command(zone_make_template.cmd,      'make-template')
zone.add_command(zone_new.scaffold_server_cmd, 'scaffold-server')
zone.add_command(zone_drop.cmd,               'delete')
zone.add_command(zone_footsteps.cmd,          'footsteps')
zone_export.tree_cmd.hidden = True
zone_list.cmd.hidden = True
zone_inject.cmd.hidden = True


# ── object (individual zone objects / placements) ─────────────────────────────

@zone.group('object')
def zone_object_group():
    """Add, edit, inspect, and remove individual zone objects (placements)."""
    pass

zone_object_group.add_command(zone_objects.list_cmd,                'list')
zone_object_group.add_command(zone_object.export_object_cmd,        'export')
zone_object_group.add_command(zone_object.import_object_cmd,        'import')
zone_object_group.add_command(zone_object.replace_object_cmd,       'replace')
zone_object_group.add_command(zone_add_object.cmd,                  'clone')
zone_object_group.add_command(zone_delete.cmd,                      'delete')
zone_object_group.add_command(zone_apply_changes.set_placement_cmd, 'set-placement')
zone_object_group.add_command(zone_object.swap_placement_cmd,       'swap-placement')
zone_object_group.hidden = True


# ── object (top-level zone placement/object operations) ─────────────────────

@cli.group('object')
def object_group():
    """Zone object/placement export, import, editing, and JSON inspection."""
    pass

object_group.add_command(xi_simplified.object_json_cmd,              'json')
object_group.add_command(zone_object.export_object_cmd,              'export')
object_group.add_command(zone_object.import_object_cmd,              'import')
object_group.add_command(zone_object.replace_object_cmd,             'replace')
object_group.add_command(zone_add_object.cmd,                        'clone')
object_group.add_command(zone_delete.cmd,                            'delete')
object_group.add_command(zone_apply_changes.set_placement_cmd,       'set-placement')
object_group.add_command(zone_object.swap_placement_cmd,             'swap-placement')


@zone.group('fx')
def zone_fx_group():
    """List zone visual effects (0x05 generators) within a DAT."""
    pass

zone_fx_group.add_command(fx_list.list_cmd, 'list')
zone_fx_group.hidden = True


# ── fx (visual effects: 0x05 generators) ──────────────────────────────────────

@cli.group()
def fx():
    """Visual effects in a DAT (0x05 particle/light generators)."""
    pass

fx.add_command(fx_list.list_cmd,     'list')
fx.add_command(fx_dump.dump_cmd,     'dump')
fx.add_command(xi_simplified.fx_json_cmd, 'json')
fx.add_command(fx_delete.delete_cmd,       'delete')
fx.add_command(fx_delete.delete_group_cmd, 'delete-group')
fx.add_command(fx_set.set_cmd,       'set')
fx.add_command(fx_copy.copy_cmd,            'copy')
fx.add_command(fx_copy.copy_group_cmd,      'copy-group')
fx.add_command(fx_export.export_cmd, 'export')
fx_list.list_cmd.hidden = True
fx_dump.dump_cmd.hidden = True


# ── tex (DAT textures: 0x20) ──────────────────────────────────────────────────

@cli.group()
def tex():
    """Extract and re-import textures (0x20 sections) from any DAT."""
    pass

tex.add_command(tex_list.list_cmd,     'list')
tex.add_command(xi_simplified.tex_json_cmd, 'json')
tex.add_command(tex_export.export_cmd, 'export')
tex.add_command(tex_import.import_cmd, 'import')
tex_list.list_cmd.hidden = True


# ── launcher ────────────────────────────────────────────────────────────────

@cli.group()
def launcher():
    """Package custom content for release."""
    pass

launcher.add_command(launcher_ui_themes.cmd, 'ui-themes')


# ── utils ─────────────────────────────────────────────────────────────────────

@cli.group()
def utils():
    """Standalone utility commands."""
    pass


utils.add_command(dds2png.cmd,  'dds2png')
utils.add_command(png2dds.cmd,  'png2dds')


# ── event (zone event DAT: NPC bytecode, cutscenes, dialogue) ────────────────

@cli.group()
def event():
    """Decode per-zone event DATs (NPC bytecode / cutscene scripts / dialogue)."""
    pass

event.add_command(event_cmds.cutscene_group, 'cutscene')
event.add_command(event_cmds.dialogue_group, 'dialogue')

# `dialogue` is the single home for all NPC-text work: authoring new dialogue events
# (`actors`, `new` — defined in xi.event.xi_commands) plus editing the underlying
# per-zone dialog *string table* (`export/search/info/edit/reset` — formerly the
# top-level `xi dialog` group). No new args were needed: each command already takes
# a zone id / zone name / DAT path and resolves the right DAT itself — the string-table
# commands → the dialog DAT, `new` → both the dialog DAT and the event DAT.
event_cmds.dialogue_group.add_command(dialog_cmds.export_cmd, 'export')
event_cmds.dialogue_group.add_command(dialog_cmds.search_cmd, 'search')
event_cmds.dialogue_group.add_command(dialog_cmds.info_cmd,   'info')
event_cmds.dialogue_group.add_command(dialog_cmds.edit_cmd,   'edit')
event_cmds.dialogue_group.add_command(dialog_cmds.reset_cmd,  'reset')
# Future: `import` (re-apply an edited export JSON wholesale) + `inject` (add a
# brand-new entry, growing the table).


# ── misc (unused-zone discovery + LSB scaffolding) ───────────────────────────

@cli.group()
def misc():
    """Misc helpers, including unused/cut zone discovery and LSB staging."""
    pass

misc.add_command(misc_scan.cmd,    'scan')
misc.add_command(misc_trace.cmd,   'trace')
misc.add_command(misc_preview.cmd, 'preview')
misc.add_command(misc_stage.cmd,   'stage')
misc.add_command(misc_orphans.cmd, 'orphans')
misc.add_command(misc_navmesh.cmd, 'navmesh-prep')


# ── server (local LSB database + process status) ──────────────────────────────

@cli.group()
def server():
    """Interact with the local FFXI server (database queries, process status)."""
    pass

server.add_command(server_cmds.db_cmd,     'db')
server.add_command(server_cmds.status_cmd, 'status')
