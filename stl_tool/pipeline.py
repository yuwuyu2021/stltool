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

    def as_dict(self):
        return {
            "缝合容差": self.tolerance,
            "自动补孔": self.fill_holes,
            "修复法向": self.fix_normals,
            "清理退化面": self.remove_degenerate,
        }


def analyze(mesh):
    return MeshAnalysis(mesh)


def prepare_mesh(mesh, options=None):
    options = options or ConvertOptions()
    notes = []
    m = mesh

    if options.remove_degenerate:
        before = len(m.faces)
        if hasattr(m, "faces"):
            m = remove_degenerate(m)
        if len(m.faces) < before:
            notes.append("已清理 {} 个退化/重复面。".format(before - len(m.faces)))

    if options.fix_normals and m.faces.shape[0] > 1:
        try:
            if not m.is_winding_consistent:
                trimesh.repair.fix_normals(m)
                notes.append("已修复法向朝向不一致的面。")
        except Exception:
            pass

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