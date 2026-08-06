# Level-Editor UI Design System

The web level editor (`web/leveleditor/`) has a "premium panel" visual language:
accent-tinted heroes, metric tiles, cards, and gradient call-to-actions built from a
single accent variable. **Any new panel, tab, or section in the editor should be built
from these patterns** — not the flat grey `<div>`/`<button>` defaults.

Reference implementations (copy from these when in doubt):

| System   | Where                                                            | What it is                                  |
|----------|-----------------------------------------------------------------|---------------------------------------------|
| `evx-*`  | `panels/events-panel.js` `renderMergedInfo()` + `css/events.css` | Event Info dashboard (hero + tiles + composition + danger zone) |
| `csp-*`  | `panels/cutscene-author.js` Publish tab + `css/events.css`       | Cutscene Publish "release" screen (hero + tiles + byte deltas + file list + folds) |

Prefix new systems with a short 3-letter tag (`evx`, `csp`, …) so classes stay
greppable and never collide across panels.

---

## 1. The core technique — one accent, `color-mix` everywhere

Every tinted component takes a single accent colour via a `--ac` CSS variable set
**inline** on the root element, then derives its fill / border / glow from it with
`color-mix`. That's what makes a component recolour to "info blue", "success green",
"danger red" without new CSS.

```html
<div class="xxx-hero" style="--ac:#7fd88f"> … </div>
```

```css
.xxx-hero {
  background:
    radial-gradient(130% 150% at 0% 0%, color-mix(in srgb, var(--ac) 22%, transparent), transparent 58%),
    #15161b;                                            /* fallback surface first */
  border: 1px solid color-mix(in srgb, var(--ac) 32%, #2a2a31);
}
```

Always put a **plain-hex fallback line before** the `color-mix` line for the same
property (older engines skip the invalid line and keep the fallback).

---

## 2. Tokens (palette)

No `:root` variables yet — these are the hex values used consistently across
`evx-*` / `csp-*`. Reuse them verbatim; don't introduce new near-duplicates.

### Surfaces (dark → light)
| Role                         | Hex        |
|------------------------------|------------|
| App background (body)        | `#1a1a1f`  |
| Well / inset (code, chips, compbar) | `#0e0f13` |
| Hero surface                 | `#15161b`  |
| Card surface                 | `#16171c`  |
| Tile surface                 | `#17181d`  |
| Row hover                    | `#1c1d24` → `#20222b` |

### Borders
| Role                | Hex        |
|---------------------|------------|
| Card / tile border  | `#24252c`  |
| Inner divider       | `#23242b`  |
| Dashed empty-state  | `#2f3039`  |
| Ghost button        | `#33343d` → hover `#454652` |

### Text
| Role                  | Hex        |
|-----------------------|------------|
| Title / big value     | `#eef1f8`  |
| Body strong           | `#cdd2e0`  |
| Body / mono paths     | `#aab1c2` / `#aeb4c2` |
| Uppercase label / key | `#7e8698`  |
| Faint / offsets       | `#6b7280` / `#5f6675` / `#757c8c` |

### Accents (use as `--ac`, semantic)
| Meaning                 | Hex        |
|-------------------------|------------|
| Info / cool primary     | `#7fd6e6` (cyan) · `#82aaff` (blue) |
| Success / "go"          | `#7fd88f`  |
| Warning / busy          | `#f7c873`  |
| Category / secondary    | `#c792ea` (purple) |
| Error                   | `#ff8f8f` · `#ff7b72` |
| Negative delta          | `#ffb27f` (orange) |

### Type & shape
- Font: **Roboto** for UI; `ui-monospace, "Cascadia Code", monospace` for ids / bytes / paths.
- Radii: hero/CTA `11–13px`, cards/tiles/folds `10–11px`, chips/pills `999px`, wells `8px`.
- Icons: **Material Symbols Outlined** — `<span class="material-symbols-outlined">rocket_launch</span>`.

---

## 3. Components

Each is copy-paste ready. Swap the prefix, keep the structure.

### 3.1 Hero banner
Big identity strip at the top of a panel — icon tile · title + mode badge · sub-line · status pill.

```html
<div class="csp-hero" style="--ac:#7fd88f">
  <div class="csp-hero-ico"><span class="material-symbols-outlined">rocket_launch</span></div>
  <div class="csp-hero-main">
    <div class="csp-hero-title">Event 25000<span class="csp-mode">Overwrite</span></div>
    <div class="csp-hero-sub"><span class="material-symbols-outlined csp-sub-ico">person</span>Maat</div>
  </div>
  <div class="csp-status"><span class="material-symbols-outlined">check_circle</span><span>Published</span></div>
</div>
```
Pattern: 48px accent icon tile (inset ring via `box-shadow: inset 0 0 0 1px`), 21px/700 title,
right-aligned status/meta. Tint the whole card by `--ac`.

### 3.2 Metric tiles
Responsive grid of big-number cards. Left accent bar, corner icon, optional signed delta.

```html
<div class="csp-tiles">
  <div class="csp-tile" style="--ac:#82aaff">
    <span class="material-symbols-outlined csp-tile-ico">description</span>
    <div class="csp-tile-val">1,574,534</div>
    <div class="csp-tile-key">Event DAT</div>
    <div class="csp-tile-delta csp-d-up">+248 B</div>
  </div>
</div>
```
```css
.csp-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(104px, 1fr)); gap: 8px; }
.csp-tile  { position: relative; padding: 12px; border-radius: 10px; background: #17181d; border: 1px solid #24252c; overflow: hidden; }
.csp-tile::before { content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background: var(--ac); opacity:.85; }
.csp-tile-val { font: 700 20px/1.05 Roboto; color:#eef1f8; font-variant-numeric: tabular-nums; }
.csp-tile-key { font-size:10px; text-transform:uppercase; letter-spacing:.5px; color:#7e8698; margin-top:4px; }
```
Values are big + tabular-nums; keys are tiny uppercase. Format numbers with
`Number#toLocaleString()`; show change as a coloured delta chip, not raw `a → b`.

### 3.3 Card (labelled section)
Container with an uppercase icon header. Recolour the header for warn/ok/err variants.

```html
<div class="csp-card csp-card-ok">
  <div class="csp-card-h"><span class="material-symbols-outlined">check_circle</span>Published · 11 files written</div>
  … body …
</div>
```
Header: `font: 700 10.5px; text-transform:uppercase; letter-spacing:.7px; color:#7e8698`.
Variants: `-warn` (`#f7c873`), `-ok` (`#7fd88f`), `-err` (`#ff8f8f`) recolour the header +
border, often with a faint top-tinted gradient background.

### 3.4 Gradient CTA (primary action)
The one prominent button per screen. Bright gradient, **dark** text, hover-brightness, active nudge.

```css
.csp-cta {
  display:inline-flex; align-items:center; gap:10px; width:100%;
  padding:12px 18px; border-radius:11px; border:none; cursor:pointer;
  font:700 14.5px Roboto; color:#06180f;
  background: linear-gradient(100deg, #86e0a0, #58c58c);   /* green = go */
  box-shadow: 0 7px 20px -9px #46b47f;
  transition: filter .14s, box-shadow .16s, transform .08s;
}
.csp-cta:hover  { filter: brightness(1.06); box-shadow: 0 9px 24px -9px #46b47f; }
.csp-cta:active { transform: translateY(1px); }
```
Cyan variant (used by "Open Timeline Sequencer"): `linear-gradient(100deg,#7fd6e6,#74b6ef)`,
text `#07141b`, shadow `#4aa5c8`. A trailing `arrow_forward` icon with `margin-left:auto`
reads as "go".

### 3.5 Ghost button (secondary)
Quiet companion to a CTA. `background:#1a1b21; border:1px solid #33343d; color:#cdd2e0`, hover lifts both.

### 3.6 Status pill / badge
Rounded `--ac`-tinted chip: `color:var(--ac); background:color-mix(… 13-16% …); border:color-mix(… 36-42% …)`.
For a live/working state, spin the icon: `.status-busy .material-symbols-outlined { animation: spin 1s linear infinite; }`.

### 3.7 Composition bar + legend
Proportional stacked bar for "what this thing is made of" (beats, tracks, opcodes by kind).

```html
<div class="evx-compbar">
  <span class="evx-seg" style="--cc:#7fd88f; flex:1"></span>
  <span class="evx-seg" style="--cc:#82aaff; flex:2"></span>
</div>
<div class="evx-legend"><span class="evx-leg"><i style="--cc:#7fd88f"></i>Dialogue <b>1</b></span></div>
```
Each segment gets its own `--cc` colour and `flex:<count>`; legend swatches reuse `--cc`.

### 3.8 Collapsible fold
`<details>`-based, styled summary with a count pill on the right. Use for bytecode, logs, raw dumps —
anything long and secondary.

```html
<details class="csp-fold" open>
  <summary><span class="material-symbols-outlined">code</span>Bytecode<span class="csp-fold-count">22 ops</span></summary>
  <div class="csp-disasm">…</div>
</details>
```
Hide the default marker (`summary::-webkit-details-marker{display:none}`, `list-style:none`);
put the count in a `#0e0f13` pill via `margin-left:auto`.

### 3.9 Empty state
Dashed-border card that tells the user what to do before there's data.

```html
<div class="csp-empty">
  <span class="material-symbols-outlined">science</span>
  <div><div class="csp-empty-h">Nothing compiled yet</div><div class="csp-empty-t">Run Preview compile to …</div></div>
</div>
```

### 3.10 Danger zone
Red-tinted card for destructive actions (`evx-danger`): red uppercase header, explanatory
body, one solid red button. Gate it behind "is this a custom/user-owned thing" so retail data
can't be nuked.

### 3.10b Section separators (group headers, nav categories)
A section separator is a **muted uppercase label with a single hairline rule above it**
(`border-top: 1px solid #24252c`, `color:#7e8698`, `font:700 10px`, letter-spacing ~`.09em`,
**no background**). First-of-group drops the rule. Applies to settings content group heads
(`.spane-grouphead`), nav categories (`.snav-cat`/`.lnav-cat`), and any "GROUP NAME" divider.
**Never** a filled band / dark rectangle — that reads as "slapped on". The hairline must contrast
with the canvas, so these live on the dark modal canvas (`#191a20`), not the light `.modal` grey.

Individual controls: checkboxes/sliders carry an accent (`accent-color` — `#82aaff` checkboxes,
`#7fd6e6` sliders), value read-outs are accent mono (`#7fd6e6`, tabular-nums), labels are `#cdd2e0`
(brighten on hover), secondary buttons are the ghost style (§3.5), hints are `#868d9c` 11.5px.

### 3.10b Form fields (inputs / selects / textareas)
ONE skin everywhere — a **borderless dark well**: `background:#0d0e12`, `border:none`,
`border-radius:8px`, focus = the accent ring `box-shadow: 0 0 0 3px color-mix(in srgb, #7fd6e6 18%, transparent)`
(never a focus border). `base.css` already ships this as the default, so **new fields need no
skin at all** — only layout (width/flex/padding). Never re-add a `1px solid` border or a 4-7px
radius to a field; if a state must read differently (disabled, changed), shift the background,
not the border. Deliberate exceptions: edge-to-edge search/SQL bars (`.db-table-search`,
`.db-query-input`) which are flat with a bottom hairline.

**Specificity trap:** the base `input:not(…)×6` rule is `(0,6,1)` — a plain `.my-input` class
**silently loses** to it for padding/background/border. Selects are fine (`select` is `(0,0,1)`).
If a field skin must override base on an `<input>`, repeat the six `:not()`s under your class
(see the `.cs-author` block in `css/events.css`) or use `!important` (the `.nz-input` pattern).

### 3.11 Modal shell & left-nav
Shared chrome in `css/modals.css` — used by the Cutscene author, Event dialogue, and Settings
modals. **Don't build a bespoke nav; reuse this one** so every modal matches.

- **Shell** (`.modal`): `1px solid #34363f` border + `border-radius:12px` + a soft drop shadow
  (`0 20px 55px -14px rgba(0,0,0,.7)`) — never a heavy pure-black border. Title bar (`.modal-bar`)
  stays one shade lighter (`#2a2a31`) than the body so it reads as a header.
- **Canvas**: premium modals set a **dark** body (`#191a20`) so the accent cards/tiles sit on the
  right tone — the default `.modal` grey (`#25252a`) is too light for the card language.
- **No id selectors; `.modal-body` has no padding.** Style modals with **classes**, never
  `#modal-id …` (an id scopes one instance and drifts from its sibling — both premium modals share
  `.evt-dialog-modal`; the cutscene one also has `.cs-author`). `.modal-body` is a bare layout
  container: **no padding on it, ever** — the nav rail and content pane own their spacing.
- **Padding (keep it uniform).** Content panes use **`12px 16px 28px`** (`.cs-tab-content`,
  `.evt-dialog-pane`), and the nav rail's **top padding matches the pane's top** (`12px`) so the
  first nav pill lines up with the first content card. Inner content wrappers (`.evx`, `.csp`) add
  **no** padding of their own — the pane owns it. One value, one source of truth; don't let a new
  tab set its own pane padding.
- **Left rail** (`.modal-lnav` + `.lnav-btn`): the rail is inset (`padding:12px`), buttons are
  **rounded pills** (`border-radius:9px`), and the active tab is an accent-tinted pill
  (`background: color-mix(in srgb, #7fd6e6 15%, transparent)` + a `color-mix(… 34% …)` inset ring +
  accent text/icon) — **not** a square edge-flush left border. Hover/active use **translucent**
  overlays (`#ffffff0e`, `color-mix(… , transparent)`) so they render correctly on any canvas.
  Category labels (`.lnav-cat`) are muted uppercase `#7e8698`. A prominent rail action (e.g. a
  launcher) uses `.lnav-action` — the same pill, tinted blue.

---

## 4. Gotchas (these have bitten us)

- **Base-theme specificity.** `base.css` styles bare `button` / `input` / `select`. Inside a
  panel with an **id** (e.g. `#cs-author-modal button` → specificity `1,0,1`), a plain class
  like `.csp-cta` (`0,1,0`) **loses**, so your gradient gets clobbered by `#484857`. Fix: scope
  the component rules under the same id — `#cs-author-modal .csp-cta` (`1,1,0`) wins. At the
  global level a plain `.ui-cta` class already beats `button`, so only id-scoped panels need this.
- **`position:fixed` inside `backdrop-filter`.** A `backdrop-filter` ancestor makes `fixed`
  children anchor to *it*, not the viewport — floating tips/menus jump off-screen. Append them to
  `document.body`. (See memory: `editor-fixed-in-backdrop-filter`.)
- **Always `esc()`** every interpolated string in these template literals (names, paths, errors).
- **`node --check <file>.js`** after every JS edit — the editor loads raw ES modules, a syntax
  error blanks the panel. Grep for an existing name before declaring one.
- **Stale web cache.** The dev server caches `main.js`; a hard refresh (Ctrl+Shift+R) is often
  needed to see CSS/JS changes. (See memory: `editor-web-view-stale-cache`.)

---

## 5. Checklist for a new section

1. Pick a 3-letter prefix; scope CSS under the panel's id if it lives in an id'd modal/panel.
2. Lead with a **hero** (identity + status), not an `<h3>`.
3. Numbers → **metric tiles** with `toLocaleString()` + delta chips, never raw `a → b bytes`.
4. Group content into **cards** with uppercase icon headers.
5. Exactly **one gradient CTA**; everything else is ghost/base buttons.
6. Long/secondary output → **collapsible folds**, open only when it's the primary content.
7. Give every list/panel an **empty state** and a **loading state**.
8. Destructive actions → **danger zone**, gated on ownership.
9. `node --check`, then hard-refresh the editor to verify.
