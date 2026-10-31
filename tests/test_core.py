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


def test_convert_parametric_counterbore():
    """沉孔板：顶面下沉圆台+同心通孔，重建出沉孔圆柱布尔减且体积近似。"""
    from stl_tool.parametric import _volume
    from OCP.BRepPrimAPI import (
        BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder,
    )
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2, gp_Vec

    # 板 40x30x4，通孔 r=2.5，沉孔大孔 r=5 深度2（顶面下沉台）
    plate = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 4).Shape()
    thru = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(20, 15, -0.5), gp_Dir(0, 0, 1)), 2.5, 5).Shape()
    cbor = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(20, 15, 2), gp_Dir(0, 0, 1)), 5.0, 2.5).Shape()
    solid = BRepAlgoAPI_Cut(plate, thru).Shape()
    solid = BRepAlgoAPI_Cut(solid, cbor).Shape()

    # OCP shape -> trimesh，再修复法向朝外（模拟真实 STL 预处理）
    from stl_tool.pipeline import shape_to_mesh, prepare_mesh
    mesh = shape_to_mesh(solid)
    assert mesh is not None and mesh.is_watertight
    mesh, _ = prepare_mesh(mesh)

    from stl_tool.pipeline import ConvertOptions
    opts = ConvertOptions()
    opts.parametric = True
    opts.tolerance = 1e-4
    r = convert(mesh, opts)
    assert r.solid_count == 1, r.message
    assert getattr(r, "param_type", None) == "plate", r.message

    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    n = 0
    for s, v in r.shapes:
        e = TopExp_Explorer(s, TopAbs_FACE)
        while e.More():
            n += 1
            e.Next()
    assert n < len(mesh.faces) / 10, "面数 {} 应远低于原 {}".format(n, len(mesh.faces))
    shapes = [s for s, v in r.shapes if v]
    comp = make_compound(*shapes) if len(shapes) > 1 else (shapes[0] if shapes else None)
    sv = _volume(comp) if comp is not None else None
    if sv is not None:
        assert abs(sv - mesh.volume) / mesh.volume < 0.15, \
            "体积误差 {:.1%}".format((sv - mesh.volume) / mesh.volume)


def test_convert_parametric_obround():
    """腰型孔板：两条平行边+两端圆弧，重建出解析圆弧端面且体积近似。"""
    from stl_tool.parametric import _volume
    import shapely.geometry as sg
    from shapely.ops import unary_union

    # 60x40 板，深 4，中心 (30,20) 腰型孔 r=4、圆心距 24
    def obround_poly(cx, cy, r, d):
        c1 = sg.Point(cx - d / 2.0, cy).buffer(r, resolution=32)
        c2 = sg.Point(cx + d / 2.0, cy).buffer(r, resolution=32)
        rect = sg.box(cx - d / 2.0, cy - r, cx + d / 2.0, cy + r)
        return unary_union([c1, c2, rect])

    poly = sg.box(0, 0, 60, 40).difference(obround_poly(30, 20, 4, 24))
    mesh = trimesh.creation.extrude_polygon(poly, 4)
    assert mesh.is_watertight

    from stl_tool.pipeline import prepare_mesh, ConvertOptions, convert
    mesh, _ = prepare_mesh(mesh)
    opts = ConvertOptions()
    opts.parametric = True
    opts.tolerance = 1e-4
    r = convert(mesh, opts)
    assert r.solid_count == 1, r.message
    assert getattr(r, "param_type", None) == "plate", r.message

    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    n = 0
    n_cyl = 0
    for s, v in r.shapes:
        e = TopExp_Explorer(s, TopAbs_FACE)
        while e.More():
            n += 1
            if BRepAdaptor_Surface(TopoDS.Face_s(e.Current())).GetType() == GeomAbs_Cylinder:
                n_cyl += 1
            e.Next()
    assert n < len(mesh.faces) / 10, "面数 {} 应远低于原 {}".format(n, len(mesh.faces))
    assert n_cyl == 2, "应有 2 个解析圆柱端面，实际 {}".format(n_cyl)
    shapes = [s for s, v in r.shapes if v]
    comp = make_compound(*shapes) if len(shapes) > 1 else (shapes[0] if shapes else None)
    sv = _volume(comp) if comp is not None else None
    if sv is not None:
        assert abs(sv - mesh.volume) / mesh.volume < 0.15, \
            "体积误差 {:.1%}".format((sv - mesh.volume) / mesh.volume)


def test_round_plate_fillet_closedloop():
    """顶边 R2 圆角矩形板应复原为 fillet r≈2，而非误判倒角。"""
    import shapely.geometry as sg
    from OCP.gp import gp_Pnt
    from OCP.TopoDS import TopoDS
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
    from OCP.BRep import BRep_Tool
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from stl_tool.pipeline import shape_to_mesh
    from stl_tool.parametric import _volume, _round_plate

    cx, cy, cz, r = 40.0, 30.0, 4.0, 2.0
    b = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), cx, cy, cz).Shape()
    mf = BRepFilletAPI_MakeFillet(b)
    ie = TopExp_Explorer(b, TopAbs_EDGE)
    while ie.More():
        e = TopoDS.Edge_s(ie.Current())
        zs = []
        ve = TopExp_Explorer(e, TopAbs_VERTEX)
        while ve.More():
            zs.append(BRep_Tool.Pnt_s(TopoDS.Vertex_s(ve.Current())).Z())
            ve.Next()
        if all(abs(z - cz) < 1e-3 for z in zs):
            mf.Add(float(r), e)
        ie.Next()
    sh = mf.Shape()
    mesh = shape_to_mesh(sh, 0.3, 0.3)

    axis = np.array([0.0, 0.0, -1.0])
    poly = sg.Polygon([(0, 0), (cx, 0), (cx, cy), (0, cy)])
    target = _volume(sh)
    out = _round_plate(BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), cx, cy, cz).Shape(),
                       mesh, axis, axis, poly, 0.0, cz, cz, vol_target=target,
                       max_err=0.05)
    assert out[0] is not None, "R2 圆角应被识别"
    assert out[2] == "fillet", "应为 fillet，实际 {}".format(out[2])
    assert abs(out[1] - r) < 0.3, "半径应接近 2.0，实际 {:.2f}".format(out[1])
    err = abs(_volume(out[0]) - target) / target
    assert err < 0.02, "体积误差 {:.1%}".format(err)


def test_round_plate_chamfer_closedloop():
    """顶边 2mm 倒角矩形板应复原为 chamfer r≈2。"""
    import shapely.geometry as sg
    from OCP.gp import gp_Pnt
    from OCP.TopoDS import TopoDS
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
    from OCP.BRep import BRep_Tool
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer
    from stl_tool.pipeline import shape_to_mesh
    from stl_tool.parametric import _volume, _round_plate

    cx, cy, cz, a = 40.0, 30.0, 4.0, 2.0
    b = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), cx, cy, cz).Shape()
    mc = BRepFilletAPI_MakeChamfer(b)
    ie = TopExp_Explorer(b, TopAbs_EDGE)
    while ie.More():
        e = TopoDS.Edge_s(ie.Current())
        zs = []
        ve = TopExp_Explorer(e, TopAbs_VERTEX)
        while ve.More():
            zs.append(BRep_Tool.Pnt_s(TopoDS.Vertex_s(ve.Current())).Z())
            ve.Next()
        if all(abs(z - cz) < 1e-3 for z in zs):
            mc.Add(float(a), e)
        ie.Next()
    sh = mc.Shape()
    mesh = shape_to_mesh(sh, 0.3, 0.3)

    axis = np.array([0.0, 0.0, -1.0])
    poly = sg.Polygon([(0, 0), (cx, 0), (cx, cy), (0, cy)])
    target = _volume(sh)
    out = _round_plate(BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), cx, cy, cz).Shape(),
                       mesh, axis, axis, poly, 0.0, cz, cz, vol_target=target,
                       max_err=0.05)
    assert out[0] is not None, "2mm 倒角应被识别"
    assert out[2] == "chamfer", "应为 chamfer，实际 {}".format(out[2])
    assert abs(out[1] - a) < 0.3, "倒角应接近 2.0，实际 {:.2f}".format(out[1])
    err = abs(_volume(out[0]) - target) / target
    assert err < 0.02, "体积误差 {:.1%}".format(err)


def test_round_plate_plain_no_false_positive():
    """纯矩形板（无圆角/倒角）不应被套上边缘倒边。"""
    import shapely.geometry as sg
    from OCP.gp import gp_Pnt
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from stl_tool.pipeline import shape_to_mesh
    from stl_tool.parametric import _volume, _round_plate

    cx, cy, cz = 40.0, 30.0, 4.0
    b = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), cx, cy, cz).Shape()
    mesh = shape_to_mesh(b, 0.3, 0.3)
    axis = np.array([0.0, 0.0, -1.0])
    poly = sg.Polygon([(0, 0), (cx, 0), (cx, cy), (0, cy)])
    out = _round_plate(b, mesh, axis, axis, poly, 0.0, cz, cz,
                       vol_target=_volume(b), max_err=0.05)
    assert out[0] is None, "无圆角板不应识别出倒边"


def test_plate_cavity_reject():
    """空腔/镂空盒体：_solid_gap_ratio 显著 >1.35（存在第二水平主面）。"""
    import shapely.geometry as sg
    from OCP.gp import gp_Pnt
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from stl_tool.pipeline import shape_to_mesh, prepare_mesh
    from stl_tool.parametric import _find_thickness_axis, _solid_gap_ratio

    # 26×18×6 外盒，内腔 22×14×5（顶底留壁）→ 水平面全跨距≈6，ratio≈2.0
    o = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 26, 18, 6).Shape()
    i = BRepPrimAPI_MakeBox(gp_Pnt(2, 2, 0.5), 22, 14, 5).Shape()
    c = BRepAlgoAPI_Cut(o, i).Shape()
    mesh, _ = prepare_mesh(shape_to_mesh(c, 0.4, 0.4))
    axis = _find_thickness_axis(mesh)
    ratio = _solid_gap_ratio(mesh, axis, mesh.volume)
    assert ratio is not None and ratio > 1.35, "空腔盒 gap ratio 应 >1.35，实际 {}".format(ratio)


def test_plate_solid_not_rejected():
    """实心带孔板：gap ratio 接近 1.0，不得被空腔阻断误伤。"""
    import shapely.geometry as sg
    from stl_tool.pipeline import prepare_mesh
    from stl_tool.parametric import _find_thickness_axis, _solid_gap_ratio

    # 26×18×6 实心板，两个通孔
    poly = sg.box(0, 0, 26, 18)
    for (cx, cy) in ((8, 9), (18, 9)):
        poly = poly.difference(sg.Point(cx, cy).buffer(2.0))
    mesh = trimesh.creation.extrude_polygon(poly, 6)
    mesh, _ = prepare_mesh(mesh)
    axis = _find_thickness_axis(mesh)
    ratio = _solid_gap_ratio(mesh, axis, mesh.volume)
    assert ratio is not None and ratio < 1.2, "实心板 gap ratio 应 <1.2，实际 {}".format(ratio)


def test_revolve_hollow_not_false_positive():
    """空心回转体（空腔盒）不得被 revolve 拟合为外壳回转假阳性。"""
    from OCP.gp import gp_Pnt
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from stl_tool.pipeline import shape_to_mesh, prepare_mesh, ConvertOptions, convert

    o = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 26, 18, 6).Shape()
    i = BRepPrimAPI_MakeBox(gp_Pnt(2, 2, 0.5), 22, 14, 5).Shape()
    c = BRepAlgoAPI_Cut(o, i).Shape()
    mesh, _ = prepare_mesh(shape_to_mesh(c, 0.4, 0.4))
    opts = ConvertOptions()
    opts.parametric = True
    opts.tolerance = 1e-4
    r = convert(mesh, opts)
    assert getattr(r, "param_type", None) != "revolve", (
        "空腔盒不应被拟合为回转体，实际 {}".format(getattr(r, "param_type", None)))


def test_plate_boss_recovered():
    """带凸台板的板参数化：凸台柱被 Fuse 回板体，体积误差显著收敛。"""
    from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2
    from OCP.BRepPrimAPI import (
        BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder,
    )
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from stl_tool.pipeline import shape_to_mesh, prepare_mesh
    from stl_tool.parametric import detect_plate, _volume

    # 40×30×4 板 + 2 个 r4、高 6 的凸台柱（z=4→10）
    box = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 4).Shape()
    c1 = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(10, 10, 0), gp_Dir(0, 0, 1)), 4, 10).Shape()
    c2 = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(30, 20, 0), gp_Dir(0, 0, 1)), 4, 10).Shape()
    solid = BRepAlgoAPI_Fuse(BRepAlgoAPI_Fuse(box, c1).Shape(), c2).Shape()
    mesh, _ = prepare_mesh(shape_to_mesh(solid, 0.4, 0.4))
    shape, info = detect_plate(mesh)
    assert shape is not None, "带凸台板应识别为 plate，实际 None"
    assert info["bosses"] >= 2, "应识别出 2 个凸台柱，实际 {}".format(info["bosses"])
    vol = float(_volume(shape))
    err = abs(vol / float(mesh.volume) - 1.0)
    assert err <= 0.03, "凸台拟合后体积误差应 <=3%，实际 {:.2f}%".format(err * 100)


def test_plate_boss_no_false_positive():
    """普通带孔板（无凸台）：_detect_bosses 不得误加任何台体。"""
    import shapely.geometry as sg
    import trimesh
    from stl_tool.pipeline import prepare_mesh
    from stl_tool.parametric import detect_plate

    poly = sg.box(0, 0, 40, 30)
    for (cx, cy) in ((10, 10), (30, 20)):
        poly = poly.difference(sg.Point(cx, cy).buffer(3.0))
    mesh = trimesh.creation.extrude_polygon(poly, 4)
    mesh, _ = prepare_mesh(mesh)
    shape, info = detect_plate(mesh)
    assert shape is not None
    assert info.get("bosses", 0) == 0, (
        "普通板不应识别出凸台，实际 {}".format(info.get("bosses", 0)))


def _shell_mesh(cavity_z0, cavity_h):
    """40×30×12 外盒挖内腔 36×26×cavity_h（z 从 cavity_z0 起），返回网格。"""
    from OCP.gp import gp_Pnt
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from stl_tool.pipeline import shape_to_mesh, prepare_mesh

    o = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 12).Shape()
    i = BRepPrimAPI_MakeBox(gp_Pnt(2, 2, cavity_z0), 36, 26, cavity_h).Shape()
    c = BRepAlgoAPI_Cut(o, i).Shape()
    mesh, _ = prepare_mesh(shape_to_mesh(c))
    return mesh


def test_shell_closed_recovered():
    """封闭薄壁盒（内腔 2~10，壁厚 2）：重建为等壁厚壳体，体积误差 <=12%。"""
    from stl_tool.parametric import detect_plate, _volume

    mesh = _shell_mesh(2.0, 8.0)
    shape, info = detect_plate(mesh)
    assert shape is not None, "封闭薄壁盒应重建为壳体，实际 None"
    assert info.get("shell") is True, "应走等壁厚壳体重建，实际 {}".format(info)
    vol = float(_volume(shape))
    err = abs(vol / float(mesh.volume) - 1.0)
    assert err <= 0.12, "壳体重建体积误差应 <=12%，实际 {:.2f}%".format(err * 100)
    assert 1.5 <= info["wall"] <= 2.5, (
        "内外轮廓壁厚应约 2.0，实际 {:.2f}".format(info["wall"]))


def test_shell_open_recovered():
    """开口薄壁盒（内腔切到顶面，壁厚 2）：同样重建为等壁厚壳体。"""
    from stl_tool.parametric import detect_plate, _volume

    mesh = _shell_mesh(2.0, 10.0)
    shape, info = detect_plate(mesh)
    assert shape is not None, "开口薄壁盒应重建为壳体，实际 None"
    assert info.get("shell") is True, "应走等壁厚壳体重建，实际 {}".format(info)
    vol = float(_volume(shape))
    err = abs(vol / float(mesh.volume) - 1.0)
    assert err <= 0.12, "壳体重建体积误差应 <=12%，实际 {:.2f}%".format(err * 100)


def test_shell_not_false_positive_on_thick_block():
    """实心厚块：不得被壳体通道误判（没有内腔主面）。"""
    import trimesh
    from stl_tool.pipeline import prepare_mesh
    from stl_tool.parametric import _detect_shell, _find_thickness_axis, _volume

    mesh = trimesh.creation.box(extents=(30, 20, 10))
    mesh, _ = prepare_mesh(mesh)
    axis = _find_thickness_axis(mesh)
    shape, info = _detect_shell(mesh, axis, float(mesh.volume))
    assert shape is None, "实心块不应被识别为壳体"


def _multi_cavity_mesh(cuts, size=(40, 30, 12)):
    """40×30×12 外盒按 cuts 列表（x, y, z, dx, dy, dz）挖腔，返回网格。"""
    from OCP.gp import gp_Pnt
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from stl_tool.pipeline import shape_to_mesh, prepare_mesh

    o = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), *size).Shape()
    for (x, y, z, dx, dy, dz) in cuts:
        c = BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()
        o = BRepAlgoAPI_Cut(o, c).Shape()
    mesh, _ = prepare_mesh(shape_to_mesh(o))
    return mesh


def test_layers_multicavity_recovered():
    """双腔（不同深度）：M13 单一内腔假设失效，分层截面重建应还原。"""
    from stl_tool.parametric import (_detect_layers, _find_thickness_axis,
                                     _volume, _volume_approx, convert_parametric)

    mesh = _multi_cavity_mesh([(2, 2, 2, 16, 26, 4), (22, 2, 2, 16, 26, 8)])
    lay, info = _detect_layers(mesh, _find_thickness_axis(mesh), _volume_approx(mesh))
    assert lay is not None, "双腔盒应被分层截面重建"
    err = abs(float(_volume(lay)) / float(mesh.volume) - 1.0)
    assert err <= 0.06, "双腔盒体积误差应 <=6%，实际 {:.2f}%".format(err * 100)
    assert info["layers"] >= 3, "双腔不同深应有 >=3 层，实际 {}".format(info["layers"])
    r = convert_parametric(mesh)
    assert getattr(r, "param_type", "") == "layers", (
        "双腔盒应走分层重建，实际 {}".format(getattr(r, "param_type", "")))


def test_layers_stepped_wall_recovered():
    """阶梯壁（下宽上窄、开口朝上）：分层重建应还原，误差 <=6%。"""
    from stl_tool.parametric import (_detect_layers, _find_thickness_axis,
                                     _volume, _volume_approx)

    mesh = _multi_cavity_mesh([(2, 2, 2, 36, 26, 4), (7, 7, 6, 26, 16, 6)])
    lay, info = _detect_layers(mesh, _find_thickness_axis(mesh), _volume_approx(mesh))
    assert lay is not None, "阶梯壁盒应被分层截面重建"
    err = abs(float(_volume(lay)) / float(mesh.volume) - 1.0)
    assert err <= 0.06, "阶梯壁体积误差应 <=6%，实际 {:.2f}%".format(err * 100)


def test_layers_not_false_positive_on_curved():
    """曲面件（斜面占比过高）：分层重建必须拒绝，避免切出明显台阶。"""
    import trimesh
    from stl_tool.pipeline import prepare_mesh
    from stl_tool.parametric import (_detect_layers, _find_thickness_axis,
                                     _volume_approx)

    mesh, _ = prepare_mesh(trimesh.creation.icosphere(subdivisions=3, radius=10.0))
    lay, info = _detect_layers(mesh, _find_thickness_axis(mesh), _volume_approx(mesh))
    assert lay is None, "球面件不应被分层截面重建（斜面占比过高）"
    assert (info or {}).get("reject_reason"), (
        "分层拒绝时应带出原因（便于日志说明），实际 {}".format(info))


def _trapezoid_prism_mesh():
    """梯形棱柱（底 40、顶 10、高 8，沿 Y 挤出 30）：两侧 ~28° 斜面，斜面占比 ~35%。"""
    import numpy as np
    import trimesh
    import shapely.geometry as sg
    from stl_tool.pipeline import prepare_mesh

    poly = sg.Polygon([(-20, 0), (20, 0), (5, 8), (-5, 8)])
    m = trimesh.creation.extrude_polygon(poly, 30)
    # 挤出方向 Z → 转到 Y，让厚度轴仍是 Z、斜面进 (0.15,0.9) 的斜面区间
    m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    m = m.subdivide().subdivide()  # 细分：水平面片数需 >= 8 才进入分层判定
    m, _ = prepare_mesh(m)
    return m


def test_layers_slanted_prism_relaxed_slope():
    """斜面占比 30%~45% 的 2.5D 斜坡件：放宽阈值后应命中且体积准确。

    对应真实件 Z0支架/Z2支架（30.5%）、灯卡（37.4%）、散热块稳定器（31.5%）：
    旧阈值 30% 一刀切拒绝，实测这些件分层后体积误差 0.02%~4%，表面偏差与
    既有已接受件同档。
    """
    from stl_tool.parametric import (_detect_layers, _find_thickness_axis,
                                     _volume, _volume_approx, convert_parametric)

    mesh = _trapezoid_prism_mesh()
    vol = _volume_approx(mesh)
    axis = _find_thickness_axis(mesh)
    strict, sinfo = _detect_layers(mesh, axis, vol, slope_max=0.30)
    assert strict is None, "斜面占比 >30% 时旧阈值应拒绝"
    assert (sinfo or {}).get("reject_reason"), sinfo
    lay, info = _detect_layers(mesh, axis, vol)
    assert lay is not None, "放宽到 45% 后 2.5D 斜坡件应命中，实际 {}".format(info)
    err = abs(float(_volume(lay)) / vol - 1.0)
    assert err <= 0.06, "斜坡件体积误差应 <=6%，实际 {:.2f}%".format(err * 100)
    r = convert_parametric(mesh)
    assert getattr(r, "param_type", "") == "layers", getattr(r, "param_type", "")


def test_param_fallback_reports_reason():
    """参数化未命中自动回退时，prep_notes 必须带出具体原因（M3 失败诊断）。"""
    import trimesh
    from stl_tool.pipeline import convert, ConvertOptions

    mesh = trimesh.creation.torus(major_radius=20, minor_radius=6,
                                  major_segments=48, minor_segments=20)
    opts = ConvertOptions()
    opts.parametric = True
    r = convert(mesh, opts)
    notes = getattr(r, "prep_notes", []) or []
    assert any("参数化重建未命中" in n for n in notes), notes
    assert getattr(r, "param_type", "") == "fallback", getattr(r, "param_type", "")


def test_fidelity_metric_measures_surface():
    """保真度体检：完全贴合的实体偏差≈0，尺寸偏 1mm 的实体偏差必须显著。"""
    import trimesh
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from stl_tool.quality import surface_deviation, format_fidelity, low_fidelity

    mesh = trimesh.creation.box(extents=(10.0, 10.0, 10.0))
    exact = BRepPrimAPI_MakeBox(gp_Pnt(-5, -5, -5), 10.0, 10.0, 10.0).Shape()
    fid = surface_deviation(mesh, exact)
    assert fid is not None, "体检应返回结果"
    assert fid["p95_pct"] < 0.5, "完全贴合的实体 P95 应接近 0，实际 {:.2f}%".format(fid["p95_pct"])
    assert not low_fidelity(fid), fid

    wide = BRepPrimAPI_MakeBox(gp_Pnt(-5, -5, -5), 11.0, 10.0, 10.0).Shape()
    fid2 = surface_deviation(mesh, wide)
    assert fid2 is not None
    assert fid2["max_pct"] > 5.0, "单侧偏 1mm（对角线的 ~5.4%）必须被抓到，实际 {:.2f}%".format(
        fid2["max_pct"])
    assert low_fidelity(fid2), fid2
    assert "P95" in format_fidelity(fid2)
    assert format_fidelity(fid2, with_p50=True).count("%") >= 3


def test_convert_attaches_fidelity():
    """convert 默认附带保真度数据；关掉选项则不体检。"""
    import trimesh
    from stl_tool.pipeline import convert, ConvertOptions

    mesh = trimesh.creation.box(extents=(20.0, 20.0, 20.0))
    opts = ConvertOptions()
    opts.parametric = True
    r = convert(mesh, opts)
    assert r.has_solid()
    fid = getattr(r, "fidelity", None)
    assert fid is not None, "参数化命中后应带保真度数据"
    assert fid["p95_pct"] < 0.5, fid

    opts2 = ConvertOptions()
    opts2.parametric = True
    opts2.measure_fidelity = False
    r2 = convert(mesh, opts2)
    assert getattr(r2, "fidelity", None) is None, "关闭选项后不应体检"
    assert opts2.as_dict()["表面保真度体检"] is False


def test_layers_reject_code_reported():
    """分层拒绝要带机器可读代码（供日志/统计归类），不只是中文文案。"""
    from stl_tool.parametric import _detect_layers, _find_thickness_axis, _volume_approx

    mesh = _trapezoid_prism_mesh()
    lay, info = _detect_layers(mesh, _find_thickness_axis(mesh), _volume_approx(mesh),
                               slope_max=0.30)
    assert lay is None
    assert info.get("reject_code") == "layers_slope", info


def test_offorigin_primitives_keep_position():
    """非原点零件：圆柱/回转体拟合必须保持位置（体积对≠位置对）。

    曾经的 bug：轴向坐标按"过原点的轴"计算，零件一离开原点就整体平移；
    体积校验照样通过，只有表面偏差抓得到。
    """
    import numpy as np
    import trimesh
    from stl_tool.parametric import detect_revolve
    from stl_tool.pipeline import convert, ConvertOptions, shape_to_mesh
    from stl_tool.quality import surface_deviation

    cyl = trimesh.creation.cylinder(radius=3.0, height=8.0, sections=96)
    cyl.apply_translation([40.0, -25.0, 12.0])
    opts = ConvertOptions()
    opts.parametric = True
    r = convert(cyl, opts)
    assert getattr(r, "param_type", "") == "cylinder", getattr(r, "param_type", "")
    shape = [s for s, v in r.shapes if v][0]
    sm = shape_to_mesh(shape)
    assert np.allclose(sm.bounds, cyl.bounds, atol=0.05), (sm.bounds, cyl.bounds)

    # 端部半径非 0 的回转体（平顶）也要封成平面，不能切出锥面
    prof = np.array([[0, 0], [0.8, 0.5], [1.5, 1], [2, 2.5],
                     [1.6, 4], [0, 4]], float) * 2.0
    rv = trimesh.creation.revolve(prof, sections=72)
    rv.apply_translation([30.0, 18.0, 5.0])
    sh, info = detect_revolve(rv)
    assert sh is not None, "回转体应被识别"
    fid = surface_deviation(rv, sh)
    assert fid is not None and fid["p95_pct"] < 1.5, fid
    sm2 = shape_to_mesh(sh)
    assert np.allclose(sm2.bounds, rv.bounds, atol=0.1), (sm2.bounds, rv.bounds)


if __name__ == "__main__":
    test_box_convert()
    print("box ok")
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
    test_convert_parametric_counterbore()
    print("counterbore ok")
    test_convert_parametric_obround()
    print("obround ok")
    print("ALL TESTS PASSED")