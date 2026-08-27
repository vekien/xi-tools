# Title / lobby main menu (`loby2win`)

The character-select strip under the logo (**Select Character**, **Create Character**,
**Delete Character**, **Config**, **Back**) is a **`0x30` UiMenu**, not baked into
`titlwin` alone.

| | |
|--|--|
| **DAT** | `ROM/119/50.DAT` (use the **pivot** copy the client loads) |
| **Section tag** | `loby` |
| **Resource name** | `menu    loby2win` |
| **Siblings** | `chsw` / `chswin` (keyboard), `chs3` / `chs360` (controller) — same 5 actions |

Related: [ui_chrome.md](ui_chrome.md) (pack layout), [wardrobe_numbers.md](wardrobe_numbers.md)
(sprites), `xi ui layout menu-pos`.

---

## Moving items — labels follow hitboxes

UiMenu elements are primarily **layout + hit boxes**. Experiment on CatsEye pivot
`119/50` (`loby`):

- Patching element **x/y** moved **both** the clickable region **and** the on-screen
  label text.
- So for this menu, the client draws the caption **relative to the element box** (or
  resolves text in a way that tracks the element). You do **not** need a separate
  sprite move for the five main labels.

### Commands — `xi title menu`

Pass the DAT path like `xi ui tex si` (no `--ffxi`):

```bash
DAT=D:/cexi/…/catseyexi/ROM/119/50.DAT

# List title menus with ButtonID + nav
uv run xi title menu $DAT

# Move one row by element index
uv run xi title menu $DAT --menu loby --elem 1 --x 554 --y 671

# Move / size by ButtonID (Create = 2)
uv run xi title menu $DAT --menu loby --btn 2 --x -800 --y -800 --w 0 --h 0

# Rewire nav (Select down skips Create → Delete)
uv run xi title menu $DAT --menu loby --btn 1 --nav-down 3
uv run xi title menu $DAT --menu loby --btn 3 --nav-up 1
uv run xi title menu $DAT --menu loby --btn 2 --isolate

# Keyboard / controller variants
uv run xi title menu $DAT --menu chsw --btn 2 --isolate --x -800 --y -800
```

Legacy: `xi ui layout menu-pos` still works for x/y only (any DAT, default `0/1`).

### Known quirks

- Focus / cursor / “on-click” highlight can lag or look slightly off after large moves
  (cursor offset fields exist on the element and were not retuned). Cosmetic; not fully
  mapped.
- Element order in the file is not strict screen order (e.g. y values may not increase
  monotonically with index).

### Vanilla-ish positions (before scatter tests)

| Index | Typical role (order on screen) | Approx. x, y | Size |
|------:|--------------------------------|-------------:|------|
| 0 | Select Character | 354, 443 | 143×24 |
| 1 | Create Character | 354, 471 | 143×24 |
| 2 | Delete Character | 354, 499 | 143×24 |
| 3 | Config | 354, 555 | 143×24 |
| 4 | Back | 354, 527 | 143×24 |

(Confirm roles in-game after edits; index ↔ label is by text-id, not by sorting y.)

---

## Element payload (beyond x/y)

Each `loby` button is ~64–80 bytes. After the usual position header (same family as
xiclient `ButtonDefinitionHeader`), the tail carries:

```
… nav up/down/left/right …
u16  titleTextId      e.g. 126, 128, 130, 132, 183
char[16] "menu    lobbywin"   ← string table / resource namespace for that id
[optional second id + "menu    lobbywin" for help text]
i16  -1, -1                   ← end markers on shorter records
```

Observed **title text IDs** on the five rows (pivot after edits still had these ids):

| Elem | Title text id | Help id (if present) |
|-----:|--------------:|---------------------:|
| 0 | 126 | 127 |
| 1 | 128 | 129 |
| 2 | 130 | 131 |
| 3 | 132 | (none on 64-byte form) |
| 4 | 183 | (none) |

So the visible words are **not stored inside the UiMenu as plain ASCII**. The element
only stores **(namespace `menu lobbywin`, numeric id)**. The client looks up the string
(and font) from that pair.

---

## 1. Can we change the text?

| Approach | Status |
|----------|--------|
| Edit x/y only | Moves label; does **not** change wording |
| Find string table for `lobbywin` ids 126… | **Not pinned yet** — plain `"Select Character"` / `"Create Character"` do **not** appear as ASCII/UTF-16 in `119/50`, `0/1`, or the usual `ROM/97` XISTRING pools |
| Related lobby copy in `ROM/97/36.DAT` | Longer help lines exist (e.g. “Create a new character.”) — **not** proven to be the five short button captions |
| In-game menu labels (`ROM/97/41.DAT` etc.) | Documented in [ROM_97_menu_strings.md](../dats/ROM_97_menu_strings.md); different menus |

**Practical path to rename a button (once the table is found):**

1. Keep element geometry.
2. Either overwrite the looked-up string in-place (same or shorter length + null), or
   point the element’s title text id at another existing id in the same namespace.
3. Retest nav and any help popup (second id on 80-byte elements).

**Open work:** resolve global/resource id → file for `menu lobbywin` text ids (DLL
string resolver / runtime trace). Until then, treat caption **rewording** as
unconfirmed.

---

## 2. Can we change the font (title screen only)?

| Asset | Scope | Notes |
|-------|--------|--------|
| `chmkfnt` in `ROM/119/50` | Title / char-create pack | Safe to retexture for **this DAT only** (`xi ui tex sx/si`) |
| `titlwin` | Baked art (logo, copyright, some chrome) | Edit pixels; not the five menu strings |
| `font` / `dg_font` in `ROM/0/1` | **Global** HUD | Changing glyphs hits the whole client |
| Runtime draw of `loby` captions | Unknown font binding | If they use global `font`, a title-only font means either a pack-local font the menu already references, or a client change |

**Title-only styling without touching global fonts:** edit **`chmkfnt`** / **`titlwin`**
in pivot `119/50`. Whether the five `loby` labels use `chmkfnt` vs global `font` is
**not proven** — if a font swap on `chmkfnt` does not change those five lines, they are
global-font (or another atlas).

---

## 3. Can we disable / remove “Create Character”?

**Yes for keyboard/pad nav** — hide is not enough. Up/Down follows **ButtonID nav
links** on each element, not screen Y. Off-screen Create was still selected because
neighbours still pointed at ButtonID `2`.

### Nav fields (xiclient `ButtonDefinitionHeader`)

On each element, after the usual x/y/w/h block:

| Off | Type | Field |
|----:|------|--------|
| +18 | i16 | **ButtonID** (1…N within the menu) |
| +23 | i8 | Nav **Up** → ButtonID |
| +24 | i8 | Nav **Down** |
| +25 | i8 | Nav **Left** |
| +26 | i8 | Nav **Right** |

`loby` vanilla vertical chain (by ButtonID):

```
1 Select  ↔  2 Create  ↔  3 Delete  ↔  5 Back  ↔  4 Config  ↔  (wrap to 1)
```

Create is **ButtonID 2** (title text id **128**). Same id on `chsw` / `chs3`.

### Working hide + skip (proven on pivot)

1. **Reroute** anyone who had Up/Down == 2 to skip over Create (use Create’s old U/D).  
   - Select (1): Down `2` → `3` (Delete)  
   - Delete (3): Up `2` → `1` (Select)  
   - (`chsw`/`chs3`: 1→3 and 3→1 the same way)
2. **Isolate** Create: set its U/D/L/R all to **−1**.  
3. **Park** Create off-screen (e.g. x=y=−800) so mouse can’t hit it.

After patch, Up/Down never lands on Create; the row is invisible.

| Approach | Effect | Risk |
|----------|--------|------|
| Off-screen **only** | Hidden but **still in nav** | Low (incomplete) |
| **Nav rewrite + hide** (above) | Skipped by keys/pad + invisible | Low |
| Zero w/h only | May still focus | Low–med |
| Delete element / lower `numElements` | True remove | **High** crash risk |
| Server deny create | No new chars even if UI remains | Outside DAT |

`menu-pos` does not yet expose nav bytes — patch with a small script (or extend
`menu-pos` later). Always do **`loby` + `chsw` + `chs3`** together.

Note: array index ≠ ButtonID after scatter tests; key off **ButtonID** or **title
text id 128**, not “elem 1”.

---

## Expansion list (right-hand logos)

Separate from `loby2win`. Styled expansion names are almost certainly **`0x31` sprites**
(and/or baked art), not these five UiMenu rows. Moving them is layout-dest / texture
work ([ui_chrome.md](ui_chrome.md)), not `menu-pos --menu loby`.

`ROM/0/1` menu **`play` / `playermo`** is a different “game / expansion select” list
(10 plain rows) used in other flows — do not assume it is the right-hand art column on
the modern CatsEye title screen.

---

## Summary

| Goal | Feasible now? | How |
|------|---------------|-----|
| Move main menu rows | **Yes** | `menu-pos` on `loby` (+ `chsw`/`chs3`); labels track boxes |
| Change caption text | **Not yet** | Need `lobbywin` string table for ids 126/128/… |
| Title-only font | **Partial** | `chmkfnt` / `titlwin` in `119/50`; global font if labels use `ROM/0/1` |
| Remove Create | **Hide yes; hard-delete no** | Off-screen / zero size; avoid deleting records |

---

## Restore scatter-test positions

If the pivot was used for ± offset tests, reset from a clean `119/50` or set:

```text
elem 0 → 354, 443
elem 1 → 354, 471
elem 2 → 354, 499
elem 3 → 354, 555
elem 4 → 354, 527
```
