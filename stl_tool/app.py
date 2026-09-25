import multiprocessing as mp
import os
import time

import numpy as np
import trimesh
from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QAction
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QStatusBar, QVBoxLayout, QWidget,
)

from . import APP_NAME, VERSION
from .widget3d import GLCADViewWidget

GITHUB_REPO = "https://github.com/yuwuyu2021/stltool"
GITHUB_RELEASES = GITHUB_REPO + "/releases"


def _step_out_path(src_file, src_dir, out_dir):
    """镜像输入目录结构到输出目录，输出 *.step。"""
    stem = os.path.splitext(os.path.basename(src_file))[0]
    rel = ""
    if src_dir:
        rel_dir = os.path.dirname(os.path.relpath(src_file, src_dir))
        if rel_dir and rel_dir != ".":
            rel = rel_dir + os.sep
    return os.path.join(out_dir, rel + stem + ".step")


def _proc_convert(src_file, src_dir, out_dir, schema, write_pcurves):
    """在独立子进程中执行单个文件的完整转换（spawn 目标函数，须为模块顶层）。

    子进程拥有独立 GIL，长耗时重建不再阻塞 GUI 事件循环。
    返回可 Pickle 的汇总字典；预览用 trimesh 网格一并返回。
    """
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp

    from .pipeline import convert, ConvertOptions, make_compound, shape_to_mesh
    from .step_exporter import write_step

    t1 = time.time()
    try:
        mesh = trimesh.load(src_file, force="mesh")
    except Exception as exc:
        return {"ok": False, "error": "读取失败：" + str(exc), "preview_mesh": None}
    if mesh is None or mesh.faces is None or len(mesh.faces) == 0:
        return {"ok": False, "error": "空网格", "preview_mesh": None}
    vol_mesh = float(mesh.volume) if mesh.is_watertight else None

    try:
        opts = ConvertOptions()
        opts.parametric = True  # 自动选择：参数化重建 → 未命中自动回退
        res = convert(mesh, opts)
        shapes = [s for s, v in res.shapes if v] or [s for s, _ in res.shapes]
        shape = make_compound(*shapes) if len(shapes) > 1 else (shapes[0] if shapes else None)
        if shape is None:
            raise RuntimeError("没有可导出的实体形状。")
        out = _step_out_path(src_file, src_dir, out_dir)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        ok_w, msg = write_step(shape, out, schema=schema, write_pcurves=write_pcurves)
        if not ok_w:
            raise RuntimeError(msg)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "preview_mesh": mesh}

    kind = getattr(res, "param_type", "") or "逐三角"
    n_new = 0
    for sh in shapes:
        e = TopExp_Explorer(sh, TopAbs_FACE)
        while e.More():
            n_new += 1
            e.Next()
    pr = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, pr)
    vol_step = pr.Mass()
    step_mesh = None
    try:
        sm = shape_to_mesh(shape, linear_deflection=0.8, angular_deflection=0.5)
        if sm is not None and len(sm.faces) > 0:
            step_mesh = sm
    except Exception:
        pass
    return {
        "ok": True,
        "out_path": out,
        "preview_mesh": mesh,
        "step_mesh": step_mesh,
        "vol_mesh": vol_mesh,
        "vol_step": vol_step,
        "kind": kind,
        "n_new": n_new,
        "seconds": time.time() - t1,
    }


class BatchWorker(QThread):
    log_msg = Signal(str)
    preview = Signal(object)      # 当前文件 STL 网格（trimesh）
    step_preview = Signal(object) # 当前文件 STEP 结果预览网格（trimesh）
    progress = Signal(int, int)
    finished = Signal(dict)

    def __init__(self, files, src_dir, out_dir, schema="AP214IS", write_pcurves=True, parent=None):
        super().__init__(parent)
        self.files = files
        self.src_dir = src_dir
        self.out_dir = out_dir
        self.schema = schema
        self.write_pcurves = write_pcurves

    def run(self):
        ctx = mp.get_context("spawn")
        pool = ctx.Pool(1)  # 1 个转换子进程：界面进程 GIL 完全空闲，界面与旋转保持流畅
        t0 = time.time()
        ok = failed = 0
        failed_files = []
        n = len(self.files)
        try:
            for i, f in enumerate(self.files):
                self.progress.emit(i, n)
                self.log_msg.emit("-" * 62)
                self.log_msg.emit("[{}/{}] 文件：{}".format(i + 1, n, os.path.basename(f)))
                try:
                    res = pool.apply(_proc_convert, (f, self.src_dir, self.out_dir,
                                                     self.schema, self.write_pcurves))
                except Exception as exc:
                    res = {"ok": False, "error": "子进程异常：" + str(exc), "preview_mesh": None}
                if res.get("ok"):
                    pm = res.get("preview_mesh")
                    if pm is not None:
                        self.preview.emit(pm)
                    vol_mesh = res.get("vol_mesh")
                    if vol_mesh is not None and vol_mesh > 0:
                        err = (res.get("vol_step", 0.0) - vol_mesh) / vol_mesh * 100.0
                    else:
                        err = float("nan")
                    vol_m = vol_mesh if vol_mesh is not None else 0.0
                    self.log_msg.emit("  顶点/面（见预览），体积 网格 {:.3f}".format(vol_m))
                    self.log_msg.emit("  导出成功：{}".format(res["out_path"]))
                    self.log_msg.emit("  重建：{} | 实体面 {} | 体积 网格 {:.3f} / STEP {:.3f}（{:+.1f}%），耗时 {:.1f}s".format(
                        res.get("kind", ""), res.get("n_new", 0), vol_m,
                        res.get("vol_step", 0.0), err, res.get("seconds", 0.0)))
                    sm = res.get("step_mesh")
                    if sm is not None:
                        self.step_preview.emit(sm)
                    ok += 1
                else:
                    failed += 1
                    failed_files.append((f, res.get("error", "未知错误")))
                    pm = res.get("preview_mesh")
                    if pm is not None:
                        self.preview.emit(pm)
                    self.log_msg.emit("  转换失败：" + str(res.get("error", "")))
            self.progress.emit(n, n)
        finally:
            pool.terminate()
            pool.join()
        self.log_msg.emit("-" * 62)
        self.finished.emit({
            "total": n, "ok": ok, "failed": failed,
            "seconds": time.time() - t0, "out_dir": self.out_dir,
            "failed_files": failed_files,
        })


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.busy = False
        self.current_mesh = None
        self.prev_title = "预览"
        self.setWindowTitle("{} v{}".format(APP_NAME, VERSION))
        self.resize(1120, 780)
        self._build_ui()
        self._build_menu()
        self.setAcceptDrops(True)
        self._set_busy(False)

    # ---------- 界面 ----------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # 顶部操作区
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("STL 输入"))
        self.edit_input = QLineEdit()
        self.edit_input.setReadOnly(True)
        self.edit_input.setPlaceholderText("选择一个 .stl 文件，或一个包含多个 .stl 的目录（批量转换）")
        row1.addWidget(self.edit_input, 1)
        btn_file = QPushButton("选择文件…")
        btn_file.clicked.connect(self.pick_file)
        row1.addWidget(btn_file)
        btn_dir = QPushButton("选择目录(批量)…")
        btn_dir.clicked.connect(self.pick_dir)
        row1.addWidget(btn_dir)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("输出目录"))
        self.edit_out = QLineEdit()
        self.edit_out.setReadOnly(True)
        self.edit_out.setPlaceholderText("转换后的 .step 文件将输出到这里")
        row2.addWidget(self.edit_out, 1)
        btn_out = QPushButton("选择…")
        btn_out.clicked.connect(self.pick_out)
        row2.addWidget(btn_out)
        self.chk_orbit = QCheckBox("自动旋转预览")
        self.chk_orbit.setChecked(True)
        self.chk_orbit.toggled.connect(self.on_orbit_toggle)
        row2.addWidget(self.chk_orbit)
        self.btn_start = QPushButton("开始转换")
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self.start_batch)
        self.btn_start.setMinimumWidth(120)
        row2.addWidget(self.btn_start)
        root.addLayout(row2)

        # 自动策略（只读展示，不提供给用户选择）
        info = QLabel(
            "自动策略（无需设置）：清洗（补孔·修复法向·清理退化面） → 参数化重建（薄板/回转体/体素）→ "
            "未命中自动回退面拟合 · 输出 STEP AP214 可编辑实体"
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "color:#445; background:#eef1f5; border:1px solid #ccd3dc; border-radius:4px; padding:5px 8px;"
        )
        root.addWidget(info)

        # 上预览 / 下日志
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.viewer = GLCADViewWidget(title="预览")
        self.viewer.set_orbit(True)
        self.viewer.view.setBackgroundColor((212, 214, 218, 255))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setPlaceholderText("转换日志将显示在这里…")
        self.splitter.addWidget(self.viewer)
        self.splitter.addWidget(self.log_view)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([520, 220])
        root.addWidget(self.splitter, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # 状态栏：左侧常驻 GitHub 仓库地址
        sb = QStatusBar()
        repo = QLabel('<a href="{}">GitHub 仓库：yuwuyu2021/stltool（下载最新版本 / 反馈）</a>'.format(GITHUB_REPO))
        repo.setOpenExternalLinks(True)
        sb.addWidget(repo)
        ver = QLabel("v{}".format(VERSION))
        sb.addPermanentWidget(ver)
        self.setStatusBar(sb)

    def _build_menu(self):
        m = self.menuBar()
        fm = m.addMenu("文件")
        a_file = QAction("选择 STL 文件…", self)
        a_file.setShortcut(QKeySequence("Ctrl+O"))
        a_file.triggered.connect(self.pick_file)
        fm.addAction(a_file)
        a_dir = QAction("选择目录(批量)…", self)
        a_dir.setShortcut(QKeySequence("Ctrl+D"))
        a_dir.triggered.connect(self.pick_dir)
        fm.addAction(a_dir)
        fm.addSeparator()
        a_quit = QAction("退出", self)
        a_quit.setShortcut(QKeySequence("Ctrl+Q"))
        a_quit.triggered.connect(self.close)
        fm.addAction(a_quit)

        vm = m.addMenu("视图")
        a_reset = QAction("重置视角", self)
        a_reset.triggered.connect(self.viewer.reset_view)
        vm.addAction(a_reset)
        a_orbit = QAction("自动旋转预览（开关）", self)
        a_orbit.triggered.connect(lambda: self.viewer.set_orbit(not self.viewer.orbit_enabled))
        vm.addAction(a_orbit)

        hm = m.addMenu("帮助")
        a_repo = QAction("GitHub 仓库", self)
        a_repo.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_REPO)))
        hm.addAction(a_repo)
        a_dl = QAction("下载最新版本（Releases）", self)
        a_dl.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_RELEASES)))
        hm.addAction(a_dl)
        hm.addSeparator()
        a_about = QAction("关于", self)
        a_about.triggered.connect(self.show_about)
        hm.addAction(a_about)

    # ---------- 日志 ----------
    def log(self, msg):
        self.log_view.appendPlainText("[{}] {}".format(time.strftime("%H:%M:%S"), msg))

    def _set_busy(self, busy):
        self.busy = busy
        self.btn_start.setEnabled(not busy)
        self.btn_start.setText("转换中…" if busy else "开始转换")

    # ---------- 输入选择 ----------
    def pick_file(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 STL 文件", "", "STL 文件 (*.stl);;所有文件 (*)")
        if path:
            self.edit_input.setText(path)
            self._update_tooltip()
            self.log("已选择文件：{}".format(path))
            self._maybe_preview_file(path)

    def pick_dir(self):
        if self.busy:
            return
        d = QFileDialog.getExistingDirectory(self, "选择包含 STL 文件的目录（批量转换）")
        if d:
            self.edit_input.setText(d)
            self._update_tooltip()
            self.log("已选择目录：{}".format(d))

    def pick_out(self):
        if self.busy:
            return
        d = QFileDialog.getExistingDirectory(self, "选择 STEP 输出目录")
        if d:
            self.edit_out.setText(d)
            self.log("输出目录：{}".format(d))

    def _update_tooltip(self):
        src = self.edit_input.text()
        self.edit_input.setToolTip(src if src else "")
        if src and os.path.isfile(src) and not self.edit_out.text():
            self.edit_out.setText(os.path.dirname(os.path.abspath(src)))
            self.log("输出目录已自动设为输入文件所在目录。")

    # ---------- 预览 ----------
    def _maybe_preview_file(self, path):
        try:
            mesh = trimesh.load(path, force="mesh")
        except Exception:
            return
        if mesh is not None and len(mesh.faces) > 0:
            self.current_mesh = mesh
            self.viewer.set_mesh(mesh, show_boundary=True, show_bad_faces=True, show_faces=False)
            self.viewer.title_label.setText("预览：{}".format(os.path.basename(path)))
            self.log("已加载 {}：顶点 {} / 面 {} / 水密 {}".format(
                os.path.basename(path), len(mesh.vertices), len(mesh.faces), mesh.is_watertight))

    def on_orbit_toggle(self, on):
        self.viewer.set_orbit(on)

    # ---------- 批量转换 ----------
    def start_batch(self):
        if self.busy:
            return
        src = self.edit_input.text().strip()
        if not src:
            QMessageBox.information(self, "提示", "请先选择要转换的 STL 文件或目录。")
            return
        out = self.edit_out.text().strip()
        if not out:
            QMessageBox.information(self, "提示", "请先选择 STEP 输出目录。")
            return

        if os.path.isfile(src):
            files = [src]
            src_dir = None
        else:
            if not os.path.isdir(src):
                QMessageBox.warning(self, "无效输入", "所选路径不存在：\n{}".format(src))
                return
            files = sorted(os.path.join(dp, fn) for dp, _, fns in os.walk(src) for fn in fns
                           if fn.lower().endswith(".stl"))
            src_dir = src
        files = [f for f in files if f.lower().endswith(".stl")]
        if not files:
            QMessageBox.information(self, "没有文件", "所选目录中没有 .stl 文件。")
            return

        self.log("=" * 62)
        self.log("开始批量转换：共 {} 个文件 → {}".format(len(files), out))
        self._set_busy(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(files))
        self.progress.setValue(0)

        self.worker = BatchWorker(files, src_dir, out)
        self.worker.log_msg.connect(self._on_worker_log)
        self.worker.preview.connect(self._on_preview)
        self.worker.step_preview.connect(self._on_step_preview)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_worker_log(self, msg):
        self.log(msg)

    def _on_preview(self, mesh):
        try:
            self.viewer.set_mesh(mesh, show_boundary=True, show_bad_faces=True, show_faces=False)
        except Exception:
            pass

    def _on_step_preview(self, mesh):
        self.viewer.set_mesh(mesh, show_boundary=False, show_bad_faces=False, show_faces=False)
        self.viewer.title_label.setText("预览：转换结果（STEP 实体）")

    def _on_progress(self, cur, total):
        self.progress.setValue(cur)

    def _on_finished(self, d):
        self.progress.setVisible(False)
        self._set_busy(False)
        self.log("-" * 62)
        self.log("==== 转换完成汇总 ====")
        self.log("总文件 {} · 成功 {} · 失败 {}".format(d["total"], d["ok"], d["failed"]))
        self.log("总耗时 {:.1f}s · 输出目录 {}".format(d["seconds"], d["out_dir"]))
        if d["failed"]:
            self.log("—— 失败清单 ——")
            for f, err in d["failed_files"]:
                self.log("  {}：{}".format(os.path.basename(f), err))
        self.log("可在“{}”中打开生成的 .step 文件。".format(d["out_dir"]))
        msg = "转换完成：成功 {} / {}，失败 {}，耗时 {:.1f}s".format(
            d["ok"], d["total"], d["failed"], d["seconds"])
        self.log(msg)

    # ---------- 其他 ----------
    def show_about(self):
        QMessageBox.about(
            self, "关于 " + APP_NAME,
            "{} v{}\n\nSTL → 可编辑实体 STEP 转换工具\n\n"
            "本界面已内置最佳参数策略：读取 STL 后自动清洗网格、自动选择重建方式"
            "（薄板/回转体/体素/面拟合），转换并导出 AP214 STEP 实体文件。\n\n"
            "下载与更新见 GitHub Releases：\n{}".format(APP_NAME, VERSION, GITHUB_RELEASES))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].toLocalFile().lower().endswith(".stl"):
                event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".stl"):
                self.edit_input.setText(path)
                self._update_tooltip()
                self.log("已拖入文件：{}".format(path))
                self._maybe_preview_file(path)
                break