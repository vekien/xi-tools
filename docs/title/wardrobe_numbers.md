# Title screen wardrobe numbers (3–8)

The login screen can show six small badges for unlocked wardrobes **3–8** (icon + digit).
Those are **not** drawn by editing `dg_font` or patching `FFXiMain.dll`. They are sprite
layout records inside the English title UI container.

**DAT:** `ROM/119/50.DAT` (magic `lobb`)  
**Typical override path:** `Ashita/polplugins/DATs/catseyexi/ROM/119/50.DAT`

Related 3D background scene work lives in [`README.md`](README.md) / `ROM/0/23.DAT`.
Full title UI map (`lobbywin`, UiMenu vs UiElementGroup, textures): **[ui_chrome.md](ui_chrome.md)**.
UI texture/layout format notes: [HANDOVER Part 2](../HANDOVER.md) and
[`xi.ui.xi_core`](../../src/xi/ui/xi_core.py).

**Not a UiMenu.** The hide is on the single large **`0x31` UiElementGroup** in `119/50`
(Data Struct may show section tag `lobb`; set name inside the payload is `menu/lobbywin`).

---

## What you see on screen

Each of the six slots is a **pair** of sprites in the container’s `0x31` layout chunk:

| Piece | Texture name | Role |
|-------|----------------|------|
| Icon | `wardrb` (32×32, in this DAT) | Small wardrobe/chest badge |
| Digit | `font` (global atlas; same family as in-game fonts from `ROM/0/1.DAT`) | The **3**, **4**, **5**, **6**, **7**, **8** |

They sit near the **end** of the layout record stream (after most title chrome). There are
exactly **six** `wardrb` icons and **six** digit overlays.

### Payload ownership (important)

In `lobb` layout, a record’s **payload is owned by the texture name that follows it**, not
the name on the header immediately before the payload. Confirmed on this DAT when scaling
sprites (`_rects_by_owner` in `xi_core.py`).

So the cluster looks like:

```
… header "font"  | payload → owned by wardrb   (icon: full 32×32 → dest ~0,0–16,16)
  header "wardrb"| payload → owned by font     (digit: small dest ~10,7–16,15)
  header "font"  | payload → owned by wardrb
  header "wardrb"| payload → owned by font
  … ×6 …
```

Vanilla / sheet geometry for each icon (`layout_reference.json` → `ROM/119/50` → `wardrb`):

```
src  (w,h,x,y) = (32, 32, 0, 0)     # whole wardrb texture
dest quad      = (0,0)-(16,16)      # relative to parent menu element
```

Digit overlays (live DAT; dest is parent-relative):

```
dest ~ (9–10, 7)–(16, 15)           # ~7×8 px number box on the badge
src  ~ 16–19 × 23–24 @ x≈74,94,114,134,156,173  y≈130–131
```

Those source coords address the shared **`font`** atlas (not the 32×32 `wardrb` image).
Do **not** edit `dg_font` / global font DATs to hide title-only digits — that hits the
whole client.

`wardrb` itself is only the icon; extract with:

```bash
uv run xi ui tex sx ROM/119/50.DAT
# → exports/ui/119/50/wardrb.png
```

---

## DLL side (not required to hide)

CatsEye’s `FFXiMain.dll` contains a UI path table (not present in the unpacked retail
image we keep for RE):

| Id (u16) | Tag (u16) | Path |
|---------:|----------:|------|
| `0x71` | `0x51` | `/wardrobe` |
| `0x72` | `0x51` | `/wardrobe2` |
| … | … | … |
| `0x78` | `0x51` | `/wardrobe8` |

That table is menu-tree / unlock wiring. **Hiding the badges does not need a DLL patch**
if you only want them gone from the title art: zero the layout dest quads (below).

---

## How to hide (proven)

**Goal:** remove wardrobe 3–8 icons and/or digits from the title screen.

```bash
uv run xi title wardrobe path/to/50.DAT                 # list the 6 icons + 6 digits
uv run xi title wardrobe path/to/50.DAT --hide          # zero all 12 dest quads
uv run xi title wardrobe path/to/50.DAT --hide --no-icons   # digits only
```

`xi title sprite --owner wardrb --index N --hide` clears the **icons only**: the digit
payloads are owned by `font`, which is not a texture in this DAT, so the by-owner sprite
index never lists them. The manual method below is what `xi title wardrobe` does.

**Method:** set each sprite’s **destination quad** (8× `u16` screen corners) to all zeros.
A zero-area dest is not drawn. Source rects and texture pixels can stay untouched.

1. Work on the **pivot/override** copy the client actually loads (CatsEyeXI:
   `…/DATs/catseyexi/ROM/119/50.DAT`), not only the pristine install tree.
2. Parse `0x31` layout records (`parse_layout_records`).
3. For each consecutive pair where the **following** name is `wardrb`, take that payload
   as an **icon** (after the usual 41→prefix 0 / 42→prefix 1 rule).
4. For each header named `wardrb` whose following name is `font`, take that payload as a
   **digit** (small dest width/height).
5. At the quad start of each payload, write eight zero `u16`s.
6. Relaunch the client (no `FFXiMain` rebuild).

### Counts to expect

| Kind | Count | Notes |
|------|------:|-------|
| `wardrb` icons | 6 | dest was `(0,0)-(16,16)`, src full texture |
| digit overlays | 6 | dest ~7×8 on the badge, src in `font` atlas |

Hiding **only digits** → step 4 only. Hiding **icons + digits** → both.

### Example (Python, same logic as the working patch)

```python
from pathlib import Path
import struct
from xi.ui.xi_core import parse_layout_records, _record_prefix, all_texture_sizes

path = Path(r"…/catseyexi/ROM/119/50.DAT")
data = bytearray(path.read_bytes())
recs = parse_layout_records(bytes(data))

for i in range(len(recs) - 1):
    name, off, length = recs[i]
    owner = recs[i + 1][0]

    # Icon: payload owned by wardrb
    if owner == "wardrb":
        pre = _record_prefix(bytes(data), off, length, (32, 32))
        if pre is None:
            continue
        base = off + pre
        sw, sh, sx, sy = struct.unpack_from("<4H", data, base + 16)
        if (sw, sh) in ((32, 32), (31, 31)) and (sx, sy) == (0, 0):
            struct.pack_into("<8H", data, base, *([0] * 8))

    # Digit: header wardrb, payload owned by font
    if name == "wardrb" and owner == "font":
        pre = 0 if length == 41 else 1 if length == 42 else 0
        base = off + pre
        dest = struct.unpack_from("<8H", data, base)
        dw, dh = abs(dest[2] - dest[0]), abs(dest[5] - dest[1])
        if dw <= 20 and dh <= 30:
            struct.pack_into("<8H", data, base, *([0] * 8))

path.write_bytes(data)
```

### Verified result

Zeroing all **12** dest quads on the CatsEye pivot `119/50` removed the wardrobe icons
and the 3–8 digits from the title screen. No `FFXiMain.dll` change.

---

## What not to do

| Approach | Why not |
|----------|---------|
| Edit `dg_font` / global `font` in `ROM/0/1` | Shared by the whole UI; breaks in-game text |
| Blank only `wardrb.png` | Hides icons; **digits** still draw from `font` sprites |
| Grow/shrink keyframe-style hacks on `23.DAT` | Wrong file — wardrobe chrome is not in the `titl` scene |
| Assume DLL patch is required | Layout dest zero is enough for visual hide |

---

## Restore

Copy pristine `ROM/119/50.DAT` from the game install over the pivot file, or re-apply
dest quads from `src/xi/ui/data/layout_reference.json` (`ROM/119/50` → `wardrb` entries)
and re-export digit dests from a backup of the pre-patch DAT.

---

## Related commands

```bash
uv run xi ui tex sx ROM/119/50.DAT          # extract textures (incl. wardrb)
uv run xi ui tex si ROM/119/50.DAT          # re-import edited PNGs
# layout helpers live under xi ui layout … (menu-pos targets ROM/0/1 by default)
```

For title **background zones / cameras**, use `xi title …` on `ROM/0/23.DAT` — separate
from this UI chrome.
