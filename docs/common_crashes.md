# Common Client Crashes

A field guide to crashes the FFXI client (Ashita) throws after a xi publish, and how
to diagnose them fast. The client rarely tells you *why* it died — it just closes — so
most of the work is reading the Ashita log and diffing DATs/tables against retail.

Each entry below is a real crash we hit, its root cause, the signature to look for, and
the fix.

---

## Where to look first

1. **Ashita log** — `<client>\Ashita\logs\MM_DD_YYYY HH.MM.SS.txt`. The newest file is
   the last run. A crash usually shows as the log **ending abruptly** a fraction of a
   second after the client reads the DAT that triggered it (e.g. a cutscene's event DAT
   `ROM/21/52.DAT`). There's no exception line — the last few `pivot | lpFileName = …`
   entries tell you *what it was loading when it died*.
2. **The overlay pivot** — Ashita's `pivot` plugin serves DATs from
   `Ashita\polplugins\DATs\<pack>\…` **in place of** the Game-dir copies. Grep the log
   for `using overlay` to see which files (and crucially which **FTABLE/VTABLE** tables)
   are coming from a pack instead of `Game\FINAL FANTASY XI\`.
3. **Diff against retail** — for a DAT that "looks fine," compare its section layout /
   byte template against a known-good retail DAT of the same kind before assuming the
   writer is wrong. Most crashes are *registration/routing* bugs, not malformed bytes.

---

## Crash: cutscene with a camera track crashes on play

**Symptom.** Dialogue-only cutscenes work. Add a camera track → client closes as
soon as the shot starts. Ashita log ends right after the event DAT read; often
**no** scene DAT open is logged.

**This is almost always one of the rules below.** Full write-up:
**[events/camera_scene_ids.md](events/camera_scene_ids.md)** — read it before
changing camera allocation.

### 1) High scene ref `p` / file id in the 71k band (★ #1 cause, 2026-07-15)

Event stores small **`p`**; client uses `file_id = 30704 + datid_helper(p)`:

| `p` | file-id band | Custom cameras |
|-----|----------------|----------------|
| 0..299 | 30704..31003 | Retail — do not allocate |
| **300..599** | **56941..57240** | **SAFE only band** |
| ≥ 600 | 70947..~76k | **CRASHES** |

**A/B:** same *retail* camera DAT at `p=53` / `p=599` works; at `p=1019`
(fid **71366**) instant crash. **Bytes do not matter** — high-tier id is enough.

**Fix (shipped):** `_camera_scene_fileid` only mid-band; rejects stale 71k
`cameraSceneFileId` on publish.

### 2) Scene DAT under ROM10 instead of base ROM

Retail camera packs use **`ROM/…` + VTABLE=1**. Custom scenes under **`ROM10`**
(and pivot ROM10 overlays) caused endless “works until pack deploy” failures.
Publish now writes **`ROM/490/<slot>.DAT`**, `vt=1`, clears old ROM10 entries,
mirrors into pivot as `…/pack/ROM/490/<slot>.DAT`.

### 3) Look-at ~100m from eye

Editor used to store `look = eye + forward×100`. Retail keeps look **~2m** from
eye. Far look-ats in *custom* scenes crash. Capture uses ×2.5; compile
`_sanitize_look` clamps.

### 4) Multi-point route with `interpMode = 0`

Still keyframes often have `smooth: 0`. That was stamped onto the whole curve →
mode 0 on a 3-point route → crash. Multi-point now forces mode **4** (retail).

### 5) Pivot / overlay table shadow (secondary)

If a pack ships FTABLE/VTABLE, Game-only patches are invisible. **Publish
Cutscenes to Pivot** (default ON) patches pack root tables + copies the scene
DAT. Restart **client** after table/scene path changes.

### Log signature
```
pivot | lpFileName = '…//ROM/21/52.DAT'     ← event load
<log ends ~1s later>                         ← died on camera 0x45
# often no open of the scene DAT at all
```

### Quick checklist
1. Disasm camera `0x45` → file id? If **71xxx**, reallocate mid-band.
2. Path under **`ROM/`**, VTABLE **1**, not ROM10.
3. Look dist ~2–3; multi-point mode **4**.
4. Pivot root FTABLE/VTABLE + pack `ROM/…` scene copy.
5. Restart client (not necessarily map server).

---

## Crash: cutscene churns a new scene file every publish

**Symptom.** A camera cutscene that worked once starts crashing after you re-publish it
a few times, and `ROM10/490/` fills up with extra `<slot>.DAT` files.

**Root cause.** Each publish was **allocating a fresh** camera-scene file-id instead of
overwriting the same one. The running client cached the (empty) slot at startup, so every
newly-allocated file was one the client couldn't load → crash on `0x45`.

**Fix (shipped).** The camera-scene file-id is round-tripped through the saved cutscene
def (`cameraSceneFileId`) and echoed back to the editor, so republishing **overwrites the
same file** rather than leaking a new one. See `_camera_scene_fileid` in
[`xi_bridge.py`](../src/xi/zone/xi_bridge.py). If you see churn again, check that the
editor is sending `cameraSceneFileId` back on publish and that the id still falls in a
reachable datid band.

**Related class.** Both this and the overlay-shadow crash above are the same failure
mode: **the file-id the event references doesn't resolve to a valid DAT in the table set
the client is actually reading.** When a cutscene crashes on load, that's the first thing
to check.

---

## Crash: custom zone throws FFXI-2003 "Failed to read data" on zone-in

**Symptom.** Zoning into a custom zone dies immediately (sometimes with the FFXI-2003
error box rather than a silent close).

**Root cause.** The client computes a fixed set of per-zone companion file-ids
(model + 1100 / + 1700 / + 2600 for event / dialog / **npc-entity** list) and requires
**all** of them to resolve — especially the npc/entity file (model + 2600). A blank
`zone new` that didn't clone all three companions leaves one unregistered.

**Fix.** `clone_zone_companions` in [`xi_inject.py`](../src/xi/zone/xi_inject.py) copies
event/dialog/npc DATs from a template zone and registers them. If you hit FFXI-2003 on a
custom zone, confirm all three companion ids resolve in the client's tables (same
Game-vs-overlay diff as above applies).

**Related.** [zone/zones.md](zone/zones.md), [zone/templates.md](zone/templates.md).

---

## General checklist when the client crashes after a publish

1. Open the newest Ashita log; note the **last DAT read** before it ends.
2. Grep the log for `using overlay` — is a **pack** serving the FTABLE/VTABLE for the
   ROM namespace you published into (usually `ROM10`)? If so, suspect a shadowed
   registration.
3. Diff the offending **file-id's table entry** across Game vs every overlay pack. They
   must agree (same `subdir`/`slot`, VTABLE version `10` for custom content).
4. Only after tables check out, diff the **DAT bytes/section layout** against a retail
   equivalent — malformed bytes are the rarer cause.
5. After fixing tables, **restart the client** (it caches the table set at startup) and,
   for the editor backend, kill anything on `:8777` before relaunching + hard-refresh.
