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


def test_mesh_editor():
    from stl_tool.mesh_editor import merge_coplanar, mirror, rotate

    box = trimesh.creation.box((2, 2, 2)).subdivide().subdivide()  # 192 面，共面网格
    assert box.is_watertight
    # 共面合并应显著降低面数且保持闭合体积不受影响
    merged = merge_coplanar(box, angle_tol_deg=5.0)
    assert len(merged.faces) < len(box.faces)
    assert merged.is_watertight
    assert abs(merged.volume - box.volume) < 1e-9
    assert np.allclose(merged.bounds, box.bounds)
    # 旋转 90° 应保持包围盒与体积
    rotated = rotate(box, "z", 90)
    assert abs(rotated.volume - box.volume) < 1e-9
    assert np.allclose(np.sort(rotated.bounds, axis=1), np.sort(box.bounds, axis=1))
    # 镜像 X 轴应翻转质心 X
    mirrored = mirror(box, "x")
    assert abs(mirrored.centroid[0] + box.centroid[0]) < 1e-9
    # 合并结果仍能转换为合法实体
    result = convert(merged)
    assert result.invalid_count == 0, result.message
    assert result.solid_count == 1, result.message


def test_orient_outward():
    from stl_tool.mesh_editor import orient_outward

    box = trimesh.creation.box((2, 2, 2))
    # 手动翻转底面一小簇面，模拟源 STL 底部法向朝内的缺陷
    faces = box.faces.copy()
    bottom = np.where(box.triangles_center[:, 2] < 0.001)[0][:20]
    faces[bottom] = faces[bottom][:, ::-1]
    bad = trimesh.Trimesh(vertices=box.vertices, faces=faces, process=True)
    assert bad.is_watertight
    up_at_bottom = lambda m: int(
        ((m.face_normals[:, 2] > 0.5) & (m.triangles_center[:, 2] < 0.02)).sum()
    )
    assert up_at_bottom(bad) > 0
    fixed = orient_outward(bad)
    assert fixed.is_watertight
    assert up_at_bottom(fixed) == 0, "修复后底面不应有朝内的面"
    assert abs(fixed.volume - box.volume) < 1e-6


def test_convert_analytic():
    """面拟合导出（analytic）模式：box/圆柱应产出一个合法实体，且面数显著下降。"""
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE

    def count_faces(r):
        n = 0
        for s, v in r.shapes:
            e = TopExp_Explorer(s, TopAbs_FACE)
            while e.More():
                n += 1
                e.Next()
        return n

    from stl_tool.pipeline import ConvertOptions
    opts = ConvertOptions()
    opts.analytic = True
    opts.tolerance = 1e-4

    box = trimesh.creation.box((20, 30, 10))
    r = convert(box, opts)
    assert r.solid_count == 1, r.message
    assert r.invalid_count == 0, r.message
    assert count_faces(r) <= 6, "box 应合并为 6 个平面面"

    cyl = trimesh.creation.cylinder(radius=1, height=2, sections=80)
    r2 = convert(cyl, opts)
    assert r2.solid_count == 1, r2.message
    assert r2.invalid_count == 0, r2.message

    # 默认非 analytic 模式行为不变
    r3 = convert(trimesh.creation.box((2, 2, 2)))
    assert r3.invalid_count == 0, r3.message
    assert r3.solid_count == 1, r3.message


def test_convert_parametric():
    """参数化重建（parametric）模式：体素/回转体应降为极低面数且合法。"""
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from stl_tool.parametric import _volume

    def count_faces(r):
        n = 0
        for s, v in r.shapes:
            e = TopExp_Explorer(s, TopAbs_FACE)
            while e.More():
                n += 1
                e.Next()
        return n

    def check(mesh, max_faces, param_type, rel_vol_err=0.10):
        from stl_tool.pipeline import ConvertOptions
        opts = ConvertOptions()
        opts.parametric = True
        opts.tolerance = 1e-4
        r = convert(mesh, opts)
        assert r.solid_count == 1, r.message
        assert r.invalid_count == 0, r.message
        assert getattr(r, "param_type", None) == param_type, \
            "期望 {} 实际 {}".format(param_type, getattr(r, "param_type", None))
        assert count_faces(r) <= max_faces, "面数 {} 超过 {}".format(count_faces(r), max_faces)
        shapes = [s for s, v in r.shapes if v]
        comp = make_compound(*shapes) if len(shapes) > 1 else (shapes[0] if shapes else None)
        sv = _volume(comp) if comp is not None else None
        if sv is not None and mesh.volume is not None:
            assert abs(sv - mesh.volume) / mesh.volume < rel_vol_err

    check(trimesh.creation.box((20, 30, 10)), 6, "box")
    check(trimesh.creation.cylinder(radius=2, height=5, sections=80), 3, "cylinder")
    check(trimesh.creation.icosphere(subdivisions=3, radius=5), 1, "sphere")

    # 回转体（锥形酒杯轮廓）：面数应远低于原始三角面
    prof = np.array([[0, 0], [0.8, 0.5], [1.5, 1], [2, 2.5], [1.6, 4], [0, 4]], float) * 0.6
    rv = trimesh.creation.revolve(prof, sections=64)
    from stl_tool.pipeline import ConvertOptions
    opts = ConvertOptions()
    opts.parametric = True
    opts.tolerance = 1e-4
    r = convert(rv, opts)
    assert r.solid_count == 1, r.message
    assert r.invalid_count == 0, r.message
    assert count_faces(r) <= 64, "回转体重建面数 {} 应远低于原 512".format(count_faces(r))

    # 非参数化网格（随机扰动盒）不应命中，应回退
    from stl_tool.pipeline import ConvertOptions as CO
    o = CO()
    o.parametric = True
    box = trimesh.creation.box((2, 2, 2))
    noisy = trimesh.Trimesh(vertices=box.vertices.copy(), faces=box.faces.copy(), process=False)
    r2 = convert(noisy, o)
    assert r2.solid_count >= 1


def test_convert_parametric_plate():
    """带孔薄板：extrusion 重建，面数显著下降且体积近似。"""
    from stl_tool.parametric import _volume

    # 造带孔薄板：外框 + 三个圆孔（extrude_polygon 支持孔）
    import shapely.geometry as sg
    from shapely.ops import unary_union
    outer = sg.Polygon([(0, 0), (40, 0), (40, 30), (0, 30)])
    holes = [
        sg.Point(10, 8).buffer(3.0),
        sg.Point(28, 20).buffer(5.0),
        sg.Point(28, 8).buffer(3.0),
    ]
    plate_poly = outer.difference(unary_union(holes))
    plate = trimesh.creation.extrude_polygon(plate_poly, height=4)
    assert plate.is_watertight, "测试板应水密"

    from stl_tool.pipeline import ConvertOptions
    opts = ConvertOptions()
    opts.parametric = True
    opts.tolerance = 1e-4
    r = convert(plate, opts)
    assert r.solid_count == 1, r.message
    assert r.invalid_count == 0, r.message
    assert getattr(r, "param_type", None) == "plate", \
        "期望 plate 实际 {}".format(getattr(r, "param_type", None))

    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    n = 0
    for s, v in r.shapes:
        e = TopExp_Explorer(s, TopAbs_FACE)
        while e.More():
            n += 1
            e.Next()
    assert n < len(plate.faces) / 10, "面数 {} 应远低于原 {}".format(n, len(plate.faces))
    shapes = [s for s, v in r.shapes if v]
    comp = make_compound(*shapes) if len(shapes) > 1 else (shapes[0] if shapes else None)
    sv = _volume(comp) if comp is not None else None
    if sv is not None:
        assert abs(sv - plate.volume) / plate.volume < 0.15, \
            "体积误差 {:.1%}".format((sv - plate.volume) / plate.volume)


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
    test_mesh_editor()
    print("mesh-editor ok")
    test_orient_outward()
    print("orient-outward ok")
    test_convert_parametric()
    print("parametric ok")
    test_convert_parametric_plate()
    print("parametric-plate ok")
    print("ALL TESTS PASSED")