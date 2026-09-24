# STL 转可编辑实体 STEP 工具

将 STL 三角网格文件转换为 CAD 软件可打开、可编辑的 STEP（B-Rep）实体文件。

## 特性
- **自动网格分析**：闭合性（watertight）、流形、开放边界边数、体积、表面积、连通片数、退化面/重复面/法向异常面检测
- **自动转换策略**：闭合网格→直接缝合为实体；开放网格→自动补孔后重建；无法闭合→降级导出开放壳并提示
- **实体校验**：导出前使用 BRepCheck 验证实体有效性
- **可编辑 STEP**：导出 AP203 / AP214 标准，支持曲面曲线（pcurve）
- **完整 GUI**：STL 拖放/打开、OpenGL 3D 预览、边界/法向异常高亮、实时日志、后台转换不卡界面

## 安装
需要 Python 3.9+（本仓库开发于 Python 3.14）。
```bash
pip install -r requirements.txt
```

## 运行
```bash
python main.py
```

## 测试
```bash
python tests\test_core.py
```

## 使用方法
1. 打开或拖入一个 `.stl` 文件，左侧自动显示网格分析结果
2. 在右侧 3D 视图中检查网格（红色边 = 开放边界，黄色面 = 法向异常）
3. 调整转换设置（补孔、修复法向、清理退化面、缝合容差）
4. 点击“转换为实体”预览结果，或直接“导出 STEP 文件”

## 技术栈
- `trimesh` —— STL 读取、拓扑与质量分析、孔洞修补
- `cadquery-ocp` (OCP) —— OpenCASCADE 官方 Python 绑定，B-Rep 实体构建与 STEP 导出
- `PyQt6` + `pyqtgraph` —— GUI 与 OpenGL 3D 预览

## 当前版本
v0.1.0

详见 `plan.md`。

### 许可证
[MIT](LICENSE)