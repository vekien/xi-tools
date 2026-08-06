"""Spell → visual-effect resolution (ports xim/UE5 SpellTables + EffectRoutine).

A spell index resolves to its effect DAT via the animation table (``0xAF0 + animIndex``)
and is *played* by flattening its ``main`` 0x07 EffectRoutine into a timed schedule of
0x05 particle-generator spawns.  See :mod:`xi.spell.xi_spell`.
"""
