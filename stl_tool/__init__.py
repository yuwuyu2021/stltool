VERSION = "0.7.0"
APP_NAME = "STL 转 STEP 工具"

import os
import sys


def app_icon_path():
    """程序图标路径：源码运行取项目 assets/icon.ico，打包后取 PyInstaller 解包目录。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "assets", "icon.ico")
    return p if os.path.exists(p) else ""