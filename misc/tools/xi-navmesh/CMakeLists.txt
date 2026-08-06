cmake_minimum_required(VERSION 3.15)
cmake_policy(SET CMP0091 NEW)   # honour MSVC_RUNTIME_LIBRARY (static-link the CRT)
project(xi_navmesh CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Bundled RecastNavigation (Recast + Detour) + the tile chunker, plus our wrapper.
# NOTE: do NOT define DT_POLYREF64 — Detour then uses 32-bit poly/tile refs, which
# is what the CatsEyeXI/LandSandBoat server expects (8-byte tile headers in .nav).
file(GLOB RECAST_SRC "${CMAKE_CURRENT_SOURCE_DIR}/recast/*.cpp")
file(GLOB DETOUR_SRC "${CMAKE_CURRENT_SOURCE_DIR}/detour/*.cpp")

add_library(xi_navmesh SHARED
    xi_navmesh.cpp
    ChunkyTriMesh.cpp
    ${RECAST_SRC}
    ${DETOUR_SRC}
)

target_include_directories(xi_navmesh PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/recast"
    "${CMAKE_CURRENT_SOURCE_DIR}/detour"
)

# Output a clean, unprefixed name on every platform: xi_navmesh.dll / .so / .dylib
set_target_properties(xi_navmesh PROPERTIES PREFIX "" OUTPUT_NAME "xi_navmesh")

if(MSVC)
    target_compile_options(xi_navmesh PRIVATE /O2 /W3 /wd4244 /wd4267)
    target_compile_definitions(xi_navmesh PRIVATE _CRT_SECURE_NO_WARNINGS)
    # Static CRT (/MT) → no VCRUNTIME140 dependency; the .dll is fully self-contained.
    set_property(TARGET xi_navmesh PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
else()
    target_compile_options(xi_navmesh PRIVATE -O2 -w)
endif()
