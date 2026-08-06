# FFXI `menu` DAT Text Encoding — ROR-1 (rotate-right-1-bit)

Text inside `menu`-magic DATs (e.g. the mission/quest text DB `ROM/118/115.DAT`) is **not
plain ASCII**. Each byte is stored **rotated left 1 bit**. To read it, **rotate each byte right
by 1 bit**. This is why plain-text `grep`/`bytes.find()` for strings like `San d'Oria` or
`Mission` find nothing in these files.

This is a lightweight obfuscation, not encryption — there is no key.

---

## The codec

```python
def ror1(b: bytes) -> bytes:
    """Decode: stored bytes -> plaintext (rotate each byte right 1 bit)."""
    return bytes(((c >> 1) | ((c & 1) << 7)) & 0xff for c in b)

def rol1(b: bytes) -> bytes:
    """Encode: plaintext -> stored bytes (rotate each byte left 1 bit)."""
    return bytes(((c << 1) | (c >> 7)) & 0xff for c in b)
```

`ror1` and `rol1` are inverses. `ror1(rol1(x)) == x`.

### Reading text from a file

```python
data = open("ROM/118/115.DAT", "rb").read()
plain = ror1(data[start:end])          # decode a region
print(plain.decode("latin1", "replace"))
```

### Searching for a string (the gotcha)

You cannot `find()` the plaintext directly — encode the needle first:

```python
needle = rol1(b"Mission Orders:")     # build the encoded form
offset = data.find(needle)            # then search the raw file
```

---

## How it was discovered

Decoding `ROM/118/115.DAT` section `sd_ms_e`: a 15-byte sequence
`9a d2 e6 e6 d2 de dc 40 9e e4 c8 ca e4 e6 74` recurred verbatim at multiple offsets
(identical ciphertext ⇒ position-independent transform, i.e. not a rolling key). Single-byte XOR
produced no readable result for any key, so the transform was bit-level. Testing the 8 bit
rotations, **rotate-right-1** turned the bytes into `Mission Orders:`. Decoding a wider region
then yielded:

```
Mission 5 … The Three Kingdoms (San d'Oria) … Mission Orders: …
Mission 6 … The Three Kingdoms (Bastok)     … Mission Orders: …
```

### Bit-rotation reference (all 8, for the probe sequence)

| Rotation | Result |
|---|---|
| **ROR 1** | `Mission Orders:` ✅ |
| ROR 2 / ROL 6 | garbage |
| ROR 4 / ROL 4 | garbage |
| ROL 1 (ROR 7) | garbage |

Only ROR-1 yields text.

---

## Where it applies

- **Applies:** `menu`-magic DATs that store body text — confirmed on `ROM/118/115.DAT`
  (mission/quest text). Likely the same for other `menu`-format text sections.
- **Does NOT apply:** `XISTRING` files (`ROM/97/*` menu labels) — those are **plain ASCII**.
  The auto-translate dictionary (`ROM/168/25.DAT`) is also **plain ASCII** (with `02 02`
  control-code prefixes). Only the bit-rotated `menu` body text needs ROR-1.

### Quick test for "is this file ROR-1 encoded?"

If a DAT clearly holds text but ASCII greps come up empty, ROR-1-decode a chunk and look for
readable words. If `rol1(b"Mission")` (or any expected word) is `find()`-able in the raw bytes,
it's ROR-1.
