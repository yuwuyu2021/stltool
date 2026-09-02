# STL 转可编辑实体 STEP 工具 项目计划

当前版本：v0.0.3

## 目标
开发一个带完整 GUI 的 Windows 工具：读取 STL 三角网格，自动分析网格质量与拓扑结构，自动选择策略重建为 B-Rep 实体（Solid），并导出为 CAD 软件可打开、可编辑的 STEP 文件（AP203/AP214）。

## 技术方案
- 读取 / 分析网格：`trimesh`（支持二进制/ASCII STL，拓扑分析）
- 实体构建 / STEP 导出：`cadquery-ocp`（OCP，OpenCASCADE 官方 Python 绑定）
- GUI + 3D 预览：`PyQt6` + `pyqtgraph`（OpenGL)；分析结果可视化高亮
- 网格精简（可选）：`fast-simplification`

### 转换策略（自动分析决策）
1. 网格流形且闭合（watertight）→ 逐三角面构建 B-Rep 面，缝合（Sewing）成闭合壳，构建实体
2. 轻度开放（边界边少）→ 自动补孔（trimesh.repair.fill_holes）后按策略 1 缝合
3. 重度开放 / 多连通组件 → 提示用户：逐壳构建实体或导出为多实体装配
4. 缝合后一律用 BRepCheck 验证实体有效性，无效则提示并给修复建议

## 里程碑
- [x] M1 核心转换引擎：STL 读取 → 网格分析 → B-Rep 实体构建 → STEP 导出（命令行可跑通）
  - 验收：程序生成/用户提供 STL 能无损转换为合法实体 STEP；CAD 可打开编辑
  - ✅ 已完成：mesh_analyzer、solid_builder、step_exporter、pipeline；测试通过（box/sphere/开放网格补孔/无补孔导出/STEP）
- [x] M2 完整 GUI：文件打开/拖放、3D 预览、分析结果面板、导出参数、进度与日志
  - ✅ 已完成：PyQt6 + pyqtgraph OpenGL 预览、边界/法向高亮、后台线程转换、进度条、日志、导出对话框
- [ ] M3 自动修复与鲁棒性：孔洞修补、缝合容差调节、多体/多壳处理、失败诊断建议、大网格精简
  - ⏳ 部分完成：孔洞修补、法向修复、退化面清理、缝合容差、多壳导出宏。待办：大网格精简(fast-simplification)、更精细的失败诊断
- [ ] M4 打磨发布：单元测试、性能优化、打包脚本（PyInstaller）、README、文档
  - ⏳ 部分完成：核心单元测试、README。待办：打包脚本、更多测试用例（含样例 STL fixtures）

## 量化指标
- 转换成功率：闭合网格 100%，轻度开放网格 ≥90%（当前测试：闭合 100%）
- 容量：10 万面以内可交互操作；50 万面以上提供精简选项（待实现精简）
- 实体有效性：导出前 BRepCheck 全部通过（当前测试：全部通过）

## 进度
- v0.0.1：M1 核心转换引擎 + M2 完整 GUI 完成并通过验证；初始化 git 仓库首次提交
- v0.0.2：预览界面升级——灰色网格表线背景、深灰模型、新增 STL/STEP 双预览窗口、生成预览/重置视图按钮、shape_to_mesh 实体离散预览；修复相机中心 numpy 数组导致预览刷屏报错
- v0.0.3：恢复光照着色、新增模型颜色调色板（QColorDialog 可选色并应用到双预览）
```