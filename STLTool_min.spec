# -*- mode: python ; coding: utf-8 -*-
# STLTool 最小自举安装式单文件 exe 配置
# 仅打包引导器 + 内嵌资源，运行时自动建 .venv 并安装依赖。
# 使用: pyinstaller STLTool_min.spec

import os
import glob

# stl_tool 应用源码打包为数据（引导器在运行时将其复制为可用模块）
STL_SRC = [
    (p, "stl_tool")
    for p in glob.glob(os.path.join("stl_tool", "*.py"))
]

assets = [("assets/python-embed.zip", "assets")]

a = Analysis(
    ["bootstrap.py"],
    pathex=[],
    binaries=[],
    datas=assets + STL_SRC,
    hiddenimports=[],
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
    name="STLTool",
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
    icon="assets/icon.ico",
)