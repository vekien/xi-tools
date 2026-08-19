# Character creation → xi-model-viewer progress

**Bar:** live FFXI character creation (same race/gender/face sequences).

## Status

| piece | status | notes |
|---|---|---|
| DAT inventory (procmon) | **done** | 439 unique paths classified |
| Skeleton map | **done** | skeletons inside mesh DATs (SQLE type 11) |
| Animation map | **done** | cluster −4…+1 + cameras + cues |
| Camera map | **done** | 2 cams × (fov+matrix) per race |
| Zone map | **done** | none — UI stage, not a zone |
| Docs | **done** | `docs/model/character_creation_dats.md` |
| 8 faces × A/B textures | **done** | A/B = 2nd faceCHG map in head DMB |
| headY neck bone align | **done** | body4.y − head1.y (not chin median) |
| pb equip ↔ clip pairing | **done** | idle/m1–3 naked; seq/m4 equip |
| FrameChannel 60fps | **done** | was 30 → half-speed / frozen feel |
| Animation panel on creation | **done** | was gated on browserKind===entity |
| Zone ROM/1/5.DAT | **documented** | stage backdrop |
| Long seq OC action bytecode | **open** | pose track only without it |
| Load zone in viewer | **open** | optional backdrop |

## Biggest remaining gap vs game

1. **Creation sequence** is still pose-track only — needs OC:01.00 → race action table bytecode.
2. Side-by-side visual critic pass (timing, head attach, camera) against live client.

## Artifacts

| path | what |
|---|---|
| `D:\xi-tools\research\procmon_character_creation_dats.txt` | unique DAT list from capture |
| `D:\xi-tools\research\procmon_charcreate_classified.csv` | classified inventory |
| `D:\xi-tools\research\classify_charcreate_dats.py` | classifier |
| `D:\xi-tools\research\validate_creation_pairs.py` | 384-pair checker |
| `D:\xi-tools\docs\model\character_creation_dats.md` | full doc |
| `D:\xi-model-viewer\ui\js\creation.js` | races, clips, parsers, animator |
| `D:\xi-model-viewer\ui\src\CreationList.jsx` | UI composer |

Last update: face A/B mesh fix + bump wrap + 384/384 validation.
