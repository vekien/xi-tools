# FFXI main menu (menuwind + socialme) RE

FFXiMain.dll, the CatsEyeXI build (2025-12-26). All VAs assume base 0x10000000. This was static only: a dump of the
unpacked FFXiMain.dll, disassembled with capstone, plus the PC Ghidra export,
the PS2 decompile (CTkMenuWind / CTkSocial), xiclient (CTkMenuWind.cpp, PrimMng.cpp, StringManager) and the live client's
DATs: menu DAT ROM\119\51.DAT, menu texts ROM\165\75.DAT (table 0xE, help lines) and ROM\165\76.DAT (table 0xD, titles).
Every address and byte pattern below was checked against the dump. Each signature matches exactly once unless stated.

Class names: menuwind's handler = **CTkMenuWind** (PS2 TkMenuWind.cpp). socialme's handler = **CTkSocial** (PS2 TkSocial.cpp).

## 0. Shared shape (handler objects, events)

- Menu table entries (name[16], 16 zero bytes, +0x20 the handler global, +0x24 flags = 2 "handler handles cancel",
  +0x28 bytes 01 02 81 = area 0x81):
  - "menu    menuwind" at 0x10370268, global **0x105760CC**
  - "menu    socialme" at 0x10370294, global **0x105762E8**
- Handler object: +0 vtable (17 slots, same layout as configmenu's Config handler), +8 its window (0 while none),
  +0xC byte 1, +0xE short -1. The objects are made at login by FUN_10163640 (the function that makes all menu handlers)
  and freed at logout by FUN_101652d0.
- Vtable slots: 0 = destructor, 1 = opened (OnInitialUpdate; CreateDrawMenu calls it each time the window opens or is
  reused), 4 = draw (each frame), 6 = input (`__thiscall(handler, short event, short item)`, `ret 8`),
  9 = OnActive, 10 = OnDrawCalc(char).
- Window key input FUN_10117e60 (thiscall window, key): the window acts first. Keys 1..4 move the cursor along the
  layout's links (up, down, right, left). Key 5 is select: a status-5 (disabled) item beeps and stops there; otherwise
  the item's status is set to 1. Key 6 is cancel: the window closes itself unless its flags+0x34 have bit 2, which
  menuwind and socialme do have. After that, the window hands (key, cursor item) to owner vtable+0x18 (slot 6), but only
  if the window isn't closing and its layout is still registered (0x1015ce20).
- Events the handlers see: 1 down, 2 up, 3 right, 4 left, 5 select, 6 cancel, 0x1C = the main-menu key pressed again
  while a menu is up (*best reading, confirm live*). When nothing is open, the manager's key dispatch 0x1015ca30 opens
  the main menu on key 7 (0x1015db70 -> CreateDrawMenu("menu    menuwind")).
- Button status (SetButtonStatus **0x10118760**, `__thiscall(window, int status, int item)`, `ret 8`; it does nothing
  when the item isn't in the window): 5 Disabled, 0xB normal, 0xF arrow look (items 13 and 14), 0x10 Inactive (title
  tab, item 15). Matches xiclient's InteractionState. GetButtonStatus is 0x101187c0(window, item).
- CreateDrawMenu **0x1015d080** = `__thiscall(mgr 0x105ECB98, const char name[16], char show, char flag)`.
  DestroyDrawMenu **0x1015d4c0** = `__thiscall(mgr, window, 1)`. The click sound is 0x100379a0, the beep 0x10037a60 and
  the cancel sound 0x10037a00. CommandCalc **0x10080bd0**(text, 0) runs a typed command.

## 1. menuwind (page 1): CTkMenuWind

- Constructor **0x1013F720** (new 0x28). It loads three shapes from sheet "menu    windowps" into +0x18/+0x1C/+0x20,
  which are item 12's Map / Mog House / Monstrosity looks.
- Vtable **0x10331FF0**: [0] 0x1013F780 dtor, [1] **0x1013FBD0 opened**, [2] 0x1013FDA0, [4] 0x1013FAD0 draw (draws
  item 12's extra shape at item 12's rect), [6] **0x1013FF40 input**, [9] 0x1013FEB0 OnActive, [10] 0x1013FDB0
  OnDrawCalc (each frame it re-checks items 12, 6 and 10 and sets their status). The rest are base stubs
  (0x1012a6xx/0x1012a7xx).
- Input 0x1013FF40 has two jump tables. First the event: a byte table at **0x10140238** indexed by event-3, which picks
  from a 4-entry jump table at 0x10140228:
  - events 3, 4, 0x1C -> 0x1013FF63 = **page flip**
  - event 5 -> 0x1013FFBB = select
  - event 6 -> 0x10140213 = DestroyDrawMenu(own window)
  - anything else -> return
- Select uses `dec eax; cmp eax,0Eh; jmp [eax*4+0x10140254]`, so items 1..15 go through a jump table. Visual order is
  by the layout's y. Labels and help lines come from the text DATs.

| id | y | label (title / help) | action |
|---|---|---|---|
| 1 | 6 | Status | Monstrosity ? [0x10576140]->0x10144850 : persona+0x1C=1, open "menu    persona " |
| 2 | 22 | Equipment | Monstrosity ? 0x101416e0 : PUP main/sub (0x12) with flag ? open "menu    pupequip" : [0x10576038]->0x10132c70 (equip window) |
| 3 | 38 | Magic List | open "menu    mgcmenu " |
| 4 | 54 | Items | 0x1021ae60 on [0x1063218c](0,1); 0x101f0b00 (item window) |
| 10 | 70 | Synthesis | open "menu    cmbmenu " |
| 5 | 86 | Abilities | Monstrosity ? open "menu    mp_mnabi" : open "menu    abimenu " |
| 6 | 102 | Party | 0x101ffe00 (OpenPartyMenu) |
| 7 | 118 | Trade | 0x1013f9b0 (OpenTrade); beep on fail |
| 8 | 134 | Search | 0x10208d20 on [0x1062f7f4](0) (InitInfo); open "menu    searchma" |
| 9 | 150 | Linkshell | 0x101caab0(1,1) ? beep : 0x101e2bd0 on [0x1062e880] |
| 11 | 166 | Region Info | open "menu    region  " |
| 12 | 182 | Map (Mog House in the Mog House) | m_idx (+0x14)==1 && 0x100f0840() ? open "menu    myroom  " : 0x101f5f30(0) (map) |
| 13, 14 | — | page arrows (comwin #31 and #32, beside the frame) | input(this, 4, item): a flip |
| 15 | — | title tab ("comwin" #16) | input(this, 4, item) (it can't be reached) |

  Monstrosity check = **0x10097B60** (zone+0x40D46 != 0, or main job 0x17).
- Opened 0x1013FBD0 closes itself during an event or when the player is dead. It sets item 12's title/help (Map vs Mog
  House) and m_idx. In Monstrosity it changes item 2 (shape windowps #644, texts 0x2BC/0xA5) and disables items 6 and
  10. It may disable items 6 and 10 in other cases too. Then it calls SetButtonStatus(0x10, 15), (0xF, 14), (0xF, 13),
  sets window+0x86 = 0 (the window plays no confirm sound; the handler plays it) and plays the open sound.
  **It refers to items by id. Page 1 must keep ids 1..12, and the title must stay 15.**

## 2. socialme (page 2): CTkSocial

- Made in FUN_10163640 at 0x1016402E (new 0x14, vtable stored inline). Vtable **0x10334290**: [0] 0x10164D20 dtor,
  [1] **0x1014D470 opened**, [4] 0x1014D460 (`ret`), [6] **0x1014D500 input**, [9] 0x1014D4C0 OnActive,
  [10] 0x1014D4F0 (`ret 4`).
- Opened 0x1014D470: SetButtonStatus(0x10, 15), (0xF, 14), (0xF, 13); window+0x86 = 0; open sound; then
  `if (Monstrosity) SetButtonStatus(5, 5)`, which disables Set Bazaar.
- Input 0x1014D500 has the same shape. The event byte table at **0x1014D7BC** picks from the jump table at 0x1014D7AC:
  - events 3, 4, 0x1C -> 0x1014D523 = flip back to menuwind
  - event 5 -> 0x1014D580: returns at once if the zone global [0x104DDC28] == 0, then `jmp [eax*4+0x1014D7D8]`
  - event 6 -> 0x1014D72C: CreateDrawMenu(menuwind, 0, 0), copy the cursor over, close menuwind by name
    (0x1015d640), close itself, cancel sound. Pressing cancel on page 2 closes both pages.
- Select items:

| id | y | label (title / help) | action |
|---|---|---|---|
| 1 | 6 | Missions | open "menu    missionm" |
| 2 | 22 | Quests | CommandCalc("/quest") |
| 3 | 38 | Key Items | CommandCalc("/keyitem") |
| 4 | 54 | View House ("View contents of Mog House, Mog Case, and other such storage.") | 0x101ca7d0(0) (player flag 0x100) ? (zone checks 0x100f9270 / 0x100f91f0: beep, or message 0xBC + beep) : open "menu    mogcont " |
| 5 | 70 | Set Bazaar | open "menu    bazaar  " (disabled in Monstrosity) |
| 6 | 86 | MacroPalette | open "menu    mcrmenu " |
| 7 | 102 | Config | open "menu    configwi" (configmenu.cpp's list) |
| 8 | 118 | Help Desk | 0x10215bd0 |
| 10 | 134 | Current Time | CommandCalc("/clock") |
| 9 | 150 | Communication (Friend List, Emote List) | open "menu    commenu " |
| 11 | 166 | Shut Down | 0x1015f690 (yes/no "Shut down?") |
| 12 | 182 | Log Out | 0x1015f720 (yes/no "Log out?") |
| 13-15 | — | arrows and title | input(this, 4, item): a flip |

- **For items 1..12 the select code never touches `this` (esi).** Each case calls only globals: the manager, statics
  and command strings. So socialme's input can be called with the socialme handler object while its window is closed
  (+8 == 0), from any page, and every action runs exactly as the game runs it, sounds included.

## 3. Page flipping

- menuwind -> socialme: at **0x1013FF63** in 0x1013FF40.
  1. CreateDrawMenu("menu    socialme", 1, 0).
  2. If both windows exist, SetCursol(social window, map(cursor)) via **0x101189B0**. The map function is 0x1013FEE0,
     which reads a byte-pair table at 0x1036DFA0; 0x1013FF10 is the reverse.
  3. DestroyDrawMenu(own window).
- socialme -> menuwind: the same at **0x1014D523** in 0x1014D500.
- Both are reached only from these places:
  - Events 3 (Right), 4 (Left) and 0x1C (the menu key again). The byte table entries [0], [1] and [0x19] at
    0x10140238 / 0x1014D7BC are 0, the flip case.
  - Select on items 13..15. Those cases call `[vtable+0x18](this, 4, item)`, which re-enters slot 6 as event 4.
  - Event 7 (the PS2 also flipped on it) goes to "nothing" on PC.
- Nothing else opens socialme by name except:
  - FUN_10232420 (and FUN_10203d80 for menuwind). These are tasks that close the main-menu pages while they run and
    reopen whichever was open. In single-menu mode socialme is never open, so they only reopen menuwind.
  - 0x1014D820, a bare "open socialme" helper with no callers or pointers found.
- **Disabling the flip.** No patch is needed. With menuwind's handler on a copied table, the new slot 6 returns early
  for events 3, 4 and 0x1C, and for select on 13..15. The window has already done its part: Right/Left links in the DAT
  point to the item itself, so the cursor doesn't move.
  - Patch alternative, if ever wanted: in the event byte table at 0x10140238 (operand at input+0x18), change
    entries [0], [1] and [0x19] from 00 to 03. That is a data write in .text, so not preferred.

## 4. Layouts (ROM\119\51.DAT, chunk type 0x30)

Both payloads have the same header: name, 01, **0x0F blocks after the window**. menuwind is 0x450 bytes and socialme
0x430.

- **Window block** (0x50):
  - x 384, y 48, 112 x **202**, anchor 1, 2 sprites
  - sprite slot 0 = "menu    comwin  " **#11** (the frame with 12 wells); slot 6 = "anc     anc_s   " #0
  - strings "-1", "-1"
- **Items 1..12**:
  - each 88 x 16 at x 16, y 6 + 16·k (k = visual row), a "menu    windowps" sprite
  - strings = (help number in table 0xE, title number in table 0xD)
  - links (before, after, up, down, right, left): a wrapped vertical loop in visual order; right and left = itself
  - menuwind's visual order: 1, 2, 3, 4, **10**, 5, 6, 7, 8, 9, 11, 12. Item 6 has a second sprite (windowps #765,
    slot 4) and item 10 one too (#764 at slot 4). Those blocks are 0x50.
  - socialme's visual order: 1..8, **10**, 9, 11, 12, all 0x40 blocks
  - page 1 sprites: windowps #13, 15, 17, 19, 21 (+765), 20, 28, 18, 16, 680 (+764), 25, 23
  - page 2 sprites: #24, 26, 22, 31, 30, 29, 27, 36, 766, 34, 35, 33
  - Both pages draw from the **same sheet**, so page 2's blocks drop into page 1's layout unchanged except for id, y
    and links.
- Item 13: x -16, y 7, 15 x 9, comwin #31 (left arrow). Item 14: x 113, y 7, comwin #32 (right arrow). Their links point
  only to themselves and item 1.
- Item 15: the title tab, comwin #16, 73 x 9, all links 0xFF.
- **The frame:** "menu    comwin  " sprite k = a panel with k+1 wells, but **only up to #14 (15 wells, 250 tall)**.
  Sprites 15 and up are other parts. A 24-item column needs a frame of our own. That is easy, because a frame sprite is:
  - part 0: the background quad 0,0 to 112,H, wh (112, H), uv (0, 0), byte24 = 0, colours
    7f7f7f7f 7f7f7f7f 40404040 40404040 (top light, bottom dim), blend 01 00 01 01, image "menu    newtex  "
    (newtex is drawn at up to 640 x 480 elsewhere, so H = 394 should be fine)
  - then for each well i: right half (66, 8+16i)-(106, 24+16i), wh 40x16, uv (0, 16); left half (18, 8+16i)-(66, 24+16i),
    wh 48x16, uv (16, 0). Both colour 00000070 x4, blend 01000101, image "menu    buttonto".
  - H = 16·wells + 10 (202 for 12, 250 for 15), so **394 for 24**.
- The parse (layout add 0x1015ccb0 -> 0x1011ac10) allocates elements by the header's count byte (`new(count*20+4)`).
  No hard cap was seen, so 25 blocks should be accepted (confirm live). The merged payload is about 0x6B0 bytes, well
  under configmenu's kPayloadMax of 0x1000.

## 5. Recommended design (singlemenu.cpp, configmenu.cpp's way)

Add `[client] singlemenu` (default 0). Factor configmenu's list, layout and sheet helpers into a shared header
(find / payload_of / in_use / layout add-remove / sheet add-remove), or copy them.

### A. Finding things (all by signature, verified as configmenu does)

- Menu table entries, 32 bytes each (name + 16 zeros); the handler global is at +0x20:
  - `6D 65 6E 75 20 20 20 20 6D 65 6E 75 77 69 6E 64` + 16× `00` -> 0x10370268 -> 0x105760CC
  - `6D 65 6E 75 20 20 20 20 73 6F 63 69 61 6C 6D 65` + 16× `00` -> 0x10370294 -> 0x105762E8
- menuwind ctor (table at **+0x28**): unique at 0x1013F720
  `56 8B F1 8D 4E 04 E8 ?? ?? ?? ?? 68 ?? ?? ?? ?? B9 ?? ?? ?? ?? 66 C7 46 0E FF FF C7 46 08 00 00 00 00 C6 46 0C 01 C7 06 ?? ?? ?? ?? C7 46 14 00 00 00 00 E8`
- menuwind input (slot 6): unique at 0x1013FF40
  `0F BF 44 24 04 83 C0 FD 56 83 F8 19 8B F1 0F 87 ?? ?? ?? ?? 33 C9 8A 88 ?? ?? ?? ?? FF 24 8D ?? ?? ?? ?? 57 6A 00 6A 01 68 ?? ?? ?? ?? B9 ?? ?? ?? ?? E8`
  Verify: the operand at +0x29 points to "menu    socialme" and +0x2E == the manager. (Without the 57 at +0x23, the
  first 0x23 bytes match both inputs.)
- menuwind opened (slot 1): unique at 0x1013FBD0
  `66 A1 ?? ?? ?? ?? 56 57 33 FF 66 85 C0 8B F1 74 0A 0F BF C0 8B 3C 85 ?? ?? ?? ?? 6A 00 E8`
- socialme input: unique at 0x1014D500
  `0F BF 44 24 04 83 C0 FD 56 83 F8 19 8B F1 0F 87 ?? ?? ?? ?? 33 C9 8A 88 ?? ?? ?? ?? FF 24 8D ?? ?? ?? ?? 6A 00 6A 01 68 ?? ?? ?? ?? B9 ?? ?? ?? ?? E8`
  Verify: +0x28 points to "menu    menuwind" and +0x2D == the manager. Once the handler exists, also verify that
  `(*(void***)*0x105762E8)[6]` equals it.
- socialme opened: unique at 0x1014D470
  `56 8B F1 6A 0F 6A 10 8B 4E 08 E8 ?? ?? ?? ?? 8B 4E 08 6A 0E 6A 0F E8 ?? ?? ?? ?? 8B 4E 08 6A 0D 6A 0F E8 ?? ?? ?? ?? 8B 46 08 C6 80 86 00 00 00 00 E8 ?? ?? ?? ?? E8 ?? ?? ?? ?? 84 C0 74 0C 8B 4E 08 6A 05 6A 05 E8`
  Call operands: **+0x0A -> SetButtonStatus 0x10118760**, **+0x36 -> Monstrosity check 0x10097B60**.
- Check that the menuwind table slots [1] and [6] equal the opened and input hits (as configmenu checks its slots).
  Copy all 17 slots.

### B. The merged layout

Built from the game's in-memory payloads, as configmenu reads them: the first layout of each name, `payload_of` plus the
DAT chunk length check. Check that both are the known shape: header count 0x0F, ids 1..15 in block order, items 88x16 at
x 16, frame comwin #11, title links all 0xFF. Fail safe otherwise.

1. Header: menuwind's, with the count byte = 15 + 12 = **27**. **The arrows (13, 14) and the title (15) stay in place:
   the game takes an item by its block's place** (neighbour lookup FUN_1011ae20: block n after the window is item n,
   and it returns 0 unless that block's id == n; SetCursol FUN_101189b0 clamps to the count at window+0x58). So ids
   must run 1..27 in block order. (The first build dropped the arrows and put the title last: the cursor went to 0 and
   stuck, found from a CatsEyeXI player's report, 2026-10-21.)
2. Window block: height 202 + 192 = **394**. Sprite slot 0 -> our sheet's frame sprite (24 wells). Keep the anc sprite.
3. Page 1 blocks 1..15 copied in place (items, arrows, title tab). The arrows (13, 14) are put out of sight since
   2026-10-24: sprite 0 of each is our sheet's sprite 1 (one clear 1x1 texel) and its y is -0x2000, so nothing is drawn
   beside the frame and the mouse can't land on them (a hit would have moved the cursor to an invisible item).
4. socialme blocks 1..12 copied after them, each with **id += 15 (16..27)** and **y += 192**.
6. Links rebuilt by visual order:
   `[1,2,3,4,10,5,6,7,8,9,11,12, 16,17,18,19,20,21,22,23,25,24,26,27]`.
   before = up = the previous, after = down = the next, wrapping round; right = left = itself.
7. The sheet: our own name (for example "menu    vcmainmn"; keep it apart from configmenu's "menu    vanacore" so the
   two features stay independent). Images "menu    newtex  " and "menu    buttonto"; one sprite, the 24-well frame
   (2·24 + 1 parts).
8. Register as configmenu does: add the sheet; remove the game's "menu    menuwind" layout only while no window made
   from it is up (in_use); add ours; keep g_original to put back. Leave socialme's layout alone (it's needed when the
   setting goes off).

### C. The handler table copy

Each frame, menuwind's handler `*(void**)*0x105760CC` gets our table, as configmenu's set_table does. Our slots:

```cpp
// slot 6
void __fastcall single_input(char* h, void*, int event, int item) {
  const short e = (short)event, it = (short)item;
  if (ours_open(window_of(h))) {
    if (e == 3 || e == 4) return;                       // no page flip on Left/Right
    if (e == 0x1C) { g_menu_input(h, 6, item); return; } // menu key again: close (or just return)
    if (e == 5 && it >= 13 && it <= 15) return;          // the arrows/title would re-dispatch as event 4
    if (e == 5 && it >= 16 && it <= 27) {                // page 2's own action, exactly as the game runs it
      char* social = *(char**)0x105762E8;                // scanned global
      if (social) g_social_input(social, 5, it - 15); else beep();
      return;
    }
  }
  g_menu_input(h, event, item);
}
// slot 1
void __fastcall single_opened(char* h, void*) {
  g_menu_opened(h);
  char* w = window_of(h);
  if (ours_open(w) && g_is_monstrosity()) g_set_status(w, 5, 20); // socialme's Set Bazaar rule
}
```

Notes:

- `g_social_input` is `__thiscall(void*, int, int)`, as configmenu's `Input`. Its select needs the zone global, which
  the game checks itself. It plays the click or beep sound.
- Every page-2 sub-menu opens with CreateDrawMenu(name, 1, 0), area 0x81, in the main menu's slot, exactly as from
  page 2 (hudwin's follow_menu still applies).
- Cancel on page 2 (event 6) is never reached in single mode. menuwind's own cancel just closes it.

### D. Elsewhere

- hudwin.cpp row "mainmenu" (112 x 202): it should be 112 x 394 while singlemenu is on. The window list still shows
  only "menu    menuwind" (gameevents sees "menuwind" and never "socialme").
- Setting off: take our layout out and put the game's back (not while open). Put the game's table back, and remove the
  sheet once no window uses it. Exactly configmenu's take_out.

## 6. Open questions (confirm live)

1. Event 0x1C really is the menu key while the menu is open. It flips in the table either way, and swallowing 3, 4 and
   0x1C covers it. What should it do in single mode: close, or nothing?
2. Cancelling a sub-menu opened from a page-2 item (Config, Macros, Missions, View House, Bazaar, Communication)
   returns to the merged menuwind. No code reopens socialme by name, so the return is the manager's generic
   previous-window path; it should land on menuwind.
3. The 24-well frame draws correctly: the newtex background at 394 tall, and wells lined up with the items (frame wells
   at y 8+16i, items at y 6+16i, as the game's own). It fits the screen at the interface resolution in use (top 48 +
   394; fine at 2880 x 1620, check small interface heights).
4. The window accepts 25 blocks and ids 16..27, with gaps at 13 and 14. Mouse hover and click on items 16..27, and the
   help bar showing page 2's lines. Those lines are carried in the blocks' strings, for example 468 "View list of
   current missions.".
5. With the cursor on a page-2 item: open and close the menu, and check the cursor memory.
6. In Monstrosity: Set Bazaar (id 20) is greyed. View House's own checks (player flag 0x100, the zone, message 0xBC)
   behave as on page 2.
