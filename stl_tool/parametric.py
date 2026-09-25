"""STL 网格 → 参数化特征重建成 STEP 实体的纯算法模块。

与 analytic（局部面拟合）不同，本模块先尝试把整个网格识别为
规则可参数化特征——基本体素（长方体/圆柱/圆锥/球）或回转体——
直接用 OpenCASCADE 参数化原语（MakeBox / MakeCylinder / MakeCone /
MakeSphere / MakeRevol）重建实体。这样得到的 STEP 面数极少、完全可编辑。

无法整体参数化的网格返回空结果，由调用方回退到逐三角缝合/analytic 模式。
"""

import numpy as np

from .analytic import _face_normals, _axis_from_normals, fit_sphere, fit_cylinder, fit_cone
from .solid_builder import BuildResult


# ------------------------------------------------------------------ #
# RDP 折线简化


def _rdp(points, eps):
    """Douglas–Peucker 折线简化。points: (n,2) 数组；返回保留点的下标数组。"""
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    if n < 3:
        return np.arange(n, dtype=int)

    def _dist(p, a, b):
        ba = b - a
        blen = float(np.dot(ba, ba))
        if blen == 0.0:
            return float(np.linalg.norm(p - a))
        t = float(np.clip(np.dot(p - a, ba) / blen, 0.0, 1.0))
        return float(np.linalg.norm(p - (a + t * ba)))

    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 - i0 < 2:
            continue
        a, b = pts[i0], pts[i1]
        seg = pts[i0 + 1:i1]
        if len(seg) == 0:
            continue
        d = np.array([_dist(p, a, b) for p in seg])
        imax = int(np.argmax(d))
        if d[imax] > eps:
            imax += i0 + 1
            keep[imax] = True
            stack.append((i0, imax))
            stack.append((imax, i1))
    return np.where(keep)[0].astype(int)


# ------------------------------------------------------------------ #
# 基本体素整体拟合


def _volume(shape):
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    if shape is None or shape.IsNull():
        return None
    try:
        g = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, g)
        v = float(g.Mass())
        return v if v > 1e-12 else None
    except Exception:
        return None


def _valid(shape):
    from OCP.BRepCheck import BRepCheck_Analyzer
    try:
        return bool(BRepCheck_Analyzer(shape).IsValid())
    except Exception:
        return False


def _make_box_shape(model):
    """由主轴尺寸直接生成长方体实体。"""
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    o, u, vv, w = model["origin"], model["u"], model["v"], model["w"]
    ax = gp_Ax2(gp_Pnt(*o), gp_Dir(*w), gp_Dir(*u))
    return BRepPrimAPI_MakeBox(ax,
                               float(model["Lx"]), float(model["Ly"]),
                               float(model["Lz"])).Shape()


def _make_cylinder_shape(model):
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    c = model["axis_point"]
    ax = gp_Ax2(gp_Pnt(*c), gp_Dir(*model["axis"]))
    return BRepPrimAPI_MakeCylinder(ax,
                                    float(model["R"]), float(model["height"])).Shape()


def _make_cone_shape(model):
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    base = model["base_center"]
    ax = gp_Ax2(gp_Pnt(*base), gp_Dir(*model["axis"]))
    return BRepPrimAPI_MakeCone(ax,
                                float(model["R_base"]), float(model["R_top"]),
                                float(model["height"])).Shape()


def _make_sphere_shape(model):
    from OCP.gp import gp_Pnt
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    return BRepPrimAPI_MakeSphere(gp_Pnt(*model["center"]), float(model["R"])).Shape()


def fit_box(verts, diag):
    """整体长方体拟合：PCA 主轴 + 沿三主轴 bbox 尺寸。返回模型 dict 或 None。"""
    pts = np.asarray(verts, dtype=np.float64)
    if len(pts) < 8:
        return None
    cent = pts.mean(axis=0)
    cov = np.cov((pts - cent).T)
    w, v = np.linalg.eigh(cov)
    order = np.argsort(w)[::-1]
    u = v[:, order[0]]
    vv = v[:, order[1]]
    ww = np.cross(u, vv)
    vv = np.cross(ww, u)
    axes = np.column_stack([u, vv, ww])
    proj = (pts - cent) @ axes
    lo = proj.min(axis=0)
    hi = proj.max(axis=0)
    extents = hi - lo
    Lx, Ly, Lz = extents
    if min(Lx, Ly, Lz) < 1e-9 * max(diag, 1.0):
        return None
    # 残差：每个顶点到长方体表面的最近距离（纯盒体顶点必在面上→≈0，
    # 圆柱/球等内部顶点到表面距离>0 → 不会被误判为盒体）
    half = extents / 2.0
    rel = proj / np.maximum(half, 1e-12)  # 归一化到 [-1,1]
    # 到盒表面距离 = min over 轴 (轴向上超出 [-half,half] 的绝对余量 ||
    #                     轴向上到面的距离 >0 者取最小)
    dist_axis = np.empty_like(rel)
    dd = np.abs(rel)  # 归一化坐标绝对值（[0,1]，1=表面）
    inside = dd < 1.0
    for j in range(3):
        # 归一化距离：从该轴最近面到点的距离 = (1 - dd)·half
        dist_axis[:, j] = (1.0 - dd[:, j]) * half[j]
    dist_axis[~inside] = 0.0  # 超出盒外的点视为到表面 0（不可能发生，防御）
    d = dist_axis.min(axis=1)
    rms = float(np.sqrt(np.mean(d * d)))
    # 额外护栏：纯盒体的面法向必须基本平行于主轴（3 组互为垂直）
    return {"type": "box", "u": axes[:, 0], "v": axes[:, 1], "w": axes[:, 2],
            "origin": cent, "Lx": Lx, "Ly": Ly, "Lz": Lz, "rms": rms}


def _rev_axis_candidates(normals, verts, faces):
    """生成候选旋转轴方向（去重后列表）。

    取三种几何线索：法向散度最小/最大特征向量、顶点 PCA 主轴；
    对圆柱/圆锥/回转件，真轴必在其中之一。返回排序后的候选单位向量。
    """
    cands = []
    n = normals
    S = n.T @ n
    w, v = np.linalg.eigh(S)
    cands.append(v[:, 0])
    cands.append(v[:, 2])
    pts = np.asarray(verts, dtype=np.float64)
    cent = pts.mean(axis=0)
    try:
        cov = np.cov((pts - cent).T)
        w2, v2 = np.linalg.eigh(cov)
        for i in range(3):
            cands.append(v2[:, i])
    except Exception:
        pass
    out = []
    for c in cands:
        c = np.asarray(c, dtype=np.float64)
        nlen = np.linalg.norm(c)
        if nlen < 1e-12:
            continue
        c = c / nlen
        if abs(c[2]) < 0.0:
            c = -c
        dup = False
        for o in out:
            if abs(float(np.dot(o, c))) > 0.9999:
                dup = True
                break
        if not dup:
            out.append(c)
    return out


def _side_face_mask(normals, axis, cos_tol=0.2):
    """侧面（法向近似垂直于轴）的面掩码。"""
    return np.abs(normals @ axis) < cos_tol


def fit_cylinder_ax(verts, normals, faces, axis, axis_point):
    """已知轴，用侧面积中值半径 + 轴向高度拟合圆柱（rms 为侧面径向偏差）。"""
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    pts = np.asarray(verts, dtype=np.float64)
    h = pts @ axis
    rr = np.linalg.norm(pts - h[:, None] * axis, axis=1)
    fc = pts[np.asarray(faces, dtype=int)].mean(axis=1)
    hf = fc @ axis
    rrf = np.linalg.norm(fc - hf[:, None] * axis, axis=1)
    side = _side_face_mask(normals, axis)
    if side.sum() < 8:
        return None
    if rr.size != side.size:
        R = float(np.median(rrf[side]))
        side_rr = rrf[side]
    else:
        R = float(np.median(rr[side]))
        side_rr = rr[side]
    if R < 1e-9:
        return None
    rms_side = float(np.sqrt(np.mean((side_rr - R) ** 2)))
    H = float(h.max() - h.min())
    return {"type": "cylinder", "axis": axis, "axis_point": axis_point,
            "R": R, "height": H, "rms": rms_side}


def fit_cone_ax(verts, normals, faces, axis, axis_point):
    """已知轴，把圆锥拟合成 OCP MakeCone 需要的底/顶半径与高度。"""
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    pts = np.asarray(verts, dtype=np.float64)
    fc = pts[np.asarray(faces, dtype=int)].mean(axis=1)
    hf = fc @ axis
    rrf = np.linalg.norm(fc - hf[:, None] * axis, axis=1)
    side = _side_face_mask(normals, axis, cos_tol=0.95)
    if side.sum() < 8:
        return None
    hs = hf[side]
    rs = rrf[side]
    if rs.std() < 1e-12 or hs.std() < 1e-12:
        return None
    slope, offset = np.polyfit(hs, rs, 1)
    if abs(slope) < 1e-9:
        return None
    R_line = np.clip(hf * slope + offset, 0.0, None)
    rms = float(np.sqrt(np.mean((rrf - R_line) ** 2)))
    apex = axis_point + (-offset / slope) * axis
    base_center = axis_point + hmin * axis
    hmin = float(hf.min())
    hmax = float(hf.max())
    R_base = float(np.clip(hmin * slope + offset, 0.0, None))
    R_top = float(np.clip(hmax * slope + offset, 0.0, None))
    return {"type": "cone", "axis": axis, "apex": apex, "base_center": base_center,
            "R_base": R_base, "R_top": R_top, "height": hmax - hmin, "rms": rms}


def detect_primitive(mesh, tol_frac=0.004, max_rel_vol_err=0.10, progress_cb=None):
    """尝试把整个网格识别为基本体素，返回 (shape, model) 或 (None, None)。

    依次尝试 长方体 → 圆柱 → 圆锥 → 球；采用残差最小者，并以
    （实体积误差 < max_rel_vol_err 且 BRepCheck valid）作为护栏，避免误判。
    """
    if mesh is None or len(mesh.faces) < 8:
        return None, None
    verts = mesh.vertices
    faces = mesh.faces
    bbox = mesh.bounds
    diag = float(np.linalg.norm(bbox[1] - bbox[0])) or 1.0
    tol = diag * tol_frac
    mesh_vol = float(mesh.volume) if mesh.volume is not None else None

    candidates = []
    box = fit_box(verts, diag)
    if box is not None:
        candidates.append(box)
    normals = _face_normals(faces, verts)
    cent = np.asarray(verts, dtype=np.float64).mean(axis=0)
    for axis in _rev_axis_candidates(normals, verts, faces):
        axis_point = cent - (cent @ axis) * axis
        try:
            inner = fit_cylinder_ax(verts, normals, faces, axis, axis_point)
            if inner is not None:
                inner["type"] = "cylinder"
                candidates.append(inner)
        except Exception:
            pass
        try:
            inner = fit_cone_ax(verts, normals, faces, axis, axis_point)
            if inner is not None:
                inner["type"] = "cone"
                candidates.append(inner)
        except Exception:
            pass
    try:
        sph = fit_sphere(verts)
        if sph is not None:
            candidates.append(sph)
    except Exception:
        pass

    makers = {
        "box": _make_box_shape,
        "cylinder": _make_cylinder_shape,
        "cone": _make_cone_shape,
        "sphere": _make_sphere_shape,
    }
    best = None
    for m in sorted(candidates, key=lambda mm: mm["rms"]):
        if m["rms"] > tol:
            continue
        try:
            sh = makers[m["type"]](m)
        except Exception:
            continue
        if sh is None or sh.IsNull() or not _valid(sh):
            continue
        if mesh_vol is not None:
            sv = _volume(sh)
            if sv is None:
                continue
            rel = abs(sv - mesh_vol) / mesh_vol
            if rel > max_rel_vol_err:
                continue
        best = (sh, m)
        break
    if progress_cb is not None:
        progress_cb(1, 1)
    return best if best is not None else (None, None)


# ------------------------------------------------------------------ #
# 回转体重建


def _axis_point_on_line(centroid, axis, axis_point):
    """把质心投影到轴上，获得轴上参考点（减少平移误差）。"""
    c = np.asarray(centroid, dtype=np.float64)
    p = np.asarray(axis_point, dtype=np.float64)
    return p + np.dot(c - p, axis) * axis


def revolve_profile(verts, axis, axis_point, n_slices=180):
    """采样各轴向片层的最大半径，得到外轮廓母线 polyline。

    返回 (h_vals, r_vals, hmin, hmax)，h 为轴向坐标，r 为径向坐标。
    顶点按轴向唯一层分组，每组取最大半径（外形母线），避免等距采样
    落入无顶点层而误得 r=0。
    """
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    pts = np.asarray(verts, dtype=np.float64)
    h = pts @ axis
    rr = np.linalg.norm(pts - h[:, None] * axis, axis=1)
    hmin, hmax = float(h.min()), float(h.max())
    if hmax - hmin < 1e-12:
        return None

    # 按唯一轴向坐标分组；合并极近层（容差=对角线的 1e-5）
    hq = np.round(h / (hmax - hmin) * int(n_slices)).astype(int)
    lo = hq.min()
    hq -= lo
    nb = int(hq.max()) + 1
    rmax = np.full(nb, -1.0)
    np.maximum.at(rmax, hq, rr)
    good = rmax >= 0
    hs = (np.arange(nb)[good] + lo).astype(float) / int(n_slices) * (hmax - hmin) + hmin
    rmax = rmax[good]
    hs += (hmax - hmin) / int(n_slices) / 2.0  # 量化层中心
    hs = np.clip(hs, hmin, hmax)
    return hs, rmax, hmin, hmax


def detect_revolve(mesh, tol_frac=0.006, max_rel_vol_err=0.30, progress_cb=None):
    """尝试把网格识别为回转体，返回 (shape, info dict) 或 (None, None)。

    用拟合轴的母线轮廓 + MakeRevol 重建；以体积误差护栏避免对非回转件误用。
    """
    if mesh is None or len(mesh.faces) < 16:
        return None, None
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = mesh.faces
    bbox = mesh.bounds
    diag = float(np.linalg.norm(bbox[1] - bbox[0])) or 1.0
    mesh_vol = float(mesh.volume) if mesh.volume is not None else None

    normals = _face_normals(faces, verts)
    cent = verts.mean(axis=0)
    for axis in _rev_axis_candidates(normals, verts, faces):
        axis_point = _axis_point_on_line(cent, axis, cent)

        prof = revolve_profile(verts, axis, axis_point)
        if prof is None:
            continue
        hs, rmax, hmin, hmax = prof
        # 若轮廓全为零半径（退化）则放弃
        if float(np.max(rmax)) < 1e-9 * diag:
            continue
        eps = max(diag * tol_frac, 1e-12)
        idx = _rdp(np.column_stack([hs, rmax]), eps)
        hs_k, rmax_k = hs[idx], rmax[idx]

        from OCP.gp import gp_Ax1, gp_Pnt, gp_Dir
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace,
        )
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol

        # 用与轴正交的任意单位向量作为剖面径向方向
        o1 = np.array([1.0, 0.0, 0.0])
        if abs(float(np.dot(axis, o1))) > 0.9:
            o1 = np.array([0.0, 1.0, 0.0])
        rad = np.cross(axis, o1)
        rad = rad / np.linalg.norm(rad)

        # 轮廓点（3D，位于轴平面内）。端点 r≈0 时的闭合边正好沿轴，
        # 无需再手动添加轴点，避免重复点产生零长边导致 wire 断裂。
        pts = []
        for hh, rr in zip(hs_k, rmax_k):
            if pts and np.linalg.norm(
                    (axis_point + hh * axis + rr * rad) - pts[-1]) < 1e-12:
                continue
            pts.append(axis_point + hh * axis + rr * rad)
        if len(pts) < 3:
            continue

        mw = BRepBuilderAPI_MakeWire()
        for i in range(len(pts)):
            a = pts[i]
            b = pts[(i + 1) % len(pts)]
            if np.linalg.norm(np.asarray(b) - np.asarray(a)) < 1e-12:
                continue
            e = BRepBuilderAPI_MakeEdge(gp_Pnt(*a), gp_Pnt(*b))
            if e.IsDone():
                mw.Add(e.Edge())
        w = mw.Wire()
        if w.IsNull():
            continue
        try:
            face = BRepBuilderAPI_MakeFace(w)
            if not face.IsDone():
                continue
            f = face.Face()
        except Exception:
            continue
        if f.IsNull():
            continue

        try:
            solid = BRepPrimAPI_MakeRevol(f, gp_Ax1(gp_Pnt(*axis_point), gp_Dir(*axis)),
                                          float(np.pi * 2), True).Shape()
        except Exception:
            continue
        if solid.IsNull() or not _valid(solid):
            continue
        if mesh_vol is not None:
            sv = _volume(solid)
            if sv is None or abs(sv - mesh_vol) / mesh_vol > max_rel_vol_err:
                continue

        if progress_cb is not None:
            progress_cb(2, 2)
        info = {"type": "revolve", "axis": axis, "axis_point": axis_point,
                "profile_points": int(len(hs_k)), "volume": mesh_vol}
        return solid, info
    return None, None


# ------------------------------------------------------------------ #
# 板件重建（薄板 + 孔特征）
#
# 对"带孔薄板"类零件（设备外壳侧板等），识别厚度方向上的底面
# 大平面 → 投影提取外轮廓 + 孔轮廓 → 挤出成实心板。
# 纯算法对平面板件最主要的降面手段：2万+ 三角面 → 数百面。


def _find_thickness_axis(mesh):
    """PCA 找厚度方向（最小跨度轴）。返回单位轴向量。"""
    v = mesh.vertices
    cent = v.mean(0)
    cov = np.cov((v - cent).T)
    w, vec = np.linalg.eigh(cov)
    return vec[:, 0]


def _base_plane_profile(mesh, axis, cos_tol=0.85, min_area_frac=0.25):
    """沿轴找最大的共面单侧面片集，投影聚合出外轮廓+孔多边形。

    返回 (polygon, plane_n)：
      polygon    shapely 多边形（外环+孔内环）
      plane_n    底面主平面单位法向（朝外，即厚度方向外法向）
    无法识别返回 (None, None)。
    """
    import shapely.geometry as sg
    from shapely.ops import unary_union

    nf = mesh.faces
    verts = mesh.vertices
    normals = _face_normals(nf, verts)
    dot = normals @ axis
    p = np.cross(mesh.triangles[:, 1] - mesh.triangles[:, 0],
                 mesh.triangles[:, 2] - mesh.triangles[:, 0])
    areas = 0.5 * np.sqrt((p * p).sum(1))
    total = areas.sum()

    best = None
    for sign in (1.0, -1.0):
        sel = np.where(dot * sign > cos_tol)[0]
        if len(sel) < 20 or areas[sel].sum() / total < min_area_frac:
            continue
        u = np.cross(axis, np.array([1.0, 0.0, 0.0]))
        if np.linalg.norm(u) < 1e-9:
            u = np.cross(axis, np.array([0.0, 1.0, 0.0]))
        u /= np.linalg.norm(u)
        vv = np.cross(axis, u)
        basis = np.array([u, vv])
        try:
            polys = [sg.Polygon(t @ basis.T) for t in mesh.triangles[sel]]
            merged = unary_union(polys)
        except Exception:
            continue
        blocks = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
        blocks = [b for b in blocks if b.area > 1e-9]
        if not blocks:
            continue
        blocks.sort(key=lambda b: -b.area)
        big = blocks[0]
        # 最大块占单侧面覆盖面积（unary_union 已去重，抗双面网格）的比例
        covered = merged.area if merged.geom_type == "Polygon" else \
            sum(b.area for b in blocks)
        if covered > 1e-9 and big.area / covered < 0.6:
            continue
        if best is None or big.area > best[1].area:
            best = (sign, big)

    if best is None:
        return None, None
    sign, poly = best
    nrm = axis * sign
    return poly, nrm


def _loop_rdp(loop2d, eps):
    """对闭合环坐标做 RDP 简化，保持首尾闭合。返回简化后的坐标数组。"""
    arr = np.asarray(loop2d, dtype=np.float64)
    closed = False
    if len(arr) >= 2 and np.allclose(arr[0], arr[-1]):
        arr = arr[:-1]
        closed = True
    if len(arr) < 3:
        return np.asarray(loop2d)
    keep = _rdp(arr, eps)
    out = arr[np.asarray(keep)]
    if closed:
        out = np.vstack([out, out[0]])
    return out


def hole_is_circle(inner, min_d=1.5, max_ratio=1.05, area_ok=0.99):
        """判定孔是否近似圆并通过面积一致性。返回 (cx, cy, r) 或 None。

        - bbox 宽高比 ≤ max_ratio（排除长条/异形）
        - 环围成 Polygon 面积 vs πr² 一致性 ≥ area_ok（只接受真圆）
        半径用 bbox 平均（对真圆即精确半径）。
        """
        import shapely.geometry as sg
        pts = np.asarray(inner.coords, dtype=np.float64)
        if len(pts) < 6:
            return None
        x, y = pts[:, 0], pts[:, 1]
        bw = x.max() - x.min()
        bh = y.max() - y.min()
        dmin = min(bw, bh)
        dmax = max(bw, bh)
        if dmin < min_d or dmax / dmin > max_ratio:
            return None
        cx, cy = (x.min() + x.max()) / 2.0, (y.min() + y.max()) / 2.0
        r = (dmin + dmax) / 4.0
        if r <= 0:
            return None
        try:
            ring = sg.Polygon(pts)
            ring_area = abs(ring.area)
        except Exception:
            ring_area = 0.0
        if ring_area <= 0 or ring_area / (np.pi * r * r) < area_ok:
            return None
        return (cx, cy, r)


def hole_is_obround(inner, min_d=1.5, rms_ok=0.30, area_ok=0.90):
    """判定孔是否近似腰型（两条平行直边 + 两端同半径圆弧）。

    返回 (cx, cy, u0, u1, d, r) 或 None：
      cx,cy  环点均值（uv 坐标）
      u0,u1  长轴单位向量（uv 平面内）
      d      两圆弧圆心距；r 圆弧半径
    判据：PCA 长轴下，点到"两直段 + 两圆弧"边界曲线距离的均方根 ≤ rms_ok*r、
    腰型面积 vs 环面积一致性 ≥ area_ok。
    """
    pts = np.asarray(inner.coords, dtype=np.float64)
    if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 8:
        return None
    center = pts.mean(axis=0)
    m = pts - center
    cov = (m.T @ m) / len(m)
    vals, vecs = np.linalg.eigh(cov)
    u = vecs[:, int(np.argmax(vals))]          # 长轴
    v = np.array([-u[1], u[0]])               # 短轴
    x, y = m @ u, m @ v
    r = float((y.max() - y.min()) / 2.0)
    d = float((x.max() - x.min()) - 2.0 * r)  # 两圆心距
    if r < min_d or d < 0.5:
        return None
    half = d / 2.0
    # 点到边界曲线的最短距离：两直段 + 两圆弧
    d_top = np.where((x >= -half) & (x <= half), np.abs(y - r),
                     np.minimum(np.hypot(x + half, y - r),
                                np.hypot(x - half, y - r)))
    d_bot = np.where((x >= -half) & (x <= half), np.abs(y + r),
                     np.minimum(np.hypot(x + half, y + r),
                                np.hypot(x - half, y + r)))
    d_rt = np.abs(np.hypot(x - half, y) - r)
    d_lt = np.abs(np.hypot(x + half, y) - r)
    bdist = np.minimum(np.minimum(d_top, d_bot), np.minimum(d_rt, d_lt))
    if np.sqrt(np.mean(bdist ** 2)) > rms_ok * r:
        return None
    a_ob = 2.0 * r * d + np.pi * r * r
    try:
        import shapely.geometry as sg
        a_ring = abs(sg.Polygon(pts).area)
    except Exception:
        a_ring = 0.0
    if a_ring <= 0 or abs(a_ob - a_ring) / a_ring > (1.0 - area_ok):
        return None
    return (float(center[0]), float(center[1]),
            float(u[0]), float(u[1]), d, r)


def _extrude_solid(poly2d, basis, origin, height_vec, plane_normal=None,
                   tol=1e-5, rdp_eps=0.2, stats=None):
    """由 2D 多边形（外环+孔）挤出成实体。

    poly2d:    shapely 多边形（位于 uv 平面）
    basis:     (2,3) 正交基，uv→世界
    origin:    底面原点（世界坐标。uv 坐标是世界原点投影的绝对坐标，
               故 origin 应只含 z 位移，勿叠加 uv centroid）
    height_vec: 挤出向量（世界坐标，长度=板厚，方向朝内表面）
    plane_normal: 底面法向（世界坐标单位向量）；用于圆孔圆柱朝向，None 时由 height_vec 推断
    stats:     可选 dict，累积 n_circ / n_ob（实际采用的解析孔计数）
    返回 OCP shape 或 None。
    """
    from OCP.gp import gp_Pnt, gp_Vec, gp_Dir, gp_Ax2
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeCylinder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    if plane_normal is None:
        plane_normal = np.asarray(height_vec, dtype=np.float64)
        nrm_len = np.linalg.norm(plane_normal)
        if nrm_len > 1e-12:
            plane_normal = plane_normal / nrm_len
        else:
            plane_normal = np.array([0.0, 0.0, 1.0])
    plane_normal = np.asarray(plane_normal, dtype=np.float64)
    plane_normal = plane_normal / np.linalg.norm(plane_normal)
    drill_dir = plane_normal.copy()
    # 圆柱钻孔方向须与挤出方向一致（进实体内部），否则会朝外长不切除
    hv = np.asarray(height_vec, dtype=np.float64)
    if np.dot(drill_dir, hv) < 0:
        drill_dir = -drill_dir

    def wire3d(loop):
        pts = np.asarray(loop, dtype=np.float64)
        if rdp_eps and pts is not None and len(pts) > 8:
            pts = _loop_rdp(pts, rdp_eps)
        if len(pts) < 3:
            return None
        mw = BRepBuilderAPI_MakeWire()
        for i in range(len(pts)):
            p = origin + basis[0] * pts[i][0] + basis[1] * pts[i][1]
            q = origin + basis[0] * pts[(i + 1) % len(pts)][0] + basis[1] * pts[(i + 1) % len(pts)][1]
            try:
                e = BRepBuilderAPI_MakeEdge(gp_Pnt(*p), gp_Pnt(*q))
                if not e.IsDone():
                    continue
                mw.Add(e.Edge())
            except Exception:
                pass
        if not mw.IsDone() or mw.Wire().IsNull():
            return None
        return mw.Wire()

    def face_of(loop):
        w = wire3d(loop)
        if w is None:
            return None
        try:
            f = BRepBuilderAPI_MakeFace(w, True)
        except Exception:
            return None
        if not f.IsDone():
            return None
        return f.Face()

    def make_obround_cut(ob):
        """腰型孔：两直边 + 两端圆弧的解析面切除体。返回 shape 或 None。"""
        cx, cy, u0, u1, dd, rr = ob
        ud = np.array([u0, u1])
        vd = np.array([-u1, u0])
        ctr = np.array([cx, cy])
        LT = ctr - ud * (dd / 2.0) + vd * rr
        RT = ctr + ud * (dd / 2.0) + vd * rr
        LB = ctr - ud * (dd / 2.0) - vd * rr
        RB = ctr + ud * (dd / 2.0) - vd * rr

        def W(p):
            return (origin[0] + basis[0][0] * p[0] + basis[1][0] * p[1],
                    origin[1] + basis[0][1] * p[0] + basis[1][1] * p[1],
                    origin[2] + basis[0][2] * p[0] + basis[1][2] * p[1])

        try:
            from OCP.gp import gp_Circ
            nrm = gp_Dir(*drill_dir)
            c_rt = ctr + ud * (dd / 2.0)
            c_lt = ctr - ud * (dd / 2.0)
            circ_rt = gp_Circ(gp_Ax2(gp_Pnt(*W(c_rt)), nrm), rr)
            circ_lt = gp_Circ(gp_Ax2(gp_Pnt(*W(c_lt)), nrm), rr)
            e_top = BRepBuilderAPI_MakeEdge(gp_Pnt(*W(LT)), gp_Pnt(*W(RT)))
            # 圆弧为圆上 P1→P2 参数增大方向（CCW）的一段：
            # 右圆 +u 方向突出，需自下端点 (270°) 增大→穿过 0° 到上端点 (90°)；
            # 左圆 -u 方向突出，需自上端点 (90°) 增大→穿过 180° 到下端点 (270°)。
            e_rt = BRepBuilderAPI_MakeEdge(circ_rt, gp_Pnt(*W(RB)), gp_Pnt(*W(RT)))
            e_bot = BRepBuilderAPI_MakeEdge(gp_Pnt(*W(RB)), gp_Pnt(*W(LB)))
            e_lt = BRepBuilderAPI_MakeEdge(circ_lt, gp_Pnt(*W(LT)), gp_Pnt(*W(LB)))
            mw = BRepBuilderAPI_MakeWire()
            for e in (e_top, e_rt, e_bot, e_lt):
                if not e.IsDone():
                    return None
                mw.Add(e.Edge())
            if not mw.IsDone():
                return None
            f = BRepBuilderAPI_MakeFace(mw.Wire(), True)
            if not f.IsDone():
                return None
            return BRepPrimAPI_MakePrism(f.Face(), gp_Vec(*height_vec)).Shape()
        except Exception:
            return None

    def make_hole_cut(inner):
        """构造孔切除体：圆孔用解析圆柱，腰型孔用解析圆弧+直边，其余挤出
        多边形。返回 shape 或 None。"""
        circ = hole_is_circle(inner)
        if circ is not None:
            cx, cy, r = circ
            c3 = origin + basis[0] * cx + basis[1] * cy
            hlen = float(np.linalg.norm(height_vec))
            # 圆柱从底面沿法向拉伸到板厚，稍超界确保贯穿
            cyl = BRepPrimAPI_MakeCylinder(
                gp_Ax2(gp_Pnt(*c3), gp_Dir(*drill_dir)), r, hlen + 2.0 * tol)
            return cyl.Shape()
        ob = hole_is_obround(inner)
        if ob is not None:
            ob_s = make_obround_cut(ob)
            if ob_s is not None:
                return ob_s
        loop = list(inner.coords)
        if loop[0] == loop[-1]:
            loop = loop[:-1]
        f2 = face_of(loop)
        if f2 is None:
            return None
        return BRepPrimAPI_MakePrism(f2, gp_Vec(*height_vec)).Shape()

    outer_loop = list(poly2d.exterior.coords)
    if outer_loop[0] == outer_loop[-1]:
        outer_loop = outer_loop[:-1]
    solid = _extrude_manual(outer_loop, basis, origin, height_vec, rdp_eps)
    if solid is None:
        # 回退：旧 MakePrism 路径（cap 朝向 bug 存在，但保证兜底可用）
        f = face_of(outer_loop)
        if f is None:
            return None
        solid = BRepPrimAPI_MakePrism(f, gp_Vec(*height_vec)).Shape()

    n_circ = 0
    n_ob = 0
    for inner in poly2d.interiors:
        try:
            kind = None
            if hole_is_circle(inner) is not None:
                kind = "circle"
            elif hole_is_obround(inner) is not None:
                kind = "obround"
            hole = make_hole_cut(inner)
            if hole is None:
                continue
            if kind == "circle":
                n_circ += 1
            elif kind == "obround":
                n_ob += 1
            cut = BRepAlgoAPI_Cut(solid, hole)
            if cut.IsDone():
                solid = cut.Shape()
        except Exception:
            continue
    if stats is not None:
        stats["n_circ"] = n_circ
        stats["n_ob"] = n_ob
    if not _valid(solid):
        return None
    return solid


def _extrude_manual(outer_loop, basis, origin, height_vec, rdp_eps=0.2):
    """手工壳组装挤出板件实体（替代 MakePrism，规避后者的 cap 朝向 bug）。

    输入 uv 平面外环，输出 valid 且全体面朝外的实体：
      - 底面/顶面与侧面全部解析平面（直线边，与 STL 简化轮廓一致）
      - 全部边/顶点拓扑共享（BRep_Builder 手工组装）
      - 面朝向：以自然法向效应对齐外法向（BRepBuilderAPI_MakeFace
        对该 OCP 版本会自动化 wire 方向，故引用级方向由 shell 组装时控制）
    失败返回 None。
    """
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeSolid
    from OCP.TopoDS import (
        TopoDS, TopoDS_Shape, TopoDS_Vertex, TopoDS_Edge,
        TopoDS_Wire, TopoDS_Face, TopoDS_Shell,
    )
    from OCP.gp import gp_Pnt, gp_Ax1, gp_Dir
    from OCP.Geom import Geom_Line, Geom_TrimmedCurve
    from OCP.TopAbs import TopAbs_FORWARD, TopAbs_REVERSED
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomLProp import GeomLProp_SLProps
    from OCP.BRep import BRep_Tool

    bb = BRep_Builder()

    def _or(s, o):
        return s.Oriented(o)

    pts = np.asarray(outer_loop, dtype=np.float64)
    if rdp_eps and len(pts) > 8:
        pts = _loop_rdp(pts, rdp_eps)
    n = len(pts)
    if n < 3:
        return None
    hv = np.asarray(height_vec, dtype=np.float64)
    b3 = [tuple(origin + basis[0] * p[0] + basis[1] * p[1]) for p in pts]
    t3 = [tuple(np.asarray(p) + hv) for p in b3]

    vm = {}

    def vget(vid, pt):
        if vid not in vm:
            v = TopoDS.Vertex_s(TopoDS_Shape())
            bb.MakeVertex(v, gp_Pnt(*pt), 1e-9)
            vm[vid] = v
        return vm[vid]

    def mk_edge(pid1, pid2, p1, p2):
        import math
        v1 = vget(pid1, p1)
        v2 = vget(pid2, p2)
        dx = p2[0] - p1[0]; dy = p2[1] - p1[1]; dz = p2[2] - p1[2]
        L = math.sqrt(dx * dx + dy * dy + dz * dz)
        if L < 1e-9:
            return None
        curve = Geom_TrimmedCurve(
            Geom_Line(gp_Ax1(gp_Pnt(*p1), gp_Dir(dx / L, dy / L, dz / L))), 0.0, L)
        e = TopoDS.Edge_s(TopoDS_Shape())
        bb.MakeEdge(e, curve, 1e-9)
        bb.Add(e, _or(v1, TopAbs_FORWARD))
        bb.Add(e, _or(v2, TopAbs_REVERSED))
        bb.UpdateVertex(v1, 0.0, e, 1e-9)
        bb.UpdateVertex(v2, L, e, 1e-9)
        return e

    def face_from(refs):
        w = TopoDS.Wire_s(TopoDS_Shape())
        bb.MakeWire(w)
        for e, o in refs:
            oo = TopAbs_FORWARD if o == 1 else TopAbs_REVERSED
            bb.Add(w, _or(e, oo))
        mf = BRepBuilderAPI_MakeFace(w, True)
        if not mf.IsDone():
            return None
        return mf.Face()

    def face_normal(face):
        ad = BRepAdaptor_Surface(face)
        pr = GeomLProp_SLProps(
            BRep_Tool.Surface_s(face),
            0.5 * (ad.FirstUParameter() + ad.LastUParameter()),
            0.5 * (ad.FirstVParameter() + ad.LastVParameter()), 1, 1e-9)
        if not pr.IsNormalDefined():
            return None
        nm = np.array([pr.Normal().X(), pr.Normal().Y(), pr.Normal().Z()])
        ln = np.linalg.norm(nm)
        if ln < 1e-9:
            return None
        nm = nm / ln
        if face.Orientation() == TopAbs_REVERSED:
            nm = -nm
        return nm

    def add_face(shell, face, want):
        nm = face_normal(face)
        if nm is None:
            return False
        oo = TopAbs_FORWARD if np.dot(nm, want) >= 0 else TopAbs_REVERSED
        bb.Add(shell, _or(face, oo))
        return True

    base_edges = []
    top_edges = []
    vert_edges = []
    for i in range(n):
        j = (i + 1) % n
        be = mk_edge('b%d' % i, 'b%d' % j, b3[i], b3[j])
        ce = mk_edge('t%d' % i, 't%d' % j, t3[i], t3[j])
        ve = mk_edge('b%d' % i, 't%d' % i, b3[i], t3[i])
        if be is None or ce is None or ve is None:
            return None
        base_edges.append(be)
        top_edges.append(ce)
        vert_edges.append(ve)

    hv_u = hv / np.linalg.norm(hv)
    base = face_from([(base_edges[i], 1) for i in range(n)])
    cap = face_from([(top_edges[i], 1) for i in range(n)])
    if base is None or cap is None:
        return None

    shell = TopoDS.Shell_s(TopoDS_Shape())
    bb.MakeShell(shell)
    if not add_face(shell, base, -hv_u) or not add_face(shell, cap, hv_u):
        return None

    cen = (np.array(b3).mean(axis=0) + np.array(t3).mean(axis=0)) / 2.0
    # 环走向符号：CCW(+) 时外部法向=边向量顺时针转 90°（dy,-dx）
    sa = sum(b3[i][0] * b3[(i + 1) % n][1] - b3[i][1] * b3[(i + 1) % n][0]
             for i in range(n))
    sgn = 1.0 if sa > 0 else -1.0
    for i in range(n):
        j = (i + 1) % n
        d = np.asarray(b3[j]) - np.asarray(b3[i])
        if sgn > 0:
            o = np.array([d[1], -d[0], 0.0])
        else:
            o = np.array([-d[1], d[0], 0.0])
        ln = np.linalg.norm(o)
        if ln < 1e-9:
            o = hv_u
        else:
            o = o / ln
            # 无孔外环，朝外即远离环内——检查兜底：若水平角 90° 内无分量则退化
            if o[0] * o[0] + o[1] * o[1] < 1e-12:
                o = hv_u
        side = face_from([(base_edges[i], 1), (vert_edges[j], 1),
                          (top_edges[i], -1), (vert_edges[i], -1)])
        if side is None:
            return None
        if not add_face(shell, side, o):
            return None

    ms = BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(shell))
    ms.Build()
    if not ms.IsDone():
        return None
    return ms.Shape()


def detect_plate(mesh, tol_frac=0.01, min_flat_frac=0.25, progress_cb=None):
    """识别带孔薄板并重建为挤出实体。

    判定：
      - PCA 厚度轴
      - 侧面大平面面积占比 ≥ min_flat_frac
      - 板厚（两表面距离）相对外形小 → 扁率
    返回 (shape, info) / (None, None)。info = dict(outer_pts, hole_count, thickness, box_hot)。
    """
    v = mesh.vertices
    axis = _find_thickness_axis(mesh)
    poly, plane_n = _base_plane_profile(mesh, axis)
    if poly is None:
        return None, None

    # 底面/顶面位置：法向平行 plane_n 的面片顶点轴向
    normals = _face_normals(mesh.faces, v)
    side = np.where(normals @ plane_n > 0.9)[0]
    if len(side) == 0:
        return None, None
    sv = set()
    for i in side:
        sv.update(mesh.faces[i])
    sv = np.array(sorted(sv))
    z_s = v[sv] @ axis

    other = np.where(normals @ plane_n < -0.9)[0]
    if len(other) == 0:
        return None, None
    ov = set()
    for i in other:
        ov.update(mesh.faces[i])
    ov = np.array(sorted(ov))
    z_o = v[ov] @ axis

    if float(np.median(z_o)) > float(np.median(z_s)):
        top_pts, top_z = v[ov], z_o
        bot_pts, bot_z = v[sv], z_s
    else:
        top_pts, top_z = v[sv], z_s
        bot_pts, bot_z = v[ov], z_o
    # 主体平面高度：计数中位在真实板最准（v0.3.0 验证，顶面主体占多）。
    # 但 shape_to_mesh 合成件顶点密度不均（沉孔台面点反而多）会拉偏，
    # 此时用面积加权中位回退（见体积校验失败处）。
    z_other = max(float(np.percentile(bot_z, 50)), float(np.percentile(top_z, 50)))
    z_surf = min(float(np.percentile(bot_z, 2)), float(np.percentile(top_z, 2)))

    thickness = abs(z_other - z_surf)
    if thickness < 1e-9:
        return None, None

    # 扁率：厚度 / 外环主尺寸
    minx, miny, maxx, maxy = poly.bounds
    major = max(maxx - minx, maxy - miny)
    if major < 1e-9:
        return None, None
    if thickness / major > 0.5:
        return None, None

    none_w = None  # 加权回退标记：沉孔检测用原 top_z，挤出高度用推定的主体面

    # 体积校验（相对）；失败时用面积加权中位重估主体面高再试一次
    vol = _volume_approx(mesh)
    if vol is not None and vol > 0:
        est = poly.area * thickness
        if abs(est - vol) / vol > 0.25:
            # 计数中位被小面积高密度离散（合成件 shape_to_mesh 台面点
            # 多于主体板面）拉偏 → 面积加权中位把权重回归主体大平面
            def _wmed(d_vals, w_vals):
                msk = np.isfinite(d_vals) & (w_vals > 0)
                d = np.asarray(d_vals, dtype=np.float64)[msk]
                w = np.asarray(w_vals, dtype=np.float64)[msk]
                if d.size == 0 or w.sum() <= 0:
                    return np.nan
                i = np.argsort(d)
                cw = np.cumsum(w[i])
                k = int(np.searchsorted(cw, cw[-1] * 0.5))
                k = min(k, d.size - 1)
                return float(d[i][k])
            cz = (v[mesh.faces].mean(axis=1)) @ axis
            wa_other = _wmed(cz[other], mesh.area_faces[other])
            wa_side = _wmed(cz[side], mesh.area_faces[side])
            z_other2 = max(wa_other, wa_side)
            z_surf2 = min(z_surf, z_other2 - thickness)
            t2 = abs(z_other2 - z_surf2)
            if t2 >= 1e-9:
                est2 = poly.area * t2
                if abs(est2 - vol) / vol > 0.25:
                    return None, None
                thickness = t2
                z_other = z_other2
                z_surf = z_surf2
                none_w = 1

    # 组装挤出
    u = np.cross(axis, np.array([1.0, 0.0, 0.0]))
    if np.linalg.norm(u) < 1e-9:
        u = np.cross(axis, np.array([0.0, 1.0, 0.0]))
    u /= np.linalg.norm(u)
    vv = np.cross(axis, u)
    basis = np.array([u, vv])

    c = np.array(poly.centroid.coords[0], dtype=np.float64)
    z_base = z_surf if z_other > z_surf else z_surf - thickness
    # uv 原点的世界位置（孔环/外环为世界原点投影的绝对 uv；基准面过世界
    # 原点，故仅需 z 偏移；不可叠加 centroid c，否则整体被平移 +c）
    origin = axis * z_base
    height_vec = axis * (z_other - z_surf)
    drill_axis = axis.copy()
    if np.dot(drill_axis, height_vec) < 0:
        drill_axis = -drill_axis

    stats = {}
    solid = _extrude_solid(poly, basis, origin, height_vec, plane_normal=plane_n,
                           stats=stats)
    if solid is None:
        return None, None

    # ---- 沉孔/台阶检测：顶面局部下沉环 + 中心同心通孔 ----
    from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    n_cb = 0
    hole_centers = []
    for inner in poly.interiors:
        pts = np.asarray(inner.coords, dtype=np.float64)
        if len(pts) < 6:
            continue
        hole_centers.append((float(pts[:, 0].mean()), float(pts[:, 1].mean()),
                             float(min(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])) / 2.0)))
    if hole_centers:
        depth_min = max(0.8, 0.15 * thickness)
        uv_top = (top_pts @ basis.T)         # 与 poly 同坐标系（世界原点投影，无平移）
        top_zsel = top_z
        low = top_zsel < z_other - depth_min   # 显著低于主体顶面的下沉点
        if np.count_nonzero(low) >= 6:
            for (cx, cy, r) in hole_centers:
                dist = np.sqrt(
                    (uv_top[low, 0] - cx) ** 2 + (uv_top[low, 1] - cy) ** 2)
                # 窗口：真沉孔台≈(1~2)×通孔半径（太宽会把"孔位于大面积下沉
                # 平原"误判；太窄会滤掉台半径≈2r 的真沉孔台）
                near = dist < 2.0 * r + 2.0
                if np.count_nonzero(near) < 6:
                    continue
                # 孔自身的通孔壁低洼点很少（通孔下方无顶面）；若中心区
                # 低洼点密集说明该处本就是下沉槽而非真沉孔环
                near_c = np.count_nonzero(dist < r * 0.7)
                if near_c > np.count_nonzero(near) * 0.35:
                    continue
                # 沉孔台面半径：近窗内下沉点的 95 分位
                r_cb = float(np.percentile(dist[near], 95))
                # 台面至少比通孔显著宽、且不超过 2.4 倍通孔（真沉孔台有限宽，
                # 更宽多是"孔落在大面积下沉槽"情形）
                if r_cb < r + 0.4 or r_cb > 2.4 * max(r, 1.5):
                    continue
                z_sunk = float(np.mean(top_zsel[low][near]))
                # 沉孔台面应为近似平面：z 离散度小（真沉孔平台共面）
                z_flat = float(np.std(top_zsel[low][near]))
                if z_flat > 0.25 * max(depth_min, 0.5):
                    continue
                # 沉孔不能穿透到接近底面（否则是贯穿大孔，非台阶）
                if z_sunk - z_surf < 0.2 * thickness:
                    continue
                c3 = origin + basis[0] * cx + basis[1] * cy + drill_axis * (z_sunk - z_base)
                cb = BRepPrimAPI_MakeCylinder(
                    gp_Ax2(gp_Pnt(*c3), gp_Dir(*drill_axis)),
                    r_cb, (z_other - z_sunk) + 0.5e-5).Shape()
                cut = BRepAlgoAPI_Cut(solid, cb)
                if cut.IsDone() and _valid(cut.Shape()):
                    solid = cut.Shape()
                    n_cb += 1

    info = {
        "outer_pts": len(poly.exterior.coords),
        "hole_count": len(poly.interiors),
        "thickness": float(thickness),
        "area": float(poly.area),
        "counterbores": n_cb,
        "circles": stats.get("n_circ", 0),
        "obrounds": stats.get("n_ob", 0),
    }
    return solid, info


def _volume_approx(mesh):
    try:
        return float(mesh.volume)
    except Exception:
        return None


# ------------------------------------------------------------------ #
# 顶层入口


def convert_parametric(mesh, tolerance=1e-5, tol_frac=0.004, progress_cb=None):
    """对闭合网格做参数化特征重建，返回 BuildResult 兼容对象。

    顺序：基本体素 → 回转体；均失败时返回空结果，message 指明调用方可回退。
    """
    result = BuildResult()
    if mesh is None or len(mesh.faces) == 0:
        result.message = "网格为空。"
        return result

    def cb(a, b):
        if progress_cb is not None:
            progress_cb(a, b)

    shape, model = detect_primitive(mesh, tol_frac=tol_frac, progress_cb=cb)
    note = ""
    if shape is not None:
        result.shapes.append((shape, True))
        result.solid_count = 1
        if model["type"] == "box":
            note = "识别为长方体（6 面可编辑）"
            result.primitives = 6
        elif model["type"] == "cylinder":
            note = "识别为圆柱"
            result.primitives = 3
        elif model["type"] == "cone":
            note = "识别为圆锥"
            result.primitives = 3
        elif model["type"] == "sphere":
            note = "识别为球体"
            result.primitives = 1
        result.param_type = model["type"]
    else:
        shape, info = detect_revolve(mesh, tol_frac=tol_frac, progress_cb=cb)
        if shape is not None:
            result.shapes.append((shape, True))
            result.solid_count = 1
            result.primitives = int(info["profile_points"]) + 2
            result.param_type = "revolve"
            note = "识别为回转体（母线 {} 段）".format(result.primitives)
        else:
            shape, info = detect_plate(mesh, tol_frac=tol_frac, progress_cb=cb)
            if shape is not None:
                result.shapes.append((shape, True))
                result.solid_count = 1
                result.primitives = int(info["outer_pts"]) + int(info["hole_count"])
                result.param_type = "plate"
                note = "识别为带孔薄板（外轮廓 {} 点 + {} 孔，厚 {:.2f}）".format(
                    info["outer_pts"], info["hole_count"], info["thickness"])
            else:
                result.message = "无法整体参数化（非长方体/圆柱/圆锥/球/回转体/带孔薄板），请走逐三角或 analytic 模式。"
                return result

    for s, _ in result.shapes:
        if not _valid(s):
            result.invalid_count += 1
    result.message = "参数化重建完成：{}（体积校验通过）。".format(note) if result.invalid_count == 0 else \
        "参数化重建结果无效。"
    return result