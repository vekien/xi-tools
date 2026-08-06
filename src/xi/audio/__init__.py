"""FFXI audio: decode `.bgw` (music) and `.spw` (sound effects) to WAV.

Pure-Python port of the Windower pol-utils FFXI ADPCM codec — no external
binary (vgmstream) required. Covers ADPCM and raw PCM, which is everything the
Windows FFXI client ships. See xi.audio.xi_core for the format details.
"""
