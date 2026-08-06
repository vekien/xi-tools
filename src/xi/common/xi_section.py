#!/usr/bin/env python3
"""Packing helpers for the 16-byte DAT section (chunk) header.

Every DAT chunk starts with a 4-byte tag followed by a packed u32 "info" word at
offset +4::

    bits  0-6   type
    bits  7-25  size, in 16-byte units      <- 19 bits
    bit   26    is_shadow
    bit   27    is_extracted
    bits 28-30  ver_num
    bit   31    is_virtual

The size field is **19 bits, not 20**. This is confirmed against the retail client:
the chunk walker in the unpacked FFXiMain code section reads the info word, shifts
right by 7, masks with ``0x7FFFF`` and shifts left by 4 to advance to the next
chunk. Nine sites share that exact shape (``1003C989``, ``10056829``, ``10071096``,
``10071C1E``, ``100732C9``, ``1007343B``, ``100735B0``, ``100735CC``, ``100735F6``)
and none use a wider mask. The bitfield above also sums to exactly 32 bits, so a
20-bit size could only exist by deleting one of the named flags.

That matters when *writing*. Bit 26 belongs to ``is_shadow``, so a section whose
size overflows 19 bits does not simply get a bigger number — the carry lands in the
flags, and the client (which masks to 19 bits) then reads the section as 8 MiB
*shorter* than it was written. The next-chunk boundary falls inside the payload and
the walk desynchronises: corrupt zone or hard crash, from a DAT that still looks
well-formed to any parser that reads the size as 20 bits.

Retail runs close to this ceiling — ``ROM4/0/8.DAT`` (Ilrusi Atoll) carries a
``0x1C`` section at 98.58% of it — so writers must refuse to overflow rather than
silently corrupt the flags.
"""

SECTION_SIZE_BITS = 19
SECTION_SIZE_SHIFT = 7
SECTION_UNIT = 0x10

MAX_SECTION_UNITS = (1 << SECTION_SIZE_BITS) - 1        # 0x7FFFF
MAX_SECTION_BYTES = MAX_SECTION_UNITS * SECTION_UNIT    # 8,388,592 bytes

_SIZE_MASK = MAX_SECTION_UNITS << SECTION_SIZE_SHIFT    # bits 7-25
_TYPE_MASK = 0x7F                                       # bits 0-6


class SectionTooLargeError(ValueError):
    """A section would not fit the client's 19-bit size field."""


def size_units(padded_size: int, *, what: str = "section") -> int:
    """Convert a padded byte length to 16-byte units, refusing 19-bit overflow."""
    if padded_size <= 0:
        raise ValueError(f"{what} size must be positive, got {padded_size}")
    if padded_size % SECTION_UNIT:
        raise ValueError(
            f"{what} size {padded_size} is not a multiple of {SECTION_UNIT} "
            "(pad before encoding the header)"
        )
    units = padded_size // SECTION_UNIT
    if units > MAX_SECTION_UNITS:
        raise SectionTooLargeError(
            f"{what} is {padded_size:,} bytes, over the client's 19-bit section "
            f"limit of {MAX_SECTION_BYTES:,} bytes (by {padded_size - MAX_SECTION_BYTES:,}). "
            "Writing it would overflow into the is_shadow flag and the client would "
            "read the section 8 MiB short, desynchronising the chunk walk."
        )
    return units


def encode_section_meta(padded_size: int, type_code: int, flags: int = 0,
                        *, what: str = "section") -> int:
    """Build a section info word from a padded size and type code.

    ``flags`` supplies any bits outside the size and type fields (is_shadow,
    is_extracted, ver_num, is_virtual); its size and type bits are ignored.
    """
    units = size_units(padded_size, what=what)
    preserved = flags & ~(_SIZE_MASK | _TYPE_MASK) & 0xFFFFFFFF
    return preserved | (units << SECTION_SIZE_SHIFT) | (type_code & _TYPE_MASK)


def set_section_size(meta: int, padded_size: int, *, what: str = "section") -> int:
    """Replace the size field of an existing info word, leaving every other bit alone."""
    units = size_units(padded_size, what=what)
    return (meta & ~_SIZE_MASK & 0xFFFFFFFF) | (units << SECTION_SIZE_SHIFT)
