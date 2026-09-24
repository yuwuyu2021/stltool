import os
import time

import numpy as np
import trimesh
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QStatusBar,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import APP_NAME, VERSION
from .widget3d import GLCADViewWidget
from .mesh_analyzer import MeshAnalysis
from .mesh_editor import rotate as mesh_rotate, mirror as mesh_mirror, merge_coplanar, orient_outward
from .pipeline import analyze, convert, ConvertOptions, make_compound, shape_to_mesh
from .step_exporter import write_step


class ConvertWorker(QThread):
    progressed = pyqtSignal(int, int)
    done = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, mesh, options, output_path=None, schema="AP214IS", write_pcurves=True, parent=None):
        super().__init__(parent)
        self.mesh = mesh
        self.options = options
        self.output_path = output_path
        self.schema = schema
        self.write_pcurves = write_pcurves

    def run(self):
        import tempfile

        try:
            result = convert(self.mesh, self.options, progress_cb=self._progress)
            # 总是导出一份真实 STEP 文件（转换时用临时文件，导出时用目标路径）
            step_path = self.output_path
            delete_after = False
            shape = None
            for s, valid in result.shapes:
                if valid:
                    shape = combine_here([s for s, v in result.shapes if v])
                    break
            if shape is None:
                shape = combine_here([s for s, _ in result.shapes])
            if shape is None:
                raise RuntimeError("没有任何可用于导出的形状。")
            if step_path is None:
                fd, step_path = tempfile.mkstemp(suffix=".step")
                os.close(fd)
                delete_after = True
            ok, msg = write_step(shape, step_path, schema=self.schema, write_pcurves=self.write_pcurves)
            if not ok:
                raise RuntimeError(msg)
            report = {
                "result": result,
                "analysis_lines": result.analysis.summary_lines(),
                "prep_notes": result.prep_notes,
                "options": self.options.as_dict(),
                "step_path": step_path,
                "delete_after": delete_after,
                "output_path": self.output_path,
            }
            self.done.emit(report)
        except Exception as exc:
            self.error.emit(str(exc))

    def _progress(self, cur, total):
        self.progressed.emit(cur, total)


def combine_here(shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    if not shapes:
        return None
    if len(shapes) == 1:
        return shapes[0]
    b = BRep_Builder()
    comp = TopoDS_Compound()
    b.MakeCompound(comp)
    for s in shapes:
        b.Add(comp, s)
    return comp


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.mesh = None
        self.base_mesh = None
        self.analysis = None
        self.worker = None
        self.busy = False
        self.current_step_path = None
        self.step_preview_mesh = None
        self.selected_faces = set()
        self.stl_pick_verts = None
        self.setWindowTitle("{} v{}".format(APP_NAME, VERSION))
        self.resize(1280, 800)
        self._build_ui()
        self._build_menu()
        self.setAcceptDrops(True)
        self._set_busy(False)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)

        self.file_label = QLabel("未打开文件")
        self.file_label.setWordWrap(True)
        lv.addWidget(self.file_label)
        self.file_label.setStyleSheet("color:#555;")

        self.analysis_box = QTextBrowser()
        self.analysis_box.setMinimumHeight(220)
        self.analysis_box.setPlaceholderText("打开 STL 后显示网格分析结果…")
        lv.addWidget(self.analysis_box)

        conv_grp = QGroupBox("转换设置")
        cv = QVBoxLayout(conv_grp)
        self.chk_fill = QCheckBox("自动补孔")
        self.chk_fill.setChecked(True)
        self.chk_fix = QCheckBox("修复法向")
        self.chk_fix.setChecked(True)
        self.chk_deg = QCheckBox("清理退化面")
        self.chk_deg.setChecked(True)
        tol_row = QHBoxLayout()
        tol_row.addWidget(QLabel("缝合容差"))
        self.spin_tol = QDoubleSpinBox()
        self.spin_tol.setRange(0.000001, 1000.0)
        self.spin_tol.setDecimals(6)
        self.spin_tol.setValue(0.05)
        self.spin_tol.setSingleStep(0.01)
        tol_row.addWidget(self.spin_tol)
        cv.addWidget(self.chk_fill)
        cv.addWidget(self.chk_fix)
        cv.addWidget(self.chk_deg)
        cv.addLayout(tol_row)
        lv.addWidget(conv_grp)

        edit_grp = QGroupBox("STL 编辑（旋转/镜像/共面合并）")
        ee = QVBoxLayout(edit_grp)
        self.face_count_label = QLabel("面片数：—")
        self.face_count_label.setStyleSheet("font-weight:bold; color:#333;")
        ee.addWidget(self.face_count_label)
        rot_lab = QLabel("旋转 90°")
        rot_lab.setStyleSheet("color:#666; font-size:11px;")
        ee.addWidget(rot_lab)
        for axis, lab, sign in (("x", "X", 1), ("y", "Y", 1), ("z", "Z", 1)):
            row = QHBoxLayout()
            row.addWidget(QLabel(lab))
            row.addStretch(1)
            for sname, ssign in (("逆时针+90°", 90), ("顺时针-90°", -90)):
                btn = QPushButton(sname)
                btn.setFixedWidth(70)
                btn.clicked.connect(lambda *_, a=axis, s=ssign: self.rotate_mesh(a, s))
                row.addWidget(btn)
            ee.addLayout(row)
        mir_lab = QLabel("镜像（沿轴向平面翻转）")
        mir_lab.setStyleSheet("color:#666; font-size:11px;")
        ee.addWidget(mir_lab)
        mir_row = QHBoxLayout()
        for ax in ("X", "Y", "Z"):
            btn = QPushButton(ax)
            btn.setFixedWidth(44)
            btn.clicked.connect(lambda *_, a=ax.lower(): self.mirror_mesh(a))
            mir_row.addWidget(btn)
        mir_row.addStretch(1)
        ee.addLayout(mir_row)
        # 共面合并
        mg_lab = QLabel("共面合并（相邻面法向夹角阈值 °）")
        mg_lab.setStyleSheet("color:#666; font-size:11px;")
        ee.addWidget(mg_lab)
        mg_row = QHBoxLayout()
        self.spin_merge = QDoubleSpinBox()
        self.spin_merge.setRange(0.1, 45.0)
        self.spin_merge.setDecimals(1)
        self.spin_merge.setValue(5.0)
        mg_row.addWidget(self.spin_merge, 1)
        btn_merge = QPushButton("合并共面面")
        btn_merge.clicked.connect(self.apply_merge)
        mg_row.addWidget(btn_merge)
        ee.addLayout(mg_row)
        btn_reset_mesh = QPushButton("重置网格（撤销编辑）")
        btn_reset_mesh.clicked.connect(self.reset_mesh)
        ee.addWidget(btn_reset_mesh)
        btn_orient = QPushButton("修复法向（朝外，修 CAD 底面空）")
        btn_orient.clicked.connect(self.orient_mesh)
        ee.addWidget(btn_orient)
        sel_row = QHBoxLayout()
        self.chk_select = QCheckBox("选择多面（Shift+点击）")
        self.chk_select.stateChanged.connect(self.on_select_mode)
        btn_clear_sel = QPushButton("清空选择")
        btn_clear_sel.clicked.connect(self.clear_selection)
        btn_merge_sel = QPushButton("合并选中面")
        btn_merge_sel.clicked.connect(self.merge_selected)
        self.sel_count_label = QLabel("已选 0 面")
        sel_row.addWidget(self.chk_select)
        sel_row.addWidget(btn_clear_sel)
        ee.addLayout(sel_row)
        sel_row2 = QHBoxLayout()
        sel_row2.addWidget(self.sel_count_label)
        sel_row2.addWidget(btn_merge_sel)
        sel_row2.addStretch(1)
        ee.addLayout(sel_row2)
        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel("范围选中"))
        pick_row.addWidget(QLabel("半径%"))
        self.spin_pick_radius = QDoubleSpinBox()
        self.spin_pick_radius.setRange(0.0, 100.0)
        self.spin_pick_radius.setDecimals(1)
        self.spin_pick_radius.setSingleStep(1.0)
        self.spin_pick_radius.setValue(0.0)
        pick_row.addWidget(self.spin_pick_radius, 1)
        pick_row.addWidget(QLabel("(0=单选)"))
        ee.addLayout(pick_row)
        lv.addWidget(edit_grp)

        exp_grp = QGroupBox("STEP 导出设置")
        ev = QGridWrap(exp_grp)
        ev.addRow("标准", self.schema_combo())
        ev.addRow("单位", self.unit_combo())
        self.chk_pcurves = QCheckBox("写曲面曲线(pcurve)")
        self.chk_pcurves.setChecked(True)
        ev.addWidget(self.chk_pcurves)
        self.chk_analytic = QCheckBox("面拟合导出（analytic，合并平面区）")
        self.chk_analytic.setChecked(False)
        self.chk_analytic.setToolTip("把共面/同向的平面三角区拟合成单一平面面，其余区域逐三角缝合；"
                                     "适合平面为主的机械件，可显著减少实体面数。")
        ev.addWidget(self.chk_analytic)
        lv.addWidget(exp_grp)

        self.btn_convert = QPushButton("转换为实体")
        self.btn_export = QPushButton("导出 STEP 文件…")
        self.btn_convert.clicked.connect(self.on_convert)
        self.btn_export.clicked.connect(self.on_export)
        lv.addWidget(self.btn_convert)
        lv.addWidget(self.btn_export)

        btn_row = QHBoxLayout()
        self.btn_preview = QPushButton("生成预览")
        self.btn_reset = QPushButton("重置视图")
        self.btn_preview.clicked.connect(self.on_preview)
        self.btn_reset.clicked.connect(self.reset_all_views)
        btn_row.addWidget(self.btn_preview)
        btn_row.addWidget(self.btn_reset)
        lv.addLayout(btn_row)

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("模型颜色"))
        self.model_color = QColor(0x55, 0xFF, 0x7F)
        self.btn_color = QPushButton()
        self.btn_color.setFixedWidth(120)
        self._apply_color_button_style()
        self.btn_color.clicked.connect(self.pick_model_color)
        color_row.addWidget(self.btn_color)
        lv.addLayout(color_row)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("color:#0a6;")
        lv.addWidget(self.result_label)
        lv.addStretch(1)

        # 双预览窗口：STL 在上，STEP 在下
        self.viewer_stl = GLCADViewWidget(title="STL 预览")
        self.viewer_stl.facePicked.connect(self.on_stl_face_picked)
        self.viewer_step = GLCADViewWidget(title="STEP 预览")
        preview_splitter = QSplitter(Qt.Orientation.Vertical)
        preview_splitter.addWidget(self.viewer_stl)
        preview_splitter.addWidget(self.viewer_step)
        preview_splitter.setStretchFactor(0, 1)
        preview_splitter.setStretchFactor(1, 1)
        splitter.addWidget(left)
        splitter.addWidget(preview_splitter)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 900])
        root.addWidget(splitter, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(130)
        self.log_view.setFont(QFont("Consolas", 9))
        root.addWidget(self.log_view)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.setStatusBar(QStatusBar())

    def _build_menu(self):
        m = self.menuBar()
        fm = m.addMenu("文件")
        a_open = QAction("打开 STL…", self)
        a_open.setShortcut(QKeySequence("Ctrl+O"))
        a_open.triggered.connect(self.open_file)
        fm.addAction(a_open)
        a_export = QAction("导出 STEP…", self)
        a_export.setShortcut(QKeySequence("Ctrl+E"))
        a_export.triggered.connect(self.on_export)
        fm.addAction(a_export)
        a_quit = QAction("退出", self)
        a_quit.setShortcut(QKeySequence("Ctrl+Q"))
        a_quit.triggered.connect(self.close)
        fm.addAction(a_quit)

        vm = m.addMenu("视图")
        a_reset = QAction("重置视角", self)
        a_reset.triggered.connect(self.reset_all_views)
        vm.addAction(a_reset)

        hm = m.addMenu("帮助")
        a_about = QAction("关于", self)
        a_about.triggered.connect(self.show_about)
        hm.addAction(a_about)

    def schema_combo(self):
        from PyQt6.QtWidgets import QComboBox
        self.combo_schema = QComboBox()
        self.combo_schema.addItems(["AP214IS", "AP203IS", "AP203", "AP214CD"])
        self.combo_schema.setCurrentText("AP214IS")
        return self.combo_schema

    def unit_combo(self):
        self.combo_unit = QComboBox()
        self.combo_unit.addItems(["毫米", "米", "英寸"])
        self.combo_unit.setCurrentText("毫米")
        return self.combo_unit

    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText("[{}] {}".format(ts, msg))

    def _set_busy(self, busy):
        self.busy = busy
        for w in (self.btn_convert, self.btn_export):
            w.setEnabled(not busy)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 STL 文件", "", "STL 文件 (*.stl);;所有文件 (*)")
        if path:
            self.load_stl(path)

    def load_stl(self, path):
        try:
            mesh = trimesh.load(path, force="mesh")
        except Exception as exc:
            QMessageBox.critical(self, "读取失败", "无法读取该 STL 文件：\n{}".format(exc))
            return
        if mesh is None or mesh.faces is None or len(mesh.faces) == 0:
            QMessageBox.warning(self, "空网格", "文件中没有可用的三角形网格。")
            return
        self.mesh = mesh
        self.base_mesh = mesh.copy()
        self.analysis = analyze(mesh)
        self._last_stl = path
        self.file_label.setText(path)
        self.file_label.setToolTip(path)
        self.analysis_box.setPlainText("\n".join(self.analysis.summary_lines()))
        self.viewer_stl.set_mesh(mesh, show_boundary=True, show_bad_faces=True, color=self.model_rgba())
        self.result_label.setText("已加载：{} 顶点 / {} 面。自动分析完成，可执行转换。".format(
            len(mesh.vertices), len(mesh.faces)))
        self.log("打开 {}：{} 顶点 / {} 面 / 水密={}".format(
            os.path.basename(path), len(mesh.vertices), len(mesh.faces), mesh.is_watertight))
        self._update_face_count()

    def _update_face_count(self, mesh=None):
        mesh = mesh or self.mesh
        if mesh is None:
            self.face_count_label.setText("面片数：—")
            return
        self.face_count_label.setText("面片数：{}（顶点 {}）".format(len(mesh.faces), len(mesh.vertices)))

    def _require_mesh(self):
        if self.mesh is None:
            QMessageBox.information(self, "提示", "请先打开一个 STL 文件。")
            return False
        return True

    def _render_stl(self, fit_view=True):
        if self.mesh is None:
            self.viewer_stl.clear()
            return
        sel = None
        if self.selected_faces:
            n = len(self.mesh.faces)
            mask = np.zeros(n, dtype=bool)
            mask[list(self.selected_faces)] = True
            sel = mask
        self.viewer_stl.set_mesh(self.mesh, show_boundary=True, show_bad_faces=True,
                                 color=self.model_rgba(), selected=sel,
                                 pickable=self.chk_select.isChecked(),
                                 show_faces=self.chk_select.isChecked(),
                                 fit_view=fit_view)

    def _update_selection_label(self):
        self.sel_count_label.setText("已选 {} 面".format(len(self.selected_faces)))

    def on_select_mode(self):
        self.selected_faces.clear()
        self._update_selection_label()
        self._render_stl(fit_view=False)

    def on_stl_face_picked(self, face, point=None):
        if self.mesh is None:
            return
        # 范围选中：以命中点为圆心、半径内（面心距离）的三角面全部加入选择
        radius = self.spin_pick_radius.value()
        if radius > 0 and point is not None:
            verts = np.asarray(self.mesh.vertices, dtype=float)
            diag = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))) or 1.0
            r = radius / 100.0 * diag  # 滑块的 0-100 视为包围盒对角线的百分比
            cents = verts[self.mesh.faces].mean(axis=1)
            dist = np.linalg.norm(cents - np.asarray(point), axis=1)
            hit = np.where(dist <= r)[0]
            for i in hit.tolist():
                self.selected_faces.add(int(i))
        elif 0 <= face < len(self.mesh.faces):
            if face in self.selected_faces:
                self.selected_faces.discard(face)
            else:
                self.selected_faces.add(face)
        self._update_selection_label()
        self._render_stl(fit_view=False)
        self.log("已选 {} 面".format(len(self.selected_faces)))

    def clear_selection(self):
        self.selected_faces.clear()
        self._update_selection_label()
        self._render_stl(fit_view=False)

    def merge_selected(self):
        if not self._require_mesh() or self.busy:
            return
        if len(self.selected_faces) < 2:
            QMessageBox.information(self, "提示", "请先用 Shift+点击 选中至少 2 个相邻三角面再合并。")
            return
        # 把选中面视为一个区域做共面合并（仅合并严格共面的选中面）
        from .mesh_editor import merge_coplanar
        tol = self.spin_merge.value()
        new_mesh = merge_coplanar(self.mesh, angle_tol_deg=tol, restrict=self.selected_faces)
        before = len(self.mesh.faces)
        self.selected_faces.clear()
        self._update_selection_label()
        self._apply_mesh_edit(new_mesh, "合并选中面 → {} 面".format(len(new_mesh.faces)))
        self._render_stl()

    def _apply_mesh_edit(self, new_mesh, note):
        self.mesh = new_mesh
        self.analysis = analyze(new_mesh)
        self._render_stl(fit_view=False)
        self._update_face_count()
        self.log(note)
        self.result_label.setText("当前 STL 编辑后：{} 面".format(len(new_mesh.faces)))

    def rotate_mesh(self, axis, angle_deg):
        if not self._require_mesh() or self.busy:
            return
        self._apply_mesh_edit(mesh_rotate(self.mesh, "xyz".index(axis), angle_deg),
                              "旋转 {} 轴 {}°".format(axis.upper(), angle_deg))

    def mirror_mesh(self, axis):
        if not self._require_mesh() or self.busy:
            return
        self._apply_mesh_edit(mesh_mirror(self.mesh, "xyz".index(axis)),
                              "镜像 {}".format(axis.upper()))

    def apply_merge(self):
        if not self._require_mesh() or self.busy:
            return
        tol = self.spin_merge.value()
        from .mesh_editor import merge_coplanar
        new_mesh = merge_coplanar(self.mesh, angle_tol_deg=tol)
        before = len(self.mesh.faces)
        self._apply_mesh_edit(new_mesh, "共面合并（阈值 {}°），{} → {} 面".format(
            tol, before, len(new_mesh.faces)))

    def reset_mesh(self):
        if self.base_mesh is None:
            QMessageBox.information(self, "提示", "尚未加载网格。")
            return
        self._apply_mesh_edit(self.base_mesh.copy(), "已重置网格（撤销所有编辑）")

    def orient_mesh(self):
        if not self._require_mesh() or self.busy:
            return
        if not self.mesh.is_watertight:
            QMessageBox.information(self, "提示", "“修复法向朝外”仅适用于闭合（watertight）网格。")
            return
        try:
            from .mesh_editor import orient_outward
            new_mesh = orient_outward(self.mesh)
        except Exception as exc:
            QMessageBox.critical(self, "法向修复失败", str(exc))
            self.log("法向修复失败：" + str(exc))
            return
        fixed = int((new_mesh.face_normals[:, 2] > 0.5).sum())
        self._apply_mesh_edit(new_mesh, "修复法向（朝外）完成，水密={} 体积={:.4f}".format(
            new_mesh.is_watertight, new_mesh.volume if new_mesh.is_watertight else float("nan")))

    def current_options(self):
        o = ConvertOptions()
        o.fill_holes = self.chk_fill.isChecked()
        o.fix_normals = self.chk_fix.isChecked()
        o.remove_degenerate = self.chk_deg.isChecked()
        o.tolerance = self.spin_tol.value()
        o.analytic = self.chk_analytic.isChecked()
        return o

    def current_schema(self):
        return self.combo_schema.currentText()

    def on_convert(self):
        if self.mesh is None:
            QMessageBox.information(self, "提示", "请先打开一个 STL 文件。")
            return
        if self.busy:
            return
        self._set_busy(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.result_label.setText("正在分析并构建实体…")
        self.log("开始转换：{} 面。".format(len(self.mesh.faces)))
        self._start_worker(None)

    def on_export(self):
        if self.mesh is None:
            QMessageBox.information(self, "提示", "请先打开一个 STL 文件。")
            return
        if self.busy:
            return
        # 需先完成过一次转换，生成真实 STEP 临时文件，导出=复制该文件
        src = getattr(self, "current_step_path", None)
        if not src or not os.path.exists(src):
            QMessageBox.information(self, "提示", "请先点击“转换为实体”以生成 STEP 预览文件，再执行导出。")
            return
        default = ""
        if getattr(self, "_last_stl", None):
            stem, _ = os.path.splitext(self._last_stl)
            default = stem + ".step"
        path, _ = QFileDialog.getSaveFileName(self, "导出 STEP 文件", default, "STEP 文件 (*.step);;所有文件 (*)")
        if not path:
            return
        if not path.lower().endswith((".step", ".stp")):
            path += ".step"
        try:
            import shutil
            shutil.copyfile(src, path)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            self.log("导出失败：" + str(exc))
            return
        self.log("导出成功：" + path)
        self.result_label.setText("导出成功：{}".format(path))
        QMessageBox.information(self, "导出完成", "STEP 文件已复制到：\n{}".format(path))

    def _start_worker(self, output_path):
        self.worker = ConvertWorker(
            self.mesh,
            self.current_options(),
            output_path,
            schema=self.current_schema(),
            write_pcurves=self.chk_pcurves.isChecked(),
        )
        self.worker.progressed.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, cur, total):
        if total > 0:
            self.progress.setValue(int(cur * 100 / total))

    def _on_done(self, report):
        self.progress.setVisible(False)
        self._set_busy(False)
        r = report["result"]
        lines = []
        for n in report["prep_notes"]:
            self.log("预处理：" + n)
            lines.append("• " + n)
        for n in r.analysis.summary_lines():
            lines.append(n)
        lines.append("—— 实体构建 ——")
        lines.append("实体: {} | 开放壳: {} | 无效形状: {}".format(
            r.solid_count, r.shell_count, r.invalid_count))
        lines.append(r.message)
        self.analysis_box.setPlainText("\n".join(lines))

        step_path = report.get("step_path")
        if step_path:
            self.current_step_path = step_path
        self._show_step_preview(step_path)

        if report.get("output_path"):
            # 直接导出到目标路径（worker 已写盘）
            self.log("导出成功：" + step_path)
            self.result_label.setText("导出成功：{}（实体 {} 个）".format(step_path, r.solid_count))
            QMessageBox.information(self, "导出完成",
                                    "STEP 文件已生成：\n{}\n\n实体：{} 个\n无效形状：{} 个".format(
                                        step_path, r.solid_count, r.invalid_count))
        else:
            self.result_label.setText(
                "转换完成，STEP 预留在临时目录。请在预览确认无误后点击“导出”（将复制该文件）。")
            self.log("临时 STEP 已生成（预览依据）：" + step_path)

    def _on_error(self, msg):
        self.progress.setVisible(False)
        self._set_busy(False)
        self.result_label.setText("发生错误：{}".format(msg))
        self.log("错误：" + msg)
        QMessageBox.critical(self, "错误", msg)

    def _show_step_preview(self, step_path=None):
        """预览依据真实导出的 STEP 文件读回（所见即所得），而非直接离散内存 shape。"""
        try:
            from .step_exporter import read_step
            from .pipeline import shape_to_mesh

            if not step_path or not os.path.exists(step_path):
                self.viewer_step.clear()
                self.step_preview_mesh = None
                return
            shape = read_step(step_path)
            if shape is None:
                self.viewer_step.clear()
                self.step_preview_mesh = None
                self.log("STEP 预览：读回真实文件失败。")
                return
            mesh = shape_to_mesh(shape, linear_deflection=0.8, angular_deflection=0.5)
            if mesh is not None and len(mesh.faces) > 0:
                self.step_preview_mesh = mesh
                self.viewer_step.set_mesh(mesh, show_boundary=False, show_bad_faces=False,
                                          color=self.model_rgba())
            else:
                self.viewer_step.clear()
                self.step_preview_mesh = None
        except Exception as exc:
            self.viewer_step.clear()
            self.step_preview_mesh = None
            self.log("STEP 预览失败：" + str(exc))

    def reset_all_views(self):
        self.viewer_stl.reset_view()
        self.viewer_step.reset_view()

    def on_preview(self):
        if self.mesh is None:
            QMessageBox.information(self, "提示", "请先打开一个 STL 文件。")
            return
        # 刷新 STL 预览并自动居中
        self.viewer_stl.set_mesh(self.mesh, show_boundary=True, show_bad_faces=True,
                                 color=self.model_rgba())
        self.viewer_stl.reset_view()
        self.log("已刷新 STL 预览。")

    def model_rgba(self):
        c = self.model_color
        return (c.red() / 255.0, c.green() / 255.0, c.blue() / 255.0, 1.0)

    def _apply_color_button_style(self):
        c = self.model_color.name()
        text_color = "#000" if self.model_color.lightness() > 128 else "#fff"
        self.btn_color.setText(self.model_color.name())
        self.btn_color.setStyleSheet(
            "background:{}; color:{}; border:1px solid #bbb;".format(c, text_color)
        )

    def pick_model_color(self):
        color = QColorDialog.getColor(self.model_color, self, "选择模型颜色")
        if color.isValid():
            self.model_color = color
            self._apply_color_button_style()
            if self.mesh is not None:
                self.apply_model_color()
            self.log("模型颜色已设为 {}".format(self.model_color.name()))

    def apply_model_color(self):
        if self.viewer_stl.mesh_item is not None:
            self.viewer_stl.set_mesh(self.mesh, show_boundary=True, show_bad_faces=True,
                                     color=self.model_rgba())
        if self.viewer_step.mesh_item is not None:
            self.viewer_step.set_mesh(self.step_preview_mesh, show_boundary=False, show_bad_faces=False,
                                      color=self.model_rgba())

    def show_about(self):
        QMessageBox.about(self, "关于 " + APP_NAME,
                          "{} v{}\n\n功能：\n- 读取 STL 三角网格并自动分析（闭合性/流形/边界/质量）\n"
                          "- 自动选择策略重建为 B-Rep 实体\n- 导出可编辑的 STEP 文件（AP203/AP214）\n\n"
                          "基于 trimesh + OpenCASCADE(OCP) + PyQt6".format(APP_NAME, VERSION))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].toLocalFile().lower().endswith(".stl"):
                event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".stl"):
                self.load_stl(path)
                break


class QGridWrap(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = None
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(9, 9, 9, 9)

    def addRow(self, label, widget):
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setFixedWidth(70)
        row.addWidget(lab)
        row.addWidget(widget)
        self._box.addLayout(row)

    def addWidget(self, w):
        self._box.addWidget(w)