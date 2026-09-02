import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import trimesh

from stl_tool.pipeline import analyze, convert, make_compound
from stl_tool.step_exporter import write_step


def make_box():
    return trimesh.creation.box((20, 30, 10))


def make_sphere(subdiv=3):
    return trimesh.creation.icosphere(subdivisions=subdiv, radius=5.0)


def make_open_plate():
    mesh = trimesh.creation.box((20, 20, 4))
    tri = mesh.faces
    keep = []
    for i, t in enumerate(tri):
        zs = mesh.vertices[t, 2]
        if np.all(zs > 0.99):
            continue
        keep.append(i)
    mesh = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces[keep])
    return mesh


def test_box_convert():
    mesh = make_box()
    result = convert(mesh)
    assert result.solid_count == 1, result.message
    assert result.invalid_count == 0
    assert result.has_solid()


def test_sphere_convert():
    mesh = make_sphere()
    result = convert(mesh)
    assert result.solid_count == 1, "{} faces".format(len(mesh.faces) if result else "")
    assert result.invalid_count == 0


def test_open_plate_autofill():
    mesh = make_open_plate()
    result = convert(mesh)
    assert result.solid_count >= 1, result.message


def test_open_plate_no_fill_reports_shell():
    from stl_tool.pipeline import ConvertOptions

    mesh = make_open_plate()
    opts = ConvertOptions()
    opts.fill_holes = False
    result = convert(mesh, opts)
    assert result.solid_count == 0
    assert result.shell_count >= 1


def test_step_export():
    mesh = make_box()
    result = convert(mesh)
    shapes = [s for s, v in result.shapes if v]
    shape = make_compound(*shapes)
    tmp = os.path.join(tempfile.gettempdir(), "stltool_test.step")
    ok, msg = write_step(shape, tmp, schema="AP214IS")
    assert ok, msg
    assert os.path.exists(tmp)
    assert os.path.getsize(tmp) > 1000


def test_analysis_fields():
    mesh = make_box()
    a = analyze(mesh)
    assert a.face_count == 12
    assert a.is_watertight
    assert a.volume is not None and a.volume > 0


def test_trigger_with_tiny_degenerate_faces():
    """真实扳机 STL：包含面积约 1e-6 的极小退化面，过去会导致缝合后 BRepCheck invalid
    （CAD 打开内部空/底部缺失）。默认选项（清理退化面+补孔）应产出合法实体。"""
    from OCP.BRepCheck import BRepCheck_Analyzer

    fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "block26_trigger.stl")
    assert os.path.exists(fpath)
    mesh = trimesh.load(fpath, force="mesh")
    assert mesh.is_watertight
    result = convert(mesh)
    assert result.invalid_count == 0, result.message
    assert result.solid_count == 1, result.message
    shape = make_compound(*[s for s, v in result.shapes if v])
    assert BRepCheck_Analyzer(shape).IsValid()


if __name__ == "__main__":
    test_box_convert()
    print("box ok")
    test_sphere_convert()
    print("sphere ok")
    test_open_plate_autofill()
    print("open plate autofill ok")
    test_open_plate_no_fill_reports_shell()
    print("open plate no-fill ok")
    test_step_export()
    print("step ok")
    test_analysis_fields()
    print("analysis ok")
    test_trigger_with_tiny_degenerate_faces()
    print("degenerate-trigger ok")
    print("ALL TESTS PASSED")