"""Run *inside* Blender (``blender -b --python _fbx_to_gltf.py -- <in.fbx> <out.gltf>``).

Imports an FBX and re-exports it as a separate-file glTF (.gltf + .bin) so the
mesh importer can read positions / weights / UVs back with the standard glTF
accessor reader. Bone names are preserved so joint indices survive the trip.
"""

import sys

import bpy


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    fbx_in, gltf_out = argv[0], argv[1]

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    bpy.ops.import_scene.fbx(filepath=fbx_in)

    bpy.ops.export_scene.gltf(
        filepath=gltf_out,
        export_format="GLTF_SEPARATE",
        export_animations=False,
        export_skins=True,
        export_influence_nb=2,
        export_yup=True,
        use_selection=False,
    )


if __name__ == "__main__":
    main()
