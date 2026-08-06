import struct

from xi.xi_config import MAX_ENTITY_MODELID

MODEL_FILE_OFFSET = 98239
MODEL_SAFE_START  = 15000
# Highest custom entity modelid the expanded FTABLE/VTABLE has slots for, and the
# ceiling inject/recommend validate against. Single source of truth lives in
# xi_config (MAX_ENTITY_MODELID); the gear region is derived to start just above
# the file_id this maps to, so entity and gear can never collide.
MODEL_SAFE_END    = MAX_ENTITY_MODELID

# Band boundaries per the FFXiMain resolver at VA 0x100C513D. Byte-checked
# against the retail FTABLE (2026-08): band 3 is only REGISTERED for modelids
# 3000-3193 (fids 99907-100100; 3194-3499 are all VTABLE=0 and no retail
# mob_pools row uses them), and band 4's first registered fid is exactly
# 101739 = 3500 + 98239. An external claim of a +98546 band-4 base is refuted
# by that alignment (run at fid 102239 = modelid 4000 under +98239).
RANGES = [
    (0,    1499,  1300),
    (1500, 2999, 50295),
    (3000, 3499, 96907),
    (3500, None, 98239),
]
# Scan/display ceiling for the open-ended (3500, None) modelid range used by
# `entity list`. Keep it at least as high as the custom entity ceiling so flooded
# custom models above retail's ~25000 extent still show up.
MAX_3500_MODELID = max(25000, MAX_ENTITY_MODELID)


def modelid_to_file_id(modelid: int) -> int:
    for start, end, offset in RANGES:
        if end is None or start <= modelid <= end:
            return modelid + offset
    raise ValueError(f'modelid {modelid} is out of all known ranges')


def modelid_blob(modelid: int) -> str:
    raw = struct.pack('<HH', 0, modelid) + bytes(16)
    return '0x' + raw.hex().upper()
