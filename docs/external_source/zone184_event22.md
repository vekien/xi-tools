# Event dump — zone 184, event 0x0016 (22)

- **File:** `ROM/20/121.DAT` (95 blocks)
- **Flags:** `B` = sets ExecPointer (branch/jump/yield) · `R` = sets RetFlag (blocks the stack)
- **Decode** mirrors the gameplay module's focus-op decoder (scheduler/camera) plus ImidData refs, actor magic ids, and embedded text.

## Block 0 · actor `0x7FFFFFF0` · event `0xFFFE` (65534) *(wildcard)*

Bytecode offset 1, length 1 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (614)</summary>

`0x0007AE5B` `0x0001770E` `0x00005DD2` `0x00000580` `0x000704E1` `0xFFFE4F84` `0x00000465` `0x00000C55` `0x00000028` `0x000704D5` `0xFFFE6C3E` `0x00000000` `0x000000C8` `0x00002E5D` `0x00002E5E` `0x00000001` `0x000000B8` `0x0000001E` `0x00000009` `0x0007190C` `0xFFFF01BD` `0x00002E5F` `0x000000A0` `0x00002E53` `0x0000007E` `0x00000064` `0x00000013` `0x00029F9C` `0xFFFFA7CE` `0xFFFFB14D` `0x00000806` `0x000000FF` `0x0000000E` `0x000000F3` `0x00000038` `0x0000005A` `0x000001F4` `0x00000096` `0x0000003E` `0x0000003F` `0x00000168` `0x000000DA` `0x0000000A` `0x0000006C` `0x0000000F` `0x00001CCD` `0x00000800` `0x0000003C` `0x000000F0` `0x0000002E` `0x00000002` `0x00000036` `0x00000003` `0x00000048` `0x00000004` `0x00000040` `0x000001A4` `0x0000021C` `0x00000032` `0x00001CCE` `0x00001CCF` `0x00000005` `0x00000010` `0x000000C9` `0x000000B4` `0x00000078` `0x0000021E` `0x00000045` `0x00000D2A` `0x00000049` `0x00000050` `0x0000006E` `0x0000006A` `0x000000E8` `0x00001CD0` `0x00001CD1` `0x0000004D` `0x00001CD2` `0x00001CD3` `0x00001CD4` `0x00001CD5` `0x00001CD6` `0x00000037` `0x00000458` `0x00000011` `0x000000E0` `0x00000069` `0x00001CD7` `0x00001CD8` `0x00001CD9` `0x00001CDA` `0x00001CDB` `0x00001CDC` `0x00001CDD` `0x00001CDE` `0x00001CE0` `0x00001CE1` `0x00001CE2` `0x0000012C` `0x0007533B` `0xFFFF1509` `0x00000C9C` `0x0000007B` `0x000000F4` `0x000000F7` `0x00001CBE` `0x00001CBF` `0x00001CC0` `0x00001CC1` `0x00001CC2` `0x00001CC3` `0x00001CC4` `0x00001CC5` `0x00001CC6` `0x00001CC7` `0x00001CC8` `0x00001CC9` `0x00001CCA` `0x00001CCB` `0x00001CCC` `0x0009BB0D` `0x00004E11` `0x00003E7F` `0x00000FDC` `0x0000000D` `0x0009CB61` `0x00004E03` `0x0009C1DE` `0x00004DF3` `0x000989E8` `0x00004784` `0x00091F6D` `0xFFFFB1ED` `0x00003E80` `0x00092F97` `0xFFFFB1BB` `0x000925D7` `0xFFFFB0FE` `0x000908E2` `0xFFFFAD77` `0x0009BBF0` `0x0000EA61` `0x00000044` `0x0009CBE6` `0x0000EAA2` `0x0009C279` `0x0000E9C2` `0x0009B26C` `0x0000EA8D` `0x000000BA` `0x0000008C` `0x00054D72` `0x00004EB7` `0x000007AF` `0x00055A2F` `0x00004E74` `0x000566A0` `0x00004DED` `0x00054B76` `0x00004DBE` `0x00000FE6` `0x0000001B` `0x00087501` `0xFFFF7E58` `0x00000933` `0x00000A22` `0x000000C2` `0x00001D63` `0x000000C1` `0x00001D64` `0x00001D65` `0x00001D66` `0x00001D68` `0x00001D67` `0x00000030` `0x00001D69` `0x00000019` `0x00001D6A` `0x00001D6B` `0x00001D6C` `0x00001D6D` `0x00001D6E` `0x00001D6F` `0x00001D70` `0x00001D73` `0x00000006` `0x00001D74` `0x00000007` `0x00001D75` `0x00000008` `0x00001D76` `0x00001D77` `0x00001D71` `0x0000000B` `0x00001D72` `0x0000000C` `0x00001D78` `0x00001D79` `0x00001D7A` `0x00000012` `0x000000D2` `0x00001D7B` `0x00001D87` `0x00001D7C` `0x00001D88` `0x00001D7D` `0x00001D89` `0x00001D7E` `0x00001D8A` `0x00001D81` `0x00001D8D` `0x00001D82` `0x00001D8E` `0x00001D83` `0x00001D8F` `0x00001D84` `0x00001D90` `0x00001D85` `0x00001D91` `0x00001D7F` `0x00001D8B` `0x00001D80` `0x00001D8C` `0x00001D86` `0x00001D92` `0x00001D93` `0x00001D94` `0x00001D95` `0x00001D96` `0x00001D97` `0x00001D98` `0x00001D99` `0x00001D9A` `0x00001D9B` `0x00001D9C` `0x000002D8` `0x00001D9D` `0x00001D9E` `0x00001D9F` `0x00000A55` `0x00001DA0` `0x00001DA1` `0x00001DA2` `0x00001DA3` `0x00001DA4` `0x00001DA5` `0x0000026F` `0x00000020` `0x00001DA6` `0x00001DA7` `0x00001DA8` `0x00001DA9` `0x00001DAA` `0x00001DAB` `0x00001DAC` `0x00001DAD` `0x00001DAE` `0x00001DAF` `0x00001DB0` `0x00001DB1` `0x0008C548` `0xFFFF8E9B` `0x00000EFC` `0x00001DB2` `0x00001DBE` `0x00001DB3` `0x00001DBF` `0x00001DB4` `0x00001DC0` `0x00001DB5` `0x00001DC1` `0x00001DB8` `0x00001DC4` `0x00001DB9` `0x00001DC5` `0x00001DBA` `0x00001DC6` `0x00001DBB` `0x00001DC7` `0x00001DBC` `0x00001DC8` `0x00001DB6` `0x00001DC2` `0x00001DB7` `0x00001DC3` `0x00001DBD` `0x00001DC9` `0x0008D4E4` `0xFFFFA67D` `0x00001DCA` `0x00001DCB` `0x00001DCC` `0x00001DCD` `0x00001DCE` `0x00001DCF` `0x00001DD0` `0x00001DD1` `0x00001DD2` `0x00001DD3` `0x00001DD4` `0x00001DD5` `0x00000014` `0x00001DD6` `0x00001DD7` `0x00001DD8` `0x00000031` `0x00001DD9` `0x00001DDA` `0x00001DDB` `0x00001DDC` `0x00001DDD` `0x00001DDE` `0x00001DDF` `0x00001DE0` `0x00001DE3` `0x00001DE4` `0x00001DE5` `0x00001DE6` `0x00001DE7` `0x00001DE1` `0x00001DE2` `0x00001DE8` `0x0008D265` `0xFFFF9ACE` `0x00001DE9` `0x00001DEA` `0x00001DEB` `0x00001DEC` `0x00001DED` `0x00000015` `0x00001DEE` `0x00000034` `0x00001E07` `0x000002D7` `0x00001E08` `0x0000007A` `0x00001E09` `0x00001E0A` `0x00001E0B` `0x00001E0C` `0x00001E0D` `0x00001E0E` `0x00001E0F` `0x00001E10` `0x00001E11` `0x00001E12` `0x00001E13` `0x00001E14` `0x00001E15` `0x00001E16` `0x00001E17` `0x00001E18` `0x00001E19` `0x00001E1A` `0x00001E1C` `0x00001E1D` `0x00001E1E` `0x00000018` `0x00001E1F` `0x00001E1B` `0x00001E20` `0x00001E21` `0x00001E22` `0x00001E23` `0x0000071C` `0x0000072F` `0x00000754` `0x00001E24` `0x00001E25` `0x00001E26` `0x00001E27` `0x00001E28` `0x00001E29` `0x00001E2A` `0x00001E2B` `0x00001E2C` `0x00001E2D` `0x00001E2E` `0x00001E2F` `0x00001E30` `0x00001E31` `0x00001E32` `0x00001E33` `0x00001E34` `0x00001E35` `0x00001E38` `0x00001E39` `0x00001E3A` `0x00001E3B` `0x00001E3C` `0x00001E36` `0x00001E37` `0x00001E3D` `0x00001E3E` `0x00001E3F` `0x0000005F` `0x00000052` `0x00000057` `0x0000005C` `0x00000059` `0x0000005D` `0x00000061` `0x00000060` `0x00000017` `0x0000001F` `0x0000018D` `0x00000175` `0x00000170` `0x00000171` `0x0000014C` `0x000001C1` `0x0000027C` `0x0000027B` `0x0000027D` `0x000002D3` `0x00000321` `0x00000326` `0x000001E1` `0x00000343` `0x00000386` `0x00000388` `0x00000361` `0x00000387` `0x0000038A` `0x00000282` `0x00000283` `0x00000288` `0x00000289` `0x0000028A` `0x0000028B` `0x0000029E` `0x000000C6` `0x000000B5` `0x0000008E` `0x0000014B` `0x00000148` `0x00000166` `0x0000017A` `0x00000182` `0x00000183` `0x00000192` `0x000001DF` `0x000001E9` `0x0000001C` `0x000000E6` `0x000000EB` `0x000000F5` `0x0000007F` `0x000000E9` `0x0000009E` `0x0000009D` `0x000000E2` `0x000000E1` `0x0000014F` `0x0000014E` `0x00000167` `0x000000E5` `0x00000169` `0x0000016A` `0x00000133` `0x00000174` `0x0000018C` `0x00000194` `0x00000195` `0x00000178` `0x00000240` `0x00000197` `0x00000198` `0x000001A3` `0x0000019E` `0x00000246` `0x00000247` `0x00000248` `0x0000024B` `0x0000024A` `0x0000024F` `0x000001C3` `0x000001C4` `0x000001EF` `0x000001F0` `0x0000001A` `0x00000095` `0x0000001D` `0x00000073` `0x00000024` `0x0000005E` `0x00000072` `0x0000003A` `0x00000016` `0x00000035` `0x00000085` `0x00000081` `0x00000023` `0x00000033` `0x00000082` `0x00000039` `0x00000083` `0x000000A2` `0x00000022` `0x00000021` `0x00000065` `0x00000086` `0x00000062` `0x0000008D` `0x0000008A` `0x0000008B` `0x0000002B` `0x00000067` `0x0000002D` `0x000000A1` `0x00000066` `0x00000075` `0x00000068` `0x00000077` `0x000001FE` `0x0000009F` `0x000000A4` `0x000000A9` `0x000000AC` `0x000000B2` `0x000000B0` `0x0000013D` `0x00000108` `0x0000010C` `0x00000100` `0x000000FB` `0x0000011C` `0x00000041` `0x00000042` `0x00000047` `0x00000160` `0x00000043` `0x0000004B` `0x0000004C` `0x0000005B` `0x00000058` `0x00000053` `0x00000054` `0x00000161` `0x0000015C` `0x0000016D` `0x000000C5` `0x000000C3` `0x000000C7` `0x0000015E` `0x000000C4` `0x0000016E` `0x000000D0` `0x000000CD` `0x000000CA` `0x000000D8` `0x000000D5` `0x000000D6` `0x0000013B` `0x00000136` `0x0000013A` `0x00000137` `0x0000016F` `0x00000138` `0x00000139` `0x0000009A` `0x00000091` `0x0000009B` `0x00000099` `0x00000093` `0x00000092` `0x00000098` `0x00000071` `0x000000DD` `0x000000F8` `0x000000F1` `0x00000124` `0x0000012E` `0x00000129` `0x00000122` `0x00000132` `0x00000126` `0x00000155` `0x00000025` `0x00000029` `0x0000007D` `0x00000079` `0x0000007C` `0x00000074` `0x000000EC` `0x0000019C` `0x00000193` `0x000001C6` `0x000001C7` `0x000704D9` `0x0001F146` `0x000003FD` `0xFFFFF9A6` `0xFFFE96B0` `0xFFFF7B95` `0x00000BA9` `0x000000DF` `0xFFFEA820` `0x00000CF7` `0xFFFFFD34` `0xFFFECE42` `0xFFFF7C5C` `0x00000C09` `0x00000027` `0xFFFFFD4E` `0xFFFEF152` `0xFFFFFBD1` `0xFFFEF8E0` `0x00000C84` `0x00001E52` `0x00001E4F` `0x00000046` `0x00070194` `0xFFFF075E`

</details>

## Block 0 · actor `0x7FFFFFF0` · event `0x0016` (22)

Bytecode offset 65, length 1057 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x20` | **SetCliEventUcFlag** | `0x01` | 2 | B |  | Sets the `CliEventUcFlag` flag value. _(This flag is used to lock the player from controlling their character.)_ |
| 2 | `0x46` | **DEFCAMERA** | `0x01` | 2 | B | CameraControl disable | Enables and disables the player camera control. Also disables rendering some menus to allow the game to play cutscenes without unneeded info on screen. |
| 4 | `0x42` | **SetCliEventCancelSetData** |  | 1 | B |  | Sets the `CliEventCancelSetData` flag to 0. If `CliEventCancelSetFlag` is set, then `CliEventCancelFlag` is also set to 0. |
| 5 | `0x45` | **LOADSCHEDULER** | `0x0C` | 17 | B | SchedulerLoad res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='fdo1' | Loads and starts a scheduled task with the given two entities. |
| 22 | `0x55` | **WAITLOADSCHEDULER_Main** | `0x0C` | 15 | BR | SchedulerWait res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='fdo1' | Waits for the Main/Load schedular to finish its current action. |
| 37 | `0x43` | **UsedTellServer** | `0x00` | 2 | BR |  | Used to tell the server the server when the client has updated an event or has completed it. |
| 39 | `0x43` | **UsedTellServer** | `0x01` | 2 | BR |  | Used to tell the server the server when the client has updated an event or has completed it. |
| 41 | `0x03` | **GetsValueStores** | `0x06` | 5 | B |  | Gets a value then stores it. |
| 46 | `0x03` | **GetsValueStores** | `0x07` | 5 | B |  | Gets a value then stores it. |
| 51 | `0x02` | **IF** | `0x05` | 8 | B | goto +230; refs +3:imid[11]=0x00000000 | Handles multiple types of `if` conditional statements. |
| 59 | `0x48` | **ShowMessage** | `0x0D` | 3 | B | refs +1:imid[13]=0x00002E5D | Loads and prints an event message to chat, without a speaker entity. |
| 62 | `0x24` | **QUERY** | `0x0E` | 7 | BR | refs +1:imid[14]=0x00002E5E +3:imid[15]=0x00000001 +5:imid[11]=0x00000000 | Creates a dialog window with selectable options for the player to choose from. |
| 69 | `0x25` | **QUERYWAIT** |  | 1 | BR |  | Waits for a dialog select (created by opcode `0x0024`) to be made by the player. |
| 70 | `0x02` | **IF** | `0x00` | 8 | B | goto +146; refs +3:imid[11]=0x00000000 | Handles multiple types of `if` conditional statements. |
| 78 | `0x01` | **SetExecPointer** | `0xE6` | 3 | B | goto +230 | Directly sets the `ExecPointer` position. |
| 81 | `0x02` | **IF** | `0x00` | 8 | B | goto +230; refs +3:imid[15]=0x00000001 | Handles multiple types of `if` conditional statements. |
| 89 | `0x03` | **GetsValueStores** | `0x01` | 5 | B | refs +3:imid[11]=0x00000000 | Gets a value then stores it. |
| 94 | `0x02` | **IF** | `0x07` | 8 | B | goto +202; refs +3:imid[15]=0x00000001 | Handles multiple types of `if` conditional statements. |
| 102 | `0x8D` | **OpensMapWindow** | `0x10` | 5 | B | refs +1:imid[16]=0x000000B8 +3:imid[15]=0x00000001 | Opens the map window with the given properties. This handler is used mainly when an NPC opens your map but it is not with the sub-menus visible. Mainly to show an overview of the map with no extra bloat on screen or markings on the map. |
| 107 | `0x1C` | **SetsUpdatesDecreases** | `0x11` | 3 | BR | refs +1:imid[17]=0x0000001E | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 110 | `0xB8` | **OpensMapRequested** | `0x10` | 27 | B | refs +1:imid[16]=0x000000B8 +3:imid[15]=0x00000001 +5:imid[18]=0x00000009 +7:imid[19]=0x0007190C +9:imid[20]=0xFFFF01BD; "Tales'Beginning" | Opens the map (if requested), adds and sets markers. |
| 137 | `0x48` | **ShowMessage** | `0x15` | 3 | B | refs +1:imid[21]=0x00002E5F | Loads and prints an event message to chat, without a speaker entity. |
| 140 | `0x23` | **MESWAIT** |  | 1 | BR |  | Waits for the local player to interact with a dialog message. |
| 141 | `0x1C` | **SetsUpdatesDecreases** | `0x16` | 3 | BR | refs +1:imid[22]=0x000000A0 | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 144 | `0x02` | **IF** | `0x07` | 8 | B | goto +218; refs +3:imid[15]=0x00000001 | Handles multiple types of `if` conditional statements. |
| 152 | `0x8A` | **CloseMap** |  | 1 | B |  | Closes the map window. (ie. after being opened via opcode `0x0089`) |
| 153 | `0x1C` | **SetsUpdatesDecreases** | `0x11` | 3 | BR | refs +1:imid[17]=0x0000001E | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 156 | `0x48` | **ShowMessage** | `0x17` | 3 | B | refs +1:imid[23]=0x00002E53 | Loads and prints an event message to chat, without a speaker entity. |
| 159 | `0x01` | **SetExecPointer** | `0x44` | 3 | B | goto +1092 | Directly sets the `ExecPointer` position. |
| 162 | `0x01` | **SetExecPointer** | `0xE6` | 3 | B | goto +230 | Directly sets the `ExecPointer` position. |
| 165 | `0x03` | **GetsValueStores** | `0x01` | 5 | B | refs +3:imid[15]=0x00000001 | Gets a value then stores it. |
| 170 | `0x34` | **LoadEventMap** | `0x18` | 3 | BR | refs +1:imid[24]=0x0000007E | Appears to load and unload an additional zone to be used with the event. |
| 173 | `0x1C` | **SetsUpdatesDecreases** | `0x19` | 3 | BR | refs +1:imid[25]=0x00000064 | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 176 | `0x80` | **LOADWAIT** | `0x22` | 5 | BR | refs +1:imid[290]=0x00001DCB | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 181 | `0x80` | **LOADWAIT** | `0x25` | 5 | BR | refs +1:imid[293]=0x00001DCE | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 186 | `0x80` | **LOADWAIT** | `0x26` | 5 | BR | refs +1:imid[294]=0x00001DCF | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 191 | `0x80` | **LOADWAIT** | `0x27` | 5 | BR | refs +1:imid[295]=0x00001DD0 | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 196 | `0x80` | **LOADWAIT** | `0x28` | 5 | BR | refs +1:imid[296]=0x00001DD1 | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 201 | `0x80` | **LOADWAIT** | `0x29` | 5 | BR | refs +1:imid[297]=0x00001DD2 | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 206 | `0x80` | **LOADWAIT** | `0x2A` | 5 | BR | refs +1:imid[298]=0x00001DD3 | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 211 | `0x80` | **LOADWAIT** | `0x2B` | 5 | BR | refs +1:imid[299]=0x00001DD4 | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 216 | `0x80` | **LOADWAIT** | `0x2C` | 5 | BR | refs +1:imid[300]=0x00001DD5 | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 221 | `0x80` | **LOADWAIT** | `0x2D` | 5 | BR | refs +1:imid[301]=0x00000014 | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 226 | `0x80` | **LOADWAIT** | `0x2E` | 5 | BR | refs +1:imid[302]=0x00001DD6 | Tests the given entity for several conditions. Yields or moves forward depending on the results. _(Appears to be used to check if the entity is loading an action or similar.)_ |
| 231 | `0x38` | **SetCameraMode** | `0x1A` | 3 | B | CameraMode mask=0x0020 | Sets the lower-word of `CliEventModeLocal` to a masked value. |
| 234 | `0x37` | **SetEventPos** | `0x1B` | 9 | B | refs +1:imid[27]=0x00029F9C +3:imid[28]=0xFFFFA7CE +5:imid[29]=0xFFFFB14D +7:imid[30]=0x00000806 | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 243 | `0x97` | **SaveWindBase** | `0x0B` | 5 | B | refs +1:imid[11]=0x00000000 +3:imid[15]=0x00000001 | Saves the current zone `WindBase` and `WindWidth` values then sets new ones. |
| 248 | `0xAB` | **HandlesVariousSub** | `0x09` | 2 | BR |  | Handles various sub-cases; mostly dealing with altering entity render flags. |
| 250 | `0x77` | **LockClock** | `0x1F` | 5 | B | refs +1:imid[31]=0x000000FF +3:imid[32]=0x0000000E | Disables the game clock and sets the client to a specific time for the event. Can also set the weather at the same time. |
| 255 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B | +2:local player | Sets the entities event hide flag within `Render.Flags0`. |
| 261 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 267 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 273 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 279 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 285 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 291 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 297 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 303 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 309 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 315 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 321 | `0x4E` | **SetEntityVisible** | `0x00` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 327 | `0x5C` | **PlayMusic** | `0x00` | 4 | B |  | Handles multiple cases regarding the music player. |
| 331 | `0x5C` | **PlayMusic** | `0x01` | 4 | B |  | Handles multiple cases regarding the music player. |
| 335 | `0x9A` | **YieldsUntilMusic** |  | 1 | BR |  | Yields until the music server is no longer reading data. |
| 336 | `0x45` | **LOADSCHEDULER** | `0x22` | 17 | B | SchedulerLoad res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z00b' | Loads and starts a scheduled task with the given two entities. |
| 353 | `0x45` | **LOADSCHEDULER** | `0x0C` | 17 | B | SchedulerLoad res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='fdi1' | Loads and starts a scheduled task with the given two entities. |
| 370 | `0x55` | **WAITLOADSCHEDULER_Main** | `0x22` | 15 | BR | SchedulerWait res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z00b' | Waits for the Main/Load schedular to finish its current action. |
| 385 | `0x45` | **LOADSCHEDULER** | `0x0C` | 17 | B | SchedulerLoad res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='ovl1' | Loads and starts a scheduled task with the given two entities. |
| 402 | `0x45` | **LOADSCHEDULER** | `0x22` | 17 | B | SchedulerLoad res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z00a' | Loads and starts a scheduled task with the given two entities. |
| 419 | `0x55` | **WAITLOADSCHEDULER_Main** | `0x22` | 15 | BR | SchedulerWait res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z00a' | Waits for the Main/Load schedular to finish its current action. |
| 434 | `0x45` | **LOADSCHEDULER** | `0x0C` | 17 | B | SchedulerLoad res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='ovl1' | Loads and starts a scheduled task with the given two entities. |
| 451 | `0x45` | **LOADSCHEDULER** | `0x22` | 17 | B | SchedulerLoad res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z00c' | Loads and starts a scheduled task with the given two entities. |
| 468 | `0x1C` | **SetsUpdatesDecreases** | `0x23` | 3 | BR | refs +1:imid[35]=0x0000005A | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 471 | `0x5D` | **SetsEasesPlaying** | `0x0B` | 5 | B | refs +1:imid[11]=0x00000000 +3:imid[36]=0x000001F4 | Sets, or eases, the current playing music to a new volume. |
| 476 | `0x1C` | **SetsUpdatesDecreases** | `0x25` | 3 | BR | refs +1:imid[37]=0x00000096 | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 479 | `0x9C` | **StoresClientLanguage** | `0x05` | 3 | B |  | Stores the client language id. |
| 482 | `0x02` | **IF** | `0x05` | 8 | B | goto +561; refs +3:imid[15]=0x00000001 | Handles multiple types of `if` conditional statements. |
| 490 | `0x7D` | **LoadCameraScheduler** | `0x26` | 3 | B | SchedulerLoad res=5174 action='main' | Loads and starts a scheduled task using the local player as the entity. (Appears to be used to display rank up animations.) |
| 493 | `0x01` | **SetExecPointer** | `0x34` | 3 | B | goto +564 | Directly sets the `ExecPointer` position. |
| 496 | `0x7D` | **LoadCameraScheduler** | `0x27` | 3 | B | SchedulerLoad res=5175 action='main' | Loads and starts a scheduled task using the local player as the entity. (Appears to be used to display rank up animations.) |
| 499 | `0x1C` | **SetsUpdatesDecreases** | `0x28` | 3 | BR | refs +1:imid[40]=0x00000168 | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 502 | `0x5C` | **PlayMusic** | `0x00` | 4 | B |  | Handles multiple cases regarding the music player. |
| 506 | `0x5C` | **PlayMusic** | `0x01` | 4 | B |  | Handles multiple cases regarding the music player. |
| 510 | `0x9A` | **YieldsUntilMusic** |  | 1 | BR |  | Yields until the music server is no longer reading data. |
| 511 | `0x27` | **CallFUNC_REQSet** | `0x03` | 7 | BR |  | Calls a helper `FUNC_REQSet` which in turn calls `XiEvent::ReqSet` after checking some conditions. |
| 518 | `0x27` | **CallFUNC_REQSet** | `0x03` | 7 | BR |  | Calls a helper `FUNC_REQSet` which in turn calls `XiEvent::ReqSet` after checking some conditions. |
| 525 | `0x27` | **CallFUNC_REQSet** | `0x03` | 7 | BR |  | Calls a helper `FUNC_REQSet` which in turn calls `XiEvent::ReqSet` after checking some conditions. |
| 532 | `0x27` | **CallFUNC_REQSet** | `0x03` | 7 | BR |  | Calls a helper `FUNC_REQSet` which in turn calls `XiEvent::ReqSet` after checking some conditions. |
| 539 | `0x27` | **CallFUNC_REQSet** | `0x03` | 7 | BR |  | Calls a helper `FUNC_REQSet` which in turn calls `XiEvent::ReqSet` after checking some conditions. |
| 546 | `0x27` | **CallFUNC_REQSet** | `0x03` | 7 | BR |  | Calls a helper `FUNC_REQSet` which in turn calls `XiEvent::ReqSet` after checking some conditions. |
| 553 | `0x1C` | **SetsUpdatesDecreases** | `0x2A` | 3 | BR | refs +1:imid[42]=0x0000000A | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 556 | `0x55` | **WAITLOADSCHEDULER_Main** | `0x22` | 15 | BR | SchedulerWait res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z00c' | Waits for the Main/Load schedular to finish its current action. |
| 571 | `0x45` | **LOADSCHEDULER** | `0x0C` | 17 | B | SchedulerLoad res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='ovl1' | Loads and starts a scheduled task with the given two entities. |
| 588 | `0x45` | **LOADSCHEDULER** | `0x2B` | 17 | B | SchedulerLoad res=30812 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='blon' | Loads and starts a scheduled task with the given two entities. |
| 605 | `0x45` | **LOADSCHEDULER** | `0x22` | 17 | B | SchedulerLoad res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z003' | Loads and starts a scheduled task with the given two entities. |
| 622 | `0x55` | **WAITLOADSCHEDULER_Main** | `0x22` | 15 | BR | SchedulerWait res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z003' | Waits for the Main/Load schedular to finish its current action. |
| 637 | `0x45` | **LOADSCHEDULER** | `0x22` | 17 | B | SchedulerLoad res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z000' | Loads and starts a scheduled task with the given two entities. |
| 654 | `0x45` | **LOADSCHEDULER** | `0x2B` | 17 | B | SchedulerLoad res=30812 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='bash' | Loads and starts a scheduled task with the given two entities. |
| 671 | `0x55` | **WAITLOADSCHEDULER_Main** | `0x22` | 15 | BR | SchedulerWait res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z000' | Waits for the Main/Load schedular to finish its current action. |
| 686 | `0x4B` | **UpdatesEntitiesYaw** | `0x22` | 7 | B | refs +1:imid[290]=0x00001DCB +5:imid[25]=0x00000064 | Updates the given entities yaw direction. |
| 693 | `0x1C` | **SetsUpdatesDecreases** | `0x2C` | 3 | BR | refs +1:imid[44]=0x0000000F | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 696 | `0x45` | **LOADSCHEDULER** | `0x0C` | 17 | B | SchedulerLoad res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='ovl1' | Loads and starts a scheduled task with the given two entities. |
| 713 | `0x45` | **LOADSCHEDULER** | `0x0C` | 17 | B | SchedulerLoad res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='blof' | Loads and starts a scheduled task with the given two entities. |
| 730 | `0x45` | **LOADSCHEDULER** | `0x22` | 17 | B | SchedulerLoad res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z001' | Loads and starts a scheduled task with the given two entities. |
| 747 | `0x55` | **WAITLOADSCHEDULER_Main** | `0x22` | 15 | BR | SchedulerWait res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z001' | Waits for the Main/Load schedular to finish its current action. |
| 762 | `0x6F` | **DelaysEventVM** |  | 1 | BR |  | Delays the event VM execution until `ReqStack[RunPos].WaitTime` has reached 0. Used as a yieldable sleep call. |
| 763 | `0x76` | **ChecksEntitiesRender** | `0x22` | 5 | BR | refs +1:imid[290]=0x00001DCB | Checks the given entities `Render.Flags0` and `Render.Flags3` and yields if successful. |
| 768 | `0x2B` | **ShowMessage** | `0x22` | 7 | B | refs +1:imid[290]=0x00001DCB +5:imid[45]=0x00001CCD | Loads and prints an event message with the given entity as the speaker. |
| 775 | `0x23` | **MESWAIT** |  | 1 | BR |  | Waits for the local player to interact with a dialog message. |
| 776 | `0x4B` | **UpdatesEntitiesYaw** | `0x22` | 7 | B | refs +1:imid[290]=0x00001DCB +5:imid[46]=0x00000800 | Updates the given entities yaw direction. |
| 783 | `0x1C` | **SetsUpdatesDecreases** | `0x2A` | 3 | BR | refs +1:imid[42]=0x0000000A | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 786 | `0x6F` | **DelaysEventVM** |  | 1 | BR |  | Delays the event VM execution until `ReqStack[RunPos].WaitTime` has reached 0. Used as a yieldable sleep call. |
| 787 | `0x76` | **ChecksEntitiesRender** | `0x22` | 5 | BR | refs +1:imid[290]=0x00001DCB | Checks the given entities `Render.Flags0` and `Render.Flags3` and yields if successful. |
| 792 | `0x1C` | **SetsUpdatesDecreases** | `0x2A` | 3 | BR | refs +1:imid[42]=0x0000000A | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 795 | `0x27` | **CallFUNC_REQSet** | `0x03` | 7 | BR |  | Calls a helper `FUNC_REQSet` which in turn calls `XiEvent::ReqSet` after checking some conditions. |
| 802 | `0x1C` | **SetsUpdatesDecreases** | `0x2F` | 3 | BR | refs +1:imid[47]=0x0000003C | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 805 | `0x45` | **LOADSCHEDULER** | `0x22` | 17 | B | SchedulerLoad res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z002' | Loads and starts a scheduled task with the given two entities. |
| 822 | `0x1C` | **SetsUpdatesDecreases** | `0x30` | 3 | BR | refs +1:imid[48]=0x000000F0 | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 825 | `0x02` | **IF** | `0x05` | 8 | B | goto +904; refs +3:imid[15]=0x00000001 | Handles multiple types of `if` conditional statements. |
| 833 | `0x7D` | **LoadCameraScheduler** | `0x31` | 3 | B | SchedulerLoad res=5158 action='main' | Loads and starts a scheduled task using the local player as the entity. (Appears to be used to display rank up animations.) |
| 836 | `0x01` | **SetExecPointer** | `0xB2` | 3 | B | goto +946 | Directly sets the `ExecPointer` position. |
| 839 | `0x02` | **IF** | `0x05` | 8 | B | goto +918; refs +3:imid[50]=0x00000002 | Handles multiple types of `if` conditional statements. |
| 847 | `0x7D` | **LoadCameraScheduler** | `0x33` | 3 | B | SchedulerLoad res=5166 action='main' | Loads and starts a scheduled task using the local player as the entity. (Appears to be used to display rank up animations.) |
| 850 | `0x01` | **SetExecPointer** | `0xB2` | 3 | B | goto +946 | Directly sets the `ExecPointer` position. |
| 853 | `0x02` | **IF** | `0x05` | 8 | B | goto +932; refs +3:imid[52]=0x00000003 | Handles multiple types of `if` conditional statements. |
| 861 | `0x7D` | **LoadCameraScheduler** | `0x35` | 3 | B | SchedulerLoad res=5184 action='main' | Loads and starts a scheduled task using the local player as the entity. (Appears to be used to display rank up animations.) |
| 864 | `0x01` | **SetExecPointer** | `0xB2` | 3 | B | goto +946 | Directly sets the `ExecPointer` position. |
| 867 | `0x02` | **IF** | `0x05` | 8 | B | goto +946; refs +3:imid[54]=0x00000004 | Handles multiple types of `if` conditional statements. |
| 875 | `0x7D` | **LoadCameraScheduler** | `0x37` | 3 | B | SchedulerLoad res=5176 action='main' | Loads and starts a scheduled task using the local player as the entity. (Appears to be used to display rank up animations.) |
| 878 | `0x01` | **SetExecPointer** | `0xB2` | 3 | B | goto +946 | Directly sets the `ExecPointer` position. |
| 881 | `0x1C` | **SetsUpdatesDecreases** | `0x38` | 3 | BR | refs +1:imid[56]=0x000001A4 | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 884 | `0x55` | **WAITLOADSCHEDULER_Main** | `0x22` | 15 | BR | SchedulerWait res=30760 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='z002' | Waits for the Main/Load schedular to finish its current action. |
| 899 | `0x45` | **LOADSCHEDULER** | `0x0C` | 17 | B | SchedulerLoad res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='fdo2' | Loads and starts a scheduled task with the given two entities. |
| 916 | `0x55` | **WAITLOADSCHEDULER_Main** | `0x0C` | 15 | BR | SchedulerWait res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='fdo2' | Waits for the Main/Load schedular to finish its current action. |
| 931 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 937 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 943 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 949 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 955 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 961 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 967 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 973 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 979 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 985 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 991 | `0x4E` | **SetEntityVisible** | `0x01` | 6 | B |  | Sets the entities event hide flag within `Render.Flags0`. |
| 997 | `0x1C` | **SetsUpdatesDecreases** | `0x2C` | 3 | BR | refs +1:imid[44]=0x0000000F | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 1000 | `0xAB` | **HandlesVariousSub** | `0x11` | 4 | BR |  | Handles various sub-cases; mostly dealing with altering entity render flags. |
| 1004 | `0x1C` | **SetsUpdatesDecreases** | `0x0F` | 3 | BR | refs +1:imid[15]=0x00000001 | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 1007 | `0x35` | **LoadEventMap** | `0x10` | 3 | BR | refs +1:imid[16]=0x000000B8 | Similar to opcode `0x0034`. This appears to load an additional zone for the event, however this handler does not have a call to `XiZone::Close`. |
| 1010 | `0x78` | **RestoreClock** |  | 1 | B |  | Enables the game timer and resets the zone weather. |
| 1011 | `0x1C` | **SetsUpdatesDecreases** | `0x2C` | 3 | BR | refs +1:imid[44]=0x0000000F | Sets, or updates (decreases), the current `ReqStack[RunPos].WaitTime` value. |
| 1014 | `0xAB` | **HandlesVariousSub** | `0x0A` | 2 | BR |  | Handles various sub-cases; mostly dealing with altering entity render flags. |
| 1016 | `0x02` | **IF** | `0x06` | 8 | B | goto +1092; refs +3:imid[11]=0x00000000 | Handles multiple types of `if` conditional statements. |
| 1024 | `0x30` | **Setucoff_continue** |  | 1 | B |  | Sets the `ucoff_continue` flag to 0. |
| 1025 | `0x21` | **SetEventExecEnd** |  | 1 | R |  | Sets the `EventExecEnd` flag value to `1`. |
| 1026 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |
| 1027 | `0x5C` | **PlayMusic** | `0x00` | 4 | B |  | Handles multiple cases regarding the music player. |
| 1031 | `0x5C` | **PlayMusic** | `0x01` | 4 | B |  | Handles multiple cases regarding the music player. |
| 1035 | `0x9A` | **YieldsUntilMusic** |  | 1 | BR |  | Yields until the music server is no longer reading data. |
| 1036 | `0x46` | **DEFCAMERA** | `0x00` | 2 | B | CameraControl enable | Enables and disables the player camera control. Also disables rendering some menus to allow the game to play cutscenes without unneeded info on screen. |
| 1038 | `0x45` | **LOADSCHEDULER** | `0x0C` | 17 | B | SchedulerLoad res=30904 actorA=0x7FFFFFF0 (local player) actorB=0x7FFFFFF0 (local player) action='fdi2' | Loads and starts a scheduled task with the given two entities. |
| 1055 | `0x21` | **SetEventExecEnd** |  | 1 | R |  | Sets the `EventExecEnd` flag value to `1`. |
| 1056 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (614)</summary>

`0x0007AE5B` `0x0001770E` `0x00005DD2` `0x00000580` `0x000704E1` `0xFFFE4F84` `0x00000465` `0x00000C55` `0x00000028` `0x000704D5` `0xFFFE6C3E` `0x00000000` `0x000000C8` `0x00002E5D` `0x00002E5E` `0x00000001` `0x000000B8` `0x0000001E` `0x00000009` `0x0007190C` `0xFFFF01BD` `0x00002E5F` `0x000000A0` `0x00002E53` `0x0000007E` `0x00000064` `0x00000013` `0x00029F9C` `0xFFFFA7CE` `0xFFFFB14D` `0x00000806` `0x000000FF` `0x0000000E` `0x000000F3` `0x00000038` `0x0000005A` `0x000001F4` `0x00000096` `0x0000003E` `0x0000003F` `0x00000168` `0x000000DA` `0x0000000A` `0x0000006C` `0x0000000F` `0x00001CCD` `0x00000800` `0x0000003C` `0x000000F0` `0x0000002E` `0x00000002` `0x00000036` `0x00000003` `0x00000048` `0x00000004` `0x00000040` `0x000001A4` `0x0000021C` `0x00000032` `0x00001CCE` `0x00001CCF` `0x00000005` `0x00000010` `0x000000C9` `0x000000B4` `0x00000078` `0x0000021E` `0x00000045` `0x00000D2A` `0x00000049` `0x00000050` `0x0000006E` `0x0000006A` `0x000000E8` `0x00001CD0` `0x00001CD1` `0x0000004D` `0x00001CD2` `0x00001CD3` `0x00001CD4` `0x00001CD5` `0x00001CD6` `0x00000037` `0x00000458` `0x00000011` `0x000000E0` `0x00000069` `0x00001CD7` `0x00001CD8` `0x00001CD9` `0x00001CDA` `0x00001CDB` `0x00001CDC` `0x00001CDD` `0x00001CDE` `0x00001CE0` `0x00001CE1` `0x00001CE2` `0x0000012C` `0x0007533B` `0xFFFF1509` `0x00000C9C` `0x0000007B` `0x000000F4` `0x000000F7` `0x00001CBE` `0x00001CBF` `0x00001CC0` `0x00001CC1` `0x00001CC2` `0x00001CC3` `0x00001CC4` `0x00001CC5` `0x00001CC6` `0x00001CC7` `0x00001CC8` `0x00001CC9` `0x00001CCA` `0x00001CCB` `0x00001CCC` `0x0009BB0D` `0x00004E11` `0x00003E7F` `0x00000FDC` `0x0000000D` `0x0009CB61` `0x00004E03` `0x0009C1DE` `0x00004DF3` `0x000989E8` `0x00004784` `0x00091F6D` `0xFFFFB1ED` `0x00003E80` `0x00092F97` `0xFFFFB1BB` `0x000925D7` `0xFFFFB0FE` `0x000908E2` `0xFFFFAD77` `0x0009BBF0` `0x0000EA61` `0x00000044` `0x0009CBE6` `0x0000EAA2` `0x0009C279` `0x0000E9C2` `0x0009B26C` `0x0000EA8D` `0x000000BA` `0x0000008C` `0x00054D72` `0x00004EB7` `0x000007AF` `0x00055A2F` `0x00004E74` `0x000566A0` `0x00004DED` `0x00054B76` `0x00004DBE` `0x00000FE6` `0x0000001B` `0x00087501` `0xFFFF7E58` `0x00000933` `0x00000A22` `0x000000C2` `0x00001D63` `0x000000C1` `0x00001D64` `0x00001D65` `0x00001D66` `0x00001D68` `0x00001D67` `0x00000030` `0x00001D69` `0x00000019` `0x00001D6A` `0x00001D6B` `0x00001D6C` `0x00001D6D` `0x00001D6E` `0x00001D6F` `0x00001D70` `0x00001D73` `0x00000006` `0x00001D74` `0x00000007` `0x00001D75` `0x00000008` `0x00001D76` `0x00001D77` `0x00001D71` `0x0000000B` `0x00001D72` `0x0000000C` `0x00001D78` `0x00001D79` `0x00001D7A` `0x00000012` `0x000000D2` `0x00001D7B` `0x00001D87` `0x00001D7C` `0x00001D88` `0x00001D7D` `0x00001D89` `0x00001D7E` `0x00001D8A` `0x00001D81` `0x00001D8D` `0x00001D82` `0x00001D8E` `0x00001D83` `0x00001D8F` `0x00001D84` `0x00001D90` `0x00001D85` `0x00001D91` `0x00001D7F` `0x00001D8B` `0x00001D80` `0x00001D8C` `0x00001D86` `0x00001D92` `0x00001D93` `0x00001D94` `0x00001D95` `0x00001D96` `0x00001D97` `0x00001D98` `0x00001D99` `0x00001D9A` `0x00001D9B` `0x00001D9C` `0x000002D8` `0x00001D9D` `0x00001D9E` `0x00001D9F` `0x00000A55` `0x00001DA0` `0x00001DA1` `0x00001DA2` `0x00001DA3` `0x00001DA4` `0x00001DA5` `0x0000026F` `0x00000020` `0x00001DA6` `0x00001DA7` `0x00001DA8` `0x00001DA9` `0x00001DAA` `0x00001DAB` `0x00001DAC` `0x00001DAD` `0x00001DAE` `0x00001DAF` `0x00001DB0` `0x00001DB1` `0x0008C548` `0xFFFF8E9B` `0x00000EFC` `0x00001DB2` `0x00001DBE` `0x00001DB3` `0x00001DBF` `0x00001DB4` `0x00001DC0` `0x00001DB5` `0x00001DC1` `0x00001DB8` `0x00001DC4` `0x00001DB9` `0x00001DC5` `0x00001DBA` `0x00001DC6` `0x00001DBB` `0x00001DC7` `0x00001DBC` `0x00001DC8` `0x00001DB6` `0x00001DC2` `0x00001DB7` `0x00001DC3` `0x00001DBD` `0x00001DC9` `0x0008D4E4` `0xFFFFA67D` `0x00001DCA` `0x00001DCB` `0x00001DCC` `0x00001DCD` `0x00001DCE` `0x00001DCF` `0x00001DD0` `0x00001DD1` `0x00001DD2` `0x00001DD3` `0x00001DD4` `0x00001DD5` `0x00000014` `0x00001DD6` `0x00001DD7` `0x00001DD8` `0x00000031` `0x00001DD9` `0x00001DDA` `0x00001DDB` `0x00001DDC` `0x00001DDD` `0x00001DDE` `0x00001DDF` `0x00001DE0` `0x00001DE3` `0x00001DE4` `0x00001DE5` `0x00001DE6` `0x00001DE7` `0x00001DE1` `0x00001DE2` `0x00001DE8` `0x0008D265` `0xFFFF9ACE` `0x00001DE9` `0x00001DEA` `0x00001DEB` `0x00001DEC` `0x00001DED` `0x00000015` `0x00001DEE` `0x00000034` `0x00001E07` `0x000002D7` `0x00001E08` `0x0000007A` `0x00001E09` `0x00001E0A` `0x00001E0B` `0x00001E0C` `0x00001E0D` `0x00001E0E` `0x00001E0F` `0x00001E10` `0x00001E11` `0x00001E12` `0x00001E13` `0x00001E14` `0x00001E15` `0x00001E16` `0x00001E17` `0x00001E18` `0x00001E19` `0x00001E1A` `0x00001E1C` `0x00001E1D` `0x00001E1E` `0x00000018` `0x00001E1F` `0x00001E1B` `0x00001E20` `0x00001E21` `0x00001E22` `0x00001E23` `0x0000071C` `0x0000072F` `0x00000754` `0x00001E24` `0x00001E25` `0x00001E26` `0x00001E27` `0x00001E28` `0x00001E29` `0x00001E2A` `0x00001E2B` `0x00001E2C` `0x00001E2D` `0x00001E2E` `0x00001E2F` `0x00001E30` `0x00001E31` `0x00001E32` `0x00001E33` `0x00001E34` `0x00001E35` `0x00001E38` `0x00001E39` `0x00001E3A` `0x00001E3B` `0x00001E3C` `0x00001E36` `0x00001E37` `0x00001E3D` `0x00001E3E` `0x00001E3F` `0x0000005F` `0x00000052` `0x00000057` `0x0000005C` `0x00000059` `0x0000005D` `0x00000061` `0x00000060` `0x00000017` `0x0000001F` `0x0000018D` `0x00000175` `0x00000170` `0x00000171` `0x0000014C` `0x000001C1` `0x0000027C` `0x0000027B` `0x0000027D` `0x000002D3` `0x00000321` `0x00000326` `0x000001E1` `0x00000343` `0x00000386` `0x00000388` `0x00000361` `0x00000387` `0x0000038A` `0x00000282` `0x00000283` `0x00000288` `0x00000289` `0x0000028A` `0x0000028B` `0x0000029E` `0x000000C6` `0x000000B5` `0x0000008E` `0x0000014B` `0x00000148` `0x00000166` `0x0000017A` `0x00000182` `0x00000183` `0x00000192` `0x000001DF` `0x000001E9` `0x0000001C` `0x000000E6` `0x000000EB` `0x000000F5` `0x0000007F` `0x000000E9` `0x0000009E` `0x0000009D` `0x000000E2` `0x000000E1` `0x0000014F` `0x0000014E` `0x00000167` `0x000000E5` `0x00000169` `0x0000016A` `0x00000133` `0x00000174` `0x0000018C` `0x00000194` `0x00000195` `0x00000178` `0x00000240` `0x00000197` `0x00000198` `0x000001A3` `0x0000019E` `0x00000246` `0x00000247` `0x00000248` `0x0000024B` `0x0000024A` `0x0000024F` `0x000001C3` `0x000001C4` `0x000001EF` `0x000001F0` `0x0000001A` `0x00000095` `0x0000001D` `0x00000073` `0x00000024` `0x0000005E` `0x00000072` `0x0000003A` `0x00000016` `0x00000035` `0x00000085` `0x00000081` `0x00000023` `0x00000033` `0x00000082` `0x00000039` `0x00000083` `0x000000A2` `0x00000022` `0x00000021` `0x00000065` `0x00000086` `0x00000062` `0x0000008D` `0x0000008A` `0x0000008B` `0x0000002B` `0x00000067` `0x0000002D` `0x000000A1` `0x00000066` `0x00000075` `0x00000068` `0x00000077` `0x000001FE` `0x0000009F` `0x000000A4` `0x000000A9` `0x000000AC` `0x000000B2` `0x000000B0` `0x0000013D` `0x00000108` `0x0000010C` `0x00000100` `0x000000FB` `0x0000011C` `0x00000041` `0x00000042` `0x00000047` `0x00000160` `0x00000043` `0x0000004B` `0x0000004C` `0x0000005B` `0x00000058` `0x00000053` `0x00000054` `0x00000161` `0x0000015C` `0x0000016D` `0x000000C5` `0x000000C3` `0x000000C7` `0x0000015E` `0x000000C4` `0x0000016E` `0x000000D0` `0x000000CD` `0x000000CA` `0x000000D8` `0x000000D5` `0x000000D6` `0x0000013B` `0x00000136` `0x0000013A` `0x00000137` `0x0000016F` `0x00000138` `0x00000139` `0x0000009A` `0x00000091` `0x0000009B` `0x00000099` `0x00000093` `0x00000092` `0x00000098` `0x00000071` `0x000000DD` `0x000000F8` `0x000000F1` `0x00000124` `0x0000012E` `0x00000129` `0x00000122` `0x00000132` `0x00000126` `0x00000155` `0x00000025` `0x00000029` `0x0000007D` `0x00000079` `0x0000007C` `0x00000074` `0x000000EC` `0x0000019C` `0x00000193` `0x000001C6` `0x000001C7` `0x000704D9` `0x0001F146` `0x000003FD` `0xFFFFF9A6` `0xFFFE96B0` `0xFFFF7B95` `0x00000BA9` `0x000000DF` `0xFFFEA820` `0x00000CF7` `0xFFFFFD34` `0xFFFECE42` `0xFFFF7C5C` `0x00000C09` `0x00000027` `0xFFFFFD4E` `0xFFFEF152` `0xFFFFFBD1` `0xFFFEF8E0` `0x00000C84` `0x00001E52` `0x00001E4F` `0x00000046` `0x00070194` `0xFFFF075E`

</details>

## Block 15 · actor `0x010B8122` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x00027153 +3:imid[1]=0xFFFFAFD3 +5:imid[2]=0xFFFFB2E3 +7:imid[3]=0x00000826 | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (44)</summary>

`0x00027153` `0xFFFFAFD3` `0xFFFFB2E3` `0x00000826` `0x00000028` `0x00023E55` `0xFFFFB97C` `0xFFFFB168` `0x0001360E` `0x00004DBB` `0xFFFFB5C2` `0x00070BD3` `0x0001F8D2` `0x00000000` `0x0000079F` `0x0000000D` `0x0006FBDB` `0x0001F89C` `0x0006FFD0` `0x000214D3` `0x00000078` `0x0006FA63` `0x00020660` `0x00000009` `0x00000C16` `0x000707FA` `0x0002087D` `0x00000463` `0x000704F3` `0x0001F11D` `0x0007045A` `0x0001E33D` `0x000702A6` `0x00019942` `0x00000DAB` `0xFFFFFFE8` `0xFFFE3914` `0xFFFF7554` `0x00000BFD` `0xFFFFFFDC` `0xFFFE4961` `0xFFFFFDD7` `0xFFFE8E3A` `0xFFFF7B95`

</details>

## Block 18 · actor `0x010B8125` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x00028551 +3:imid[1]=0xFFFFB72A +5:imid[2]=0xFFFFB2A2 +7:imid[3]=0x00000727 | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (31)</summary>

`0x00028551` `0xFFFFB72A` `0xFFFFB2A2` `0x00000727` `0x00000028` `0x000251AA` `0xFFFFBB53` `0xFFFFB120` `0x00022068` `0xFFFFCBC1` `0xFFFFAEE2` `0x0001A9FE` `0x00002861` `0xFFFFB1E1` `0x00071477` `0x00020464` `0x0000001B` `0x000008E6` `0x0000001D` `0x00070A0C` `0x0001F618` `0x00000000` `0x00070862` `0x0001BCB7` `0x000005DB` `0x00000393` `0xFFFE2F14` `0xFFFF7554` `0x00000C04` `0x000007AB` `0xFFFE7286`

</details>

## Block 19 · actor `0x010B8129` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x00028398 +3:imid[1]=0xFFFFB317 +5:imid[2]=0xFFFFB2F3 +7:imid[3]=0x0000080B | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (27)</summary>

`0x00028398` `0xFFFFB317` `0xFFFFB2F3` `0x0000080B` `0x0000000A` `0x00000028` `0x00025670` `0xFFFFB53A` `0xFFFFB2BF` `0x0001FA50` `0xFFFFDC44` `0xFFFFB1AA` `0x00012500` `0x0000651C` `0xFFFFB3A1` `0x0006F7B2` `0x0001F7B5` `0x00000000` `0x0000020F` `0xFFFFF882` `0xFFFE3D7F` `0xFFFF7554` `0x00000B69` `0x0000000D` `0x00000014` `0xFFFFF80F` `0xFFFE69C0`

</details>

## Block 20 · actor `0x010B8127` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x00028551 +3:imid[1]=0xFFFFA74D +5:imid[2]=0xFFFFB23F +7:imid[3]=0x000007A4 | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (40)</summary>

`0x00028551` `0xFFFFA74D` `0xFFFFB23F` `0x000007A4` `0x00000028` `0x00024E08` `0xFFFFACDF` `0xFFFFB1E9` `0x0001EEF5` `0xFFFFE238` `0xFFFFB1B1` `0x00012EE3` `0x00005E61` `0xFFFFB4ED` `0x0006F4A2` `0x0002003D` `0x00000013` `0x00000F73` `0x0006FB8A` `0x000205E3` `0x00000005` `0x000002DA` `0x0000001D` `0x000701F2` `0x0001FC0D` `0x00000000` `0x0007045A` `0x0001E33D` `0x000702A6` `0x00019942` `0x00000DAB` `0xFFFFFC64` `0xFFFE363A` `0xFFFF7554` `0x00000BDC` `0xFFFFF88D` `0xFFFE48A8` `0x0000089E` `0xFFFFFC8F` `0xFFFE6DBF`

</details>

## Block 21 · actor `0x010B8126` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x0002910E +3:imid[1]=0xFFFFAC42 +5:imid[2]=0xFFFFB29F +7:imid[3]=0x00000F4F | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (27)</summary>

`0x0002910E` `0xFFFFAC42` `0xFFFFB29F` `0x00000F4F` `0x000713DC` `0x0001FDA5` `0x00000007` `0x00000877` `0x00070919` `0x0001F191` `0x00000000` `0x0000043D` `0x00000028` `0x00070AF7` `0x00019C23` `0x00000CB1` `0xFFFFFB50` `0xFFFE1B68` `0xFFFF6F45` `0x00000B77` `0x0000003C` `0x000007AD` `0xFFFE4A5B` `0xFFFF7554` `0x0000000B` `0x00000639` `0xFFFE67C3`

</details>

## Block 22 · actor `0x010B8128` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x00028D58 +3:imid[1]=0xFFFFB4BF +5:imid[2]=0xFFFFB2D2 +7:imid[3]=0x00000010 | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (4)</summary>

`0x00028D58` `0xFFFFB4BF` `0xFFFFB2D2` `0x00000010`

</details>

## Block 23 · actor `0x010B812A` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x00029CFE +3:imid[1]=0xFFFFADCF +5:imid[2]=0xFFFFB293 +7:imid[3]=0x000007DC | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (4)</summary>

`0x00029CFE` `0xFFFFADCF` `0xFFFFB293` `0x000007DC`

</details>

## Block 24 · actor `0x010B812B` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x0002DE82 +3:imid[1]=0xFFFFAC9C +5:imid[2]=0xFFFFAC67 +7:imid[3]=0x00000806 | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (8)</summary>

`0x0002DE82` `0xFFFFAC9C` `0xFFFFAC67` `0x00000806` `0x00000028` `0x0002B2BA` `0xFFFFAA98` `0xFFFFB1AA`

</details>

## Block 25 · actor `0x010B812C` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x0002E0E5 +3:imid[1]=0xFFFFB1A4 +5:imid[2]=0xFFFFAC48 +7:imid[3]=0x00000866 | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (11)</summary>

`0x0002E0E5` `0xFFFFB1A4` `0xFFFFAC48` `0x00000866` `0x0000000D` `0x0002BE4B` `0xFFFFB4D0` `0xFFFFB006` `0x0002A928` `0xFFFFB458` `0xFFFFB2DB`

</details>

## Block 26 · actor `0x010B812D` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x0002B9C3 +3:imid[1]=0xFFFFB862 +5:imid[2]=0xFFFFB0ED +7:imid[3]=0x0000081F | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (4)</summary>

`0x0002B9C3` `0xFFFFB862` `0xFFFFB0ED` `0x0000081F`

</details>

## Block 27 · actor `0x010B812E` · event `0x0016` (22)

Bytecode offset 13, length 10 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x37` | **SetEventPos** | `0x00` | 9 | B | refs +1:imid[0]=0x0002CCEC +3:imid[1]=0xFFFFB8D2 +5:imid[2]=0xFFFFADAA +7:imid[3]=0x000008B3 | Updates the current `ExtData[1]->EventPos` and `ExtData[1]->EventDir[1]` information, calibrates the current event entity position then calls `XiAtelBuff::CopyAllPosEvent` and `XiAtelBuff::ReqExecHitCheck`. |
| 9 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (8)</summary>

`0x0002CCEC` `0xFFFFB8D2` `0xFFFFADAA` `0x000008B3` `0x0000000D` `0x0002BB91` `0xFFFFB7C5` `0xFFFFB092`

</details>

## Block 41 · actor `0x010B8148` · event `0xFFFE` (65534) *(wildcard)*

Bytecode offset 1, length 1 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (141)</summary>

`0x00001D55` `0x00000000` `0x00000001` `0x0000005A` `0x00002FFE` `0xFFFE45BD` `0xFFFF76E3` `0x00000AF5` `0x00000002` `0x0000009A` `0x00001D57` `0x00000003` `0x00001D56` `0x00000013` `0x0000021C` `0x0000000C` `0x0000000F` `0x0000001E` `0x00001CF6` `0x00001CF7` `0x00001CF8` `0x000000E7` `0x00001CF9` `0x00001CFA` `0x000001AB` `0x00001CFB` `0x000001D1` `0x00001CFC` `0x00001CFD` `0x0000007F` `0x00001CFE` `0x00001CFF` `0x00001D00` `0x000001D2` `0x00001D01` `0x00001D02` `0x00001D03` `0x00000100` `0x00001D04` `0x00001D05` `0x000001A9` `0x00001D06` `0x00001D07` `0x00001D08` `0x00000C00` `0x00001D09` `0x00001D0A` `0x00001D0B` `0x00001D0C` `0x00001D0D` `0x00001D0E` `0x00001D0F` `0x00001D10` `0x00000009` `0x00001D11` `0x00001D12` `0x00000078` `0x00001D14` `0x00001D15` `0x00001D16` `0x00001D17` `0x00001D18` `0x00001D19` `0x00001D1A` `0x00001D1B` `0x00001D1C` `0x00001D1D` `0x00001D1E` `0x00001D1F` `0x0000021D` `0x000001CC` `0x0000009B` `0x000000B3` `0x00001D20` `0x00001D21` `0x0000003C` `0x00001D22` `0x00001D23` `0x00000016` `0x00001D24` `0x00001D25` `0x00001D26` `0x00001D27` `0x00001D28` `0x00001D29` `0x0000002D` `0x00001D2A` `0x00001D2B` `0x00001D2C` `0x00001D2D` `0x00001D2E` `0x00000400` `0x00001D2F` `0x00001D30` `0x00001D31` `0x00001D32` `0x00001D33` `0x00001D34` `0x00001D35` `0x00001D36` `0x00001D37` `0x00000283` `0x00001D38` `0x00001D39` `0x00001D3A` `0x00001D3B` `0x00001D3C` `0x00000280` `0x00001D3D` `0x00001D3E` `0x00001D3F` `0x00001D40` `0x00001D41` `0x00001D42` `0x00001D43` `0x00001D44` `0x00000099` `0x0000012C` `0x00000258` `0x00000281` `0x00001D45` `0x00001D46` `0x00001D47` `0x000000F3` `0x00001D13` `0x00001D48` `0x00001D49` `0x00001D4A` `0x00001D4B` `0x00001D4C` `0x00001D4D` `0x00001D4E` `0x00001D4F` `0x00001D50` `0x00001D51` `0x000000B8` `0x00000073` `0x00000066` `0x000000C8` `0x000000C9` `0x000000D7`

</details>

## Block 42 · actor `0x010B8149` · event `0xFFFE` (65534) *(wildcard)*

Bytecode offset 1, length 1 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (16)</summary>

`0x00000080` `0x0000005A` `0x0000002D` `0x0000009A` `0x00000000` `0x000001AC` `0x0000003C` `0x0000012C` `0x0000001E` `0x00000003` `0x000001D1` `0x000000C8` `0x000000C9` `0x00000001` `0x00000002` `0x000000D7`

</details>

## Block 43 · actor `0x010B814A` · event `0xFFFE` (65534) *(wildcard)*

Bytecode offset 1, length 1 bytes.

| Offset | Op | Name | Sub | Size | Flags | Decode | Description |
|---|---|---|---|---|---|---|---|
| 0 | `0x00` | **EndReqStack** |  | 1 | R |  | Ends the current `ReqStack` execution; resetting it back to defaults. |

<details><summary>Immediate-data table (2)</summary>

`0x0000002D` `0x000001D1`

</details>

