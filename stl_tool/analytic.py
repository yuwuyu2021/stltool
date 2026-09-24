"""STL 网格 → analytic（解析曲面）B-Rep STEP 重建模块。

策略：把三角网格分割成若干连通区域，对每个区域拟合平面/圆柱/圆锥/球四种
解析曲面，按残差选择最优类型，再用 OpenCASCADE 构造对应的解析面
（gp_Pln / gp_Cylinder / gp_Cone / gp_Sphere）并以区域边界环为界，缝合为
闭合实体。适用于机械件等以平面/柱/锥/球面为主、可显著降低 STEP 面数的模型。

作为原“逐三角面缝合”导出的可选项；无法良好拟合的区域退回平面分片。
"""

import numpy as np

# ------------------------------------------------------------------ #


def _cov_eig(points):
    """点集 PCA：返回 (特征值升序数组, 特征向量列组成的(3,3)矩阵)。"""
    pts = np.asarray(points, dtype=np.float64)
    centroid = pts.mean(axis=0)
    cov = np.cov((pts - centroid).T)
    w, v = np.linalg.eigh(cov)
    return w, v, centroid


def fit_plane(points):
    """拟合平面。返回 (normal(3,),[axis_u,axis_v], centroid, rms)。"""
    pts = np.asarray(points, dtype=np.float64)
    if pts.shape[0] < 3:
        return None
    w, v, centroid = _cov_eig(pts)
    normal = v[:, 0]
    nlen = np.linalg.norm(normal)
    if nlen < 1e-12:
        return None
    normal = normal / nlen
    u = v[:, 1]
    vv = v[:, 2]
    # 正交化 u,v
    u = u - np.dot(u, normal) * normal
    u = u / np.linalg.norm(u)
    vv = vv - np.dot(vv, normal) * normal - np.dot(vv, u) * u
    vv = vv / (np.linalg.norm(vv) + 1e-12)
    d = np.dot(pts - centroid, normal)
    rms = float(np.sqrt(np.mean(d * d)))
    return {"type": "plane", "normal": normal, "u": u, "v": vv,
            "centroid": centroid, "rms": rms}


def _face_normals(tris, verts):
    """计算三角面未归一化/归一化法向与面心。"""
    t = verts[tris]
    e1 = t[:, 1] - t[:, 0]
    e2 = t[:, 2] - t[:, 0]
    fn = np.cross(e1, e2)
    nlen = np.linalg.norm(fn, axis=1, keepdims=True)
    nlen[nlen == 0] = 1.0
    return fn / nlen


def _axis_from_normals(normals):
    """由面法向的散度矩阵最小特征向量得旋转对称轴方向。"""
    S = normals.T @ normals
    w, v = np.linalg.eigh(S)
    axis = v[:, 0]
    if np.linalg.norm(axis) < 1e-12:
        return None
    return axis / np.linalg.norm(axis)


def fit_cylinder(tris, verts):
    """拟合圆柱（三角面法向散度定轴 + 投影圆拟合）。轴点=轴线上离质心最近点。"""
    normals = _face_normals(tris, verts)
    axis = _axis_from_normals(normals)
    if axis is None:
        return None
    o1 = np.array([1.0, 0.0, 0.0])
    if abs(np.dot(axis, o1)) > 0.9:
        o1 = np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, o1)
    u = u / np.linalg.norm(u)
    vv = np.cross(axis, u)
    vv = vv / np.linalg.norm(vv)
    rel = verts[tris.reshape(-1)] - verts[tris.reshape(-1)].mean(axis=0)
    pu = rel @ u
    pv = rel @ vv
    A = np.column_stack([2 * pu, 2 * pv, np.ones(len(rel))])
    b = pu * pu + pv * pv
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except Exception:
        return None
    cu, cv, alpha = sol
    R2 = alpha + cu * cu + cv * cv
    if R2 <= 1e-12:
        return None
    R = float(np.sqrt(R2))
    plane_cent = verts[tris.reshape(-1)].mean(axis=0)
    axis_point = plane_cent + cu * u + cv * vv
    # 投影回与轴垂直：把 axis_point 调整到轴线上离质心最近点
    axis_point = axis_point - np.dot(axis_point - plane_cent, axis) * axis
    rad = np.linalg.norm(np.cross(verts[tris.reshape(-1)] - axis_point, axis), axis=1)
    rms = float(np.sqrt(np.mean((rad - R) ** 2)))
    return {"type": "cylinder", "axis": axis, "axis_point": axis_point,
            "u": u, "v": vv, "R": R, "rms": rms}


def fit_cone(tris, verts):
    """拟合圆锥：法向散度定轴；r=k*|h| 线性回归定半角与顶点。"""
    normals = _face_normals(tris, verts)
    axis = _axis_from_normals(normals)
    if axis is None:
        return None
    o1 = np.array([1.0, 0.0, 0.0])
    if abs(np.dot(axis, o1)) > 0.9:
        o1 = np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, o1)
    u = u / np.linalg.norm(u)
    vv = np.cross(axis, u)
    vv = vv / np.linalg.norm(vv)
    pts = verts[tris.reshape(-1)]
    cent = pts.mean(axis=0)
    rel = pts - cent
    h = rel @ axis
    r = np.linalg.norm(rel - np.outer(h, axis), axis=1)
    nz = np.abs(h) > 1e-9
    if nz.sum() < 5:
        return None
    hh = np.abs(h[nz])
    rr = r[nz]
    # 圆锥要求 r 与 |h| 强线性相关；圆柱 r 恒定（corr≈0）应拒绝
    if rr.std() < 1e-12 or hh.std() < 1e-12:
        return None
    corr = np.corrcoef(hh, rr)[0, 1] if len(hh) > 2 else 0.0
    if not np.isfinite(corr) or abs(corr) < 0.85:
        return None
    slope, offset = np.polyfit(hh, rr, 1)
    if abs(slope) < 1e-6 or not np.isfinite(slope):
        return None
    half = float(np.arctan(slope))
    if half < 1e-4 or abs(half - np.pi / 2) < 1e-4:
        return None
    t_apex = -offset / slope
    apex = cent + t_apex * axis
    rms = float(np.sqrt(np.mean((rr - slope * hh - offset) ** 2)))
    return {"type": "cone", "axis": axis, "apex": apex, "u": u, "v": vv,
            "half_angle": half, "rms": rms}


def fit_sphere(points):
    """拟合球。返回 (center, R, rms)。失败返回 None。"""
    pts = np.asarray(points, dtype=np.float64)
    if pts.shape[0] < 8:
        return None
    A = np.column_stack([2 * pts[:, 0], 2 * pts[:, 1], 2 * pts[:, 2],
                         np.ones(len(pts))])
    b = np.sum(pts * pts, axis=1)
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except Exception:
        return None
    a, bb, cc, alpha = sol
    R2 = alpha + a * a + bb * bb + cc * cc
    if R2 <= 0:
        return None
    center = np.array([a, bb, cc])
    R = float(np.sqrt(R2))
    rad = np.linalg.norm(pts - center, axis=1)
    rms = float(np.sqrt(np.mean((rad - R) ** 2)))
    if R > 1e12:
        return None
    return {"type": "sphere", "center": center, "R": R, "rms": rms}


def fit_best(tri_faces, verts, diag, tol_frac=0.002):
    """拟合四种曲面，返回残差最小的合格模型 dict；全不合格返回 None。

    tri_faces: 区域的三片面索引数组 (m,3)；verts: 顶点。
    """
    pts = verts[tri_faces.reshape(-1)]
    models = [fit_plane(pts), fit_cylinder(tri_faces, verts),
              fit_cone(tri_faces, verts), fit_sphere(pts)]
    models = [m for m in models if m is not None]
    if not models:
        return None
    tol = diag * tol_frac
    candidates = [m for m in models if m["rms"] < tol]
    if not candidates:
        return None
    return min(candidates, key=lambda m: m["rms"])


# ------------------------------------------------------------------ #
# 区域分割


def segment_regions(mesh, tol_frac=0.003, dihedral_deg=25.0,
                    merge_angle_deg=6.0):
    """把网格三角面划分为若干解析曲面区域（法向连续性分组 + 全局重拟合）。

    策略：
    1) 区域增长按“相邻面法向夹角 < dihedral_deg”（光滑连续性）归类，天然把
       平面、圆柱壁、球面等光滑表面各自聚成整体，避免窄条平面误分叉。
    2) 对每个连通组用四种解析曲面全局拟合，选残差最小且达标者。
    3) 合并法向后近似共面的相邻组，减少碎裂。

    返回 (region_id 数组, region_models 列表)。
    """
    faces = np.asarray(mesh.faces, dtype=int)
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    nf = len(faces)
    if nf == 0:
        return np.full(0, -1, int), []
    bbox = mesh.bounds
    diag = float(np.linalg.norm(bbox[1] - bbox[0])) or 1.0

    normals = _face_normals(faces, verts)
    cos_th = np.cos(np.radians(dihedral_deg))

    # 邻接表
    from collections import defaultdict
    edge_faces = defaultdict(list)
    for i, f in enumerate(faces):
        for k in range(3):
            a, b = f[k], f[(k + 1) % 3]
            if a > b:
                a, b = b, a
            edge_faces[(int(a), int(b))].append(i)
    adj = [set() for _ in range(nf)]
    for lst in edge_faces.values():
        if len(lst) == 2:
            adj[lst[0]].add(lst[1])
            adj[lst[1]].add(lst[0])

    # 1) 法向连续性分组
    group_id = np.full(nf, -1, int)
    gid = 0
    for seed in range(nf):
        if group_id[seed] != -1:
            continue
        stack = [seed]
        group_id[seed] = gid
        while stack:
            cur = stack.pop()
            for nb in adj[cur]:
                if group_id[nb] != -1:
                    continue
                if float(np.clip(float(normals[cur] @ normals[nb]), -1, 1)) >= cos_th:
                    group_id[nb] = gid
                    stack.append(nb)
        gid += 1

    # 2) 每个组全局拟合
    group_faces = defaultdict(list)
    for i, g in enumerate(group_id):
        group_faces[g].append(i)
    region_id = np.full(nf, -1, int)
    region_models = []
    for g, flist in group_faces.items():
        fa = faces[np.asarray(flist)]
        model = fit_best(fa, verts, diag, tol_frac)
        if model is None:
            continue
        rid = len(region_models)
        for fi in flist:
            region_id[fi] = rid
        region_models.append(model)

    # 区域 -> 面索引
    from collections import defaultdict as _dd
    reg_faces = _dd(list)
    for i, rid in enumerate(region_id):
        if rid >= 0:
            reg_faces[rid].append(i)

    # 3) 合并共面且同向的相邻平面组。
    # 用三角片均值法向（朝外，无 PCA 符号歧义）判同向，再判共面（同平面）。
    if len(region_models) > 1:
        diag_tol = diag * 0.01
        region_out = {}
        region_cent = {}
        for rid, m in enumerate(region_models):
            if m["type"] == "plane":
                fidx = reg_faces.get(rid, [])
                if fidx:
                    on = normals[np.asarray(fidx)].mean(axis=0)
                    region_out[rid] = on
                    region_cent[rid] = verts[faces[np.asarray(fidx)].reshape(-1)].mean(axis=0)
        cm = np.cos(np.radians(merge_angle_deg))
        merged = list(range(len(region_models)))
        # 由三角片分配的区域面索引
        tri_region = np.full(nf, -1, int)
        for i, rid in enumerate(region_id):
            if rid >= 0:
                tri_region[i] = rid
        for i in range(len(region_models)):
            for j in range(i + 1, len(region_models)):
                if merged[i] == merged[j]:
                    continue
                ni = region_out.get(i)
                nj = region_out.get(j)
                if ni is None or nj is None:
                    continue
                if float(np.dot(ni, nj)) < cm:
                    continue
                # 共面带(同一平面)：中心沿法向投影差近似为 0
                cdir = region_cent[j] - region_cent[i]
                proj = float(np.dot(ni, cdir))
                if abs(proj) > diag_tol:
                    continue
                # 合并 j -> i
                old = j
                new = i
                for k in range(len(merged)):
                    if merged[k] == old:
                        merged[k] = new
        # 重建 region_id / region_models
        new_region_id = np.full(nf, -1, int)
        new_models = []
        remap = {}
        for i, rid in enumerate(region_id):
            if rid < 0:
                continue
            target = merged[rid]
            if target not in remap:
                remap[target] = len(new_models)
                new_models.append(region_models[target])
            new_region_id[i] = remap[target]
        region_id = new_region_id
        region_models = new_models

    return region_id, region_models


# ------------------------------------------------------------------ #
# 边界环提取


def region_boundary_loops(faces, region_faces):
    """给定 region 的面索引集合，提取闭合边界环（每个环为顶点索引列表）。

    区域边界是闭流形（每个边界顶点度为 2），沿边界边贪心追踪即可得闭环。
    返回 loops 列表（顶点索引，首尾不重复）。
    """
    from collections import defaultdict
    fs = [int(x) for x in region_faces]
    if not fs:
        return []
    fc = faces[fs]
    edge_count = defaultdict(int)
    for tri in fc:
        for k in range(3):
            a, b = int(tri[k]), int(tri[(k + 1) % 3])
            key = (a, b) if a < b else (b, a)
            edge_count[key] += 1
    # 边界有向边（保留三角面内的方向）
    nudir = defaultdict(list)  # 顶点 -> 邻接边界邻点
    for tri in fc:
        for k in range(3):
            a, b = int(tri[k]), int(tri[(k + 1) % 3])
            key = (a, b) if a < b else (b, a)
            if edge_count[key] == 1:
                nudir[a].append(b)
                nudir[b].append(a)
    used = set()
    loops = []
    started = set()
    for a in list(nudir.keys()):
        for b in nudir[a]:
            key = (min(a, b), max(a, b))
            if key in used:
                continue
            # 从有向边 a->b 起步追踪闭环
            loop = [a]
            cur, nxt = a, b
            while True:
                used.add((min(cur, nxt), max(cur, nxt)))
                if nxt == a:
                    break
                candidates = [c for c in nudir[nxt]
                              if (min(nxt, c), max(nxt, c)) not in used]
                if not candidates:
                    break
                loop.append(nxt)
                cur, nxt = nxt, candidates[0]
            if len(loop) >= 3:
                loops.append(loop)
    return loops


# ------------------------------------------------------------------ #
# OCP analytic face 构建


def analytic_solid_from_regions(mesh, region_id, region_models, tol=1e-5,
                                progress_cb=None):
    """解析曲面重建（缝合）。返回 BuildResult 兼容对象。

    方案 A（保守混合）：可靠的解析平面区 → 单一平面 face；其余三角面 →
    逐三角平面 face，全部加入同一 sewing，保证边界为共享网格边而能闭合。
    非平面区（圆柱/锥/球）暂不合并成大曲面（保留逐三角），保证必然合法。
    """
    from OCP.gp import gp_Pnt, gp_Dir, gp_Pln
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire,
        BRepBuilderAPI_MakeFace, BRepBuilderAPI_Sewing,
    )
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Solid
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.BRepCheck import BRepCheck_Analyzer

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=int)

    class R:
        shapes = []
        solid_count = 0
        shell_count = 0
        invalid_count = 0
        message = ""
        region_count = 0
        analytic_faces = 0

    result = R()
    result.region_count = len(region_models)

    from collections import defaultdict
    reg_faces = defaultdict(list)
    for i, rid in enumerate(region_id):
        if rid >= 0:
            reg_faces[int(rid)].append(i)

    region_normals = {}
    tri_normals = _face_normals(faces, verts)
    for rid, model in enumerate(region_models):
        if model is not None:
            region_normals[rid] = tri_normals[reg_faces.get(rid, [])].mean(axis=0)

    sewing = BRepBuilderAPI_Sewing(tol)
    covered = set()
    done_faces = 0
    merged_faces = 0

    # 1) 解析平面面
    for rid, model in enumerate(region_models):
        if model is None or model["type"] != "plane":
            continue
        fidx = reg_faces.get(rid, [])
        if len(fidx) < 1:
            continue
        loops = region_boundary_loops(faces, fidx)
        if not loops:
            continue
        ordered = sorted(loops, key=lambda lp: _loop_area(verts, lp), reverse=True)
        outer, inners = ordered[0], ordered[1:]
        outward = region_normals.get(rid)
        face = _make_analytic_face(model, verts, outer, inners, outward)
        if (face is not None and not face.IsNull()
                and BRepCheck_Analyzer(face).IsValid()):
            sewing.Add(face)
            done_faces += 1
            merged_faces += len(fidx)
            covered.update(fidx)
        if progress_cb is not None and (rid % 25 == 0):
            progress_cb(rid, len(region_models))

    # 2) 其余三角面逐面缝合（平面 face per triangle）
    residual = [i for i in range(len(faces)) if i not in covered]
    for i in residual:
        f = faces[i]
        mw = BRepBuilderAPI_MakeWire()
        for k in range(3):
            a, b = f[k], f[(k + 1) % 3]
            e = BRepBuilderAPI_MakeEdge(gp_Pnt(*verts[a]), gp_Pnt(*verts[b]))
            if e.IsDone():
                mw.Add(e.Edge())
        w = mw.Wire()
        if w.IsNull():
            continue
        fac = BRepBuilderAPI_MakeFace(w)
        ff = fac.Face()
        if not ff.IsNull():
            sewing.Add(ff)
            done_faces += 1
        if progress_cb is not None and (len(residual) > 0 and i % 2000 == 0):
            progress_cb(i, len(residual))

    if done_faces == 0:
        result.message = "解析曲面重建未产生任何面。"
        return result

    sewing.Perform()
    shape = sewing.SewedShape()
    if shape.IsNull():
        result.message = "缝合结果为空。"
        return result

    builder = BRep_Builder()
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        sh = exp.Current()
        if sh.Closed():
            solid = TopoDS_Solid()
            builder.MakeSolid(solid)
            builder.Add(solid, sh)
            result.shapes.append((solid, True))
            result.solid_count += 1
        else:
            result.shapes.append((sh, False))
            result.shell_count += 1
        exp.Next()

    for s, _ in result.shapes:
        if not BRepCheck_Analyzer(s).IsValid():
            result.invalid_count += 1

    result.analytic_faces = merged_faces
    result.message = "解析曲面重建完成：{} 个实体；合并平面 {} 面，其余 {} 面逐三角。".format(
        result.solid_count, merged_faces, done_faces - merged_faces)
    return result


def _loop_area(verts, loop):
    """环投影到平均平面估算的有向面积（正则化，用于内外环排序）。"""
    if len(loop) < 3:
        return 0.0
    pts = verts[np.asarray(loop)]
    cen = pts.mean(axis=0)
    r = pts - cen
    w, v, _ = _cov_eig(r)
    normal = v[:, 0]
    u = v[:, 1]
    vv = np.cross(normal, u)
    u2 = np.dot(r, u)
    v2 = np.dot(r, vv)
    area = 0.5 * np.sum(u2[:-1] * v2[1:] - u2[1:] * v2[:-1])
    return float(area)


def _make_analytic_face(model, verts, outer, inners, outward=None):
    from OCP.gp import gp_Pnt, gp_Dir, gp_Pln, gp_Ax3, gp_Cylinder, gp_Cone, gp_Sphere
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace,
    )

    def wire_from_loop(loop):
        mw = BRepBuilderAPI_MakeWire()
        pts = verts[np.asarray(loop)]
        for i in range(len(pts)):
            p1 = gp_Pnt(*pts[i])
            p2 = gp_Pnt(*pts[(i + 1) % len(pts)])
            e = BRepBuilderAPI_MakeEdge(p1, p2)
            if not e.IsDone():
                continue
            mw.Add(e.Edge())
        return mw.Wire()

    typ = model["type"]
    outer_wire = wire_from_loop(outer)
    if outer_wire.IsNull():
        return None

    # 期望的外向法向（网格三角片朝外的均值）
    want = None
    if outward is not None and np.linalg.norm(outward) > 1e-12:
        want = np.asarray(outward, dtype=np.float64)
        want = want / np.linalg.norm(want)

    if typ == "plane":
        nrm = model["normal"]
        nrm = nrm / np.linalg.norm(nrm)
        # 以三角片均值法向（朝外）为准，PCA 签名任意，仅在无参考时用
        if want is not None:
            nrm = want
        cen = model["centroid"]
        face_maker = BRepBuilderAPI_MakeFace(
            gp_Pln(gp_Pnt(*cen), gp_Dir(*nrm)), outer_wire, True)
    elif typ == "cylinder":
        ax_pt = model["axis_point"]
        ax = model["axis"]
        surf = gp_Cylinder(gp_Ax3(gp_Pnt(*ax_pt), gp_Dir(*ax)), model["R"])
        try:
            face_maker = BRepBuilderAPI_MakeFace(surf, outer_wire, True)
        except Exception:
            return None
    elif typ == "cone":
        ax = model["axis"]
        apex = model["apex"]
        surf = gp_Cone(gp_Ax3(gp_Pnt(*apex), gp_Dir(*ax)), model["half_angle"], 0.0)
        try:
            face_maker = BRepBuilderAPI_MakeFace(surf, outer_wire, True)
        except Exception:
            return None
    elif typ == "sphere":
        center = model["center"]
        surf = gp_Sphere(gp_Ax3(gp_Pnt(*center), gp_Dir(0, 0, 1)), model["R"])
        try:
            face_maker = BRepBuilderAPI_MakeFace(surf, outer_wire, True)
        except Exception:
            return None
    else:
        return None

    try:
        f = face_maker.Face()
    except Exception:
        return None
    if f.IsNull():
        return None
    # 内环（孔）：把内部边界 wire 加入该面，形成带孔 face
    if inners:
        from OCP.BRep import BRep_Builder
        builder = BRep_Builder()
        for inner in inners:
            iw = wire_from_loop(inner)
            if not iw.IsNull():
                builder.Add(f, iw)
    # 校验实际几何法向，与期望外向法向相反则反向（保证实体面朝向一致）
    if want is not None:
        try:
            from OCP.BRepLProp import BRepLProp_SLProps
            from OCP.BRepTools import BRepTools_UVBounds
            props = BRepLProp_SLProps()
            surf = BRepTools_Surface(f)
            props.SetSurface(surf)
            umin, umax, vmin, vmax = BRepTools_UVBounds().Bounds(f, surf)
            props.SetParameters(surf, 0.5 * (umin + umax), 0.5 * (vmin + vmax), 1, 1e-7)
            nx, ny, nz = props.Normal().X(), props.Normal().Y(), props.Normal().Z()
            actual = np.array([nx, ny, nz], dtype=np.float64)
            if np.linalg.norm(actual) > 1e-12 and np.dot(actual, want) < 0:
                f = f.Reversed()
        except Exception:
            pass
    return f


# ------------------------------------------------------------------ #
# 顶层入口


def convert_analytic(mesh, tolerance=1e-5, tol_frac=0.002, progress_cb=None):
    """对闭合网格做解析曲面重建，返回与 solid_builder.BuildResult 兼容的对象。"""
    if mesh is None or len(mesh.faces) == 0:
        from .solid_builder import BuildResult
        r = BuildResult()
        r.message = "网格为空。"
        return r
    region_id, region_models = segment_regions(mesh, tol_frac)
    return analytic_solid_from_regions(mesh, region_id, region_models,
                                       tol=tolerance, progress_cb=progress_cb)