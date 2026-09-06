"""Typed forms for the fixed-layout opcodes that used to travel as ``raw`` steps.

One table drives both directions: the decompiler turns the bytes into ``{"op": name, field...}``
and the compiler turns that back into the same bytes. Field kinds:

    u8      one literal byte
    u16     one literal little-endian word
    sel     a 2-byte selector: a constant (through the actor's reference table) or a register
    reg     a 2-byte selector that must be a register (a store target)
    ent     a 4-byte entity id ("self", "player", "0x010F3007", ...)
    tag     a 4-byte scheduler tag (``tag`` printable, else ``tagHex``)
    name16  a 16-byte zero-padded ASCII name
    tbl     a 2-byte absolute offset of a `table` trailer, carried as the table's label
    msg     a 2-byte selector holding a dialog message id: a line id ("m123") when constant

Semantics and sizes come from atom0s/XiEvents (OpCodes/, https://github.com/atom0s/XiEvents) (the PS2 client's handlers); the names are
ours. A table entry is keyed by opcode, or by (opcode, size) when the size selects the form.
"""

TYPED: dict = {
    # scheduler requests (XiEvent::ReqSet helpers): a = priority byte, b = request index
    0x27: ("request", [("a", "u8"), ("entity", "ent"), ("b", "u8")]),
    0x2A: ("request_level", [("a", "u8"), ("entity", "ent")]),      # GetReqLevel: a query, never waits
    # entity render flags: the byte is the new value / mask for Flags0 / Flags2 / Flags3
    0x2F: ("render_flags0", [("flag", "u8"), ("entity", "ent")]),
    0x7C: ("render_flags2", [("flag", "u8"), ("entity", "ent")]),
    0x94: ("render_flags3", [("flag", "u8"), ("entity", "ent")]),
    0x33: ("self_flags0", [("flag", "u8")]),
    0x22: ("event_hide", [("flag", "u8")]),          # SetEventHideFlag on the event entity
    # movement / facing
    0x32: ("set_speed", [("speed", "sel")]),         # ExtData[1]->MainSpeed
    0x4B: ("set_yaw", [("entity", "ent"), ("yaw", "sel")]),
    0x3A: ("get_yaw", [("entity", "ent"), ("into", "reg")]),
    0x3B: ("get_pos", [("entity", "ent"), ("x", "reg"), ("y", "reg"), ("z", "reg")]),
    # animation / waits
    0x6E: ("emote", [("entity", "ent"), ("anim", "sel")]),
    0x99: ("wait_anim", [("entity", "ent")]),        # yields while the entity plays an animation
    0x76: ("wait_render", [("entity", "ent")]),      # yields on Render.Flags0 / Flags3
    0x5E: ("stop_action", [("tag", "tag")]),         # back to the idle motion
    0x7B: ("stop_talking", [("entity", "ent")]),     # NpcSpeechFrame = -1
    0x6C: ("transparency", [("entity", "ent"), ("a", "sel"), ("b", "sel")]),   # CodeTRANSPAR
    0x9A: ("wait_music", []),
    0x30: ("continue_off", []),                      # ucoff_continue = 0
    0x78: ("reset_time", []),                        # game timer on, zone weather reset
    0x20: ("lock_player", [("flag", "u8")]),         # CliEventUcFlag
    0x38: ("event_mode", [("mode", "sel")]),         # CliEventModeLocal low word
    # arithmetic
    0x08: ("subtract", [("into", "reg"), ("value", "sel")]),
    0x15: ("div", [("into", "reg"), ("value", "sel")]),
    # scheduler calls shaped like 0x45 (task) with another helper argument
    0x62: ("task_62", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag"), ("b", "sel")]),
    0x9F: ("task_9f", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag"), ("b", "sel")]),
    0xBB: ("task_bb", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag"), ("b", "sel")]),
    0xCD: ("task_cd", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag"), ("b", "sel")]),
    0xD0: ("task_d0", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag"), ("b", "sel")]),
    0xC4: ("effect_sub", [("sub", "u8"), ("a", "sel"), ("ent1", "ent"), ("ent2", "ent")]),   # 0x73 helper with a sub byte
    0x6B: ("stop_action_of", [("tag", "tag"), ("entity", "ent")]),
    # arithmetic (dst op= value)
    0x0D: ("bit_and", [("into", "reg"), ("value", "sel")]),
    0x0F: ("bit_xor", [("into", "reg"), ("value", "sel")]),
    0x11: ("shr", [("into", "reg"), ("value", "sel")]),
    0x13: ("rand_mod", [("into", "reg"), ("value", "sel")]),
    0x16: ("sin", [("into", "reg"), ("a", "sel"), ("b", "sel")]),
    0x17: ("cos", [("into", "reg"), ("a", "sel"), ("b", "sel")]),
    # zone / time / weather / doors
    0x34: ("zone_load", [("zone", "sel")]),
    0x35: ("zone_load2", [("zone", "sel")]),
    0x39: ("set_dir", [("dir", "sel")]),                      # ExtData[1]->EventDir[1]
    0x77: ("set_time", [("time", "sel"), ("weather", "sel")]),
    0x83: ("get_time", [("into", "reg")]),
    0x97: ("set_wind", [("base", "sel"), ("width", "sel")]),
    0x4C: ("open_door", []),
    0x4D: ("close_door", []),
    (0x47, 2): ("update_pos_sv", [("sub", "u8")]),
    (0x47, 10): ("update_pos_sv", [("sub", "u8"), ("x", "sel"), ("y", "sel"), ("z", "sel"), ("dir", "sel")]),
    (0x5A, 2): ("event_pos", [("sub", "u8")]),
    (0x5A, 8): ("event_pos", [("sub", "u8"), ("x", "sel"), ("y", "sel"), ("z", "sel")]),
    # entity state
    # 0x59: the sub byte decides the size (see xi_event's size table); keyed by sub so the
    # compiler reproduces retail's width whatever the value looks like
    (0x59, "sub", 0x05): ("entity_update", [("sub", "u8"), ("entity", "ent"), ("value", "u8")]),
    (0x59, "sub", 0x01): ("entity_update", [("sub", "u8"), ("entity", "ent"), ("value", "sel")]),
    (0x59, "sub", 0x03): ("entity_update", [("sub", "u8"), ("entity", "ent"), ("value", "sel")]),
    (0x59, "sub", 0x04): ("entity_update", [("sub", "u8"), ("entity", "ent"), ("value", "sel")]),
    (0x59, "sub", 0x08): ("entity_update", [("sub", "u8"), ("entity", "ent"), ("value", "sel")]),
    (0x59, "sub", 0x00): ("entity_update", [("sub", "u8"), ("value", "sel")]),   # self turn speed
    (0x59, "sub", 0x02): ("entity_update", [("sub", "u8"), ("value", "sel")]),   # self head turn speed
    (0x59, "sub", 0x07): ("entity_update", [("sub", "u8"), ("value", "sel")]),
    (0x59, "sub", 0x06): ("entity_update", [("sub", "u8"), ("entity", "ent")]),
    # 0x7A: event VM control per entity
    (0x7A, "sub", 0x00): ("vm_reset", [("sub", "u8"), ("entity", "ent")]),
    (0x7A, "sub", 0x02): ("vm_reset", [("sub", "u8"), ("entity", "ent")]),
    (0x7A, "sub", 0x05): ("vm_reset", [("sub", "u8"), ("entity", "ent")]),
    (0x7A, "sub", 0x01): ("vm_reset", [("sub", "u8"), ("entity", "ent"), ("value", "u8")]),
    (0x7A, "sub", 0x03): ("vm_reset", [("sub", "u8")]),
    (0x7A, "sub", 0x04): ("vm_reset", [("sub", "u8"), ("a", "u8"), ("entity", "ent"), ("b", "u8")]),
    # 0xB4: UI / string helpers (sizes per sub from the size table; 6-byte subs read +2 and +4)
    (0xB4, 2): ("ui_op", [("sub", "u8")]),
    (0xB4, 4): ("ui_op", [("sub", "u8"), ("a", "sel")]),
    (0xB4, 6): ("ui_op", [("sub", "u8"), ("a", "sel"), ("b", "sel")]),
    # 0xB6: looks (sub 0B = full look, 0D / 0E = partial with race; 14 / 15 take an entity)
    (0xB6, 2): ("look_op", [("sub", "u8")]),
    (0xB6, "sub", 0x0B): ("look_op", [("sub", "u8"), ("race", "sel"), ("hair", "sel"), ("head", "sel"), ("body", "sel"), ("hands", "sel"), ("legs", "sel"), ("feet", "sel"), ("main", "sel"), ("sub2", "sel")]),
    (0xB6, "sub", 0x0D): ("look_op", [("sub", "u8"), ("a", "sel"), ("race", "sel"), ("body", "sel"), ("head", "sel"), ("feet", "sel"), ("hands", "sel")]),
    (0xB6, "sub", 0x0E): ("look_op", [("sub", "u8"), ("a", "sel"), ("race", "sel"), ("body", "sel"), ("head", "sel"), ("feet", "sel"), ("hands", "sel"), ("legs", "sel")]),
    (0xB6, "sub", 0x14): ("look_op", [("sub", "u8"), ("entity", "ent")]),
    (0xB6, "sub", 0x15): ("look_op", [("sub", "u8"), ("entity", "ent")]),
    # 0x5F: wrappers that re-run C1 / 5B / 53 and repeat until they succeed
    (0x5F, "sub", 0x00): ("sched_5f", [("sub", "u8")]),
    (0x5F, "sub", 0x01): ("sched_5f", [("sub", "u8")]),
    (0x5F, "sub", 0x02): ("sched_5f", [("sub", "u8"), ("entity", "ent")]),
    (0x5F, "sub", 0x03): ("sched_5f", [("sub", "u8"), ("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    (0x5F, "sub", 0x04): ("sched_5f", [("sub", "u8"), ("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    (0x5F, "sub", 0x05): ("sched_5f", [("sub", "u8"), ("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag"), ("b", "sel")]),
    (0x5F, "sub", 0x06): ("sched_5f", [("sub", "u8"), ("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag"), ("b", "sel")]),
    (0x5F, "sub", 0x07): ("sched_5f", [("sub", "u8"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    # 0x9D table sub-opcodes with their table reference (relocated by label)
    (0x9D, "sub", 0x0A): ("table_op", [("sub", "u8"), ("table", "tbl"), ("n", "u16"), ("a", "sel"), ("b", "sel")]),
    (0x9D, "sub", 0x0B): ("table_op", [("sub", "u8"), ("table", "tbl"), ("n", "u16"), ("a", "sel"), ("b", "sel")]),
    (0x9D, "sub", 0x0C): ("table_op", [("sub", "u8"), ("table", "tbl"), ("a", "sel"), ("b", "sel")]),
    (0x9D, "sub", 0x0F): ("table_op", [("sub", "u8"), ("table", "tbl"), ("a", "sel"), ("b", "sel"), ("c", "sel")]),
    (0x9D, "sub", 0x10): ("table_op", [("sub", "u8"), ("table", "tbl"), ("n", "u16"), ("a", "sel"), ("b", "sel")]),
    (0x7A, 6): ("vm_reset", [("sub", "u8"), ("entity", "ent")]),
    0x81: ("blink", [("sub", "u8"), ("entity", "ent")]),
    0x7D: ("player_task", [("task", "sel")]),
    0x95: ("event_npc_set", [("value", "sel")]),
    0x96: ("event_npc_clear", []),
    0xA4: ("self_flags3_a", [("flag", "u8")]),
    0xA5: ("self_flags3_b", [("flag", "u8")]),
    0xC0: ("self_flags3_c", [("value", "sel")]),
    (0xAB, 2): ("render_sub", [("sub", "u8")]),
    (0xAB, 4): ("render_sub", [("sub", "u8"), ("a", "sel")]),
    (0xAB, 6): ("render_sub", [("sub", "u8"), ("entity", "ent")]),      # subs 1B / 1C
    0xB5: ("set_name", [("data", "bytes", 3)]),
    (0xB4, 3): ("ui_op", [("sub", "u8"), ("value", "u8")]),
    (0xB4, 20): ("ui_op", [("sub", "u8"), ("into", "reg"), ("text", "name16")]),   # sub 0 / 0x13: copy a string
    (0xB6, 4): ("look_op", [("sub", "u8"), ("a", "sel")]),
    (0xB6, 14): ("look_op", [("sub", "u8"), ("a", "sel"), ("data", "bytes", 10)]),
    (0xB6, 16): ("look_op", [("sub", "u8"), ("a", "sel"), ("data", "bytes", 12)]),
    (0xB6, 20): ("look_op", [("sub", "u8"), ("a", "sel"), ("data", "bytes", 16)]),
    (0xB3, 2): ("rank_board", [("sub", "u8")]),
    (0xB3, 4): ("rank_board", [("sub", "u8"), ("a", "sel")]),
    (0xB3, 14): ("rank_board", [("sub", "u8"), ("a", "sel"), ("b", "sel"), ("c", "sel"), ("d", "sel"), ("e", "sel"), ("f", "sel")]),
    (0xB3, 18): ("rank_board", [("sub", "u8"), ("a", "sel"), ("b", "sel"), ("c", "sel"), ("d", "sel"), ("e", "sel"), ("f", "sel"), ("g", "sel"), ("h", "sel")]),
    0xA8: ("map_markers", [("sub", "u8"), ("a", "sel"), ("b", "sel")]),
    (0xD4, 8): ("query_op", [("sub", "u8"), ("a", "sel"), ("b", "sel"), ("c", "sel")]),   # D4 sub 00 (02 is the menu, decoded earlier)
    0xAD: ("sched_action", [("sub", "u8"), ("a", "sel"), ("ent1", "ent"), ("ent2", "ent")]),
    # music / camera / rooms (size-selected forms)
    (0x5C, 4): ("music_op", [("sub", "u8"), ("a", "sel")]),
    (0x5C, 6): ("music_op", [("sub", "u8"), ("a", "sel"), ("b", "sel")]),
    0x5D: ("music_vol", [("volume", "sel"), ("frames", "sel")]),
    (0x46, 2): ("camera_control", [("sub", "u8")]),
    (0x46, 4): ("camera_control", [("sub", "u8"), ("value", "sel")]),
    (0x75, 2): ("load_room", [("sub", "u8")]),
    (0x75, 4): ("load_room", [("sub", "u8"), ("room", "sel")]),
    # ---- long tail (2026-09-05, batch 4): flags, yields, HUD, sound, weather, mounts, crafting, zone tasks
    0x61: ("self_flags2", [("flag", "u8")]),
    0x74: ("self_flags1", [("flag", "u8")]),
    0x86: ("render_flags3_b", [("flag", "u8"), ("entity", "ent")]),
    0x90: ("self_flags01", []),
    0x8E: ("event_status_45", []),
    0x4F: ("set_status_event", [("value", "sel")]),
    0x98: ("wait_zone_load", []),
    0x9B: ("wait_self_anim", []),
    0x58: ("yield", []),
    0x63: ("play_anim_wait", [("anim", "sel")]),
    0x9E: ("rect_send_flag", [("flag", "u8")]),
    0xD9: ("flag_d9", [("flag", "u8")]),
    0x9C: ("get_language", [("into", "reg")]),
    0x57: ("frame_delay", [("into", "reg")]),
    0x19: ("byte_swap", [("into", "reg"), ("value", "sel")]),
    0x28: ("request_b", [("a", "u8"), ("entity", "ent"), ("b", "u8")]),
    0x44: ("if_entity_valid", [("entity", "ent")]),
    0x67: ("hud_hide", [("a", "sel"), ("b", "sel")]),
    0x68: ("hud_show", []),
    0x69: ("sound_volume", [("kind", "u8"), ("volume", "sel")]),
    0x6A: ("sound_volume_ease", [("a", "sel"), ("b", "sel"), ("c", "sel")]),
    0x64: ("distance2d", [("into", "reg"), ("x1", "sel"), ("y1", "sel"), ("x2", "sel"), ("y2", "sel")]),
    0x65: ("distance3d", [("ent1", "ent"), ("ent2", "ent"), ("into", "reg")]),
    0x2D: ("zone_task", [("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    0x51: ("zone_task_end", [("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    0x54: ("wait_zone_task", [("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    0xA2: ("wait_a2", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    0xA0: ("wait_a0", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    0xBC: ("wait_bc", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    0xC6: ("wait_c6", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    0xC5: ("task_c5", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag"), ("b", "sel")]),
    0xD5: ("task_d5", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag"), ("b", "sel")]),
    0x87: ("worldpass_a", [("sub", "u8")]),
    0x88: ("worldpass_b", [("sub", "u8")]),
    (0xA7, 2): ("wait_server_req", [("sub", "u8")]),
    (0xA7, 4): ("wait_server_req", [("sub", "u8"), ("value", "sel")]),
    0x8D: ("map_open_props", [("a", "sel"), ("b", "sel")]),
    0xC8: ("map_open_params", [("a", "sel"), ("b", "sel"), ("c", "sel")]),
    0xB8: ("map_markers_add", [("a", "sel"), ("b", "sel"), ("c", "sel"), ("d", "sel"), ("e", "sel"), ("name", "name16")]),
    0xC3: ("copy_string", [("a", "sel"), ("data", "bytes", 2), ("b", "sel")]),
    0x56: ("deprecated_56", [("data", "bytes", 4)]),
    0x6D: ("deprecated_6d", [("data", "bytes", 6)]),
    (0x31, 2): ("event_pos_b", [("sub", "u8")]),
    (0x31, 10): ("event_pos_b", [("sub", "u8"), ("x", "sel"), ("y", "sel"), ("z", "sel"), ("dir", "sel")]),
    (0x72, 4): ("event_weather", [("sub", "u8"), ("a", "sel")]),
    (0x72, 6): ("event_weather", [("sub", "u8"), ("a", "sel"), ("b", "sel")]),
    (0x72, 10): ("event_weather", [("sub", "u8"), ("a", "sel"), ("data", "bytes", 6)]),
    (0xAE, 6): ("misc_ae", [("sub", "u8"), ("data", "bytes", 4)]),
    (0xAE, 8): ("misc_ae", [("sub", "u8"), ("data", "bytes", 4), ("a", "sel")]),
    (0xAE, 10): ("misc_ae", [("sub", "u8"), ("data", "bytes", 8)]),
    (0x7E, 6): ("mount_op", [("sub", "u8"), ("entity", "ent")]),
    (0x7E, 8): ("mount_op", [("sub", "u8"), ("entity", "ent"), ("a", "sel")]),
    (0x7E, 16): ("mount_op", [("sub", "u8"), ("entity", "ent"), ("a", "sel"), ("b", "sel"), ("c", "sel"), ("d", "sel"), ("e", "sel")]),
    (0x7E, 18): ("mount_op", [("sub", "u8"), ("entity", "ent"), ("a", "sel"), ("b", "sel"), ("c", "sel"), ("d", "sel"), ("e", "sel"), ("f", "sel")]),
    (0xC2, 2): ("op_c2", [("sub", "u8")]),
    (0xC2, 4): ("op_c2", [("sub", "u8"), ("data", "bytes", 2)]),
    (0xC2, 6): ("op_c2", [("sub", "u8"), ("a", "sel"), ("data", "bytes", 2)]),
    (0xAC, 4): ("op_ac", [("sub", "u8"), ("a", "sel")]),
    (0xAC, 6): ("op_ac", [("sub", "u8"), ("data", "bytes", 4)]),
    (0xAC, 8): ("op_ac", [("sub", "u8"), ("data", "bytes", 4), ("a", "sel")]),
    (0x8C, 2): ("craft_op", [("sub", "u8")]),
    (0x8C, 8): ("craft_op", [("sub", "u8"), ("a", "sel"), ("b", "sel"), ("c", "sel")]),
    (0x8C, 10): ("craft_op", [("sub", "u8"), ("a", "sel"), ("b", "sel"), ("c", "sel"), ("data", "bytes", 2)]),
    (0x8C, 12): ("craft_op", [("sub", "u8"), ("a", "sel"), ("b", "sel"), ("c", "sel"), ("data", "bytes", 4)]),
    (0x8C, 14): ("craft_op", [("sub", "u8"), ("a", "sel"), ("b", "sel"), ("c", "sel"), ("data", "bytes", 6)]),
    (0xD8, 6): ("op_d8", [("sub", "u8"), ("data", "bytes", 4)]),
    (0xD8, 8): ("op_d8", [("sub", "u8"), ("data", "bytes", 4), ("a", "sel")]),
    (0xD8, 12): ("op_d8", [("sub", "u8"), ("data", "bytes", 4), ("a", "sel"), ("b", "sel"), ("c", "sel")]),
    # ---- the last six of Ru'Lude (2026-09-05)
    0xAA: ("vana_time", [("time", "sel"), ("year", "reg"), ("month", "reg"), ("day", "reg"), ("weekday", "reg"), ("hour", "reg"), ("minute", "reg"), ("moon", "reg")]),
    0x36: ("set_pos3", [("x", "sel"), ("z", "sel"), ("y", "sel")]),          # EventPos x, z, y (millimetres)
    0xB0: ("print_to", [("sub", "u8"), ("speaker", "ent"), ("listener", "ent"), ("text", "msg")]),
    0xB1: ("get_flag_b1", [("data", "bytes", 3)]),
    0xCE: ("wait_ce", [("a", "sel"), ("ent1", "ent"), ("ent2", "ent"), ("tag", "tag")]),
    0xD3: ("clear_motion", [("sub", "u8"), ("entity", "ent")]),
    # map window
    0x89: ("map_open", [("map", "sel")]),
    0x8A: ("map_close", []),
    0x8B: ("map_marker", [("a", "sel"), ("b", "sel"), ("c", "sel"), ("d", "sel"), ("name", "name16")]),
}

SIZES = {"u8": 1, "u16": 2, "sel": 2, "reg": 2, "ent": 4, "tag": 4, "name16": 16, "tbl": 2, "msg": 2}


def field_size(field) -> int:
    """Byte size of a field tuple: (name, kind) or (name, "bytes", n)."""
    return int(field[2]) if field[1] == "bytes" else SIZES[field[1]]


def spec_for(op: int, size: int, sub: int = -1):
    """(name, fields) for an opcode of this size (and sub byte), or None."""
    spec = TYPED.get((op, "sub", sub))
    if spec is not None:
        return spec if 1 + sum(field_size(f) for f in spec[1]) == size else None
    spec = TYPED.get((op, size))
    if spec is None:
        spec = TYPED.get(op)
        if spec is not None and 1 + sum(field_size(f) for f in spec[1]) != size:
            return None
    return spec


def opcode_for(name: str, step: dict):
    """(opcode, fields) for a step by name: the form whose fields the step carries, where a
    ``bytes`` field must match the data length exactly; among several the largest form wins."""
    name = ALIASES.get(name, name)
    def fits(fields) -> bool:
        for f in fields:
            fname, kind = f[0], f[1]
            if kind == "tag":
                if fname not in step and (fname + "Hex") not in step:
                    return False
            elif kind == "bytes":
                data = str(step.get(fname, "")).replace(" ", "")
                if len(data) != 2 * int(f[2]):
                    return False
            elif fname not in step:
                return False
        return True
    best = None
    if "sub" in step:                                 # sub-keyed forms are authoritative for their sub
        for key, (nm, fields) in TYPED.items():
            if nm == name and isinstance(key, tuple) and len(key) == 3 and key[1] == "sub" and key[2] == int(step["sub"]):
                return key[0], fields
    for key, (nm, fields) in TYPED.items():
        if nm != name or not fits(fields) or (isinstance(key, tuple) and len(key) == 3):
            continue
        size = sum(field_size(f) for f in fields)
        if best is None or size > best[2]:
            best = (key[0] if isinstance(key, tuple) else key, fields, size)
    if best is None:                                   # nothing fits: the smallest form, defaults filled
        for key, (nm, fields) in TYPED.items():
            if nm == name:
                size = sum(field_size(f) for f in fields)
                if best is None or size < best[2]:
                    best = (key[0] if isinstance(key, tuple) else key, fields, size)
    return None if best is None else (best[0], best[1])


# Step names that changed; the compiler accepts the old name and the decompiler emits the new one.
ALIASES = {
    "fade_color": "transparency",      # 0x6C, SE: CodeTRANSPAR
    "companion": "request_wait",       # 0x29, SE: CodeREQEW (ReqSetWait): run a slot of another entity's table and wait
}

# Entity-state work selectors (XiEvent::getworkofs, 0x7F00-0x7F8B): runtime values of the event
# entity (0x7F0x) and of the local player (0x7F8x). Positions are millimetres, headings are
# enDirCli(rad) * 4096 / (2*pi); "flag25" is bit 25 of Render.Flags01. Carried as {"state": name}.
STATE_SELECTORS = {
    "event_x": 0x7F00, "event_z": 0x7F01, "event_y": 0x7F02, "event_dir": 0x7F03,
    "event_job": 0x7F06, "event_race": 0x7F07, "event_level": 0x7F08, "event_server_id": 0x7F0A, "event_flag25": 0x7F0B,
    "player_x": 0x7F80, "player_z": 0x7F81, "player_y": 0x7F82, "player_dir": 0x7F83,
    "player_job": 0x7F86, "player_race": 0x7F87, "player_level": 0x7F88, "player_server_id": 0x7F8A, "player_flag25": 0x7F8B,
}
STATE_NAMES = {v: k for k, v in STATE_SELECTORS.items()}

# Square Enix's own handler names (XiEvent::Code*), from the PS2 client's DWARF symbols via the
# XiEvents notes and docs/reference/ps2_decomp_crosscheck.md. The 2003 switch ends at 0xA6.
SE_NAMES = {
    0x02: "CodeIF", 0x1F: "CodeMOVE", 0x23: "MESWAIT", 0x24: "CodeQUERY", 0x25: "CodeQUERYWAIT",
    0x28: "CodeREQSW", 0x29: "CodeREQEW", 0x2C: "CodeSCHEDULOR", 0x2D: "CodeMAPSCHEDULOR", 0x31: "CodeSMOVE",
    0x34: "XiZone::Open", 0x35: "XiZone::Open (no close)", 0x40: "CodeSETBITWORK", 0x41: "CodeGETBITWORK",
    0x45: "CodeLOADSCHEDULER", 0x46: "CodeDEFCAMERA", 0x4A: "CodeDTURA", 0x50: "CodeENDSCHEDULOR",
    0x51: "CodeENDMAPSCHEDULOR", 0x52: "CodeENDLOADSCHEDULER_Main", 0x53: "CodeWAITSCHEDULOR",
    0x54: "CodeWAITMAPSCHEDULOR", 0x55: "CodeWAITLOADSCHEDULER_Main", 0x5A: "CodeMOVE2", 0x5B: "CodeLOADEXTSCHEDULERMain",
    0x62: "CodeLOADEVENTSCHEDULER", 0x65: "CodeGETDISTANCEAA", 0x66: "CodeLOADEXTSCHEDULERMain", 0x6C: "CodeTRANSPAR",
    0x6E: "CodeEMOT", 0x71: "CodeOPENPASSWIN", 0x72: "CodeGETWEATER", 0x73: "CodeMAGICSCHEDULOR", 0x75: "CodeLOADROOM",
    0x7E: "CodeCHOCOBO", 0x7F: "CodeQUERYWAIT2", 0x80: "CodeLOADWAIT", 0x8B: "CodeSETEVENTMARK",
    0x98: "XiZone::IsReadingExtData", 0x9F: "CodeLOADEVENTSCHEDULER2", 0xA0: "CodeWAITLOADSCHEDULER_Main",
    0xA1: "CodeENDLOADSCHEDULER_Main", 0xA2: "CodeWAITLOADSCHEDULER_Main", 0xA3: "CodeENDLOADSCHEDULER_Main",
}


def render_doc() -> str:
    """The markdown behind docs/events/typed_opcodes.md (python -c "from xi.event import xi_typed; print(xi_typed.render_doc())")."""
    rows = []
    for key, (nm, fields) in TYPED.items():
        op = key if isinstance(key, int) else key[0]
        form = "" if isinstance(key, int) else (f"sub {key[2]:02X}" if len(key) == 3 else f"size {key[1]}")
        fl = ", ".join(f"`{f[0]}`:{f[1]}" + (f"[{f[2]}]" if f[1] == "bytes" else "") for f in fields)
        rows.append((nm, op, form, fl))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    out = ["## Typed retail opcodes (`xi_typed.TYPED`)", "",
           "One table drives both directions: `xi event decompile` emits these steps and the compiler writes them back byte for byte. "
           "Field kinds: `u8` / `u16` literal, `sel` selector (constant through `references[]` or a register), `reg` register, "
           "`ent` entity id, `tag` scheduler tag, `name16` 16-byte name, `tbl` table label, `msg` dialog line, `bytes[n]` verbatim. "
           "The SE column is Square Enix's handler name where the PS2 client's symbols give one (the 2003 client ends at `0xA6`).", "",
           "| step | opcode | form | fields | SE handler |", "|---|---|---|---|---|"]
    for nm, op, form, fl in rows:
        out.append(f"| `{nm}` | `{op:02X}` | {form} | {fl} | {SE_NAMES.get(op, '')} |")
    out += ["", "Old names still accepted by the compiler: " + ", ".join(f"`{a}` -> `{b}`" for a, b in sorted(ALIASES.items())) + ".", "",
            "Entity-state selectors, carried as `{\"state\": name}` wherever a register is accepted: "
            + ", ".join(f"`{k}` = `{v:04X}`" for k, v in STATE_SELECTORS.items()) + "."]
    return "\n".join(out) + "\n"


NAMES = sorted({nm for nm, _ in TYPED.values()})
