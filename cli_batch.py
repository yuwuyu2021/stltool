#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量命令行工具：对 STL 目录跑三种导出模式（逐三角 / analytic / parametric），
统计实体有效性、面数、体积误差，可导出 STEP，用于对比参数化重建的降面效果。

用法：
    python cli_batch.py <stl目录或单个stl文件> [--out <输出目录>]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import trimesh

from stl_tool.pipeline import ConvertOptions, convert, make_compound
from stl_tool.step_exporter import write_step

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE


def count_faces(result):
    n = 0
    for s, _ in result.shapes:
        e = TopExp_Explorer(s, TopAbs_FACE)
        while e.More():
            n += 1
            e.Next()
    return n


def make_result(mesh, mode, tolerance=0.05):
    opts = ConvertOptions()
    opts.analytic = mode == "analytic"
    opts.parametric = mode == "parametric"
    opts.tolerance = tolerance
    try:
        r = convert(mesh, opts)
    except Exception as exc:
        return None, str(exc)
    return r, None


def process_file(path, out_dir=None, tolerance=0.05, write_steps=True):
    name = os.path.basename(path)
    try:
        mesh = trimesh.load(path, force="mesh")
    except Exception as exc:
        print("[FAIL] {}  读取失败: {}".format(name, exc))
        return
    if mesh is None or mesh.faces is None or len(mesh.faces) == 0:
        print("[FAIL] {}  空网格".format(name))
        return

    vol = float(mesh.volume) if mesh.volume is not None else float("nan")
    print("=" * 78)
    print("文件: {}  顶点 {} 面 {}  体积 {:.3f}  水密 {}".format(
        name, len(mesh.vertices), len(mesh.faces), vol, mesh.is_watertight))

    for mode in ("parametric", "analytic", "mesh"):
        r, err = make_result(mesh, mode, tolerance)
        if err:
            print("  [{:10s}] 错误: {}".format(mode, err))
            continue
        if r is None or r.solid_count == 0:
            print("  [{:10s}] 无实体  msg={}".format(mode, r.message if r else "未知"))
            continue
        nf = count_faces(r)
        vol_err = ""
        if vol == vol:  # not nan
            from stl_tool.parametric import _volume
            shapes = [s for s, v in r.shapes if v]
            comp = make_compound(*shapes) if len(shapes) > 1 else (shapes[0] if shapes else None)
            sv = _volume(comp) if comp is not None else None
            if sv is not None and sv > 0:
                vol_err = "  体积误差 {:+6.2f}%".format((sv - vol) / vol * 100)
        kind = getattr(r, "param_type", "") or ("analytic" if mode == "analytic" else "")
        print("  [{:10s}] 实体{} 无效{} 面{}  kind={}{}".format(
            mode, r.solid_count, r.invalid_count, nf, kind, vol_err))

        if out_dir and write_steps and r.solid_count > 0:
            shapes = [s for s, v in r.shapes if v]
            comp = make_compound(*shapes)
            stem = os.path.splitext(name)[0]
            out_path = os.path.join(out_dir, "{}__{}.step".format(stem, mode))
            ok, msg = write_step(comp, out_path, schema="AP214IS", write_pcurves=True)
            print("       {:s}".format("OK -> " + out_path if ok else "导出失败: " + msg))
    print()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = [a for a in sys.argv[1:] if a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)

    target = args[0]
    out_dir = None
    if "--out" in opts:
        i = opts.index("--out")
        if i + 1 < len(args):
            out_dir = args[i + 1]
            args.remove(args[i + 1])
    tol = 0.05
    if "--tol" in opts:
        i = opts.index("--tol")
        if i + 1 < len(args):
            tol = float(args[i + 1])
            args.remove(args[i + 1])

    if os.path.isdir(target):
        files = [os.path.join(target, f) for f in sorted(os.listdir(target))
                 if f.lower().endswith(".stl")]
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        for f in files:
            process_file(f, out_dir, tolerance=tol)
    elif os.path.isfile(target):
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        process_file(target, out_dir, tolerance=tol)
    else:
        print("路径不存在: " + target)
        sys.exit(1)


if __name__ == "__main__":
    main()