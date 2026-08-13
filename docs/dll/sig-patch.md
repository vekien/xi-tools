# xi dll ffximain sig-gen / sig-apply

**Version-resilient patching.** An address [`.patch`](patch.md) is pinned to one
exact build — every function moves when the client is rebuilt, so the `va`s go
stale and the whole patch aborts (see [ffximain/inventory.md §10](../ffximain/inventory.md)).
A **signature** locates each edit by its surrounding *code pattern* with the
volatile bytes (absolute addresses, call/jmp targets) wildcarded, so an edit
follows its function wherever the new build put it. Only genuinely **rewritten**
code fails to resolve — and it fails *loud* (reported, never written).

This is the tool for the CatsEye workflow: rebase the client on a newer retail
drop, re-run `sig-apply`, ship the patched DLL. Most sites re-apply automatically;
the handful SE actually changed are flagged for a one-time re-derive.

```
uv run xi dll ffximain sig-gen   --unpacked BUILD --patch ADDR.patch --output OUT.sigpatch
uv run xi dll ffximain sig-apply --unpacked BUILD --sig OUT.sigpatch [--output DLL] [--dry-run]
```

---

## sig-gen — build the signature file

Converts an address `.patch` + **the build it was authored against** into a
`.sigpatch` (JSON). Each edit gets the smallest surrounding signature that
matches **only** sites the patch intends to touch — verified against the known
edit set, so a signature can never land on an unrelated location. Edits in dense,
self-similar code that can't get a safe signature fall back to an address pin
(so the source build still applies 100%, and they fail loud elsewhere).

| Option | Description |
|---|---|
| `--unpacked PATH` | The unpacked build the `.patch` targets (its `expect` bytes must match). |
| `--patch PATH` | Address `.patch` to convert. |
| `--output PATH` | Output `.sigpatch` (JSON). |

```
uv run xi dll ffximain sig-gen \
  --unpacked FFXiMain_unpacked.dll \
  --patch docs/ffximain/ffximain_inventory.patch \
  --output docs/ffximain/ffximain_inventory.sigpatch
```
```
Address edits    : 183
Signature entries: 159  (unique=149 multi=9)
Address-fallback : 1  (no safe signature — pinned to va)
```

The 183 address edits collapse to 159 entries because identical sites (e.g. the
18 container `operator_new(0x13bc)` allocs) share one signature applied to all
matches. **9 multi** entries carry an expected match count; **1** dense pane-mult
edit had no safe signature and kept an address pin.

## sig-apply — apply / dry-run against any build

Scans the target's `.text` for each signature and applies the edit at the match.
**Locate-all-then-write**, so co-located edits don't disturb each other. Idempotent
(already-patched sites are skipped) and safe (an `expect` mismatch is reported,
never overwritten).

| Option | Description |
|---|---|
| `--unpacked PATH` | Unpacked DLL to patch. |
| `--sig PATH` | The `.sigpatch` from `sig-gen`. |
| `--output PATH` | Output DLL [default: overwrite `--unpacked`]. |
| `--dry-run` | Report only; write nothing. |

### Applied to the build it came from — exact
```
uv run xi dll ffximain sig-apply --unpacked FFXiMain_unpacked.dll \
  --sig docs/ffximain/ffximain_inventory.sigpatch --dry-run
```
```
  would-apply    : 183
  MISSING        : 0
  ambiguous      : 0
```
A real (non-dry-run) apply reproduces the hand-built 120 DLL **byte-for-byte**.

### Applied to a different build — the point
Dry-run against the retail Aug-2026 client (same base + the large-inventory
hotfix):
```
  would-apply    : 146
  MISSING (rewritten / not found): 35
```
**146 of the sites relocate and apply with zero work.** The 35 `MISSING` are the
storage-core / bag-walk / container-UI functions the hotfix rewrote — exactly the
code you'd re-derive by hand anyway. They're reported by note so you know which,
and nothing is written while any are unresolved-by-choice (dry-run) — a real run
applies the 146 and lists the 35.

---

## What survives a client update, and what doesn't

| Change in the new build | Result |
|---|---|
| Function **moved** (relocation) | ✅ signature still matches — auto-applies |
| Function body **unchanged**, addresses shifted | ✅ address operands are wildcarded — auto-applies |
| Function **rewritten** (SE changed that code) | ⚠️ signature gone → `MISSING`, re-derive that site (guided by [inventory.md](../ffximain/inventory.md)) |
| A signature becomes **non-unique** in the new build | ⚠️ `ambiguous` → not applied, reported |

No scheme escapes the "rewritten code" case — the target no longer exists. The
win is that it's **loud and localized**: you re-derive a handful of flagged sites
instead of the whole patch, and the distribution model is unchanged (ship the
patched DLL through the launcher, which verifies game files).

## Format

`.sigpatch` is JSON. `sig`-mode entries carry an IDA-style `pattern` (space hex,
`??` wildcards), an `edit_offset` into the match, `expect`/`replace` bytes, and a
`count` (expected matches). `addr`-mode fallback entries carry a `va` instead.

```json
{ "mode": "sig",
  "pattern": "81 e1 ff 00 00 00 8a 84 16 07 98 00 00 8d 04 c0 8d 04 c0 03 c1 8d 0c 80",
  "edit_offset": 13, "expect": "8d04c08d04c0", "replace": "6bc079909090",
  "count": 1, "note": "storage: 121-slot table core, object-relative" }
```

## See also

- [patch.md](patch.md) — the address-pinned `.patch` this is generated from
- [ffximain/inventory.md](../ffximain/inventory.md) — the 80→120 change + §10 portability
- [unpack.md](unpack.md) / [pack.md](pack.md) — unpack → sig-apply → pack
