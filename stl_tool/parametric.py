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
            result.message = "无法整体参数化（非长方体/圆柱/圆锥/球/回转体），请走逐三角或 analytic 模式。"
            return result

    for s, _ in result.shapes:
        if not _valid(s):
            result.invalid_count += 1
    result.message = "参数化重建完成：{}（体积校验通过）。".format(note) if result.invalid_count == 0 else \
        "参数化重建结果无效。"
    return result