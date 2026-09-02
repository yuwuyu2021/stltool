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
        try:
            result = convert(self.mesh, self.options, progress_cb=self._progress)
            report = {
                "result": result,
                "analysis_lines": result.analysis.summary_lines(),
                "prep_notes": result.prep_notes,
                "options": self.options.as_dict(),
                "output": None,
            }
            if self.output_path:
                shape = None
                for s, valid in result.shapes:
                    if valid:
                        shape = combine_here([s for s, v in result.shapes if v])
                        break
                if shape is None:
                    shape = combine_here([s for s, _ in result.shapes])
                if shape is None:
                    raise RuntimeError("没有任何可用于导出的形状。")
                ok, msg = write_step(shape, self.output_path, schema=self.schema, write_pcurves=self.write_pcurves)
                report["output"] = {"path": self.output_path, "ok": ok, "msg": msg}
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
        self.analysis = None
        self.worker = None
        self.busy = False
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

        exp_grp = QGroupBox("STEP 导出设置")
        ev = QGridWrap(exp_grp)
        ev.addRow("标准", self.schema_combo())
        ev.addRow("单位", self.unit_combo())
        self.chk_pcurves = QCheckBox("写曲面曲线(pcurve)")
        self.chk_pcurves.setChecked(True)
        ev.addWidget(self.chk_pcurves)
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
        self.model_color = QColor(0x99, 0x9E, 0xA5)
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

    def current_options(self):
        o = ConvertOptions()
        o.fill_holes = self.chk_fill.isChecked()
        o.fix_normals = self.chk_fix.isChecked()
        o.remove_degenerate = self.chk_deg.isChecked()
        o.tolerance = self.spin_tol.value()
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
        default = ""
        if getattr(self, "_last_stl", None):
            stem, _ = os.path.splitext(self._last_stl)
            default = stem + ".step"
        path, _ = QFileDialog.getSaveFileName(self, "导出 STEP 文件", default, "STEP 文件 (*.step);;所有文件 (*)")
        if not path:
            return
        if not path.lower().endswith((".step", ".stp")):
            path += ".step"
        self._set_busy(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.result_label.setText("正在转换并导出…")
        self.log("开始导出：{}".format(path))
        self._start_worker(path)

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
        self._show_step_preview(r)
        out = report.get("output")
        if out:
            if out["ok"]:
                self.log(out["msg"] + " (" + out["path"] + ")")
                self.result_label.setText("导出成功：{}（实体 {} 个）".format(out["path"], r.solid_count))
                QMessageBox.information(self, "导出完成",
                                        "STEP 文件已生成：\n{}\n\n实体：{} 个\n无效形状：{} 个".format(
                                            out["path"], r.solid_count, r.invalid_count))
            else:
                self.log("导出失败：" + out["msg"])
                self.result_label.setText("导出失败：{}".format(out["msg"]))
                QMessageBox.critical(self, "导出失败", out["msg"])
        else:
            self.result_label.setText("转换完成：实体 {} 个".format(r.solid_count))
            QMessageBox.information(self, "转换完成",
                                    "实体：{} 个\n开放壳：{} 个\n无效形状：{} 个\n\n{}".format(
                                        r.solid_count, r.shell_count, r.invalid_count, r.message))

    def _on_error(self, msg):
        self.progress.setVisible(False)
        self._set_busy(False)
        self.result_label.setText("发生错误：{}".format(msg))
        self.log("错误：" + msg)
        QMessageBox.critical(self, "错误", msg)

    def _show_step_preview(self, result):
        try:
            shapes = [s for s, v in result.shapes]
            shape = make_compound(*shapes) if shapes else None
            if shape is None:
                self.viewer_step.clear()
                return
            mesh = shape_to_mesh(shape, linear_deflection=0.8, angular_deflection=0.5)
            if mesh is not None:
                self.step_preview_mesh = mesh
                self.viewer_step.set_mesh(mesh, show_boundary=False, show_bad_faces=False,
                                          color=self.model_rgba())
                self.result_label.setText("STEP 预览已更新")
            else:
                self.step_preview_mesh = None
                self.viewer_step.clear()
        except Exception as exc:
            self.viewer_step.clear()
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