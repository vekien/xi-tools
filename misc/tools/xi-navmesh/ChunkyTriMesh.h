// ***********************************************************************
// Assembly         :
// Author           : xenon
// Created          : 12-12-2020
//
// Last Modified By : xenon
// Last Modified On : 08-01-2020
// ***********************************************************************
// <copyright file="ChunkyTriMesh.h" company="">
//     Copyright (c) . All rights reserved.
// </copyright>
// <summary></summary>
// ***********************************************************************

#ifndef CHUNKYTRIMESH_H
#define CHUNKYTRIMESH_H

struct rcChunkyTriMeshNode
{
	float bmin[2];
	float bmax[2];
	int i;
	int n;
};

struct rcChunkyTriMesh
{
	inline rcChunkyTriMesh() : nodes(nullptr), nnodes(0), tris(nullptr), ntris(0), maxTrisPerChunk(0) {}
	inline ~rcChunkyTriMesh() { delete[] nodes; delete[] tris; }

	rcChunkyTriMeshNode* nodes;
	int nnodes;
	int* tris;
	int ntris;
	int maxTrisPerChunk;

private:
	// Explicitly disabled copy constructor and copy assignment operator.
	rcChunkyTriMesh(const rcChunkyTriMesh&);
	rcChunkyTriMesh& operator=(const rcChunkyTriMesh&);
};

/// Creates partitioned triangle mesh (AABB tree),
/// where each node contains at max trisPerChunk triangles.
bool rcCreateChunkyTriMesh(const float* verts, const int* tris, int ntris,
	int trisPerChunk, rcChunkyTriMesh* cm);

/// Returns the chunk indices which overlap the input rectable.
int rcGetChunksOverlappingRect(const rcChunkyTriMesh* cm, float bmin[2], float bmax[2], int* ids, const int maxIds);

/// Returns the chunk indices which overlap the input segment.
int rcGetChunksOverlappingSegment(const rcChunkyTriMesh* cm, float p[2], float q[2], int* ids, const int maxIds);

#endif // CHUNKYTRIMESH_H
