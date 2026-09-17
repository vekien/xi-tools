# PS2 PlayOnline Viewer disc (SLPM 621.34, November 2001)

Companion disc to the FINAL FANTASY XI β game disc documented in
[ps2_beta_2001.md](ps2_beta_2001.md). That disc is **SLPM 621.35** (typically mounted
as `E:\`). This one is **SLPM 621.34** (typically `F:\`): the PlayOnline Viewer / POL
client that ships beside the beta.

xi-tools' beta notes (§9) already say `FFXI_POL.ENC` is decrypted by a separate PS2
PlayOnline Viewer that is **not** on the game disc. SLPM 621.34 is that viewer.

Tools that already know the game disc live in the separate `xi-beta-viewer` tree
(`D:\xi-beta-viewer`). POL-disc browsing (CDFILE map, MWo3) is being added there.

No game ROM packs, FTABLE, or STABLE live on this disc. Do not point `xibeta … info`
(game mode) at `F:\`.

---

## 1. Identity

| | Game beta (`E:\`) | POL Viewer (`F:\`) |
|---|---|---|
| Volume label | `SLPM_62135` | `SLPM_62134` |
| Boot ELF | `SLPM_621.35` (~15.9 MB, DWARF) | `SLPM_621.34` (~1.65 MB) |
| SYSTEM.CNF VER | 1.02 · build `20011120_0` | 1.01 |
| Role | FFXI β + install packs | PlayOnline client / Viewer |
| `POLKEY.DAT` | 65,552 bytes | 65,552 bytes, **different** contents |

`SLPM_621.34` was built with **MW MIPS C Compiler (2.4.1.01)** (Metrowerks). Strings
cover the same PlayOnline `sq*` surface as the game ELF (`sqPolcon*`, IRC, mailer,
DNAS, `sqpolkey.bin`, …) without the fat game/DWARF payload.

MD5 (for disc hygiene, not secrets):

- `F:\POLKEY.DAT` — `4b630526c92211d11241789186473537`
- `E:\POLKEY.DAT` — `7d218399aa6a6861fe04343effc1d273`
- `F:\SLPM_621.34` — `da2b8719fcbc3ed137f7db1d51da0ea4`
- `E:\SLPM_621.35` — `296e9420a8dddf91bab6bee2a46e1f1a`

---

## 2. Layout

```
F:\
  SYSTEM.CNF          BOOT2 = cdrom0:\SLPM_621.34;1
  SLPM_621.34         Viewer ELF
  POLKEY.DAT
  A\ … L\ , Z\        ISO-style names 0.0, 0.1, … (POL assets)
  _\
    CDFILE.TXT        344 logical paths under POL\Data\PS2\…
    CDFILE.NUM
    IOPRP.IMG
    0\ 1\ 2\          packed payloads (includes *.pex.enc)
    _\                *.ERX network modules (IrxDecrypt)
```

`F:\_\CDFILE.TXT` lists logical install paths (mostly `.tm2` skins, plus `.png`,
`.fnt`, `.pex`, help, Tetra Master, `beta_license*.txt`, …). Walking letter folders
`A`→`Z` and sorting each folder's files by name lines up with CDFILE order
(357 letter files vs 344 CDFILE lines; trailing extras are non-CDFILE payloads).

Observed magics in letter folders include `TIM2`, PNG, `@ANG`, `BGM `, `WD`, `SeBl`,
and **`MWo3`** (the five `.pex` apps).

---

## 3. MWo3 — PlayOnline `.pex` modules

Five files:

| Disc path | CDFILE logical | Name |
|---|---|---|
| `A\0.0` | `POL\Data\PS2\chat.pex` | `Chat` |
| `A\0.6` | `…\login.pex` | `Login` |
| `A\0.7` | `…\mail.pex` | `Mail` |
| `A\0.8` | `…\manga.pex` | `Manga` |
| `A\0.9` | `…\pml.pex` | `Pml` |

### Header (64 bytes, little-endian)

| Off | Type | Field | Notes |
|----:|------|-------|-------|
| 0 | `char[4]` | `magic` | `MWo3` |
| 4 | `u32` | `ver` | Chat=5, Login=6, Mail=4, Manga=3, Pml=2 |
| 8 | `u32` | `load` | always `0x00E80000` (EE) |
| 12 | `u32` | `text_size` | |
| 16 | `u32` | `data_size` | |
| 20 | `u32` | `bss_size` | zero-fill after image |
| 24 | `u32` | `end` | `load + text_size + data_size + 0x40` |
| 28 | `u32` | `end` | duplicate of 24 |
| 32 | `char[32]` | `name` | NUL-padded ASCII |

Invariant: `filesize == 64 + text_size + data_size`.

Body: `[text][data]`. Text is EE code (often 64 zero bytes at the start of the text
blob). Data holds C++ RTTI / UI strings (`pol::CObj`, `aNavi_Chat_*`, `.tm2` skin
names, …).

`MWo3` does not appear as a literal in `SLPM_621.34`; the ELF does reference
`host0:POL/Data/PS2/polapp.pex` and `polmovie.pex`. Treat `MWo3` as a Metrowerks EE
module container (hypothesis: "MW overlay" family) until a loader xref names it.

---

## 4. Packed `.pex.enc` (open)

Under `F:\_\0\`…, records look like:

`u32 flags`, `u32 size`, `u32 size2`, `char path[256]`, then `size` bytes.

Seen encrypted members: `POL/Data/PS2/chat.pex.enc`, `login.pex.enc`,
`TetraMaster/TMaster.pex.enc`.

`chat.pex.enc` is 124,568 bytes (multiple of 8), entropy ≈ 8.0, not zlib. Plaintext
`chat.pex` (MWo3) is 322,432 bytes on the letter folders.

`sq_decrypt` from `xi-beta-viewer/research/pol/polcrypt.py`:

- Key `PolCtsAny` = `cb 83 24 b7 ba 3e 9e 0a` (from the game disc) → checksum fail, not MWo3.
- Early windows of `F:\POLKEY.DAT` as 8-byte keys → no hit in a first pass.

**Open lead:** key derivation for `.pex.enc` / `POLKEY.DAT` inside `SLPM_621.34`
(strings: `sqpolkey.bin`, `zoneauth: DECRYPT FAILED !`, `polbes: this is encrypted pfb version`).
Known-plaintext is available (letter-folder MWo3 vs packed `.enc`) once compression-or-framing
before `sqEncryptFile` is understood.

---

## 5. ERX modules

`F:\_\_\*.ERX` decrypt with the same `IrxDecrypt` as game-disc `MODULES/*.ERX`
(`polcrypt.irx_decrypt`, seed = length). Example: `LDR000.ERX` → `sqloader.irx` + ELF.

---

## 6. What this gives xi-tools

- Names and UI structure for the 2001 PlayOnline client (chat/login/mail/manga/PML).
- Another copy of the `sq*` cipher/IOP story, with a second `POLKEY.DAT` to compare.
- TIM2 / BGM / WD assets that match the sound/UI families already noted for the beta.
- A concrete home for the "missing Viewer" called out in [ps2_beta_2001.md](ps2_beta_2001.md) §9.

It does **not** replace the game disc for DAT / RES_TYPE / zone / character work — that
stays on SLPM 621.35.

---

## 7. CLI (xi-beta-viewer)

```bat
node scripts/xibeta.mjs F:\ pol-info
node scripts/xibeta.mjs F:\ mwo3 A/0.0
```

Set `XI_POL_DISC=F:\` for `tests/pol_mwo3.test.mjs`.
