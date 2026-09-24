"""STL 源网格编辑：旋转 / 镜像 / 共面合并（减少面片数）。"""

import numpy as np
import trimesh


def _normalized(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else np.zeros(3)


def _axis_idx(axis):
    if isinstance(axis, str):
        return "xyz".index(axis.lower())
    return int(axis)


def orient_outward(mesh, eps=None, max_iter=8):
    """把闭合网格的每个面法向修正为朝外（远离实体内部）。

    适用：源 STL 含局部法向翻转（如底部一小簇面朝内，导致 CAD 导入时
    该处显示为空/暗面）。算法：对每个面，沿其法向微移 / 反方向微移各一个
    eps，用射线点包含测试判断哪边在实体内，从而判定面是否朝内并翻转，
    迭代直至所有面朝外。返回法向全部朝外的网格；无法判断时原样返回。
    """
    if mesh is None or not mesh.is_watertight or len(mesh.faces) == 0:
        return mesh
    m = mesh.copy()
    try:
        m.fix_normals()
    except Exception:
        pass
    verts = np.asarray(m.vertices, dtype=float)
    faces = np.asarray(m.faces, dtype=int)
    diag = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))) or 1.0
    if eps is None:
        eps = diag * 1e-4
    changed = False
    for _ in range(max_iter):
        vorig = np.asarray(m.vertices, dtype=float)
        forig = np.asarray(m.faces, dtype=int)
        # 每面法向（未归一化）与面心
        t0 = vorig[forig[:, 0]]
        fn = np.cross(vorig[forig[:, 1]] - t0, vorig[forig[:, 2]] - t0)
        nlen = np.linalg.norm(fn, axis=1, keepdims=True)
        nlen[nlen == 0] = 1.0
        nn = fn / nlen
        cents = t0 + (vorig[forig[:, 1]] - t0 + vorig[forig[:, 2]] - t0) / 3.0
        try:
            o = m.contains(cents + eps * nn)
            i = m.contains(cents - eps * nn)
        except Exception:
            break
        inward = np.asarray(o, dtype=bool) & (~np.asarray(i, dtype=bool))
        if inward.sum() == 0:
            break
        m = trimesh.Trimesh(vertices=vorig, faces=forig, process=False)
        f2 = m.faces.copy()
        f2[inward] = f2[inward][:, ::-1]
        m = trimesh.Trimesh(vertices=vorig, faces=f2, process=True)
        changed = True
    if changed:
        m._cache.clear()
    return m


def rotate(mesh, axis, angle_deg):
    """绕指定轴旋转（编辑源网格），返回新网格。axis: 0=X, 1=Y, 2=Z 或 'x'/'y'/'z'。"""
    from trimesh.transformations import rotation_matrix

    axis = _axis_idx(axis)
    result = mesh.copy()
    axis_vec = np.zeros(3)
    axis_vec[axis] = 1.0
    mat = rotation_matrix(np.radians(angle_deg), axis_vec)
    result.apply_transform(mat)
    return result


def mirror(mesh, axis):
    """沿指定轴平面镜像（编辑源网格），返回新网格。axis: 0=X, 1=Y, 2=Z 或 'x'/'y'/'z'。"""
    axis = _axis_idx(axis)
    result = mesh.copy()
    new_verts = np.array(result.vertices, dtype=float, copy=True)
    new_verts[:, axis] = -new_verts[:, axis]
    result.vertices = new_verts
    result._cache.clear()
    return result


def _face_adjacent(faces):
    """返回 (edge_key) -> [face indices] 的邻接字典，edge_key 为排序后的边。"""
    edges = np.sort(faces, axis=1)
    from collections import defaultdict
    edge_to_faces = defaultdict(list)
    for idx, (a, b, c) in enumerate(edges):
        edge_to_faces[(a, b)].append(idx)  # 只用于判定共边，不区分方向
    # 需分三条边分别登记，上面只登记了 (a,b)，改为完整
    edge_to_faces = defaultdict(list)
    for idx, tri in enumerate(faces):
        edge_to_faces[tuple(sorted((tri[0], tri[1])))].append(idx)
        edge_to_faces[tuple(sorted((tri[1], tri[2])))].append(idx)
        edge_to_faces[tuple(sorted((tri[2], tri[0])))].append(idx)
    return edge_to_faces


def merge_coplanar(mesh, angle_tol_deg=2.0, restrict=None):
    """把共面的相邻三角面合并成大面，返回面数降低的新网格。

    原理：对每个由“共边且近似共面”的三角面组成的连通区域，用其平面边界
    重新做最小三角化，用少量大三角替代大量共面小三角。
    非平面区域 / 复杂带孔区域保持不变（仍正确，仅局部不精简）。
    restrict：可选的面索引集合；仅在该集合内合并（用于“合并选中面”）。
    """
    from shapely.geometry import Polygon as ShPolygon
    from trimesh.creation import triangulate_polygon

    tri = mesh.copy()
    verts = np.asarray(tri.vertices, dtype=float)
    faces = np.asarray(tri.faces, dtype=int)
    n = len(faces)
    if n == 0:
        return tri

    norms = np.asarray(tri.face_normals, dtype=float)
    norms = norms / np.linalg.norm(norms, axis=1, keepdims=True)
    cents = np.asarray(tri.triangles_center, dtype=float)
    offsets = np.einsum("ij,ij->i", norms, cents)

    diag = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
    off_tol = max(1e-6, diag * 1e-4)
    cos_tol = max(-1.0, np.cos(np.radians(angle_tol_deg)))

    edge_to_faces = _face_adjacent(faces)

    # union-find 按“共边且共面”合并
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for (a, b), flist in edge_to_faces.items():
        for i in range(len(flist) - 1):
            use = flist[i]
            if restrict is not None and use not in restrict:
                continue
            seen = set()
            for j in range(i + 1, len(flist)):
                f = flist[j]
                if f in seen:
                    continue
                seen.add(f)
                if restrict is not None and f not in restrict:
                    continue
                if np.dot(norms[use], norms[f]) > cos_tol and abs(offsets[use] - offsets[f]) < off_tol:
                    union(use, f)

    # 按根分组
    from collections import defaultdict
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    new_verts = list(verts)
    new_faces = []
    replaced = 0

    for root, members in groups.items():
        if len(members) == 1:
            new_faces.append(faces[members[0]])
            continue
        # 该平面区域的唯一顶点
        region_faces = faces[members]
        vset = set(region_faces.reshape(-1).tolist())
        # 平均平面
        cur_norm = norms[members].mean(axis=0)
        cur_norm = _normalized(cur_norm)
        cur_off = offsets[members].mean()
        # 严格共面门控：所有区域顶点到平均平面的距离低于阈值才合并，
        # 避免对近似平面/曲面区域投影造成边界错位破坏闭合性
        region_verts = np.array([verts[k] for k in vset], dtype=float)
        dev = np.abs(np.einsum("ij,j->i", region_verts, cur_norm) - cur_off)
        planar_tol = max(1e-6, diag * 1e-5)
        if dev.max() > planar_tol:
            new_faces.extend(region_faces)
            continue
        # 区域内部使用的边集（只用于判定边界：出现次数为1的边）
        region_edges = defaultdict(int)
        for tri_i in region_faces:
            region_edges[tuple(sorted((tri_i[0], tri_i[1])))] += 1
            region_edges[tuple(sorted((tri_i[1], tri_i[2])))] += 1
            region_edges[tuple(sorted((tri_i[2], tri_i[0])))] += 1
        boundary_edges = [e for e, c in region_edges.items() if c == 1]
        if len(boundary_edges) < 3:
            new_faces.extend(region_faces)
            continue

        # 起点：选一个在边界顶点中 x 最大的，用于稳定的环排序
        bverts = set()
        for e in boundary_edges:
            bverts.add(e[0]); bverts.add(e[1])
        if not bverts:
            new_faces.extend(region_faces)
            continue

        # 建立邻接（每个边界顶点连到下一顶点），构造有向环
        adj = defaultdict(list)
        for e in boundary_edges:
            adj[e[0]].append(e[1])
            adj[e[1]].append(e[0])
        # 用图遍历得到环（每个边界顶点在流形区域边界上度数为2）
        ordered = []
        start = next(iter(bverts))
        cur = start
        prev = None
        for _ in range(len(bverts) * 4 + 4):
            ordered.append(cur)
            nxt = None
            for nb in adj[cur]:
                if nb != prev:
                    nxt = nb
                    break
            if nxt is None or nxt == start:
                break
            prev, cur = cur, nxt
        # 完整闭合的环：遍历到了所有边界顶点，且末点与起点相邻
        ring_ok = len(ordered) == len(bverts) and (len(ordered) >= 3) and (start in adj.get(ordered[-1], []))
        if not ring_ok:
            new_faces.extend(region_faces)
            continue
        ring = ordered
        if len(ring) < 3:
            new_faces.extend(region_faces)
            continue

        # 投影到 2D 平面坐标
        u = _normalized(np.cross(cur_norm, [0, 0, 1]) if abs(cur_norm[2]) < 0.99 else np.cross(cur_norm, [1, 0, 0]))
        v_axis = _normalized(np.cross(cur_norm, u))
        pt3d = np.array([verts[k] for k in ring])
        center3 = pt3d.mean(axis=0)
        p2 = np.column_stack((np.dot(pt3d - center3, u), np.dot(pt3d - center3, v_axis)))

        try:
            poly = ShPolygon([(float(x), float(y)) for x, y in p2])
            if poly.is_empty or not poly.is_valid or poly.area <= 1e-12:
                new_faces.extend(region_faces)
                continue
            pverts, ptris = triangulate_polygon(poly)
        except Exception:
            new_faces.extend(region_faces)
            continue

        pverts = np.asarray(pverts, dtype=float)
        ptris = np.asarray(ptris, dtype=int)
        if len(ptris) == 0:
            new_faces.extend(region_faces)
            continue

# 把 2D 顶点映射回 3D：环上顶点保留原始 3D 坐标（严格共面，保证边界精确），
        # 内部新增顶点在平面上用原边界顶点插值
        pid_to_new = {}
        for i, (x, y) in enumerate(pverts):
            src3 = center3 + u * float(x) + v_axis * float(y)
            # 用 2D 坐标匹配原始环点
            orig3 = None
            for k in ring:
                if np.isclose(np.dot(verts[k] - center3, u), float(x), atol=planar_tol) and \
                   np.isclose(np.dot(verts[k] - center3, v_axis), float(y), atol=planar_tol):
                    orig3 = verts[k]
                    break
            if orig3 is None:
                pid_to_new[i] = len(new_verts)
                new_verts.append(src3)
            else:
                # 复用原始顶点，避免新增重复点导致闭合性/水密性破坏
                pid_to_new[i] = int(k)
        for t_ in ptris:
            new_faces.append([pid_to_new[int(vi)] for vi in t_])
        replaced += 1

    result = trimesh.Trimesh(
        vertices=np.asarray(new_verts, dtype=float),
        faces=np.asarray(new_faces, dtype=int),
        process=True,
    )
    result._cache.clear()
    return result