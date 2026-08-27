# Title UI chrome — `ROM/119/50.DAT` (`lobb`)

The login **2D chrome** (logos, fonts, wardrobe badges, race buttons, controller glyphs)
is **not** in `ROM/0/23.DAT`. That file is only the 3D background scene (`titl`).

English title UI lives in:

| Path | Role |
|------|------|
| `ROM/119/50.DAT` | EN title / lobby textures + layout (`lobb`) |
| Pivot override | e.g. `Ashita/polplugins/DATs/catseyexi/ROM/119/50.DAT` |
| `ROM/119/51.DAT` | Related `menu` pack (many small image sets; not the title pack shape) |
| `ROM/0/1.DAT` | Global HUD fonts (`font`, `dg_font`, …) referenced by name from 50 |

Locales (from `xi title export`): EN `119/50`, JP `91/16`, DE `176/74`, FR `178/13`.

Related:

- **[wardrobe_numbers.md](wardrobe_numbers.md)** — hide wardrobe 3–8 icons/digits  
- **[README.md](README.md)** — `xi title` cameras / zones on `23.DAT`  
- Model viewer: Images “title pack” mode + Data Struct click-through for `0x30` / `0x31`

---

## Section types that matter

| Type | Data Struct name | What it is |
|-----:|------------------|------------|
| `0x20` | Texture | Pixel atlases in this DAT (`titlwin`, `chmkfnt`, `wardrb`, `abxy360`, …) |
| `0x30` | **UiMenu** | Small window / control defs: name + frame box + child hit boxes |
| `0x31` | **UiElementGroup** | Sprite layout stream (dest quads + src rects on atlases) |

**Sprites are not UiMenus.** Hiding wardrobe numbers patched **`0x31` layout dest quads**,
not any `rac5` / `chm6` UiMenu.

### Typical `119/50` shape (title pack)

Unlike a normal `menu` DAT (dozens of tiny `0x31` sets, each named), **50** is:

```
magic "lobb"
  many 0x20 textures          ← titlwin, chmkfnt, wardrb, logos, …
  many 0x30 UiMenu            ← race buttons, character-make chrome, …
  ONE large 0x31 section      ← tag fourcc often "lobb"; body starts menu/lobbywin
```

Measured on CatsEye pivot `119/50` (~1.4 MB): one `0x31` ≈ **1000+** sprite payloads;
owners include `font`, `chmkfnt`, `titlwin`, `keytopHD`, `wardrb`, `abxy360`, …

---

## Where `lobbywin` comes from

Data Struct lists the section **fourcc** (e.g. `lobb`). The string **`lobbywin`** is
**not** a separate section and **not** a texture in this file.

It is the **image-set name** in the first 16 bytes of the `0x31` payload:

```
section tag @ header:  "lobb" … type 0x31
dataStart +0x00:       "menu    lobbywin"     ← category + name
dataStart +0x10:       u8
dataStart +0x11:       "menu    buttonto"     ← texture ref (often EXTERNAL)
then:                  01 00 … sprite records
```

So the model viewer Images panel shows **`menu / lobbywin`** with texture ref
**`menu / buttonto`**. If `buttonto` is not a `0x20` in this DAT, the set alone looks
“texture not in this file” — while the real title art is the bare textures listed under
**Textures** (`titlwin`, etc.).

There may be additional `menu lobbywin` string bytes earlier in the file (menu wiring);
the Images set row is the `0x31` header above.

---

## Main character menu (`loby2win`)

The five options under the logo (**Select / Create / Delete / Config / Back**) are
UiMenu **`loby`** / `menu loby2win` in `119/50`. Moving element x/y moves **label +
hitbox** together. Full notes (text ids, font scope, hiding Create): **[main_menu.md](main_menu.md)**.

---

## UiMenu (`0x30`) — hit boxes, not pixels

Same layout as `xi ui layout menu-pos` (documented for `ROM/0/1`):

```
+16  char[16]  name           e.g. "menu    race3"
+32  u8        type?
+33  u8        numElements
+48  element   frame          x, y, width, height (window / control box)
then           element[0..]   more boxes + nav (prev/next indices)
```

Element (abbrev.):

```
+0   u16  size
+2   i16  x
+4   i16  y
+10  i16  width
+12  i16  height
+16  u8   index
+19  i8   prev nav (−1 = none)
+20  i8   next nav
```

Examples on title pack: `rac5` → name `race3`, single frame box (race pick hit area).
**Readable as a table of boxes** — not as a painted UI without linking to `0x31` + textures.

Model viewer: Data Struct → click **UiMenu** → modal table (frame + elements).

---

## UiElementGroup (`0x31`) — sprite layout

### Header (menu-style set)

| Off | Field |
|----:|-------|
| +0x00 | `category[8] + name[8]` (space-padded) |
| +0x10 | u8 |
| +0x11 | texture ref `[16]` (atlas this set claims; may be external) |

Title pack: one set `menu/lobbywin`, ref often `menu/buttonto`.

### Sprite records (body)

Introduced by marker `01 00 <type> <subtype>` then `parent[8]` + `name[8]`, then payload.

Payload (41 bytes @+0, or 42 @+1):

```
[dest quad: 4 × (x,y) u16][src_w][src_h][src_x][src_y]
```

Corners are inclusive-style extents in places (whole 256 texture often stored as 255).

### Ownership rule (critical)

**A payload is owned by the texture/widget name that FOLLOWS it**, not the header name
immediately before the payload. Same rule as `xi.ui.xi_core._rects_by_owner`.

```
header A | payload_P | header B | …
           └─ owned by B (B is the atlas / id that samples P)
```

Filtering sprites for texture `titlwin` must match **owner == titlwin**, not header.
Matching the header produces noise (`font ← titlwin`, `slant ← chmkfnt`, …).

xi-tools owner counts on a clean parse of pivot `119/50` (order of magnitude):

| Owner | ~count |
|-------|-------:|
| `font` | ~390 |
| `chmkfnt` | ~188 |
| `keytopHD` | ~150 |
| `buttonto` | ~95 |
| `titlwin` | ~35–37 |
| `wardrb` | 6 |
| `abxy360` | 2 |

(`font` / `buttonto` may resolve to atlases outside this DAT.)

### Textures actually stored in `119/50`

Typical `0x20` names (EN):

`chmkfnt`, `titlwin`, `abxy360`, `otp`, `ex1us`, `ex2us`, `b1n`, `ex5us`, `wardrb`

- **`titlwin`** — large title art (copyright, logo, “Test Server” chrome, etc.)  
- **`chmkfnt`** — character-make / title font atlas  
- **`wardrb`** — wardrobe badge icon only (digits are separate `font` sprites)  
- **`abxy360`** — Xbox-style face buttons (A/B/X/Y) in one 32×32 sheet  

Do **not** edit global `dg_font` to change title-only digits — see wardrobe doc.

### CLI — `xi title sprite`

Inspect / patch dest quads and src rects (same DAT as title chrome):

```bash
uv run xi title sprite path/to/119/50.DAT
uv run xi title sprite path/to/50.DAT --owner ex1us
uv run xi title sprite path/to/50.DAT --owner ex1us --index 0 --dest-tl 768,24 --dest-br 960,62
uv run xi title sprite path/to/50.DAT --owner titlwin --index 0 --dx 12
uv run xi title sprite path/to/50.DAT --offset 0x143f11 --hide
```

Model viewer: Data Struct → click **UiElementGroup** → modal (set header, texture ref,
owner filter, sprite table). Images view: title-pack **Textures** list + Sprites panel.

---

## DLL path table (wardrobe unlock wiring)

CatsEye `FFXiMain.dll` holds menu paths (not required to hide badges):

```
/wardrobe … /wardrobe8   with ids 0x71–0x78, tag 0x51
```

That is unlock / menu-tree wiring. Drawing of the badges is still the `0x31` sprites in
`119/50`.

---

## Pivot / HD load order (model viewer)

When both overrides are configured:

1. **Pivot** (if PIVOT toggle on)  
2. **HD** (if HD toggle on)  
3. **Game** install  

Absolute tree clicks are rewritten to a `ROM\…` key before that resolution, so enabling
PIVOT reloads the override even if the row was under “FINAL FANTASY XI”.

Assets → Data can list three roots: game, HD, pivot.

---

## What not to confuse

| Thing | File / type | Notes |
|-------|-------------|--------|
| 3D flythrough | `ROM/0/23.DAT` `titl` | zones, cameras, weather |
| Title logos / wardrobe sprites | `ROM/119/50.DAT` `0x20` + `0x31` | this doc |
| Race button hit boxes | `119/50` `0x30` UiMenu | boxes only |
| Global UI font sheet | `ROM/0/1` `dg_font` / `font` | whole client |
| `lobbywin` string | inside `0x31` header | not a texture name in 50 |

---

## Quick commands

```bash
# Title scene (3D)
uv run xi title list
uv run xi title set-zone 12 15

# Title UI textures
uv run xi ui tex sx ROM/119/50.DAT
# → exports/ui/119/50/  (titlwin, wardrb, abxy360, …)

# Menu positions (same element layout as 0x30; default DAT is ROM/0/1)
uv run xi ui layout menu-pos --all
```

Hide wardrobe 3–8: **[wardrobe_numbers.md](wardrobe_numbers.md)** (zero dest quads on the
six `wardrb` + six digit sprites in the pivot `119/50` `0x31` stream).
