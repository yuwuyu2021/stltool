import numpy as np
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_Sewing,
)
from OCP.BRep import BRep_Builder
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopoDS import TopoDS, TopoDS_Shell, TopoDS_Solid
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SHELL
from OCP.gp import gp_Pnt


class BuildResult:
    def __init__(self):
        self.shapes = []
        self.solid_count = 0
        self.shell_count = 0
        self.invalid_count = 0
        self.message = ""

    def has_solid(self):
        return self.solid_count > 0


def build_solid_from_mesh(mesh, tolerance=0.05, progress_cb=None):
    verts = mesh.vertices
    faces = mesh.faces
    result = BuildResult()
    n = len(faces)
    if n == 0:
        result.message = "网格没有三角面。"
        return result

    sewing = BRepBuilderAPI_Sewing(tolerance)
    p = np.empty((3, 3), dtype=np.float64)
    for i in range(n):
        f = faces[i]
        p[0] = verts[f[0]]
        p[1] = verts[f[1]]
        p[2] = verts[f[2]]
        wire = None
        for j in range(3):
            e = BRepBuilderAPI_MakeEdge(gp_Pnt(p[j, 0], p[j, 1], p[j, 2]),
                                        gp_Pnt(p[(j + 1) % 3, 0], p[(j + 1) % 3, 1], p[(j + 1) % 3, 2]))
            if not e.IsDone():
                continue
            if wire is None:
                wire = BRepBuilderAPI_MakeWire()
            wire.Add(e.Edge())
        if wire is None:
            continue
        wire.Build()
        if not wire.IsDone():
            continue
        face = BRepBuilderAPI_MakeFace(wire.Wire())
        if not face.IsDone():
            continue
        sewing.Add(face.Face())
        if progress_cb is not None and (i % 500 == 0):
            progress_cb(i, n)

    if progress_cb is not None:
        progress_cb(n, n)

    sewing.Perform()

    shape = sewing.SewedShape()
    if shape.IsNull():
        result.message = "缝合结果为空。"
        return result

    shells = collect_shells(shape)
    if not shells:
        result.message = "未找到可用的壳。"
        return result

    builder = BRep_Builder()
    for s in shells:
        if s.Closed():
            solid = TopoDS_Solid()
            builder.MakeSolid(solid)
            builder.Add(solid, s)
            result.shapes.append((solid, True))
            result.solid_count += 1
        else:
            result.shapes.append((s, False))
            result.shell_count += 1

    for sh, _ in result.shapes:
        analyzer = BRepCheck_Analyzer(sh)
        if not analyzer.IsValid():
            result.invalid_count += 1

    if result.solid_count == 0 and result.shell_count > 0:
        result.message = "网格存在开放边界，未能形成闭合实体；已生成 {} 个开放壳。".format(result.shell_count)
    else:
        result.message = "构建完成：{} 个实体。".format(result.solid_count)
    return result


def collect_shells(shape):
    shells = []
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        shells.append(TopoDS.Shell_s(exp.Current()))
        exp.Next()
    if not shells:
        shell = TopoDS.Shell_s(shape)
        if not shell.IsNull():
            shells.append(shell)
    return shells