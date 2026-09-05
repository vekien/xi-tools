## Typed retail opcodes (`xi_typed.TYPED`, 2026-09-05)

One table drives both directions: `xi event decompile` emits these steps and the compiler writes them back byte for byte. Field kinds: `u8` / `u16` literal, `sel` selector (constant or register spec), `reg` register (a store target), `ent` entity id, `tag` four-char scheduler tag (`tagHex` when not printable), `name16` 16-byte name, `bytes[n]` literal hex, `tbl` a `table` label, `msg` a dialog line id. When an opcode has several forms, the compiler picks the one matching the step's fields (data length for `bytes`, the `sub` byte where the width depends on it). Semantics are XiEvents' (F:\XiEvents\OpCodes); names are ours.

| step | opcode | form | fields |
|---|---|---|---|
| `bit_and` | `0D` |  | `into`:reg, `value`:sel |
| `bit_xor` | `0F` |  | `into`:reg, `value`:sel |
| `blink` | `81` |  | `sub`:u8, `entity`:ent |
| `byte_swap` | `19` |  | `into`:reg, `value`:sel |
| `camera_control` | `46` | size 2 | `sub`:u8 |
| `camera_control` | `46` | size 4 | `sub`:u8, `value`:sel |
| `clear_motion` | `D3` |  | `sub`:u8, `entity`:ent |
| `close_door` | `4D` |  | (none) |
| `continue_off` | `30` |  | (none) |
| `copy_string` | `C3` |  | `a`:sel, `data`:bytes[2], `b`:sel |
| `cos` | `17` |  | `into`:reg, `a`:sel, `b`:sel |
| `craft_op` | `8C` | size 10 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel, `data`:bytes[2] |
| `craft_op` | `8C` | size 12 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel, `data`:bytes[4] |
| `craft_op` | `8C` | size 14 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel, `data`:bytes[6] |
| `craft_op` | `8C` | size 2 | `sub`:u8 |
| `craft_op` | `8C` | size 8 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel |
| `deprecated_56` | `56` |  | `data`:bytes[4] |
| `deprecated_6d` | `6D` |  | `data`:bytes[6] |
| `distance2d` | `64` |  | `into`:reg, `x1`:sel, `y1`:sel, `x2`:sel, `y2`:sel |
| `distance3d` | `65` |  | `ent1`:ent, `ent2`:ent, `into`:reg |
| `div` | `15` |  | `into`:reg, `value`:sel |
| `effect_sub` | `C4` |  | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent |
| `emote` | `6E` |  | `entity`:ent, `anim`:sel |
| `entity_update` | `59` | sub 0x00 | `sub`:u8, `value`:sel |
| `entity_update` | `59` | sub 0x01 | `sub`:u8, `entity`:ent, `value`:sel |
| `entity_update` | `59` | sub 0x02 | `sub`:u8, `value`:sel |
| `entity_update` | `59` | sub 0x03 | `sub`:u8, `entity`:ent, `value`:sel |
| `entity_update` | `59` | sub 0x04 | `sub`:u8, `entity`:ent, `value`:sel |
| `entity_update` | `59` | sub 0x05 | `sub`:u8, `entity`:ent, `value`:u8 |
| `entity_update` | `59` | sub 0x06 | `sub`:u8, `entity`:ent |
| `entity_update` | `59` | sub 0x07 | `sub`:u8, `value`:sel |
| `entity_update` | `59` | sub 0x08 | `sub`:u8, `entity`:ent, `value`:sel |
| `event_hide` | `22` |  | `flag`:u8 |
| `event_mode` | `38` |  | `mode`:sel |
| `event_npc_clear` | `96` |  | (none) |
| `event_npc_set` | `95` |  | `value`:sel |
| `event_pos` | `5A` | size 2 | `sub`:u8 |
| `event_pos` | `5A` | size 8 | `sub`:u8, `x`:sel, `y`:sel, `z`:sel |
| `event_pos_b` | `31` | size 10 | `sub`:u8, `x`:sel, `y`:sel, `z`:sel, `dir`:sel |
| `event_pos_b` | `31` | size 2 | `sub`:u8 |
| `event_status_45` | `8E` |  | (none) |
| `event_weather` | `72` | size 10 | `sub`:u8, `a`:sel, `data`:bytes[6] |
| `event_weather` | `72` | size 4 | `sub`:u8, `a`:sel |
| `event_weather` | `72` | size 6 | `sub`:u8, `a`:sel, `b`:sel |
| `fade_color` | `6C` |  | `entity`:ent, `a`:sel, `b`:sel |
| `flag_d9` | `D9` |  | `flag`:u8 |
| `frame_delay` | `57` |  | `into`:reg |
| `get_flag_b1` | `B1` |  | `data`:bytes[3] |
| `get_language` | `9C` |  | `into`:reg |
| `get_pos` | `3B` |  | `entity`:ent, `x`:reg, `y`:reg, `z`:reg |
| `get_time` | `83` |  | `into`:reg |
| `get_yaw` | `3A` |  | `entity`:ent, `into`:reg |
| `hud_hide` | `67` |  | `a`:sel, `b`:sel |
| `hud_show` | `68` |  | (none) |
| `if_entity_valid` | `44` |  | `entity`:ent |
| `load_room` | `75` | size 2 | `sub`:u8 |
| `load_room` | `75` | size 4 | `sub`:u8, `room`:sel |
| `lock_player` | `20` |  | `flag`:u8 |
| `look_op` | `B6` | size 14 | `sub`:u8, `a`:sel, `data`:bytes[10] |
| `look_op` | `B6` | size 16 | `sub`:u8, `a`:sel, `data`:bytes[12] |
| `look_op` | `B6` | size 2 | `sub`:u8 |
| `look_op` | `B6` | size 20 | `sub`:u8, `a`:sel, `data`:bytes[16] |
| `look_op` | `B6` | size 4 | `sub`:u8, `a`:sel |
| `look_op` | `B6` | sub 0x0b | `sub`:u8, `race`:sel, `hair`:sel, `head`:sel, `body`:sel, `hands`:sel, `legs`:sel, `feet`:sel, `main`:sel, `sub2`:sel |
| `look_op` | `B6` | sub 0x0d | `sub`:u8, `a`:sel, `race`:sel, `body`:sel, `head`:sel, `feet`:sel, `hands`:sel |
| `look_op` | `B6` | sub 0x0e | `sub`:u8, `a`:sel, `race`:sel, `body`:sel, `head`:sel, `feet`:sel, `hands`:sel, `legs`:sel |
| `look_op` | `B6` | sub 0x14 | `sub`:u8, `entity`:ent |
| `look_op` | `B6` | sub 0x15 | `sub`:u8, `entity`:ent |
| `map_close` | `8A` |  | (none) |
| `map_marker` | `8B` |  | `a`:sel, `b`:sel, `c`:sel, `d`:sel, `name`:name16 |
| `map_markers` | `A8` |  | `sub`:u8, `a`:sel, `b`:sel |
| `map_markers_add` | `B8` |  | `a`:sel, `b`:sel, `c`:sel, `d`:sel, `e`:sel, `name`:name16 |
| `map_open` | `89` |  | `map`:sel |
| `map_open_params` | `C8` |  | `a`:sel, `b`:sel, `c`:sel |
| `map_open_props` | `8D` |  | `a`:sel, `b`:sel |
| `misc_ae` | `AE` | size 10 | `sub`:u8, `data`:bytes[8] |
| `misc_ae` | `AE` | size 6 | `sub`:u8, `data`:bytes[4] |
| `misc_ae` | `AE` | size 8 | `sub`:u8, `data`:bytes[4], `a`:sel |
| `mount_op` | `7E` | size 16 | `sub`:u8, `entity`:ent, `a`:sel, `b`:sel, `c`:sel, `d`:sel, `e`:sel |
| `mount_op` | `7E` | size 18 | `sub`:u8, `entity`:ent, `a`:sel, `b`:sel, `c`:sel, `d`:sel, `e`:sel, `f`:sel |
| `mount_op` | `7E` | size 6 | `sub`:u8, `entity`:ent |
| `mount_op` | `7E` | size 8 | `sub`:u8, `entity`:ent, `a`:sel |
| `music_op` | `5C` | size 4 | `sub`:u8, `a`:sel |
| `music_op` | `5C` | size 6 | `sub`:u8, `a`:sel, `b`:sel |
| `music_vol` | `5D` |  | `volume`:sel, `frames`:sel |
| `op_ac` | `AC` | size 4 | `sub`:u8, `a`:sel |
| `op_ac` | `AC` | size 6 | `sub`:u8, `data`:bytes[4] |
| `op_ac` | `AC` | size 8 | `sub`:u8, `data`:bytes[4], `a`:sel |
| `op_c2` | `C2` | size 2 | `sub`:u8 |
| `op_c2` | `C2` | size 4 | `sub`:u8, `data`:bytes[2] |
| `op_c2` | `C2` | size 6 | `sub`:u8, `a`:sel, `data`:bytes[2] |
| `op_d8` | `D8` | size 12 | `sub`:u8, `data`:bytes[4], `a`:sel, `b`:sel, `c`:sel |
| `op_d8` | `D8` | size 6 | `sub`:u8, `data`:bytes[4] |
| `op_d8` | `D8` | size 8 | `sub`:u8, `data`:bytes[4], `a`:sel |
| `open_door` | `4C` |  | (none) |
| `play_anim_wait` | `63` |  | `anim`:sel |
| `player_task` | `7D` |  | `task`:sel |
| `print_to` | `B0` |  | `sub`:u8, `speaker`:ent, `listener`:ent, `text`:msg |
| `query_op` | `D4` | size 8 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel |
| `rand_mod` | `13` |  | `into`:reg, `value`:sel |
| `rank_board` | `B3` | size 14 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel, `d`:sel, `e`:sel, `f`:sel |
| `rank_board` | `B3` | size 18 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel, `d`:sel, `e`:sel, `f`:sel, `g`:sel, `h`:sel |
| `rank_board` | `B3` | size 2 | `sub`:u8 |
| `rank_board` | `B3` | size 4 | `sub`:u8, `a`:sel |
| `rect_send_flag` | `9E` |  | `flag`:u8 |
| `render_flags0` | `2F` |  | `flag`:u8, `entity`:ent |
| `render_flags2` | `7C` |  | `flag`:u8, `entity`:ent |
| `render_flags3` | `94` |  | `flag`:u8, `entity`:ent |
| `render_flags3_b` | `86` |  | `flag`:u8, `entity`:ent |
| `render_sub` | `AB` | size 2 | `sub`:u8 |
| `render_sub` | `AB` | size 4 | `sub`:u8, `a`:sel |
| `render_sub` | `AB` | size 6 | `sub`:u8, `entity`:ent |
| `request` | `27` |  | `a`:u8, `entity`:ent, `b`:u8 |
| `request_b` | `28` |  | `a`:u8, `entity`:ent, `b`:u8 |
| `request_wait` | `2A` |  | `a`:u8, `entity`:ent |
| `reset_time` | `78` |  | (none) |
| `sched_5f` | `5F` | sub 0x00 | `sub`:u8 |
| `sched_5f` | `5F` | sub 0x01 | `sub`:u8 |
| `sched_5f` | `5F` | sub 0x02 | `sub`:u8, `entity`:ent |
| `sched_5f` | `5F` | sub 0x03 | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |
| `sched_5f` | `5F` | sub 0x04 | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |
| `sched_5f` | `5F` | sub 0x05 | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |
| `sched_5f` | `5F` | sub 0x06 | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |
| `sched_5f` | `5F` | sub 0x07 | `sub`:u8, `ent1`:ent, `ent2`:ent, `tag`:tag |
| `sched_action` | `AD` |  | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent |
| `self_flags0` | `33` |  | `flag`:u8 |
| `self_flags01` | `90` |  | (none) |
| `self_flags1` | `74` |  | `flag`:u8 |
| `self_flags2` | `61` |  | `flag`:u8 |
| `self_flags3_a` | `A4` |  | `flag`:u8 |
| `self_flags3_b` | `A5` |  | `flag`:u8 |
| `self_flags3_c` | `C0` |  | `value`:sel |
| `set_dir` | `39` |  | `dir`:sel |
| `set_name` | `B5` |  | `data`:bytes[3] |
| `set_pos3` | `36` |  | `x`:sel, `z`:sel, `y`:sel |
| `set_speed` | `32` |  | `speed`:sel |
| `set_status_event` | `4F` |  | `value`:sel |
| `set_time` | `77` |  | `time`:sel, `weather`:sel |
| `set_wind` | `97` |  | `base`:sel, `width`:sel |
| `set_yaw` | `4B` |  | `entity`:ent, `yaw`:sel |
| `shr` | `11` |  | `into`:reg, `value`:sel |
| `sin` | `16` |  | `into`:reg, `a`:sel, `b`:sel |
| `sound_volume` | `69` |  | `kind`:u8, `volume`:sel |
| `sound_volume_ease` | `6A` |  | `a`:sel, `b`:sel, `c`:sel |
| `stop_action` | `5E` |  | `tag`:tag |
| `stop_action_of` | `6B` |  | `tag`:tag, `entity`:ent |
| `stop_talking` | `7B` |  | `entity`:ent |
| `subtract` | `08` |  | `into`:reg, `value`:sel |
| `table_op` | `9D` | sub 0x0a | `sub`:u8, `table`:tbl, `n`:u16, `a`:sel, `b`:sel |
| `table_op` | `9D` | sub 0x0b | `sub`:u8, `table`:tbl, `n`:u16, `a`:sel, `b`:sel |
| `table_op` | `9D` | sub 0x0c | `sub`:u8, `table`:tbl, `a`:sel, `b`:sel |
| `table_op` | `9D` | sub 0x0f | `sub`:u8, `table`:tbl, `a`:sel, `b`:sel, `c`:sel |
| `table_op` | `9D` | sub 0x10 | `sub`:u8, `table`:tbl, `n`:u16, `a`:sel, `b`:sel |
| `task_62` | `62` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |
| `task_9f` | `9F` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |
| `task_bb` | `BB` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |
| `task_c5` | `C5` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |
| `task_cd` | `CD` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |
| `task_d0` | `D0` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |
| `task_d5` | `D5` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |
| `ui_op` | `B4` | size 2 | `sub`:u8 |
| `ui_op` | `B4` | size 20 | `sub`:u8, `into`:reg, `text`:name16 |
| `ui_op` | `B4` | size 3 | `sub`:u8, `value`:u8 |
| `ui_op` | `B4` | size 4 | `sub`:u8, `a`:sel |
| `ui_op` | `B4` | size 6 | `sub`:u8, `a`:sel, `b`:sel |
| `update_pos_sv` | `47` | size 10 | `sub`:u8, `x`:sel, `y`:sel, `z`:sel, `dir`:sel |
| `update_pos_sv` | `47` | size 2 | `sub`:u8 |
| `vana_time` | `AA` |  | `time`:sel, `year`:reg, `month`:reg, `day`:reg, `weekday`:reg, `hour`:reg, `minute`:reg, `moon`:reg |
| `vm_reset` | `7A` | size 6 | `sub`:u8, `entity`:ent |
| `vm_reset` | `7A` | sub 0x00 | `sub`:u8, `entity`:ent |
| `vm_reset` | `7A` | sub 0x01 | `sub`:u8, `entity`:ent, `value`:u8 |
| `vm_reset` | `7A` | sub 0x02 | `sub`:u8, `entity`:ent |
| `vm_reset` | `7A` | sub 0x03 | `sub`:u8 |
| `vm_reset` | `7A` | sub 0x04 | `sub`:u8, `a`:u8, `entity`:ent, `b`:u8 |
| `vm_reset` | `7A` | sub 0x05 | `sub`:u8, `entity`:ent |
| `wait_a0` | `A0` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |
| `wait_a2` | `A2` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |
| `wait_anim` | `99` |  | `entity`:ent |
| `wait_bc` | `BC` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |
| `wait_c6` | `C6` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |
| `wait_ce` | `CE` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |
| `wait_music` | `9A` |  | (none) |
| `wait_render` | `76` |  | `entity`:ent |
| `wait_self_anim` | `9B` |  | (none) |
| `wait_server_req` | `A7` | size 2 | `sub`:u8 |
| `wait_server_req` | `A7` | size 4 | `sub`:u8, `value`:sel |
| `wait_zone_load` | `98` |  | (none) |
| `wait_zone_task` | `54` |  | `ent1`:ent, `ent2`:ent, `tag`:tag |
| `worldpass_a` | `87` |  | `sub`:u8 |
| `worldpass_b` | `88` |  | `sub`:u8 |
| `yield` | `58` |  | (none) |
| `zone_load` | `34` |  | `zone`:sel |
| `zone_load2` | `35` |  | `zone`:sel |
| `zone_task` | `2D` |  | `ent1`:ent, `ent2`:ent, `tag`:tag |
| `zone_task_end` | `51` |  | `ent1`:ent, `ent2`:ent, `tag`:tag |
