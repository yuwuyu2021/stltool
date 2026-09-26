# ♑ STL → STEP 实体转换工具

<div align="center">
<img src="assets/icon.png" alt="STLTool" width="128">
</div>

> 把 3D 打印 / 建模用的 **STL 网格**，一键变成 CAD 软件（SolidWorks、Fusion 360、FreeCAD 等）**能打开、能直接改尺寸**的 **STEP 实体**。

[![版本](https://img.shields.io/badge/最新版-v0.7.2-2ea44f?style=flat-square)](https://github.com/yuwuyu2021/stltool/releases)
[![平台](https://img.shields.io/badge/平台-Windows-0078d6?style=flat-square)]()
[![分发](https://img.shields.io/badge/单文件-免安装-orange?style=flat-square)]()
[![许可](https://img.shields.io/badge/许可-AGPL--3.0-red?style=flat-square)](LICENSE)

<div align="center">

[![下载最新版](https://img.shields.io/badge/⬇️%20下载最新版-Windows%20单文件-1f883d?style=for-the-badge&logo=github)](https://github.com/yuwuyu2021/stltool/releases/latest)
[![在线浏览](https://img.shields.io/badge/查看源码-软件仓库-8250df?style=for-the-badge&logo=github)](https://github.com/yuwuyu2021/stltool)

</div>

---

## 为什么需要这个工具？

STL 只是模型外面的一层"薄壳"——一堆三角形拼起来的网格，CAD 软件打开后只能看，量尺寸、改模型、出工程图都无从下手。

STEP 才是 CAD 世界的"正规军"——参数化**实体（Solid）**，面可以选中、尺寸可以改、特征可以继续做。

```
        STL（三角网格，薄壳）                 STEP（参数化实体）
        /\     /\
       /  \   /  |                            ┌─────────┐
      /____\/___/  ──────  本工具一键转换  ───▶ │  实体    │
      "只有一层壳"                            │ 可编辑   │
      改不了尺寸，无法出图                      └─────────┘
                                              可直接 CAD 操作
```

## ✨ 特性亮点

| | 说明 |
| --- | --- |
| 🤖 **全程自动** | 无需任何设置：自动补孔、修复法向、清理退化面，一键直达 STEP |
| 🧩 **智能重建** | 自动识别薄板带孔 / 回转体 / 基本体素等形状，未命中自动回退面拟合 |
| 📦 **单文件分发** | 无需安装 Python，下载 ≈280 MB 单 exe 双击即用，断网也能转 |
| 🗂️ **批量转换** | 选一个文件夹，一次转换全部 STL，并保持子目录结构 |
| 🎥 **所见即所得** | 3D 预览围绕模型自动 360° 旋转；日志实时输出 + 结果汇总 |
| 📐 **体积校验** | 网格体积 vs 实体体积偏差自动对比，转换质量一目了然 |

---

## 🚀 快速开始（推荐）

1. 打开 **[GitHub Releases 发布页](https://github.com/yuwuyu2021/stltool/releases/latest)**
2. 在最新版本的 **Assets** 中下载 `STLTool-v0.7.2-win64-singlefile.exe`
3. 双击运行（首次启动需解压，等待几秒属正常现象）

> 💡 若系统提示"未知来源"，点击 **更多信息 → 仍要运行** 即可。单文件版无需联网、无需安装。

---

## 🖱️ 界面三步

1. **选输入** — 点「选择文件…」选单个 `.stl`，或点「选择目录(批量)…」一次转整个文件夹（支持子目录）；
2. **选输出** — 转换后的 `.step` 写到这里，原目录结构保持不变；
3. **开始转换** — 剩下的交给它：

- 上方 3D 预览**自动 360° 环绕旋转**，可随时拖拽、缩放查看；
- 下方日志实时输出每份文件的清洗、重建、体积校验、耗时；
- 全部完成自动弹出**汇总**：成功 / 失败数量、总耗时、输出位置。

---

## 👩‍💻 开发者：从源码运行

<details>
<summary>展开：克隆仓库 → 创建虚拟环境 → 运行（Windows + Python 3.9+）</summary>

```bash
git clone https://github.com/yuwuyu2021/stltool.git
cd stltool
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

运行测试：

```bash
pytest tests -q
```

</details>

---

## ⚡ 技术栈

| 组件 | 用途 |
| --- | --- |
| `trimesh` | STL 读取、网格分析、孔洞修补 |
| `cadquery-ocp`（OCP） | OpenCASCADE 官方绑定：B-Rep 实体构建与 STEP 导出 |
| `shapely` + `mapbox-earcut` | 薄板轮廓三角化 |
| `PyQt6` + `pyqtgraph`（+ `PyOpenGL`） | 界面与 OpenGL 3D 预览 |

---

## 📮 更新与反馈

- 新版本一律发布在 **GitHub Releases**：[github.com/yuwuyu2021/stltool/releases](https://github.com/yuwuyu2021/stltool/releases)
- 有问题或建议，欢迎到仓库提 **Issue**：[github.com/yuwuyu2021/stltool/issues](https://github.com/yuwuyu2021/stltool/issues)

## 📄 许可证

[GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0）

> 使用了我的修改版、或以网络服务方式对外提供本工具时，需以 AGPL-3.0 开源相应代码。