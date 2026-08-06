// xi_navmesh — our own extern "C" wrapper around Recast & Detour that bakes a
// server-compatible FFXI navmesh (a Detour NAVMESHSET "*.nav") from a raw
// collision triangle soup. Python (xi `zone navmesh`) calls xi_build_navmesh()
// with the zone's decoded collision in Detour space (x,-y,-z) and a settings
// struct; we run the standard tiled Recast build and serialize the .nav.
//
// The pipeline mirrors RecastNavigation's RecastDemo "Tile Mesh" sample; the
// FFXI-specific build params + .nav serialization were cross-referenced against
// xenonsmurf's FFXINAV (NavMeshBuilder.cpp). Recast/Detour are bundled under
// recast/ and detour/ and are zlib-licensed (c) Mikko Mononen. This wrapper is
// our own code — it does not link against FFXINAV.

#include "recast/Recast.h"
#include "detour/DetourNavMesh.h"
#include "detour/DetourNavMeshBuilder.h"
#include "detour/DetourAlloc.h"
#include "ChunkyTriMesh.h"

#include <cstdio>
#include <cstring>
#include <cmath>

#ifdef _WIN32
#define XI_API extern "C" __declspec(dllexport)
#else
#define XI_API extern "C"
#endif

// Standard RecastDemo "Sample" area / flag / partition enums.
enum { PART_WATERSHED = 0, PART_MONOTONE = 1, PART_LAYERS = 2 };
enum { POLYAREA_GROUND = 0, POLYAREA_WATER = 1, POLYAREA_ROAD = 2,
       POLYAREA_DOOR = 3, POLYAREA_GRASS = 4, POLYAREA_JUMP = 5 };
enum { POLYFLAGS_WALK = 0x01, POLYFLAGS_SWIM = 0x02, POLYFLAGS_DOOR = 0x04 };

// Detour NAVMESHSET container (what the server's CNavMesh::load reads).
static const int NAVMESHSET_MAGIC = 'M' << 24 | 'S' << 16 | 'E' << 8 | 'T';
static const int NAVMESHSET_VERSION = 1;
struct NavMeshSetHeader { int magic; int version; int numTiles; dtNavMeshParams params; };
struct NavMeshTileHeader { dtTileRef tileRef; int dataSize; };

// Mirrors xi's NavSettings (Python side fills this). Plain floats/ints so it
// maps cleanly to a ctypes Structure.
struct XiNavSettings {
    float cellSize, cellHeight, agentHeight, agentRadius, agentMaxClimb, agentMaxSlope;
    float regionMinSize, regionMergeSize, edgeMaxLen, edgeMaxError, vertsPerPoly;
    float detailSampleDist, detailSampleMaxError, tileSize;
    int   partitionType;
};

static unsigned int nextPow2(unsigned int v)
{
    v--; v |= v >> 1; v |= v >> 2; v |= v >> 4; v |= v >> 8; v |= v >> 16; v++;
    return v;
}
static unsigned int ilog2(unsigned int v)
{
    unsigned int r, s;
    r = (v > 0xffff) << 4; v >>= r;
    s = (v > 0xff) << 3; v >>= s; r |= s;
    s = (v > 0xf) << 2; v >>= s; r |= s;
    s = (v > 0x3) << 1; v >>= s; r |= s;
    r |= (v >> 1);
    return r;
}

// Build one tile's navmesh data (dtAlloc'd; caller frees via Detour). Returns
// nullptr for empty/failed tiles (an empty tile is normal, not an error).
static unsigned char* buildTile(rcContext* ctx, const float* verts, int nverts,
                                const rcChunkyTriMesh* chunky, const XiNavSettings* s,
                                int tx, int ty, const float* bmin, const float* bmax, int& dataSize)
{
    rcConfig cfg; memset(&cfg, 0, sizeof(cfg));
    cfg.cs = s->cellSize;
    cfg.ch = s->cellHeight;
    cfg.walkableSlopeAngle = s->agentMaxSlope;
    cfg.walkableHeight = (int)ceilf(s->agentHeight / cfg.ch);
    cfg.walkableClimb  = (int)floorf(s->agentMaxClimb / cfg.ch);
    cfg.walkableRadius = (int)ceilf(s->agentRadius / cfg.cs);
    cfg.maxEdgeLen = (int)(s->edgeMaxLen / s->cellSize);
    cfg.maxSimplificationError = s->edgeMaxError;
    cfg.minRegionArea = (int)rcSqr(s->regionMinSize);
    cfg.mergeRegionArea = (int)rcSqr(s->regionMergeSize);
    cfg.maxVertsPerPoly = (int)s->vertsPerPoly;
    cfg.tileSize = (int)s->tileSize;
    cfg.borderSize = cfg.walkableRadius + 3;
    cfg.width = cfg.tileSize + cfg.borderSize * 2;
    cfg.height = cfg.tileSize + cfg.borderSize * 2;
    cfg.detailSampleDist = s->detailSampleDist < 0.9f ? 0 : s->cellSize * s->detailSampleDist;
    cfg.detailSampleMaxError = s->cellHeight * s->detailSampleMaxError;
    rcVcopy(cfg.bmin, bmin);
    rcVcopy(cfg.bmax, bmax);
    cfg.bmin[0] -= cfg.borderSize * cfg.cs;
    cfg.bmin[2] -= cfg.borderSize * cfg.cs;
    cfg.bmax[0] += cfg.borderSize * cfg.cs;
    cfg.bmax[2] += cfg.borderSize * cfg.cs;

    rcHeightfield* solid = rcAllocHeightfield();
    if (!solid || !rcCreateHeightfield(ctx, *solid, cfg.width, cfg.height, cfg.bmin, cfg.bmax, cfg.cs, cfg.ch)) {
        rcFreeHeightField(solid); return nullptr;
    }

    unsigned char* triareas = new unsigned char[chunky->maxTrisPerChunk];
    float tbmin[2] = { cfg.bmin[0], cfg.bmin[2] };
    float tbmax[2] = { cfg.bmax[0], cfg.bmax[2] };
    int cid[1024];
    const int ncid = rcGetChunksOverlappingRect(chunky, tbmin, tbmax, cid, 1024);
    if (!ncid) { delete[] triareas; rcFreeHeightField(solid); return nullptr; }
    for (int i = 0; i < ncid; ++i) {
        const rcChunkyTriMeshNode& node = chunky->nodes[cid[i]];
        const int* ctris = &chunky->tris[node.i * 3];
        const int nctris = node.n;
        memset(triareas, 0, nctris * sizeof(unsigned char));
        rcMarkWalkableTriangles(ctx, cfg.walkableSlopeAngle, verts, nverts, ctris, nctris, triareas);
        if (!rcRasterizeTriangles(ctx, verts, nverts, ctris, triareas, nctris, *solid, cfg.walkableClimb)) {
            delete[] triareas; rcFreeHeightField(solid); return nullptr;
        }
    }
    delete[] triareas;

    rcFilterLowHangingWalkableObstacles(ctx, cfg.walkableClimb, *solid);
    rcFilterLedgeSpans(ctx, cfg.walkableHeight, cfg.walkableClimb, *solid);
    rcFilterWalkableLowHeightSpans(ctx, cfg.walkableHeight, *solid);

    rcCompactHeightfield* chf = rcAllocCompactHeightfield();
    if (!chf || !rcBuildCompactHeightfield(ctx, cfg.walkableHeight, cfg.walkableClimb, *solid, *chf)) {
        rcFreeHeightField(solid); rcFreeCompactHeightfield(chf); return nullptr;
    }
    rcFreeHeightField(solid); solid = nullptr;
    if (!rcErodeWalkableArea(ctx, cfg.walkableRadius, *chf)) { rcFreeCompactHeightfield(chf); return nullptr; }

    if (s->partitionType == PART_WATERSHED) {
        if (!rcBuildDistanceField(ctx, *chf) ||
            !rcBuildRegions(ctx, *chf, cfg.borderSize, cfg.minRegionArea, cfg.mergeRegionArea)) {
            rcFreeCompactHeightfield(chf); return nullptr;
        }
    } else if (s->partitionType == PART_MONOTONE) {
        if (!rcBuildRegionsMonotone(ctx, *chf, cfg.borderSize, cfg.minRegionArea, cfg.mergeRegionArea)) {
            rcFreeCompactHeightfield(chf); return nullptr;
        }
    } else {
        if (!rcBuildLayerRegions(ctx, *chf, cfg.borderSize, cfg.minRegionArea)) {
            rcFreeCompactHeightfield(chf); return nullptr;
        }
    }

    rcContourSet* cset = rcAllocContourSet();
    if (!cset || !rcBuildContours(ctx, *chf, cfg.maxSimplificationError, cfg.maxEdgeLen, *cset)) {
        rcFreeCompactHeightfield(chf); rcFreeContourSet(cset); return nullptr;
    }
    if (cset->nconts == 0) { rcFreeCompactHeightfield(chf); rcFreeContourSet(cset); return nullptr; }

    rcPolyMesh* pmesh = rcAllocPolyMesh();
    if (!pmesh || !rcBuildPolyMesh(ctx, *cset, cfg.maxVertsPerPoly, *pmesh)) {
        rcFreeCompactHeightfield(chf); rcFreeContourSet(cset); rcFreePolyMesh(pmesh); return nullptr;
    }
    rcPolyMeshDetail* dmesh = rcAllocPolyMeshDetail();
    if (!dmesh || !rcBuildPolyMeshDetail(ctx, *pmesh, *chf, cfg.detailSampleDist, cfg.detailSampleMaxError, *dmesh)) {
        rcFreeCompactHeightfield(chf); rcFreeContourSet(cset); rcFreePolyMesh(pmesh); rcFreePolyMeshDetail(dmesh); return nullptr;
    }
    rcFreeCompactHeightfield(chf);
    rcFreeContourSet(cset);

    unsigned char* navData = nullptr;
    int navDataSize = 0;
    if (cfg.maxVertsPerPoly <= DT_VERTS_PER_POLYGON) {
        if (pmesh->nverts >= 0xffff) { rcFreePolyMesh(pmesh); rcFreePolyMeshDetail(dmesh); return nullptr; }
        for (int i = 0; i < pmesh->npolys; ++i) {
            if (pmesh->areas[i] == RC_WALKABLE_AREA) pmesh->areas[i] = POLYAREA_GROUND;
            if (pmesh->areas[i] == POLYAREA_GROUND || pmesh->areas[i] == POLYAREA_GRASS || pmesh->areas[i] == POLYAREA_ROAD)
                pmesh->flags[i] = POLYFLAGS_WALK;
            else if (pmesh->areas[i] == POLYAREA_WATER)
                pmesh->flags[i] = POLYFLAGS_SWIM;
            else if (pmesh->areas[i] == POLYAREA_DOOR)
                pmesh->flags[i] = POLYFLAGS_WALK | POLYFLAGS_DOOR;
        }
        dtNavMeshCreateParams p; memset(&p, 0, sizeof(p));
        p.verts = pmesh->verts; p.vertCount = pmesh->nverts;
        p.polys = pmesh->polys; p.polyAreas = pmesh->areas; p.polyFlags = pmesh->flags;
        p.polyCount = pmesh->npolys; p.nvp = pmesh->nvp;
        p.detailMeshes = dmesh->meshes; p.detailVerts = dmesh->verts; p.detailVertsCount = dmesh->nverts;
        p.detailTris = dmesh->tris; p.detailTriCount = dmesh->ntris;
        p.walkableHeight = s->agentHeight; p.walkableRadius = s->agentRadius; p.walkableClimb = s->agentMaxClimb;
        p.tileX = tx; p.tileY = ty; p.tileLayer = 0;
        rcVcopy(p.bmin, pmesh->bmin); rcVcopy(p.bmax, pmesh->bmax);
        p.cs = cfg.cs; p.ch = cfg.ch; p.buildBvTree = true;
        if (!dtCreateNavMeshData(&p, &navData, &navDataSize)) {
            rcFreePolyMesh(pmesh); rcFreePolyMeshDetail(dmesh); return nullptr;
        }
    }
    rcFreePolyMesh(pmesh);
    rcFreePolyMeshDetail(dmesh);
    dataSize = navDataSize;
    return navData;
}

// Build a tiled navmesh from a triangle soup and write it as a Detour NAVMESHSET
// (.nav) to out_path. verts = nverts*3 floats (x,y,z, in Detour space); tris =
// ntris*3 ints (vertex indices). Returns the tile count written (>= 0), or a
// negative error code.
XI_API int xi_build_navmesh(const float* verts, int nverts,
                                const int* tris, int ntris,
                                const XiNavSettings* s, const char* out_path)
{
    if (!verts || !tris || !s || !out_path || nverts <= 0 || ntris <= 0) return -1;

    rcContext ctx(false); // logging/timers disabled

    float bmin[3], bmax[3];
    rcCalcBounds(verts, nverts, bmin, bmax);

    rcChunkyTriMesh chunky;
    if (!rcCreateChunkyTriMesh(verts, tris, ntris, 256, &chunky)) return -2;

    int gw = 0, gh = 0;
    rcCalcGridSize(bmin, bmax, s->cellSize, &gw, &gh);
    const int ts = (int)s->tileSize;
    const int tw = (gw + ts - 1) / ts;
    const int th = (gh + ts - 1) / ts;
    const float tcs = s->tileSize * s->cellSize;

    // tile/poly id bit budget (22 bits total) — same as RecastDemo/FFXINAV.
    int tileBits = rcMin((int)ilog2(nextPow2((unsigned)(tw * th))), 14);
    int polyBits = 22 - tileBits;

    dtNavMeshParams params; memset(&params, 0, sizeof(params));
    rcVcopy(params.orig, bmin);
    params.tileWidth = tcs;
    params.tileHeight = tcs;
    params.maxTiles = 1 << tileBits;
    params.maxPolys = 1 << polyBits;

    dtNavMesh* navMesh = dtAllocNavMesh();
    if (!navMesh || dtStatusFailed(navMesh->init(&params))) { dtFreeNavMesh(navMesh); return -3; }

    for (int y = 0; y < th; ++y) {
        for (int x = 0; x < tw; ++x) {
            float tbmin[3] = { bmin[0] + x * tcs, bmin[1], bmin[2] + y * tcs };
            float tbmax[3] = { bmin[0] + (x + 1) * tcs, bmax[1], bmin[2] + (y + 1) * tcs };
            int dataSize = 0;
            unsigned char* data = buildTile(&ctx, verts, nverts, &chunky, s, x, y, tbmin, tbmax, dataSize);
            if (data) {
                navMesh->removeTile(navMesh->getTileRefAt(x, y, 0), 0, 0);
                if (dtStatusFailed(navMesh->addTile(data, dataSize, DT_TILE_FREE_DATA, 0, 0)))
                    dtFree(data);
            }
        }
    }

    FILE* fp = fopen(out_path, "wb");
    if (!fp) { dtFreeNavMesh(navMesh); return -4; }

    // Read tiles through a const handle so the *public* const getTile() overload
    // is selected (the non-const overload is private in this Detour build).
    const dtNavMesh* cmesh = navMesh;

    NavMeshSetHeader header; memset(&header, 0, sizeof(header));
    header.magic = NAVMESHSET_MAGIC;
    header.version = NAVMESHSET_VERSION;
    header.numTiles = 0;
    for (int i = 0; i < cmesh->getMaxTiles(); ++i) {
        const dtMeshTile* t = cmesh->getTile(i);
        if (t && t->header && t->dataSize) header.numTiles++;
    }
    memcpy(&header.params, cmesh->getParams(), sizeof(dtNavMeshParams));
    fwrite(&header, sizeof(header), 1, fp);

    for (int i = 0; i < cmesh->getMaxTiles(); ++i) {
        const dtMeshTile* t = cmesh->getTile(i);
        if (!t || !t->header || !t->dataSize) continue;
        NavMeshTileHeader tileHeader;
        tileHeader.tileRef = cmesh->getTileRef(t);
        tileHeader.dataSize = t->dataSize;
        fwrite(&tileHeader, sizeof(tileHeader), 1, fp);
        fwrite(t->data, t->dataSize, 1, fp);
    }
    fclose(fp);

    const int n = header.numTiles;
    dtFreeNavMesh(navMesh);
    return n;
}
