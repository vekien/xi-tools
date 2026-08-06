"""Per-joint body part scaling for entity DATs.

Scales specific joints' attached vertices without modifying bone
translations. This makes body parts (head, arms, etc.) grow in-place
while staying connected to the body.
"""

import struct


def parse_joint_scales(spec: str) -> dict:
    """Parse a joint scale spec string into {joint_idx: scale_factor}.

    Format: "5-7:4.0" or "5-7:4.0,20-30:2.0" for multiple ranges.
    Single joints: "5:3.0,10:2.0"
    """
    result = {}
    for part in spec.split(','):
        part = part.strip()
        if ':' not in part:
            raise ValueError(f'Invalid joint scale spec: {part} (expected RANGE:SCALE)')
        range_str, scale_str = part.rsplit(':', 1)
        scale = float(scale_str)
        if '-' in range_str:
            lo, hi = range_str.split('-')
            for j in range(int(lo), int(hi) + 1):
                result[j] = scale
        else:
            result[int(range_str)] = scale
    return result


def scale_joints_inplace(data: bytearray, sections: list,
                         joint_scales: dict) -> dict:
    """Scale specific joints' attached vertices in all mesh sections.

    joint_scales: {joint_index: scale_factor, ...}
    All descendants of specified joints are automatically included.

    Returns stats dict with 'joints' and 'vertices' counts.
    """
    stats = {'joints': 0, 'vertices': 0}

    # Find skeleton section for hierarchy
    skel_section = next((s for s in sections if s.type_code == 0x29), None)
    if not skel_section:
        return stats

    ds = skel_section.data_start
    num_joints = data[ds + 0x02]
    joints_start = ds + 0x04
    JOINT_SIZE = 30

    # Build parent map
    parents = []
    for i in range(num_joints):
        parents.append(data[joints_start + i * JOINT_SIZE])

    # Expand: include all descendants of specified joints
    expanded = {}
    for joint_idx, scale in joint_scales.items():
        expanded[joint_idx] = scale
        changed = True
        while changed:
            changed = False
            for i in range(num_joints):
                if i not in expanded and parents[i] in expanded:
                    expanded[i] = expanded[parents[i]]
                    changed = True

    stats['joints'] = len(expanded)

    # Scale vertices attached to expanded joints (all mesh sections)
    for mesh_section in (s for s in sections if s.type_code == 0x2A):
        mds = mesh_section.data_start
        flags3 = data[mds + 0x02]
        cloth = (flags3 & 0x01) != 0
        has_normals = not cloth

        try:
            vert_joints, single_count, double_count, vd_off = \
                _get_joint_array(bytes(data), mds)
        except (struct.error, IndexError):
            continue

        vertex_base = mds + vd_off

        # Scale single-joint vertices
        stride_single = 24 if has_normals else 12
        for i in range(single_count):
            if i < len(vert_joints) and vert_joints[i] in expanded:
                s = expanded[vert_joints[i]]
                base = vertex_base + i * stride_single
                for j in range(3):
                    off = base + j * 4
                    val = struct.unpack_from('<f', data, off)[0]
                    struct.pack_into('<f', data, off, val * s)
                stats['vertices'] += 1

        # Scale double-joint vertices
        stride_double = 56 if has_normals else 32
        double_base = vertex_base + single_count * stride_single
        for i in range(double_count):
            idx = single_count + i
            if idx < len(vert_joints):
                j0, j1 = vert_joints[idx] if isinstance(vert_joints[idx], tuple) \
                    else (vert_joints[idx], vert_joints[idx])
                if j0 in expanded or j1 in expanded:
                    s = expanded.get(j0, expanded.get(j1, 1.0))
                    base = double_base + i * stride_double
                    for j in range(6):
                        off = base + j * 4
                        val = struct.unpack_from('<f', data, off)[0]
                        struct.pack_into('<f', data, off, val * s)
                    stats['vertices'] += 1

    return stats


def _get_joint_array(data: bytes, ds: int) -> tuple:
    """Read the joint mapping array from a mesh section (0x2A).

    Returns (vert_joints, single_count, double_count, vd_off).
    """
    flags3 = data[ds + 0x02]
    use_joint_array = (flags3 & 0x80) != 0

    off = ds + 6
    struct.unpack_from('<I', data, off)[0]; off += 6
    joint_array_off = struct.unpack_from('<I', data, off)[0] * 2; off += 4
    num_joints = struct.unpack_from('<H', data, off)[0]; off += 2
    vc_off = struct.unpack_from('<I', data, off)[0] * 2; off += 6
    vjm_off = struct.unpack_from('<I', data, off)[0] * 2; off += 6
    vd_off = struct.unpack_from('<I', data, off)[0] * 2

    # Read joint array
    joint_array = []
    if use_joint_array:
        for i in range(num_joints):
            joint_array.append(
                struct.unpack_from('<H', data, ds + joint_array_off + i * 2)[0])

    single_count = struct.unpack_from('<H', data, ds + vc_off)[0]
    double_count = struct.unpack_from('<H', data, ds + vc_off + 2)[0]

    # Read vertex-to-joint mapping for single-joint vertices
    vert_joints = []
    p = ds + vjm_off
    for i in range(single_count):
        ref = struct.unpack_from('<H', data, p)[0]
        joint_idx = ref & 0x7F
        if use_joint_array and joint_idx < len(joint_array):
            joint_idx = joint_array[joint_idx]
        vert_joints.append(joint_idx)
        p += 4

    # Double-joint vertices
    for i in range(double_count):
        ref0 = struct.unpack_from('<H', data, p)[0]
        ref1 = struct.unpack_from('<H', data, p + 2)[0]
        j0 = ref0 & 0x7F
        j1 = ref1 & 0x7F
        if use_joint_array:
            j0 = joint_array[j0] if j0 < len(joint_array) else j0
            j1 = joint_array[j1] if j1 < len(joint_array) else j1
        vert_joints.append((j0, j1))
        p += 4

    return vert_joints, single_count, double_count, vd_off
