import numpy as np
import pyqtgraph.opengl as gl
from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt as QtCoreQt


def boundary_edges_of(mesh):
    if mesh.faces is None or len(mesh.faces) == 0:
        return np.empty((0, 2), dtype=np.int64)
    edges = mesh.edges
    sorted_edges = np.sort(edges, axis=1)
    _, idx, counts = np.unique(sorted_edges, axis=0, return_index=True, return_counts=True)
    return edges[idx[counts == 1]]


class GLCADViewWidget(QtWidgets.QWidget):
    """
    专业 CAD 风格预览控件：灰色背景 + 网格表线 + 深灰色实体。
    可显示 STL 三角网格或 OCC STEP 实体状。
    """

    def __init__(self, parent=None, title="预览"):
        super().__init__(parent)
        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor((204, 205, 208, 255))
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.mesh_item = None
        self.edge_item = None
        self.bad_item = None
        self.grid_items = []
        self._setup_grid()

        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setAlignment(QtCoreQt.AlignmentFlag.AlignCenter)
        self.title_label.setFixedHeight(18)
        self.title_label.setStyleSheet(
            "color:#444; font-weight:bold; background:#e6e7e9; padding:0px; border:1px solid #bbb;"
        )
        layout.insertWidget(0, self.title_label)

    def _setup_grid(self):
        """添加 CAD 风格的地面网格表线。"""
        color = (0.55, 0.56, 0.58, 0.7)
        g = gl.GLGridItem()
        g.setSize(200, 200)
        g.setSpacing(10, 10)
        g.setColor(color)
        g.setDepthValue(10)
        self.view.addItem(g)
        self.grid_items.append(g)

    def clear(self):
        if self.mesh_item is not None:
            self.view.removeItem(self.mesh_item)
            self.mesh_item = None
        for item in (self.edge_item, self.bad_item):
            if item is not None:
                self.view.removeItem(item)
        self.edge_item = None
        self.bad_item = None

    def set_mesh(self, mesh, show_boundary=True, show_bad_faces=True, color=(0.60, 0.62, 0.65, 1.0)):
        self.clear()
        if mesh is None or mesh.faces is None or len(mesh.faces) == 0:
            return

        verts = np.asarray(mesh.vertices, dtype=np.float32)
        faces = np.asarray(mesh.faces, dtype=np.uint32)

        n = len(faces)
        base = np.array(color, dtype=np.float32)
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
                color=(0.9, 0.1, 0.1, 1.0),
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
        from pyqtgraph import Vector

        center = Vector(float(verts[:, 0].mean()), float(verts[:, 1].mean()), float(verts[:, 2].mean()))
        lengths = verts.max(axis=0) - verts.min(axis=0)
        dist = float(np.sqrt(np.sum(np.maximum(lengths, 1e-6) ** 2))) * 2.2
        dist = max(dist, 5.0)
        self.view.setCameraPosition(pos=center, distance=dist, elevation=30, azimuth=-60)
        self.view.opts["center"] = center

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