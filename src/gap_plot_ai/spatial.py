from __future__ import annotations

"""Pure-Python lightweight replacement for scipy.spatial.cKDTree.
Sufficient for O(log N) spatial queries on the Manifold without external deps.

Supports: build, query with radius, query_pairs within radius."""

import math
import heapq
from typing import List, Optional, Tuple


class _KDNode:
    __slots__ = ('point', 'index', 'left', 'right', 'axis')
    def __init__(self, point, index, axis, left=None, right=None):
        self.point = point
        self.index = index
        self.axis = axis
        self.left = left
        self.right = right


class SimpleKDTree:
    """Minimal KD-tree for 2D points with k-nearest and radius queries."""

    def __init__(self, points):
        """Build tree from list of (x, y) tuples."""
        self._points = [(float(p[0]), float(p[1]), i) for i, p in enumerate(points)]
        self._root = self._build(self._points, 0)

    def _build(self, pts, depth):
        if not pts:
            return None
        axis = depth % 2
        pts.sort(key=lambda x: x[axis])
        mid = len(pts) // 2
        return _KDNode(
            (pts[mid][0], pts[mid][1]),
            pts[mid][2],
            axis,
            self._build(pts[:mid], depth + 1),
            self._build(pts[mid + 1:], depth + 1),
        )

    def query(self, point, k=1, distance_upper_bound=float('inf')):
        """Find k nearest neighbors. Returns list of (distance, index)."""
        px, py = float(point[0]), float(point[1])
        best = []  # max-heap of (-dist, index)

        def _search(node, depth):
            if node is None:
                return
            axis = depth % 2
            dx = px - node.point[0] if axis == 0 else py - node.point[1]
            dx_sq = dx * dx

            # Check current node
            dist_sq = (px - node.point[0]) ** 2 + (py - node.point[1]) ** 2
            if dist_sq <= distance_upper_bound ** 2:
                if len(best) < k:
                    heapq.heappush(best, (-dist_sq, node.index))
                elif -best[0][0] > dist_sq:
                    heapq.heapreplace(best, (-dist_sq, node.index))

            # Decide which side to search first
            near, far = (node.left, node.right) if dx <= 0 else (node.right, node.left)
            _search(near, depth + 1)
            if len(best) < k or dx_sq < -best[0][0]:
                _search(far, depth + 1)

        _search(self._root, 0)
        result = sorted((math.sqrt(-d), i) for d, i in best)
        return result

    def query_ball_point(self, point, radius):
        """Find all points within radius. Returns list of indices."""
        px, py = float(point[0]), float(point[1])
        r_sq = radius * radius
        found = []

        def _search(node, depth):
            if node is None:
                return
            axis = depth % 2
            dist_sq = (px - node.point[0]) ** 2 + (py - node.point[1]) ** 2
            if dist_sq <= r_sq:
                found.append((math.sqrt(dist_sq), node.index))
            diff = px - node.point[0] if axis == 0 else py - node.point[1]
            if diff <= radius:
                _search(node.left, depth + 1)
            if diff >= -radius:
                _search(node.right, depth + 1)

        _search(self._root, 0)
        return [i for _, i in sorted(found)]

    def query_pairs(self, radius):
        """Find all pairs of points within radius. Returns set of (i, j) tuples with i < j."""
        r_sq = radius * radius
        pairs = set()
        points = [(p[0], p[1], p[2]) for p in self._points]

        def _search_pairs(node, depth):
            if node is None:
                return
            axis = depth % 2
            px, py = node.point[0], node.point[1]
            # Search left and right subtrees
            for child in (node.left, node.right):
                if child is None:
                    continue
                _search_subtree(child, px, py, node.index, r_sq)
            _search_pairs(node.left, depth + 1)
            _search_pairs(node.right, depth + 1)

        def _search_subtree(node, cx, cy, ref_idx, r_sq):
            if node is None:
                return
            dist_sq = (cx - node.point[0]) ** 2 + (cy - node.point[1]) ** 2
            if dist_sq <= r_sq:
                a, b = sorted((ref_idx, node.index))
                if a != b:
                    pairs.add((a, b))
            _search_subtree(node.left, cx, cy, ref_idx, r_sq)
            _search_subtree(node.right, cx, cy, ref_idx, r_sq)

        _search_pairs(self._root, 0)
        return pairs
