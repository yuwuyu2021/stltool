"""STLTool 自举安装式引导器。

仅依赖 Python 标准库。作为最小单文件 exe 的入口运行：

首次运行：
  1. 在 exe 所在目录创建子目录 runtime/
  2. 解压内嵌的 python-embed 到 runtime/.venv/（作为自包含虚拟环境）
  3. 通过 get-pip.py 安装 pip
  4. pip 安装 requirements.txt 中声明的全部依赖到 runtime/.venv/
  5. 复制应用源码 stl_tool/ 到 runtime/.venv/Lib/site-packages/
  6. 用 runtime/.venv/pythonw.exe 启动 GUI

之后运行（runtime/.ready 存在）：直接启动 GUI，秒开。
"""
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ASSETS_DIR = "assets"

REQUIREMENTS = (
    "trimesh>=4.0\n"
    "numpy>=1.26\n"
    "PySide6>=6.6\n"
    "pyqtgraph>=0.13\n"
    "PyOpenGL>=3.1\n"
    "cadquery-ocp>=7.7\n"
)

GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

_PTH_TEMPLATE = (
    "{embed_zip}\n"
    ".\n"
    "Lib\\site-packages\n"
    "\n"
    "import site\n"
)


def resource_dir():
    """返回内嵌资源所在目录（frozen 时为 MEIPASS，否则为项目根）。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def embed_zip_path():
    return os.path.join(resource_dir(), ASSETS_DIR, "python-embed.zip")


def _pth_name(py_root):
    for name in os.listdir(py_root):
        if name.startswith("python3") and name.endswith("._pth"):
            return name
    return "python314._pth"


def root_dir():
    override = os.environ.get("STLTOOL_ROOT")
    if override:
        return os.path.abspath(override)
    return os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))


def venv_dir():
    return os.path.join(root_dir(), "runtime", ".venv")


def ready_marker():
    return os.path.join(root_dir(), "runtime", ".ready")


def _log(msg):
    print("[STLTool boot] " + msg, flush=True)


def _run(cmd, cwd=None, env=None):
    _log("running: " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, env=env)
    if proc.returncode != 0:
        raise RuntimeError("命令失败({}): {}".format(proc.returncode, " ".join(cmd)))
    return proc


def bootstrap():
    """准备运行时环境；已就绪则返回 True。"""
    if os.path.exists(ready_marker()) and os.path.exists(os.path.join(venv_dir(), "python.exe")):
        _log("运行时已就绪，跳过安装")
        return True

    runtime = os.path.join(root_dir(), "runtime")
    os.makedirs(runtime, exist_ok=True)
    target = venv_dir()

    _log("首次运行，开始自举安装 ...")
    _log("解压嵌入式 Python 到 {} ...".format(target))
    if os.path.exists(target):
        shutil.rmtree(target)
    with zipfile.ZipFile(embed_zip_path(), "r") as zf:
        zf.extractall(target)

    # 修正 .pth：启用 site 以识别 Lib/site-packages 中的包（含 pip）
    pth = os.path.join(target, _pth_name(target))
    embed_zip = [n for n in os.listdir(target) if n.endswith(".zip")]
    embed_name = embed_zip[0] if embed_zip else "python314.zip"
    with open(pth, "w", encoding="ascii", newline="") as fh:
        fh.write(_PTH_TEMPLATE.format(embed_zip=embed_name))

    py_exe = os.path.join(target, "python.exe")

    # 安装 pip
    py_path = os.path.join(target, "python.exe")
    _log("下载并安装 pip ...")
    with urllib.request.urlopen(GET_PIP_URL, timeout=180) as resp:
        get_pip = os.path.join(runtime, "get-pip.py")
        with open(get_pip, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    try:
        _run([py_path, get_pip, "--no-warn-script-location"])
    finally:
        if os.path.exists(get_pip):
            os.remove(get_pip)

    # 安装依赖
    _log("pip 安装依赖（第一次可能较久）...")
    req_file = os.path.join(runtime, "requirements.txt")
    with open(req_file, "w", encoding="utf-8", newline="") as fh:
        fh.write(REQUIREMENTS)
    try:
        _run([py_path, "-m", "pip", "install", "-r", req_file])
    finally:
        if os.path.exists(req_file):
            os.remove(req_file)

    # 复制应用源码到 site-packages
    site_packages = os.path.join(target, "Lib", "site-packages")
    os.makedirs(site_packages, exist_ok=True)
    src = os.path.join(resource_dir(), "stl_tool")
    dst = os.path.join(site_packages, "stl_tool")
    _log("复制应用源码 stl_tool/ ...")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    with open(ready_marker(), "w", encoding="utf-8") as fh:
        fh.write("ready\n")
    _log("自举安装完成")
    return True


def launch():
    target = venv_dir()
    pyw = os.path.join(target, "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = os.path.join(target, "python.exe")
    site_packages = os.path.join(target, "Lib", "site-packages")
    env = dict(os.environ)
    env["PYTHONPATH"] = site_packages + os.pathsep + env.get("PYTHONPATH", "")
    _log("启动应用 ...")
    subprocess.Popen(
        [pyw, "-m", "stl_tool.app_main"],
        cwd=site_packages,
        env=env,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008),
    )


def main():
    try:
        if bootstrap():
            launch()
    except Exception as exc:  # noqa: BLE001
        _log("启动失败: {}".format(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()