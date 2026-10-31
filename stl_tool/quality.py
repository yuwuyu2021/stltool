# -*- coding: utf-8 -*-
"""几何保真度体检：体积误差之外的第二把尺。

体积误差只能说明"料够不够"，说明不了"料在哪"——把一个斜面切成台阶，体积
也能对得很准。这里用双向最近点距离衡量重建实体与原网格的表面贴合度，并按
包围盒对角线归一化成百分比，跨零件可比。

采样的意义是控制成本：单件 1000~1500 点、双向查询，秒级内完成。
"""

import numpy as np
import trimesh

# 判定"保真度偏低"的经验阈值（相对包围盒对角线，%）。
# 已发布件实测 P95 ≤1.35%、max ≤5.56%，故取略宽于实测值的 1.5% / 6.0%。
# 两处用途：
#   1. 结果提示——超过阈值只在 prep_notes 里明说，不改变采纳结论；
#   2. 原语/回转体拟合的验收闸门（parametric._fidelity_ok）——体积对、
#      位置错或形状不符的实体在这里被拒，避免吐出整体错位的 STEP。
WARN_P95_PCT = 1.5
WARN_MAX_PCT = 6.0

SAMPLE_COUNT = 1500
SAMPLE_COUNT_HEAVY = 800
HEAVY_FACES = 60000


def _sample_points(mesh, count, seed):
    try:
        pts, _ = trimesh.sample.sample_surface(mesh, count, seed=seed)
        return np.asarray(pts, dtype=np.float64)
    except TypeError:  # 老版本 trimesh 无 seed 参数
        pts, _ = trimesh.sample.sample_surface(mesh, count)
        return np.asarray(pts, dtype=np.float64)


def surface_deviation(src_mesh, shape, samples=SAMPLE_COUNT,
                      linear_deflection=0.35, angular_deflection=0.4):
    """计算重建实体与原网格的双向表面偏差。

    返回 dict：p50/p95/max 为绝对距离，*_pct 为相对包围盒对角线的百分比，
    另含 diag（对角线长度）、faces（实体离散后面数）、samples（采样点数）。
    任何一步失败返回 None，调用方按"未体检"处理。
    """
    from .pipeline import shape_to_mesh

    if shape is None or shape.IsNull():
        return None
    if src_mesh is None or len(getattr(src_mesh, "faces", [])) == 0:
        return None
    diag = float(np.linalg.norm(src_mesh.bounds[1] - src_mesh.bounds[0]))
    if not np.isfinite(diag) or diag <= 0:
        return None

    shape_mesh = shape_to_mesh(shape, linear_deflection=linear_deflection,
                               angular_deflection=angular_deflection)
    if shape_mesh is None or len(shape_mesh.faces) == 0:
        return None

    n = SAMPLE_COUNT_HEAVY if len(shape_mesh.faces) > HEAVY_FACES else samples
    n = max(200, int(n))
    a = _sample_points(src_mesh, n, seed=0)
    b = _sample_points(shape_mesh, n, seed=0)
    if len(a) == 0 or len(b) == 0:
        return None

    _, d1, _ = trimesh.proximity.closest_point(shape_mesh, a)
    _, d2, _ = trimesh.proximity.closest_point(src_mesh, b)
    d = np.concatenate([np.asarray(d1, dtype=np.float64),
                        np.asarray(d2, dtype=np.float64)])
    d = d[np.isfinite(d)]
    if d.size == 0:
        return None

    p50 = float(np.percentile(d, 50))
    p95 = float(np.percentile(d, 95))
    mx = float(d.max())
    return {
        "samples": int(n),
        "faces": int(len(shape_mesh.faces)),
        "diag": float(diag),
        "p50": p50,
        "p95": p95,
        "max": mx,
        "p50_pct": 100.0 * p50 / diag,
        "p95_pct": 100.0 * p95 / diag,
        "max_pct": 100.0 * mx / diag,
    }


def low_fidelity(fid):
    """按经验阈值判断保真度是否偏低。

    两个用途：结果提示（不改采纳结论）与原语/回转体拟合的验收闸门。
    """
    if not fid:
        return False
    return (fid.get("p95_pct", 0.0) > WARN_P95_PCT
            or fid.get("max_pct", 0.0) > WARN_MAX_PCT)


def format_fidelity(fid, with_p50=False):
    """把体检结果格式化成一行中文，供日志/界面使用。"""
    if not fid:
        return ""
    if with_p50:
        return "表面偏差 P50 {:.2f}% / P95 {:.2f}% / max {:.2f}%（相对包围盒对角线，{} 点采样）".format(
            fid["p50_pct"], fid["p95_pct"], fid["max_pct"], fid["samples"])
    return "表面偏差 P95 {:.2f}% / max {:.2f}%（相对包围盒对角线，{} 点采样）".format(
        fid["p95_pct"], fid["max_pct"], fid["samples"])
