import numpy as np


class MeshAnalysis:
    def __init__(self, mesh):
        self.mesh = mesh
        self.vertex_count = len(mesh.vertices)
        self.face_count = len(mesh.faces)
        self.edge_count = len(mesh.edges)
        self.unique_edges_count = len(mesh.edges_unique)
        self.is_watertight = bool(mesh.is_watertight)
        self.is_manifold = bool(mesh.is_winding_consistent) and self.is_watertight
        self.boundary_edges = len(self.boundary_edges_of(mesh)) if not self.is_watertight else 0
        self.volume = float(mesh.volume) if mesh.is_volume else None
        self.area = float(mesh.area)
        self.bounds = mesh.bounds
        self.extents = (self.bounds[1] - self.bounds[0]).tolist() if self.bounds is not None else None
        self.connected_components = len(mesh.split(only_watertight=False))
        self.degenerate_faces = self.degenerate_count(mesh)
        self.duplicate_faces = self.duplicate_count(mesh)
        self.inverted_faces = self.inverted_count(mesh)
        self.dim_inverted_faces = int(self.inverted_faces.sum()) if self.inverted_faces is not None else None

    @staticmethod
    def boundary_edges_of(mesh):
        if mesh.faces is None or len(mesh.faces) == 0:
            return np.empty((0, 2), dtype=np.int64)
        edges = mesh.edges
        sorted_edges = np.sort(edges, axis=1)
        _, idx, counts = np.unique(sorted_edges, axis=0, return_index=True, return_counts=True)
        boundary = edges[idx[counts == 1]]
        return boundary

    @staticmethod
    def degenerate_count(mesh):
        tris = mesh.triangles
        area = np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1) * 0.5
        bbox = mesh.bounds
        scale = float(np.max(bbox[1] - bbox[0])) if bbox is not None else 1.0
        eps = max(1e-12, 1e-9 * scale * scale)
        return int((area < eps).sum())

    @staticmethod
    def duplicate_count(mesh):
        if mesh.faces is None or len(mesh.faces) == 0:
            return 0
        key = np.sort(mesh.faces, axis=1)
        _, counts = np.unique(key, axis=0, return_counts=True)
        return int((counts > 1).sum())

    @staticmethod
    def inverted_count(mesh):
        try:
            if not mesh.is_winding_consistent:
                fi = mesh.face_normals
                v = mesh.vertices
                corr = np.cross(v[mesh.faces[:, 1]] - v[mesh.faces[:, 0]],
                                v[mesh.faces[:, 2]] - v[mesh.faces[:, 0]])
                inverted = np.sum(fi * corr, axis=1) < 0
                return inverted
            return None
        except Exception:
            return None

    def to_dict(self):
        d = {
            "vertex_count": self.vertex_count,
            "face_count": self.face_count,
            "edge_count": self.edge_count,
            "is_watertight": self.is_watertight,
            "is_manifold": self.is_manifold,
            "boundary_edges": self.boundary_edges,
            "volume": self.volume,
            "area": self.area,
            "extents": self.extents,
            "connected_components": self.connected_components,
            "degenerate_faces": self.degenerate_faces,
            "duplicate_faces": self.duplicate_faces,
            "dim_inverted_faces": self.dim_inverted_faces,
        }
        return d

    def summary_lines(self):
        lines = []
        a = self.to_dict()
        lines.append(f"顶点数: {a['vertex_count']}")
        lines.append(f"三角面数: {a['face_count']}")
        lines.append(f"边数: {a['edge_count']}")
        lines.append(f"尺寸 (x,y,z): {self.fmt(self.extents)}")
        lines.append(f"表面积: {self.fmt(a['area'])}")
        if a['volume'] is not None:
            lines.append(f"体积: {self.fmt(a['volume'])}")
        lines.append(f"水密(闭合): {'是' if a['is_watertight'] else '否'}")
        lines.append(f"流形: {'是' if a['is_manifold'] else '否'}")
        if a['boundary_edges'] is not None:
            lines.append(f"开放边界边: {a['boundary_edges']}")
        lines.append(f"独立连通片: {a['connected_components']}")
        if a['degenerate_faces']:
            lines.append(f"退化面: {a['degenerate_faces']}")
        if a['duplicate_faces']:
            lines.append(f"重复面: {a['duplicate_faces']}")
        if a['dim_inverted_faces']:
            lines.append(f"法向异常面: {a['dim_inverted_faces']}")
        return lines

    @staticmethod
    def fmt(v):
        if v is None:
            return "-"
        if isinstance(v, (list, tuple)):
            return "(" + ", ".join(f"{x:.3f}" for x in v) + ")"
        f = float(v)
        if abs(f) >= 1000 or abs(f) < 0.001:
            return f"{f:.4e}"
        return f"{f:.4f}"