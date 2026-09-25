# -*- mode: python ; coding: utf-8 -*-
# STLTool 单文件打包配置
# 使用: pyinstaller STLTool.spec

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = core_imports = []

# 运行时窗口图标（打包后亦随 exe 解包生效）
datas += [('assets/icon.ico', 'assets')]

# ---- OCP (OpenCASCADE 绑定，96MB+) 全量收集 ----
ocp_datas, ocp_binaries, ocp_hidden = collect_all('OCP')
datas += ocp_datas
binaries += ocp_binaries
hiddenimports += ocp_hidden

# ---- PyQt6 全量收集（含 qt 插件/可执行程序） ----
qt_datas, qt_binaries, qt_hidden = collect_all('PyQt6')
datas += qt_datas
binaries += qt_binaries
hiddenimports += qt_hidden

# ---- pyqtgraph ----
pg_datas, pg_binaries, pg_hidden = collect_all('pyqtgraph')
datas += pg_datas
binaries += pg_binaries
hiddenimports += pg_hidden

# ---- trimesh（含加载器/第三方子模块，延迟导入需显式列出）----
tm_datas, tm_binaries, tm_hidden = collect_all('trimesh')
datas += tm_datas
binaries += tm_binaries
hiddenimports += tm_hidden

# ---- shapely（parametric 板件重建依赖）----
sh_datas, sh_binaries, sh_hidden = collect_all('shapely')
datas += sh_datas
binaries += sh_binaries
hiddenimports += sh_hidden

# 显式补充 trimesh 常用依赖子模块（PyInstaller 静态分析往往遗漏）
hiddenimports += [
    'trimesh.repair',
    'trimesh.creation',
    'trimesh.graph',
    'trimesh.grouping',
    'trimesh.remesh',
    'trimesh.transformations',
    'trimesh.primitives',
]

# ---- 应用自身模块 ----
hiddenimports += [
    'stl_tool.app',
    'stl_tool.widget3d',
    'stl_tool.pipeline',
    'stl_tool.solid_builder',
    'stl_tool.mesh_analyzer',
    'stl_tool.mesh_editor',
    'stl_tool.parametric',
    'stl_tool.analytic',
    'stl_tool.step_exporter',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='STLTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)
