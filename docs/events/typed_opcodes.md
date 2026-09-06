## Typed retail opcodes (`xi_typed.TYPED`)

One table drives both directions: `xi event decompile` emits these steps and the compiler writes them back byte for byte. Field kinds: `u8` / `u16` literal, `sel` selector (constant through `references[]` or a register), `reg` register, `ent` entity id, `tag` scheduler tag, `name16` 16-byte name, `tbl` table label, `msg` dialog line, `bytes[n]` verbatim. The SE column is Square Enix's handler name where the PS2 client's symbols give one (the 2003 client ends at `0xA6`).

| step | opcode | form | fields | SE handler |
|---|---|---|---|---|
| `bit_and` | `0D` |  | `into`:reg, `value`:sel |  |
| `bit_xor` | `0F` |  | `into`:reg, `value`:sel |  |
| `blink` | `81` |  | `sub`:u8, `entity`:ent |  |
| `byte_swap` | `19` |  | `into`:reg, `value`:sel |  |
| `camera_control` | `46` | size 2 | `sub`:u8 | CodeDEFCAMERA |
| `camera_control` | `46` | size 4 | `sub`:u8, `value`:sel | CodeDEFCAMERA |
| `clear_motion` | `D3` |  | `sub`:u8, `entity`:ent |  |
| `close_door` | `4D` |  |  |  |
| `continue_off` | `30` |  |  |  |
| `copy_string` | `C3` |  | `a`:sel, `data`:bytes[2], `b`:sel |  |
| `cos` | `17` |  | `into`:reg, `a`:sel, `b`:sel |  |
| `craft_op` | `8C` | size 10 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel, `data`:bytes[2] |  |
| `craft_op` | `8C` | size 12 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel, `data`:bytes[4] |  |
| `craft_op` | `8C` | size 14 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel, `data`:bytes[6] |  |
| `craft_op` | `8C` | size 2 | `sub`:u8 |  |
| `craft_op` | `8C` | size 8 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel |  |
| `deprecated_56` | `56` |  | `data`:bytes[4] |  |
| `deprecated_6d` | `6D` |  | `data`:bytes[6] |  |
| `distance2d` | `64` |  | `into`:reg, `x1`:sel, `y1`:sel, `x2`:sel, `y2`:sel |  |
| `distance3d` | `65` |  | `ent1`:ent, `ent2`:ent, `into`:reg | CodeGETDISTANCEAA |
| `div` | `15` |  | `into`:reg, `value`:sel |  |
| `effect_sub` | `C4` |  | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent |  |
| `emote` | `6E` |  | `entity`:ent, `anim`:sel | CodeEMOT |
| `entity_update` | `59` | sub 00 | `sub`:u8, `value`:sel |  |
| `entity_update` | `59` | sub 01 | `sub`:u8, `entity`:ent, `value`:sel |  |
| `entity_update` | `59` | sub 02 | `sub`:u8, `value`:sel |  |
| `entity_update` | `59` | sub 03 | `sub`:u8, `entity`:ent, `value`:sel |  |
| `entity_update` | `59` | sub 04 | `sub`:u8, `entity`:ent, `value`:sel |  |
| `entity_update` | `59` | sub 05 | `sub`:u8, `entity`:ent, `value`:u8 |  |
| `entity_update` | `59` | sub 06 | `sub`:u8, `entity`:ent |  |
| `entity_update` | `59` | sub 07 | `sub`:u8, `value`:sel |  |
| `entity_update` | `59` | sub 08 | `sub`:u8, `entity`:ent, `value`:sel |  |
| `event_hide` | `22` |  | `flag`:u8 |  |
| `event_mode` | `38` |  | `mode`:sel |  |
| `event_npc_clear` | `96` |  |  |  |
| `event_npc_set` | `95` |  | `value`:sel |  |
| `event_pos` | `5A` | size 2 | `sub`:u8 | CodeMOVE2 |
| `event_pos` | `5A` | size 8 | `sub`:u8, `x`:sel, `y`:sel, `z`:sel | CodeMOVE2 |
| `event_pos_b` | `31` | size 10 | `sub`:u8, `x`:sel, `y`:sel, `z`:sel, `dir`:sel | CodeSMOVE |
| `event_pos_b` | `31` | size 2 | `sub`:u8 | CodeSMOVE |
| `event_status_45` | `8E` |  |  |  |
| `event_weather` | `72` | size 10 | `sub`:u8, `a`:sel, `data`:bytes[6] | CodeGETWEATER |
| `event_weather` | `72` | size 4 | `sub`:u8, `a`:sel | CodeGETWEATER |
| `event_weather` | `72` | size 6 | `sub`:u8, `a`:sel, `b`:sel | CodeGETWEATER |
| `flag_d9` | `D9` |  | `flag`:u8 |  |
| `frame_delay` | `57` |  | `into`:reg |  |
| `get_flag_b1` | `B1` |  | `data`:bytes[3] |  |
| `get_language` | `9C` |  | `into`:reg |  |
| `get_pos` | `3B` |  | `entity`:ent, `x`:reg, `y`:reg, `z`:reg |  |
| `get_time` | `83` |  | `into`:reg |  |
| `get_yaw` | `3A` |  | `entity`:ent, `into`:reg |  |
| `hud_hide` | `67` |  | `a`:sel, `b`:sel |  |
| `hud_show` | `68` |  |  |  |
| `if_entity_valid` | `44` |  | `entity`:ent |  |
| `load_room` | `75` | size 2 | `sub`:u8 | CodeLOADROOM |
| `load_room` | `75` | size 4 | `sub`:u8, `room`:sel | CodeLOADROOM |
| `lock_player` | `20` |  | `flag`:u8 |  |
| `look_op` | `B6` | size 14 | `sub`:u8, `a`:sel, `data`:bytes[10] |  |
| `look_op` | `B6` | size 16 | `sub`:u8, `a`:sel, `data`:bytes[12] |  |
| `look_op` | `B6` | size 2 | `sub`:u8 |  |
| `look_op` | `B6` | size 20 | `sub`:u8, `a`:sel, `data`:bytes[16] |  |
| `look_op` | `B6` | size 4 | `sub`:u8, `a`:sel |  |
| `look_op` | `B6` | sub 0B | `sub`:u8, `race`:sel, `hair`:sel, `head`:sel, `body`:sel, `hands`:sel, `legs`:sel, `feet`:sel, `main`:sel, `sub2`:sel |  |
| `look_op` | `B6` | sub 0D | `sub`:u8, `a`:sel, `race`:sel, `body`:sel, `head`:sel, `feet`:sel, `hands`:sel |  |
| `look_op` | `B6` | sub 0E | `sub`:u8, `a`:sel, `race`:sel, `body`:sel, `head`:sel, `feet`:sel, `hands`:sel, `legs`:sel |  |
| `look_op` | `B6` | sub 14 | `sub`:u8, `entity`:ent |  |
| `look_op` | `B6` | sub 15 | `sub`:u8, `entity`:ent |  |
| `map_close` | `8A` |  |  |  |
| `map_marker` | `8B` |  | `a`:sel, `b`:sel, `c`:sel, `d`:sel, `name`:name16 | CodeSETEVENTMARK |
| `map_markers` | `A8` |  | `sub`:u8, `a`:sel, `b`:sel |  |
| `map_markers_add` | `B8` |  | `a`:sel, `b`:sel, `c`:sel, `d`:sel, `e`:sel, `name`:name16 |  |
| `map_open` | `89` |  | `map`:sel |  |
| `map_open_params` | `C8` |  | `a`:sel, `b`:sel, `c`:sel |  |
| `map_open_props` | `8D` |  | `a`:sel, `b`:sel |  |
| `misc_ae` | `AE` | size 10 | `sub`:u8, `data`:bytes[8] |  |
| `misc_ae` | `AE` | size 6 | `sub`:u8, `data`:bytes[4] |  |
| `misc_ae` | `AE` | size 8 | `sub`:u8, `data`:bytes[4], `a`:sel |  |
| `mount_op` | `7E` | size 16 | `sub`:u8, `entity`:ent, `a`:sel, `b`:sel, `c`:sel, `d`:sel, `e`:sel | CodeCHOCOBO |
| `mount_op` | `7E` | size 18 | `sub`:u8, `entity`:ent, `a`:sel, `b`:sel, `c`:sel, `d`:sel, `e`:sel, `f`:sel | CodeCHOCOBO |
| `mount_op` | `7E` | size 6 | `sub`:u8, `entity`:ent | CodeCHOCOBO |
| `mount_op` | `7E` | size 8 | `sub`:u8, `entity`:ent, `a`:sel | CodeCHOCOBO |
| `music_op` | `5C` | size 4 | `sub`:u8, `a`:sel |  |
| `music_op` | `5C` | size 6 | `sub`:u8, `a`:sel, `b`:sel |  |
| `music_vol` | `5D` |  | `volume`:sel, `frames`:sel |  |
| `op_ac` | `AC` | size 4 | `sub`:u8, `a`:sel |  |
| `op_ac` | `AC` | size 6 | `sub`:u8, `data`:bytes[4] |  |
| `op_ac` | `AC` | size 8 | `sub`:u8, `data`:bytes[4], `a`:sel |  |
| `op_c2` | `C2` | size 2 | `sub`:u8 |  |
| `op_c2` | `C2` | size 4 | `sub`:u8, `data`:bytes[2] |  |
| `op_c2` | `C2` | size 6 | `sub`:u8, `a`:sel, `data`:bytes[2] |  |
| `op_d8` | `D8` | size 12 | `sub`:u8, `data`:bytes[4], `a`:sel, `b`:sel, `c`:sel |  |
| `op_d8` | `D8` | size 6 | `sub`:u8, `data`:bytes[4] |  |
| `op_d8` | `D8` | size 8 | `sub`:u8, `data`:bytes[4], `a`:sel |  |
| `open_door` | `4C` |  |  |  |
| `play_anim_wait` | `63` |  | `anim`:sel |  |
| `player_task` | `7D` |  | `task`:sel |  |
| `print_to` | `B0` |  | `sub`:u8, `speaker`:ent, `listener`:ent, `text`:msg |  |
| `query_op` | `D4` | size 8 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel |  |
| `rand_mod` | `13` |  | `into`:reg, `value`:sel |  |
| `rank_board` | `B3` | size 14 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel, `d`:sel, `e`:sel, `f`:sel |  |
| `rank_board` | `B3` | size 18 | `sub`:u8, `a`:sel, `b`:sel, `c`:sel, `d`:sel, `e`:sel, `f`:sel, `g`:sel, `h`:sel |  |
| `rank_board` | `B3` | size 2 | `sub`:u8 |  |
| `rank_board` | `B3` | size 4 | `sub`:u8, `a`:sel |  |
| `rect_send_flag` | `9E` |  | `flag`:u8 |  |
| `render_flags0` | `2F` |  | `flag`:u8, `entity`:ent |  |
| `render_flags2` | `7C` |  | `flag`:u8, `entity`:ent |  |
| `render_flags3` | `94` |  | `flag`:u8, `entity`:ent |  |
| `render_flags3_b` | `86` |  | `flag`:u8, `entity`:ent |  |
| `render_sub` | `AB` | size 2 | `sub`:u8 |  |
| `render_sub` | `AB` | size 4 | `sub`:u8, `a`:sel |  |
| `render_sub` | `AB` | size 6 | `sub`:u8, `entity`:ent |  |
| `request` | `27` |  | `a`:u8, `entity`:ent, `b`:u8 |  |
| `request_b` | `28` |  | `a`:u8, `entity`:ent, `b`:u8 | CodeREQSW |
| `request_level` | `2A` |  | `a`:u8, `entity`:ent |  |
| `reset_time` | `78` |  |  |  |
| `sched_5f` | `5F` | sub 00 | `sub`:u8 |  |
| `sched_5f` | `5F` | sub 01 | `sub`:u8 |  |
| `sched_5f` | `5F` | sub 02 | `sub`:u8, `entity`:ent |  |
| `sched_5f` | `5F` | sub 03 | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |  |
| `sched_5f` | `5F` | sub 04 | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |  |
| `sched_5f` | `5F` | sub 05 | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |  |
| `sched_5f` | `5F` | sub 06 | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |  |
| `sched_5f` | `5F` | sub 07 | `sub`:u8, `ent1`:ent, `ent2`:ent, `tag`:tag |  |
| `sched_action` | `AD` |  | `sub`:u8, `a`:sel, `ent1`:ent, `ent2`:ent |  |
| `self_flags0` | `33` |  | `flag`:u8 |  |
| `self_flags01` | `90` |  |  |  |
| `self_flags1` | `74` |  | `flag`:u8 |  |
| `self_flags2` | `61` |  | `flag`:u8 |  |
| `self_flags3_a` | `A4` |  | `flag`:u8 |  |
| `self_flags3_b` | `A5` |  | `flag`:u8 |  |
| `self_flags3_c` | `C0` |  | `value`:sel |  |
| `set_dir` | `39` |  | `dir`:sel |  |
| `set_name` | `B5` |  | `data`:bytes[3] |  |
| `set_pos3` | `36` |  | `x`:sel, `z`:sel, `y`:sel |  |
| `set_speed` | `32` |  | `speed`:sel |  |
| `set_status_event` | `4F` |  | `value`:sel |  |
| `set_time` | `77` |  | `time`:sel, `weather`:sel |  |
| `set_wind` | `97` |  | `base`:sel, `width`:sel |  |
| `set_yaw` | `4B` |  | `entity`:ent, `yaw`:sel |  |
| `shr` | `11` |  | `into`:reg, `value`:sel |  |
| `sin` | `16` |  | `into`:reg, `a`:sel, `b`:sel |  |
| `sound_volume` | `69` |  | `kind`:u8, `volume`:sel |  |
| `sound_volume_ease` | `6A` |  | `a`:sel, `b`:sel, `c`:sel |  |
| `stop_action` | `5E` |  | `tag`:tag |  |
| `stop_action_of` | `6B` |  | `tag`:tag, `entity`:ent |  |
| `stop_talking` | `7B` |  | `entity`:ent |  |
| `subtract` | `08` |  | `into`:reg, `value`:sel |  |
| `table_op` | `9D` | sub 0A | `sub`:u8, `table`:tbl, `n`:u16, `a`:sel, `b`:sel |  |
| `table_op` | `9D` | sub 0B | `sub`:u8, `table`:tbl, `n`:u16, `a`:sel, `b`:sel |  |
| `table_op` | `9D` | sub 0C | `sub`:u8, `table`:tbl, `a`:sel, `b`:sel |  |
| `table_op` | `9D` | sub 0F | `sub`:u8, `table`:tbl, `a`:sel, `b`:sel, `c`:sel |  |
| `table_op` | `9D` | sub 10 | `sub`:u8, `table`:tbl, `n`:u16, `a`:sel, `b`:sel |  |
| `task_62` | `62` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel | CodeLOADEVENTSCHEDULER |
| `task_9f` | `9F` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel | CodeLOADEVENTSCHEDULER2 |
| `task_bb` | `BB` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |  |
| `task_c5` | `C5` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |  |
| `task_cd` | `CD` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |  |
| `task_d0` | `D0` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |  |
| `task_d5` | `D5` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag, `b`:sel |  |
| `transparency` | `6C` |  | `entity`:ent, `a`:sel, `b`:sel | CodeTRANSPAR |
| `ui_op` | `B4` | size 2 | `sub`:u8 |  |
| `ui_op` | `B4` | size 20 | `sub`:u8, `into`:reg, `text`:name16 |  |
| `ui_op` | `B4` | size 3 | `sub`:u8, `value`:u8 |  |
| `ui_op` | `B4` | size 4 | `sub`:u8, `a`:sel |  |
| `ui_op` | `B4` | size 6 | `sub`:u8, `a`:sel, `b`:sel |  |
| `update_pos_sv` | `47` | size 10 | `sub`:u8, `x`:sel, `y`:sel, `z`:sel, `dir`:sel |  |
| `update_pos_sv` | `47` | size 2 | `sub`:u8 |  |
| `vana_time` | `AA` |  | `time`:sel, `year`:reg, `month`:reg, `day`:reg, `weekday`:reg, `hour`:reg, `minute`:reg, `moon`:reg |  |
| `vm_reset` | `7A` | size 6 | `sub`:u8, `entity`:ent |  |
| `vm_reset` | `7A` | sub 00 | `sub`:u8, `entity`:ent |  |
| `vm_reset` | `7A` | sub 01 | `sub`:u8, `entity`:ent, `value`:u8 |  |
| `vm_reset` | `7A` | sub 02 | `sub`:u8, `entity`:ent |  |
| `vm_reset` | `7A` | sub 03 | `sub`:u8 |  |
| `vm_reset` | `7A` | sub 04 | `sub`:u8, `a`:u8, `entity`:ent, `b`:u8 |  |
| `vm_reset` | `7A` | sub 05 | `sub`:u8, `entity`:ent |  |
| `wait_a0` | `A0` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag | CodeWAITLOADSCHEDULER_Main |
| `wait_a2` | `A2` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag | CodeWAITLOADSCHEDULER_Main |
| `wait_anim` | `99` |  | `entity`:ent |  |
| `wait_bc` | `BC` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |  |
| `wait_c6` | `C6` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |  |
| `wait_ce` | `CE` |  | `a`:sel, `ent1`:ent, `ent2`:ent, `tag`:tag |  |
| `wait_music` | `9A` |  |  |  |
| `wait_render` | `76` |  | `entity`:ent |  |
| `wait_self_anim` | `9B` |  |  |  |
| `wait_server_req` | `A7` | size 2 | `sub`:u8 |  |
| `wait_server_req` | `A7` | size 4 | `sub`:u8, `value`:sel |  |
| `wait_zone_load` | `98` |  |  | XiZone::IsReadingExtData |
| `wait_zone_task` | `54` |  | `ent1`:ent, `ent2`:ent, `tag`:tag | CodeWAITMAPSCHEDULOR |
| `worldpass_a` | `87` |  | `sub`:u8 |  |
| `worldpass_b` | `88` |  | `sub`:u8 |  |
| `yield` | `58` |  |  |  |
| `zone_load` | `34` |  | `zone`:sel | XiZone::Open |
| `zone_load2` | `35` |  | `zone`:sel | XiZone::Open (no close) |
| `zone_task` | `2D` |  | `ent1`:ent, `ent2`:ent, `tag`:tag | CodeMAPSCHEDULOR |
| `zone_task_end` | `51` |  | `ent1`:ent, `ent2`:ent, `tag`:tag | CodeENDMAPSCHEDULOR |

Old names still accepted by the compiler: `companion` -> `request_wait`, `fade_color` -> `transparency`.

Entity-state selectors, carried as `{"state": name}` wherever a register is accepted: `event_x` = `7F00`, `event_z` = `7F01`, `event_y` = `7F02`, `event_dir` = `7F03`, `event_job` = `7F06`, `event_race` = `7F07`, `event_level` = `7F08`, `event_server_id` = `7F0A`, `event_flag25` = `7F0B`, `player_x` = `7F80`, `player_z` = `7F81`, `player_y` = `7F82`, `player_dir` = `7F83`, `player_job` = `7F86`, `player_race` = `7F87`, `player_level` = `7F88`, `player_server_id` = `7F8A`, `player_flag25` = `7F8B`.
