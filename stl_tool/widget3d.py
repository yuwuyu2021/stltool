import numpy as np
import pyqtgraph.opengl as gl
from PyQt6 import QtWidgets


def boundary_edges_of(mesh):
    if mesh.faces is None or len(mesh.faces) == 0:
        return np.empty((0, 2), dtype=np.int64)
    edges = mesh.edges
    sorted_edges = np.sort(edges, axis=1)
    _, idx, counts = np.unique(sorted_edges, axis=0, return_index=True, return_counts=True)
    return edges[idx[counts == 1]]


class GLMeshViewer(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor((0.16, 0.18, 0.22, 1.0))
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.mesh_item = None
        self.edge_item = None
        self.bad_item = None
        self.axis = gl.GLAxisItem()
        self.axis.setSize(10, 10, 10)
        self.view.addItem(self.axis)

    def clear(self):
        if self.mesh_item is not None:
            self.view.removeItem(self.mesh_item)
            self.mesh_item = None
        for item in (self.edge_item, self.bad_item):
            if item is not None:
                self.view.removeItem(item)
        self.edge_item = None
        self.bad_item = None

    def set_mesh(self, mesh, show_boundary=True, show_bad_faces=True):
        self.clear()
        if mesh is None or mesh.faces is None or len(mesh.faces) == 0:
            return

        verts = np.asarray(mesh.vertices, dtype=np.float32)
        faces = np.asarray(mesh.faces, dtype=np.uint32)

        n = len(faces)
        base = np.array([0.62, 0.78, 0.98, 0.98], dtype=np.float32)
        face_colors = np.tile(base, (n, 1))

        bad_mask = None
        if show_bad_faces and not mesh.is_winding_consistent:
            bad_mask = self._bad_face_mask(mesh)

        if bad_mask is not None:
            face_colors[bad_mask] = np.array([1.0, 0.85, 0.2, 1.0], dtype=np.float32)

        meshdata = gl.MeshData(vertexes=verts, faces=faces, faceColors=face_colors)
        self.mesh_item = gl.GLMeshItem(
            meshdata=meshdata,
            smooth=False,
            shader="shaded",
            glOptions="opaque",
        )
        self.view.addItem(self.mesh_item)

        if show_boundary and not mesh.is_watertight and len(boundary_edges_of(mesh)) > 0:
            be = boundary_edges_of(mesh)
            pts = verts[be.reshape(-1)]
            line = self._segments(pts)
            self.edge_item = gl.GLLinePlotItem(
                pos=line,
                color=(1.0, 0.25, 0.25, 1.0),
                width=2.0,
                antialias=True,
                mode="lines",
            )
            self.view.addItem(self.edge_item)

        self._fit_view(verts)

    def _segments(self, pts):
        out = np.empty((len(pts), 3), dtype=np.float32)
        for i in range(0, len(pts) - 1, 2):
            out[i] = pts[i]
            out[i + 1] = pts[i + 1]
        return out

    def _bad_face_mask(self, mesh):
        try:
            v = mesh.vertices
            corr = np.cross(
                v[mesh.faces[:, 1]] - v[mesh.faces[:, 0]],
                v[mesh.faces[:, 2]] - v[mesh.faces[:, 0]],
            )
            return np.sum(mesh.face_normals * corr, axis=1) < -1e-12
        except Exception:
            return None

    def _fit_view(self, verts):
        center = verts.mean(axis=0)
        lengths = verts.max(axis=0) - verts.min(axis=0)
        dist = float(np.sqrt(np.sum(np.maximum(lengths, 1e-6) ** 2))) * 2.2
        dist = max(dist, 5.0)
        self.view.setCameraPosition(pos=center, distance=dist, elevation=30, azimuth=-60)

    def reset_view(self):
        if self.mesh_item is not None:
            md = self.mesh_item.opts.get("meshdata")
            if md is not None and md.vertexes() is not None:
                self._fit_view(md.vertexes().view(np.ndarray))


def build_wireframe_points(bounds):
    mn, mx = bounds
    corners = np.array(
        [
            [mn[0], mn[1], mn[2]],
            [mx[0], mn[1], mn[2]],
            [mx[0], mx[1], mn[2]],
            [mn[0], mx[1], mn[2]],
            [mn[0], mn[1], mx[2]],
            [mx[0], mn[1], mx[2]],
            [mx[0], mx[1], mx[2]],
            [mn[0], mx[1], mx[2]],
        ],
        dtype=np.float32,
    )
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    pts = np.empty((len(edges) * 2, 3), dtype=np.float32)
    for i, (a, b) in enumerate(edges):
        pts[2 * i] = corners[a]
        pts[2 * i + 1] = corners[b]
    return pts