# STL 转可编辑实体 STEP 工具

> 把 3D 打印 / 建模用的 STL 网格文件，自动转换成 CAD 软件（SolidWorks、Fusion 360、FreeCAD 等）**可以打开、可以直接修改**的 STEP 实体文件。

STL 只是一层"薄壳"，用 CAD 打开既不能编辑也不好改尺寸；STEP 是真正的实体（Solid）。
本工具读取 STL 后**自动选择最合适的重建方式**（薄板带孔 / 回转体 / 基本体素 / 面拟合），一键输出准确的 STEP 实体，全程无需任何设置。

---

## 🚀 最快上手：直接下载发布包（推荐）

不需要安装 Python，也不需要任何环境：

1. 打开 **GitHub Releases 发布页**：[github.com/yuwuyu2021/stltool/releases](https://github.com/yuwuyu2021/stltool/releases)
2. 在最新版本（latest）的资产（Assets）中下载：
   **`STLTool-v0.6.x-win64-singlefile.exe`**（约 280 MB，已包含全部依赖）
3. 双击运行即可（首次启动需要解压，等待几秒是正常的）

> 单文件版本无需联网、无需安装。若系统提示"未知来源"，点击"仍要运行"即可。

## 🧭 界面怎么用（三步）

1. **选择输入**——点「选择文件…」选一个 `.stl`，或点「选择目录(批量)…」一次转换一个文件夹里的**全部 STL**（支持子目录）。
2. **选择输出目录**——转换后的 `.step` 文件将写到这里，并保持原目录结构。
3. **点「开始转换」**。

剩下的全自动：

- 自动清洗网格：补孔、修复法向、清理退化面；
- 自动选择重建方式，未命中时自动回退面拟合；
- 上方的 3D 预览会**围绕模型中心自动 360° 旋转**，可随时手动拖拽查看；
- 下方的日志实时输出每个文件的详细转换过程；
- 全部完成后显示**汇总**（成功 / 失败数量、耗时、输出位置）。

---

## 🛠 从源码运行（开发者 / 二次开发）

环境要求：Windows + Python 3.9+。

```bash
git clone https://github.com/yuwuyu2021/stltool.git
cd stltool
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

跑测试：

```bash
pytest tests -q
```

## 📚 技术栈

| 组件 | 用途 |
| --- | --- |
| `trimesh` | STL 读取、网格分析、孔洞修补 |
| `cadquery-ocp` (OCP) | OpenCASCADE 官方绑定：B-Rep 实体构建与 STEP 导出 |
| `shapely` + `mapbox-earcut` | 薄板轮廓三角化 |
| `PyQt6` + `pyqtgraph` (+`PyOpenGL`) | 界面与 OpenGL 3D 预览 |

## 🆕 更新与反馈

- 新版本一律发布在 **GitHub Releases**：<https://github.com/yuwuyu2021/stltool/releases>
- 有问题或建议，欢迎到仓库提 Issue：<https://github.com/yuwuyu2021/stltool/issues>

## 📄 许可证

[MIT](LICENSE)