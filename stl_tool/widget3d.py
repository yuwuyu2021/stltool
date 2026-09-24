import numpy as np
import pyqtgraph.opengl as gl
from pyqtgraph.opengl.shaders import FragmentShader, ShaderProgram, VertexShader
from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt as QtCoreQt, pyqtSignal


def boundary_edges_of(mesh):
    if mesh.faces is None or len(mesh.faces) == 0:
        return np.empty((0, 2), dtype=np.int64)
    edges = mesh.edges
    sorted_edges = np.sort(edges, axis=1)
    _, idx, counts = np.unique(sorted_edges, axis=0, return_index=True, return_counts=True)
    return edges[idx[counts == 1]]


_CAD_VERT = """
uniform mat4 u_mvp;
uniform mat3 u_normal;
attribute vec4 a_position;
attribute vec3 a_normal;
attribute vec4 a_color;
varying vec4 v_color;
varying vec3 v_normal;
void main() {
    v_normal = normalize(u_normal * a_normal);
    v_color = a_color;
    gl_Position = u_mvp * a_position;
}
"""

_CAD_FRAG = """
#ifdef GL_ES
precision mediump float;
#endif
varying vec4 v_color;
varying vec3 v_normal;
void main() {
    vec3 n = normalize(v_normal);
    /* 光源从左上角方向照下 */
    float d = dot(n, normalize(vec3(-1.0, 1.0, 1.0)));
    float p = max(d, 0.0);
    /* 环境光 0.45 + 漫反射 */
    float light = 0.45 + 0.55 * p;
    gl_FragColor = vec4(v_color.rgb * light, v_color.a);
}
"""


def _cad_shader():
    return ShaderProgram(
        "cad",
        [VertexShader(_CAD_VERT), FragmentShader(_CAD_FRAG)],
    )


class GLCADViewWidget(QtWidgets.QWidget):
    """
    专业 CAD 风格预览控件：灰色背景 + 网格表线 + 深灰色实体。
    可显示 STL 三角网格或 OCC STEP 实体状。
    """

    facePicked = pyqtSignal(int, object)  # 发射 (被点选的三角面索引, 命中 3D 点)；空白处为 (-1, None)

    def __init__(self, parent=None, title="预览"):
        super().__init__(parent)
        self.view = gl.GLViewWidget()
        self._pick_data = None
        self._pick_enabled = False
        self.pick_radius = 0.0
        self.view.setBackgroundColor((204, 205, 208, 255))
        self.view.installEventFilter(self)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.mesh_item = None
        self.edge_item = None
        self.bad_item = None
        self.facet_item = None
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
        for item in (self.edge_item, self.bad_item, self.facet_item):
            if item is not None:
                self.view.removeItem(item)
        self.edge_item = None
        self.bad_item = None
        self.facet_item = None
        self._pick_data = None

    def set_mesh(self, mesh, show_boundary=True, show_bad_faces=True, color=(0.60, 0.62, 0.65, 1.0),
                 selected=None, pickable=False, show_faces=True, fit_view=True):
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

        if selected is not None and len(selected) == n:
            sel = np.asarray(selected, dtype=bool)
            if sel.any():
                face_colors[sel] = np.array([1.0, 0.45, 0.1, 1.0], dtype=np.float32)

        meshdata = gl.MeshData(vertexes=verts, faces=faces, faceColors=face_colors)
        self.mesh_item = gl.GLMeshItem(
            meshdata=meshdata,
            smooth=False,
            shader=None,
            glOptions="opaque",
        )
        self.mesh_item.setShader(_cad_shader())
        self.view.addItem(self.mesh_item)
        if show_faces:
            self.facet_item = self._facet_wireframe(verts, faces)
            if self.facet_item is not None:
                self.view.addItem(self.facet_item)
        self._pick_data = (verts, faces) if pickable else None
        self._pick_enabled = bool(pickable)

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

        if fit_view:
            self._fit_view(verts)

    def _segments(self, pts):
        out = np.empty((len(pts), 3), dtype=np.float32)
        for i in range(0, len(pts) - 1, 2):
            out[i] = pts[i]
            out[i + 1] = pts[i + 1]
        return out

    def _facet_wireframe(self, verts, faces):
        """绘制所有三角面边缘的浅色线框，便于观察/点选单个面片。"""
        try:
            tris = np.take(verts, faces, axis=0)  # (n,3,3)
            out = np.empty((len(faces) * 6 + 1, 3), dtype=np.float32)
            for i in range(len(faces)):
                a, b, c = tris[i]
                out[i * 6 + 0] = a
                out[i * 6 + 1] = b
                out[i * 6 + 2] = b
                out[i * 6 + 3] = c
                out[i * 6 + 4] = c
                out[i * 6 + 5] = a
            return gl.GLLinePlotItem(
                pos=out,
                color=(0.35, 0.38, 0.42, 0.16),
                width=0.5,
                antialias=True,
                mode="lines",
            )
        except Exception:
            return None

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

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtCore import Qt as Q
        from PyQt6.QtWidgets import QWidget

        if obj is self.view and event.type() == QEvent.Type.MouseButtonRelease:
            ev = event
            if (ev.button() == Q.MouseButton.LeftButton
                    and ev.modifiers() & Q.KeyboardModifier.ShiftModifier):
                if self._pick_enabled and self._pick_data is not None:
                    p = ev.position()
                    face, pt = self._ray_pick_point(int(p.x()), int(p.y()))
                    self.facePicked.emit(face, pt)
                    return True
        return super().eventFilter(obj, event)

    def _ray_pick(self, x, y):
        """把屏幕坐标反投影为 3D 射线，与网格三角求交，返回最近的面索引（-1 表示无命中）。"""
        face, _ = self._ray_pick_point(x, y)
        return face

    def _ray_pick_point(self, x, y):
        """返回 (最近命中面索引, 命中 3D 点)；无命中返回 (-1, None)。"""
        verts, faces = self._pick_data
        if verts is None or faces is None or len(faces) == 0:
            return -1, None
        try:
            near, far = self._ray_from_screen(x, y)
            if near is None:
                return -1, None
            d = np.asarray(far) - near
            dlen = float(np.linalg.norm(d))
            if dlen < 1e-12:
                return -1, None
            d = d / dlen

            t0 = verts[faces[:, 0]]
            e1 = verts[faces[:, 1]] - t0
            e2 = verts[faces[:, 2]] - t0
            org = np.asarray(near)[None, :]
            t_all = np.full(len(faces), np.inf)
            det = np.einsum("ij,ij->i", e1, np.cross(d[None, :], e2))
            valid = np.abs(det) > 1e-12
            t_org = org - t0[valid]
            pvec = np.cross(d[None, :], e2[valid])
            inv = 1.0 / det[valid]
            u = np.einsum("ij,ij->i", t_org, pvec) * inv
            qvec = np.cross(t_org, e1[valid])
            vv = np.einsum("ij,ij->i", d[None, :], qvec) * inv
            tt = np.einsum("ij,ij->i", e2[valid], qvec) * inv
            inside = (u >= 0) & (u <= 1) & (vv >= 0) & (u + vv <= 1) & (tt >= 0)
            vpos = np.where(valid)[0]
            t_all[vpos] = np.where(inside, tt, np.inf)
            if not np.isfinite(t_all).any():
                return -1, None
            best = int(np.argmin(t_all))
            hit_pt = np.asarray(near) + d * float(t_all[best])
            return best, hit_pt
        except Exception:
            return -1, None

    def _ray_from_screen(self, x, y):
        """把屏幕坐标反投影为 3D 两点的射线（viewMatrix/projectionMatrix 均为纯 Qt 数学，无需 GL）。"""
        try:
            w = self.view.width()
            h = self.view.height()
            if w <= 0 or h <= 0:
                return None, None
            viewport = (0, 0, w, h)
            region = (x, self.view.height() - y, 1, 1)
            view = self.view.viewMatrix()
            proj = self.view.projectionMatrix(region, viewport)
            if view is None or proj is None:
                return None, None
            vm = self._qmat_to_np(view)
            pm = self._qmat_to_np(proj)
            mvp = np.dot(pm, vm)
            try:
                inv = np.linalg.inv(mvp)
            except np.linalg.LinAlgError:
                return None, None
            # 屏幕中心点的 NDC (区域中心)
            ndcx = (2.0 * (x + 0.5) / w) - 1.0
            ndcy = 1.0 - (2.0 * (y + 0.5) / h)
            near_w = np.dot(inv, np.array([ndcx, ndcy, -1.0, 1.0]))
            far_w = np.dot(inv, np.array([ndcx, ndcy, 1.0, 1.0]))
            near_w = near_w[:3] / near_w[3]
            far_w = far_w[:3] / far_w[3]
            return near_w, far_w
        except Exception:
            return None, None

    @staticmethod
    def _qmat_to_np(m):
        """把 Qt QMatrix4x4 转为 numpy 4x4（列主序），OpenGL 约定。"""
        data = m.copyDataTo()  # 16 个 float，列主序
        return np.array(data, dtype=float).reshape(4, 4)

    def faces_within_radius(self, point, radius, center_only=True):
        """返回面心（或顶点）距 point 小于 radius 的所有面索引（numpy 数组）。"""
        if self._pick_data is None or point is None or radius <= 0:
            return None
        verts, faces = self._pick_data
        cents = verts[faces].mean(axis=1)
        dist = np.linalg.norm(cents - np.asarray(point), axis=1)
        return np.where(dist <= radius)[0]

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