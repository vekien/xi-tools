#!/usr/bin/env python3
"""Decode the 4 opcode sub-sections of a `0x05` ParticleGenerator effect.

Layout (validated against xim ParticleGeneratorParser): four sub-section offsets
live at section-start `+0x80` (4× u32, section-start relative). Each sub-section is
a stream of entries: a `u32 config` then payload; `opcode = config & 0xFF`,
`size = (config >> 8) & 0x1F` in **4-byte words** (so the whole entry is `size*4`
bytes), `alloc = config >> 0xD` (per-particle memory slot). Opcode `0x00` ends a
sub-section. Sub-sections: 1 = generator updaters, 2 = particle initializers,
3 & 4 = particle updaters.
"""

import struct
from typing import Dict, List, Optional

# Opcode name tables — transcribed verbatim from xim's sec1..sec4 handlers
# (ParticleGeneratorParser.kt). Sections 3 and 4 are DISTINCT handlers. Codes that
# fall back to "op_XX" are unknown to xim too: its handler returns false and logs
# "Unknown Particle SecN OpCode", so there is no authoritative name to give them.
# (Common undecoded-by-xim examples: sec1 0x15, sec2 0x2D/0x63.)
SEC1 = {  # generator updaters (one value per generator tick)
    0x04: "EmissionFrequency", 0x05: "RelativeVelocity", 0x06: "SphericalRadius",
    0x07: "SphericalRadiusVariance", 0x08: "Gen.RotationZ", 0x09: "Gen.RotationY",
    0x0A: "GeneratorCull", 0x0B: "Gen.BasePosX", 0x0C: "Gen.BasePosY",
    0x0D: "Gen.BasePosZ", 0x0E: "Gen.RotationX", 0x0F: "Gen.RotationY",
    0x10: "Gen.RotationZ", 0x11: "Association", 0x12: "Gen.VelocityX",
    0x13: "Gen.VelocityY", 0x14: "Gen.VelocityZ",
}
SEC2 = {  # particle initializers (run once per spawned particle)
    0x01: "StandardSetup", 0x02: "TranslationVelocity", 0x03: "PosVelVariance",
    0x06: "SphPosVarSimple", 0x07: "SphPosVarMedium", 0x08: "RelativeVelocity",
    0x09: "Rotation", 0x0A: "RotationVariance", 0x0B: "RotationVelocity",
    0x0C: "RotVelVariance", 0x0F: "Scale", 0x10: "ScaleVariance",
    0x11: "SingleScaleVariance", 0x12: "ScaleVelocity", 0x13: "ScaleVelVariance",
    0x16: "Color", 0x17: "ColorVariance", 0x18: "UniformColorVariance",
    0x19: "ColorTransform", 0x1A: "ColorTransformVariance", 0x1D: "SpriteSheet",
    0x1E: "BlendFunc", 0x1F: "SphPosVarFull", 0x30: "DepthBias",
    0x31: "RandomVelocity", 0x32: "HazeOffset", 0x39: "KeyFrameValue",
    0x3A: "RingMesh", 0x3B: "IncrementalRotation", 0x3C: "OnceChildGenerator",
    0x3D: "Oscillation", 0x3E: "OscAccelX", 0x3F: "OscAccelY", 0x40: "OscAccelZ",
    0x41: "RelVelVariance", 0x42: "GroundProjection", 0x43: "DeferredBlendFunc",
    0x44: "ChildGenerator", 0x45: "ParentPositionCopy", 0x46: "ParentVelocity",
    0x47: "ParentRotate", 0x48: "ParentColor", 0x49: "ParentScale",
    0x4A: "ParentTexCoord", 0x4C: "AudioRange", 0x4E: "FixedPointPosVar",
    0x4F: "FixedPointPosVar", 0x53: "ChildGenerator", 0x54: "PointListPosition",
    0x55: "SpecularParams", 0x56: "Batching", 0x58: "PointLightParams",
    0x67: "ReverseDisplacement", 0x68: "KF.ToDVolume", 0x69: "KF.VelocityDampener",
    0x6A: "ChildGenerator", 0x6B: "PathReference", 0x6C: "KF.PointLightParams",
    0x6D: "KF.ToDSpecColor", 0x6E: "KF.ToDSpecColor", 0x6F: "KF.ToDSpecColor",
    0x70: "KF.ToDSpecColor", 0x72: "ProjectionBias", 0x74: "KF.UVVelX",
    0x75: "KF.UVVelY", 0x76: "KF.RotVelX", 0x77: "KF.RotVelY", 0x78: "KF.RotVelZ",
    0x79: "ParentRotate", 0x7B: "ProgressPositionOffset", 0x7C: "KF.PLTheta",
    0x7D: "KF.PLRange", 0x7E: "ParentTheta", 0x7F: "ParentRange",
    0x80: "KF.PLThetaMul", 0x81: "KF.PLRangeMul", 0x83: "KF.ToDRotVelX",
    0x84: "KF.ToDRotVelY", 0x85: "KF.ToDRotVelZ", 0x88: "PointLightAttachment",
    0x8B: "KF.ToDRotX", 0x8C: "KF.ToDRotY", 0x8D: "KF.ToDRotZ", 0x8E: "FootMark",
    0x90: "DaylightColorAdjuster", 0x91: "DaylightColorSetup", 0x95: "KF.ToDPosX",
    0x96: "KF.ToDPosY", 0x97: "KF.ToDPosZ", 0x9B: "ParentPositionSnapshot",
}
SEC3 = {  # particle updaters (run every tick over a particle's life)
    0x02: "Position", 0x03: "VelocityAccel", 0x05: "Rotation", 0x06: "VelocityAccel",
    0x08: "Scale", 0x09: "VelocityAccel", 0x0B: "ColorTransformApplier",
    0x0C: "ColorTransformModifier", 0x0D: "SpriteSheetFrame", 0x0E: "AgeAdvance",
    0x0F: "Prog.PosX", 0x10: "Prog.PosY", 0x11: "Prog.PosZ", 0x12: "Prog.RotX",
    0x13: "Prog.RotY", 0x14: "Prog.RotZ", 0x15: "Prog.ScaleX", 0x16: "Prog.ScaleY",
    0x17: "Prog.ScaleZ", 0x18: "Prog.ColorR", 0x19: "Prog.ColorG", 0x1A: "Prog.ColorB",
    0x1B: "Prog.ColorA", 0x1C: "Prog.TexU", 0x1D: "Prog.TexV", 0x1E: "Prog.WeightMesh0",
    0x1F: "Prog.WeightMesh1", 0x20: "Prog.WeightMesh2", 0x21: "Prog.WeightMesh3",
    0x22: "Prog.WeightMesh4", 0x24: "Prog.HazeOffsetX", 0x25: "ChildGeneratorBasic",
    0x26: "VelocityRotator", 0x27: "TexCoordU", 0x28: "TexCoordV", 0x29: "OscillationX",
    0x2A: "OscillationY", 0x2B: "OscillationZ", 0x2C: "VelocityDampener",
    0x2E: "DrawDistance", 0x2F: "VelocityRotation", 0x30: "Prog.VelX", 0x31: "Prog.VelY",
    0x32: "Prog.VelZ", 0x33: "ChildGenerator", 0x34: "PointListPosition",
    0x35: "Prog.SpecRotX", 0x36: "Prog.SpecRotY", 0x37: "Prog.SpecRotZ",
    0x38: "Prog.SpecColorR", 0x39: "Prog.SpecColorG", 0x3A: "Prog.SpecColorB",
    0x3B: "Prog.SpecColorA", 0x3C: "Clock.ColorR", 0x3D: "Clock.ColorG",
    0x3E: "Clock.ColorB", 0x3F: "Clock.AlphaMul", 0x40: "Clock.ScaleX",
    0x41: "Clock.ScaleY", 0x42: "Clock.ScaleZ", 0x43: "Clock.Volume",
    0x44: "Prog.Dampening", 0x45: "MoonPhaseSprite", 0x46: "ChildGenerator",
    0x48: "DoubleRangeDrawDistance", 0x49: "Clock.PLTheta", 0x4A: "Clock.SpecColorR",
    0x4B: "Clock.SpecColorG", 0x4C: "Clock.SpecColorB", 0x4D: "Clock.SpecColorA",
    0x4E: "DayOfWeekColor", 0x4F: "MoonPhaseColor", 0x53: "Occlusion",
    0x54: "TexU.Scroll", 0x55: "TexV.Scroll", 0x56: "Rot.X.Add", 0x57: "Rot.Y.Add",
    0x58: "Rot.Z.Add", 0x59: "AngularDistanceRot", 0x5B: "Prog.PLTheta",
    0x5C: "Prog.PLRange", 0x5D: "Prog.PLThetaMult", 0x5E: "Prog.PLRangeMult",
    0x5F: "CameraShake", 0x60: "ScreenFlash", 0x61: "ClockRot.X", 0x62: "ClockRot.Y",
    0x63: "ClockRot.Z", 0x66: "Clock.RotX", 0x67: "Clock.RotY", 0x68: "Clock.RotZ",
    0x69: "DaylightColorApplier", 0x6B: "Clock.PosX", 0x6C: "Clock.PosY",
    0x6D: "Clock.PosZ", 0x6E: "DoubleRangeWeightedMesh",
}
SEC4 = {  # particle expiration handlers (fire when a particle dies)
    0x01: "EmitChild", 0x05: "RepeatExpiration",
}
_NAMES = {1: SEC1, 2: SEC2, 3: SEC3, 4: SEC4}

_OFF_SECTION_TABLE = 0x80  # section-start: 4x u32 sub-section offsets


def decode_subsections(body: bytes, max_ops: int = 256) -> Optional[Dict[str, List[dict]]]:
    """Walk the 4 opcode sub-sections. Returns {"section1":[...], ...} where each
    entry is {op, name, size, alloc, hex[, floats]}. None if the table is absent."""
    if len(body) < _OFF_SECTION_TABLE + 16:
        return None
    offs = struct.unpack("<4I", body[_OFF_SECTION_TABLE:_OFF_SECTION_TABLE + 16])
    out: Dict[str, List[dict]] = {}
    for idx, off in enumerate(offs, start=1):
        ops: List[dict] = []
        pos = off
        names = _NAMES[idx]
        while 0 < pos and pos + 4 <= len(body) and len(ops) < max_ops:
            config = struct.unpack("<I", body[pos:pos + 4])[0]
            opc = config & 0xFF
            size = (config >> 8) & 0x1F
            alloc = config >> 0xD
            if opc == 0x00 or size == 0:
                break
            payload = body[pos + 4:pos + size * 4]
            entry = {"op": f"0x{opc:02X}", "name": names.get(opc, f"op_{opc:02X}"),
                     "size": size, "alloc": alloc, "hex": payload.hex()}
            if payload and len(payload) % 4 == 0:
                fl = struct.unpack(f"<{len(payload) // 4}f", payload)
                if all(abs(v) < 1e9 and v == v for v in fl):
                    entry["floats"] = [round(v, 4) for v in fl]
            ops.append(entry)
            pos += size * 4
        out[f"section{idx}"] = ops
    return out
