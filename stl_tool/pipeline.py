import trimesh
import numpy as np

from .mesh_analyzer import MeshAnalysis
from .solid_builder import build_solid_from_mesh


class ConvertOptions:
    def __init__(self):
        self.tolerance = 0.05
        self.fill_holes = True
        self.fix_normals = True
        self.remove_degenerate = True
        self.analytic = False
        self.parametric = False

    def as_dict(self):
        return {
            "缝合容差": self.tolerance,
            "自动补孔": self.fill_holes,
            "修复法向": self.fix_normals,
            "清理退化面": self.remove_degenerate,
            "面拟合导出": self.analytic,
            "参数化重建": self.parametric,
        }


def analyze(mesh):
    return MeshAnalysis(mesh)


def prepare_mesh(mesh, options=None):
    options = options or ConvertOptions()
    notes = []
    m = mesh

    # 先修正法向朝外（作用于原始闭合网格，避免后续 process 重建破坏水密性）
    if options.fix_normals and m.faces.shape[0] > 1:
        try:
            from .mesh_editor import orient_outward
            m2 = orient_outward(m)
            if m2 is not None and m2.is_watertight and m2.volume is not None:
                m = m2
                notes.append("已修正法向（所有面朝外）。")
        except Exception:
            pass

    if options.remove_degenerate:
        before = len(m.faces)
        if hasattr(m, "faces"):
            m = remove_degenerate(m)
        if len(m.faces) < before:
            notes.append("已清理 {} 个退化/重复面。".format(before - len(m.faces)))

    if options.fill_holes and not m.is_watertight:
        try:
            before_edges = len(MeshAnalysis.boundary_edges_of(m))
            if before_edges > 0:
                trimesh.repair.fill_holes(m)
                after_edges = len(MeshAnalysis.boundary_edges_of(m))
                if after_edges < before_edges:
                    notes.append("已自动修补 {} 条边界边上的孔洞。".format(before_edges - after_edges))
                else:
                    notes.append("补孔未能闭合所有边界。")
        except Exception:
            notes.append("自动补孔失败，将按原始网格处理。")

    return m, notes


def remove_degenerate(mesh):
    tris = mesh.triangles
    if len(tris) == 0:
        return mesh
    area2 = np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)
    scale = float(np.max(mesh.bounds[1] - mesh.bounds[0])) if mesh.bounds is not None else 1.0
    eps = max(1e-12, 1e-9 * scale * scale) * 2.0
    keep = area2 >= eps
    new_faces = mesh.faces[keep]
    m = trimesh.Trimesh(vertices=mesh.vertices, faces=new_faces, process=True)
    return m


def convert(mesh, options=None, progress_cb=None):
    opts = options or ConvertOptions()
    m, notes = prepare_mesh(mesh, opts)
    analysis = MeshAnalysis(m)
    if opts.parametric:
        from .parametric import convert_parametric
        result = convert_parametric(m, tolerance=opts.tolerance,
                                    progress_cb=progress_cb)
        if not result.has_solid():
            # 参数化失败自动回退
            from .analytic import convert_analytic
            result = convert_analytic(m, tolerance=opts.tolerance,
                                      progress_cb=progress_cb)
            result.prep_notes = notes + ["参数化重建未命中，自动回退面拟合/缝合法。"]
        else:
            result.prep_notes = notes
    elif opts.analytic:
        from .analytic import convert_analytic
        result = convert_analytic(m, tolerance=opts.tolerance,
                                  progress_cb=progress_cb)
        result.prep_notes = notes
    else:
        result = build_solid_from_mesh(m, tolerance=opts.tolerance, progress_cb=progress_cb)
        result.prep_notes = notes
    result.analysis = analysis
    return result


def make_compound(*shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    if not shapes:
        return None
    if len(shapes) == 1:
        return shapes[0]
    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)
    return comp


def combine_shapes(result):
    shapes = [s for s, _ in result.shapes]
    return make_compound(*shapes) if shapes else None


def shape_to_mesh(shape, linear_deflection=0.5, angular_deflection=0.5):
    """将 OCC shape 三角离散为 trimesh 网格，供预览渲染。"""
    import trimesh
    from OCP.TopoDS import TopoDS
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    from OCP.Poly import Poly_Triangulation

    if shape.IsNull():
        return None
    mesh = BRepMesh_IncrementalMesh(shape, linear_deflection, False, angular_deflection, True)
    mesh.Perform()
    if not mesh.IsDone():
        return None

    verts = []
    faces = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            n_verts = tri.NbNodes()
            base = len(verts)
            for i in range(1, n_verts + 1):
                p = tri.Node(i)
                verts.append((p.X(), p.Y(), p.Z()))
            for i in range(1, tri.NbTriangles() + 1):
                t = tri.Triangle(i).Get()
                n1, n2, n3 = t[0] - 1, t[1] - 1, t[2] - 1
                faces.append((base + n1, base + n2, base + n3))
        exp.Next()

    if not verts or not faces:
        return None
    m = trimesh.Trimesh(vertices=np.asarray(verts, dtype=np.float64),
                        faces=np.asarray(faces, dtype=np.int64))
    return m