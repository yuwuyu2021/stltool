# ♑ STL → STEP 实体转换工具

<div align="center">
<img src="assets/icon.png" alt="STLTool" width="128">
</div>

> 把 3D 打印 / 建模用的 **STL 网格**，一键变成 CAD 软件（SolidWorks、Fusion 360、FreeCAD 等）**能打开、能直接改尺寸**的 **STEP 实体**。

> 🧪 **这是一个仍在探索阶段的个人学习项目**——作者利用业余时间一点一点啃"网格 → 可编辑参数化实体"这块难骨头，代码还可能粗糙、结果可能出错。**你的每条意见、建议、甚至一句批评，都是这个项目最宝贵的养分。** 发现 bug 或想到更好的思路，欢迎直接 [提 Issue](https://github.com/yuwuyu2021/stltool/issues) 交流，期待你的声音。

[![版本](https://img.shields.io/badge/最新版-v0.9.0-2ea44f?style=flat-square)](https://github.com/yuwuyu2021/stltool/releases)
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
| 🔩 **特征识别** | 圆孔、沉孔、腰型孔识别为解析面（如圆柱），STEP 面数骤降、可编辑 |
| ⚡ **大网格提速** | 超 8 万面模型自动 QEM 简化（体积误差 ≈0.01%），缝合面数直降一个量级 |
| 📦 **单文件分发** | 免安装单 exe 双击即用（体积优化方向见下文，欢迎支招） |
| 🗂️ **批量转换** | 选一个文件夹，一次转换全部 STL，并保持子目录结构 |
| 🎥 **所见即所得** | 3D 预览围绕模型自动 360° 旋转；日志实时输出 + 结果汇总 |
| 📐 **体积校验** | 网格体积 vs 实体体积偏差自动对比，转换质量一目了然 |

---

## 🧭 功能现状与探索方向

这个项目是摸着石头过河，下表如实记录哪些**已经能用**、哪些**还在琢磨**。括号内可点开对应源码。

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 回转体 / 基本体素（板、柱、锥、球） | ✅ 已实现 | `parametric.py`：PCA 主轴 + 点面残差拟合，面数可降 99% |
| 圆孔 / 沉孔 / 腰型孔 | ✅ 已实现 | `parametric.py`：通孔→解析圆柱面、沉孔→阶梯切除、腰型孔→两直段+两端圆弧重建 |
| **倒角 / 弧形倒边** | ✅ 实验版已实现 | `parametric.py` `_round_plate`：板边缘棱边逐一探测 `MakeFillet`/`MakeChamfer` 体积反演半径，合成 R2 圆角板 → fillet r=1.95（体积误差 0.12%）；但**真实件多为空腔壳体，圆角偏差与空腔混淆，有待壳体识别（M12）后进一步实战检验** |
| 筋 / 凸台 | 🔬 探索中 | 壳体上的加强筋、凸台暂未专门识别 |
| 螺纹孔 / 螺柱 / 沉头螺栓 | 🔬 探索中 | 尚无法可靠识别，需先攻克螺旋扫描方向判断等问题 |

> 💬 你最希望优先支持哪种特征？遇到什么形状转换效果不好？欢迎到 [Issues](https://github.com/yuwuyu2021/stltool/issues) 告诉我，我会按真实需求排优先级。

---

## 🚀 快速开始（推荐）

1. 打开 **[GitHub Releases 发布页](https://github.com/yuwuyu2021/stltool/releases/latest)**
2. 在最新版本的 **Assets** 中下载 `STLTool-v0.9.0-win64-singlefile.exe`
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

## 📦 为什么 exe 这么大？怎么才能变小？

当前单文件 exe **约 280 MB**，坦白说偏大了，作者也一直想瘦身。先如实交代里面的东西：

- **PyInstaller 单文件自解压**的固有开销（所有文件打包 + 压缩/解压层）；
- **内置完整 Python 解释器** + 我们日常用的 `numpy`、`trimesh`、`shapely`、`PyQt6` 等库；
- 体积大头是 **OpenCASCADE（OCC）CAD 内核**——它是专业级的几何内核，一套原生库就要几百 MB，正是它让"生成真正可编辑的 STEP 实体"成为可能。

想瘦身的思路（部分已在尝试，也欢迎你支招）：

| 方向 | 思路 | 预计收益 |
| --- | --- | --- |
| 1️⃣ 模块裁剪 | PyInstaller `--exclude-module` 剔掉未用到的 QtWebEngine、matplotlib、test 等 | 中 |
| 2️⃣ 去掉无谓上层 | 从 `cadquery` 改为直接使用其底层 OCP wheel，减少一层代码与依赖 | 中 |
| 3️⃣ 目录版代替单文件 | 改用 `--onedir` 目录 + ZIP 分发，去掉自解压冗余，启动更快 | 一定 |
| 4️⃣ UPX 压缩 | 对 DLL 在线压缩（需实测与 OCC 兼容性，个别 DLL 可能被锁） | 不确定 |
| 5️⃣ 引导器路线 | 分发一个 ~10 MB 的微型引导器，首次运行联网按需拉取依赖（相当于安装器） | 最大（可到 ~10 MB） |

> 🎯 短期目标先降到 **~150 MB** 以内，之后视反馈再决定是否走目录版或引导器路线。如果你有更好的方案（比如 Alpine/精简 OCC 定制构建、或乐意指导 UPX 兼容性测试），万分欢迎来 [Issues](https://github.com/yuwuyu2021/stltool/issues) 赐教！

---

## 📮 更新与反馈

这个项目还很年轻，作者自知水平有限，真诚期待你的参与：

- 新版本一律发布在 **GitHub Releases**：[github.com/yuwuyu2021/stltool/releases](https://github.com/yuwuyu2021/stltool/releases)
- 有问题 / 有建议 / 想指点方向，欢迎到仓库提 **Issue**：[github.com/yuwuyu2021/stltool/issues](https://github.com/yuwuyu2021/stltool/issues)
- 看到明显的 bug，或感觉哪个环节"本可以更好"，请**不要客气**，直接指出就好。

## 📄 许可证

[GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0）

> 使用了我的修改版、或以网络服务方式对外提供本工具时，需以 AGPL-3.0 开源相应代码。