# STL 转可编辑实体 STEP 工具 项目计划

当前版本：v0.6.4

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
  - ⏳ 部分完成：核心单元测试、README；首次完整打包脚本(STLTool.spec, 单文件 351MB)
  - ✅ v0.0.5 修复极小退化面导致实体无效的缺陷：真实扳机 STL（含面积 ~1e-6 退化面）经"清理退化面+自动补孔"产出合法闭合实体（BRepCheck valid），新增回归测试 tests/fixtures/block26_trigger.stl
  - ⏳ 自举安装式最小 exe（bootstrap）：内嵌嵌入式 Python(python-embed)，首次运行在 exe 所在目录自动创建 runtime/.venv、下载 get-pip 并 pip 安装依赖、复制应用源码，之后直接启动，秒开。待办：引导器多国内 pip 源+自动切换、日志文件、失败弹窗、（暂停中）
- [x] M5 STL 网格编辑与面片合并（v0.0.6，已完成）
  - 需求来源：Shapr3D 导入真实 STEP 底面显示空（源 STL 底部局部凹痕 59 个小面法向朝内，CAD 背面剔除导致）；用户要求可编辑源网格。
  - 功能：① 旋转（X/Y/Z，含 90°快捷）② 镜像（X/Y/Z 平面）③ 共面合并成大面（减少 STEP 面数）④ 交互选中多个面片合并、预览高亮 ⑤ 预览显示面片数
  - ✅ 已完成：旋转/镜像/共面合并滑块/面片计数/Shift+点击选面合并均实现并单测通过（box 192→84面保持水密体积不变）
- [ ] M6 面拟合生成 analytic STEP（进行中，v0.0.8 目标）
  - 需求来源：纯网格化缝合的 STEP 面数高（扳机 2224 面），Shapr3D 易显示成"空壳/卡顿"；采用"面拟合生成 analytic STEP"方案根治。
  - 目标：新增**面拟合导出模式**——网格区域分割 → 解析曲面拟合（平面/圆柱/圆锥/球, PCA+最小二乘+残差选型）→ 边界环提取 → 用 OCP 构造 analytic face（gp_Pln/Cylinder/Cone/Sphere）→ 缝合建 solid。其余无法拟合区域退回平面分片网格化。保留原缝合导出作回退。
  - ✅ 待办：区域分割、曲面拟合、边界环、OCP analytic face 构建、集成、验证（面数降几十、valid、水密、体积近似）
  - ✅ 已完成（v0.0.8）：`analytic.py` 模块——曲面拟合（平面 PCA / 圆柱/圆锥/球最小二乘，法向散度定轴+残差选型，修复圆柱半径符号、圆锥误判圆柱）、法向连续性区域分割+全局重拟合+同向共面合并（修复反向平行面误合并，box→6 平面面）、边界环半-edge 追迹、OCP analytic face 构建与外向法向校验、保守混合缝合（解析平面面+残余逐三角）。**关键修复：仅当 analytic face BRepCheck 单独合法才合并，否则该区退回逐三角**——解决了"部分 analytic 平面面非法导致整体实体 invalid"问题，扳机 analytic 导出现为 **valid、实心（内部点命中 ~39.5%、体积 3572，体积非 0 非空壳）**。新增测试 `test_convert_analytic`。**注**：真实扳机 STL 需经 `prepare_mesh`（朝外法向+补孔+清退化面）与 tolerance=0.05 才得合法实体，此前直接逐三角亦 invalid 属既有网格质量/容差问题，analytic face 校验修复后默认与 analytic 双路径均 valid。
- [x] M7 参数化重建：基本体素 + 回转体（v0.1.0，已完成）
  - 目标：不引入大模型，用纯算法整体识别 STL 为规则几何体（box/cylinder/cone/sphere/回转体），用 OCP 参数化原语（MakeBox/MakeCylinder/MakeCone/MakeSphere/MakeRevol）重建为面数极少的 B-Rep 实体。
  - ✅ 已完成（v0.1.0）：`parametric.py` 基本体素拟合（PCA 主轴+点到面最近距离残差）+ 回转体重建（多候选轴、按轴向层分组取最大半径、RDP 轮廓简化）；多处 bug 修复（面掩码/顶点长度不一致、轴检测盖面占优误判、零长边致 wire 断裂、按等距层采样丢轮廓）；`pipeline.convert` 集成 `parametric` 开关 + 回退链；CLI 批量工具 `cli_batch.py`；测试 `test_convert_parametric`（10 测试全绿）。
  - 验收：合成体素/回转体面数降 99%、BRepCheck valid、体积误差 <10%（体素）/<30%（回转）。真实样本电机支架 6048→34 面命中。
- [x] M8 孔特征识别（板+孔特征件参数化重建，已完成 v0.2.0/v0.4.0）
  - 需求来源：侧板/盖板类真实 STL 是"薄板 + 孔洞 + 沉孔/台阶"，M7 纯基本体素/回转体无法命中，当前只能回退 mesh（面数 2 万+，未降）。CLI 实测侧板中面切出 26 个碎片多边形，非等截面挤出体。
  - 目标：纯算法识别"外轮廓（等截面挤出体）+ 内部圆孔/沉孔/台阶"，用 OCP 挤出体 + 布尔减（BRepAlgoAPI_Cut 圆柱/台阶体）重建，把侧板/盖板类面数从 2 万+降到几百。
  - ✅ 已完成（v0.2.0）基础版：`detect_plate`——PCA 厚度轴 + 大平面单侧面片投影聚合（shapely union 外环+孔）× 两侧评估 → 板厚估计（对侧表面轴向中位/百分位）→ OCP 挤出外环 + 逐孔布尔减（`_extrude_solid`，RDP 简化轮廓点到容差等级，全环节 try-except 兜底）。识别条件：侧面占比 ≥25%、扁率厚度/主尺寸 <0.5、体积估值误差 <25%，全部满足才接受以避免误判。实测：**侧板A 23918→346 面（-98.5%）、valid、体积误差 +0.6%、STEP 1.4MB（mesh 模式 >10MB）**；侧板B/C/D 及同步轮盖板为曲面/壳体件（单侧面占比仅 12-24%）合理拒绝仍走回退。新增测试 `test_convert_parametric_plate`（合成带孔板 3 孔，面数降 >90%，体积误差 <15%）。（12 测试全绿）
  - ✅ 已完成（v0.4.0）沉孔/台阶识别：顶面低洼簇（`top_z < z_other - max(0.8, 0.15*thickness)`）与孔中心近窗（`dist < 2r+2`）匹配，台面判据——孔心区低洼点占比 <35%（排除"孔落在大下沉槽"）、台半径 95 分位 ∈ [r+0.4, 2.4r]（真沉孔台有限宽）、台 z 离散度小（共面）、台深 >20% 板厚；命中即 `MakeCylinder(z_sunk, r_cb)` 布尔减。主体面高度改用"计数中位主 + 面积加权中位回退"（修复 shape_to_mesh 合成件台面顶点密度不均把顶面高度拉偏，侧板A 顶面 p50 最准）。实测：**侧板A 181→186 面（新增 2 沉孔圆柱面）、valid、体积误差 +0.21%（较 v0.3.0 +0.4% 更准）**；合成沉孔板（40×30×4，通孔 r2.5 + 台面 r5 深 2）命中 1 沉孔、误差 -0.15%。新增测试 `test_convert_parametric_counterbore`。
  - ✅ 已完成（v0.5.0）非圆孔圆角化（腰型/长条孔两端圆弧化）：`hole_is_obround` 判定——PCA 长轴下环点集到"两直段+两圆弧"边界曲线距离均方根 ≤0.3r、腰型面积与环面积一致性 ≥90%，返回 (圆心中点, 长轴, 圆心距 d, 半径 r)；`_extrude_solid` 内 `make_obround_cut` 用 `MakeEdge` 直线段 + `MakeEdge(gp_Circ, P1, P2)` 圆弧（参数增大方向裁剪，避免跨参数缝）构造腰型面并挤出，替代原逐点多边形 wire 布尔减。**并修复板件全局平移 bug**：`origin` 原叠加 uv centroid 致整个挤出体偏移 +c（体积平移不变故此前未被发现），改为 `axis*z_base`（uv 为世界原点投影绝对坐标）。实测：**侧板A 186→168 面、valid、体积误差 +0.18%**；合成腰型孔板（60×40×4，r4 + d24）536→10 面（8 平面 + **2 解析圆柱端面**）、误差 ~0%。新增测试 `test_convert_parametric_obround`（断言 2 圆柱面）。
  - ⏳ 待办：凸台/筋识别（侧板A 已见 z≈7.5 与 z≈11 两组凸起）。
- [x] M9 孔洞解析圆柱面输出（v0.3.0，已完成）
  - 需求来源：M8 板件重建中圆孔仍用逐点 wire 布尔减，STEP 里孔侧壁是一堆小三角面，面数可进一步压缩且不"解析"。
  - 目标：把判定为真圆的通孔用 `BRepPrimAPI_MakeCylinder` 解析圆柱面布尔减替换 wire。
  - 方案：`hole_is_circle` 三步判定——bbox 宽高比 ≤1.05（排除长条/操场形）→ 环比面积 vs πr² 一致性 ≥99%（排除复合弧/部分圆弧）→ 半径取 bbox 平均。圆柱轴沿挤出方向（`drill_dir` 与 height_vec 同向，修复"圆孔未切除"体积误差 0.4%）。
  - 实测：侧板A 23918→**181 面**（v0.2.0 为 346）、valid、体积误差 **+0.4%**、4s；同步轮盖板等曲面件仍合理回退（误差 0%）。12 测试全绿。

## 量化指标
- 转换成功率：闭合网格 100%，轻度开放网格 ≥90%（当前测试：闭合 100%）
- 容量：10 万面以内可交互操作；50 万面以上提供精简选项（待实现精简）
- 实体有效性：导出前 BRepCheck 全部通过（当前测试：全部通过）
- 共面合并后 STEP 面数：显著低于原始三角面数（M5 验收）

## 进度
- v0.0.1：M1 核心转换引擎 + M2 完整 GUI 完成并通过验证；初始化 git 仓库首次提交
- v0.0.2：预览界面升级——灰色网格表线背景、深灰模型、新增 STL/STEP 双预览窗口、生成预览/重置视图按钮、shape_to_mesh 实体离散预览；修复相机中心 numpy 数组导致预览刷屏报错
- v0.0.3：恢复光照着色、新增模型颜色调色板（QColorDialog 可选色并应用到双预览）
- v0.0.4：光照方向改为左上角（自定义 GLSL shader）、默认模型颜色改为浅绿 #55ff7f
- v0.0.5：修复极小退化面导致 STEP 实体无效（CAD 内显示内部空/底部缺失）的缺陷，新增真实 STL 回归测试
- v0.0.6：纯净版 STEP 预览（转换时导出真实 STEP→读回预览→导出复制）+ STL 网格编辑（旋转X/Y/Z、镜像X/Y/Z）+ 共面合并成大面（阈值可调）+ 预览交互 Shift+点击 选中多面合并 + 面片计数显示 + 范围选中（半径%）。核心引擎验证：2027 机械 STL 的底部反向小面缺陷已定位（源 STL 法向缺陷导致 CAD 底面空），提供编辑工具供用户处理。
- v0.0.7：根治"法向翻转导致 CAD 底面空"缺陷——新增 `orient_outward` 法向朝外修复算法（对每个面用射线点包含测试判定朝内并翻转，迭代至全部朝外），自动接入转换流程 `prepare_mesh`，并新增编辑面板"修复法向（朝外）"按钮。实测扳机 STL 底面朝上面从 58 降至 12（2%，纯 remesh 残迹），STEP 读回底面法向均值 -0.94 朝外、实体 valid；新增回归测试 `test_orient_outward`。
- v0.0.8：新增**面拟合导出模式**（导出设置勾选"面拟合导出 analytic"）。实现 `stl_tool/analytic.py`：解析曲面拟合（平面 PCA 优先，圆柱/圆锥/球最小二乘，法向散度定轴+残差选型+相关性拒伪圆锥）→ 法向连续性区域分割（25°内聚）+同向共面平面合并（修复反向平行面误合并，box→6 面）→ 边界环半-edge 追迹 → OCP analytic face 构建 + 实际法向校验/反向。顶层走保守混合缝合：可靠平面区拟合成单一平面面、其余三角逐面缝合。**关键：仅合并 BRepCheck 单独合法 的 analytic 面**，坏面退回逐三角，保证实体始终 valid。已用扳机真实 STL 验证 **valid + 实心**（体积 3572、内部点占比 ~39.5%、非空壳），并生成 `Block26_analytic.step`；box 6 面 valid、圆柱/球 valid；`ConvertOptions.analytic` + app 勾选项 + 测试 `test_convert_analytic`（10 测试全绿）。
- v0.1.0：新增**参数化重建模式**（`ConvertOptions.parametric`）。实现 `stl_tool/parametric.py`：基本体素拟合（box/cylinder/cone/sphere）+ 回转体重建（多候选轴、按轴向层分组取最大半径、RDP 轮廓简化、OCP MakeRevol）；柱/锥/回转轮廓有效半径统计改用面心（修复面掩码与顶点数组长度不一致 bug）、轴检测多候选（修复盖面占优时特征向量误判）；`convert()` 自动回退链 parametric → analytic → 逐三角。新增 CLI 批量工具 `cli_batch.py`（遍历目录跑三种模式对比面数/体积/耗时）。实测真实样本：**合成体素/回转体面数降 99%（box→6/cyl→3/sph→1/cone→2/酒杯 512→57）**；真实 STL 中**电机支架 6048→34 面（回转体命中，0.4s）**，侧板/盖板类（板+孔+沉孔特征件）未命中自动回退。**结论：纯算法对规整体有效，板+孔特征件需孔特征识别（M8）**。新增测试 `test_convert_parametric`（10 测试全绿）。
- v0.2.0：**M8 板件重建基础版**——`detect_plate` 识别带孔薄板并挤出重建（详见里程碑 M8）。侧板A 23918→346 面、valid、体积误差 +0.6%。新增测试 `test_convert_parametric_plate`（12 测试全绿）。
- v0.3.0：**M9 孔洞解析圆柱面**——`_extrude_solid` 圆孔改 `MakeCylinder` 布尔减（`hole_is_circle` bbox+面积一致性判定、半径取 bbox 平均、圆柱轴与挤出同向修复）。侧板A 23918→181 面、valid、体积误差 +0.4%。（12 测试全绿）
- v0.4.0：**M8 沉孔/台阶识别**——`detect_plate` 顶面低洼簇与孔中心近窗匹配判定真沉孔（近窗 `2r+2`、台半径 95 分位 ∈[r+0.4, 2.4r]、共面、台深限，排除"孔落在大下沉槽"与"合成品顶点密度不均"两类误判）；主体面高度"计数中位主+面积加权回退"。侧板A 181→186 面（含 2 沉孔）、valid、体积误差 +0.21%；合成沉孔板 1 沉孔 -0.15%。新增测试 `test_convert_parametric_counterbore`（13 测试全绿）。
- v0.5.0：**M8 非圆孔圆角化**——`hole_is_obround` 腰型孔检测（点到"两直段+两端圆弧"边界距离 RMS ≤0.3r + 面积一致性 ≥90%），`make_obround_cut` 用 `MakeEdge` 直线 + `MakeEdge(gp_Circ,P1,P2)` 圆弧构造解析切除体；**修复板件全局平移 bug**（origin 误叠 uv centroid）。侧板A 186→168 面、valid、误差 +0.18%；合成腰型孔板 536→10 面（8 平面+2 解析圆柱端面）、误差 ~0%。新增测试 `test_convert_parametric_obround`（14 测试全绿）。
- v0.6.0：**根治"STEP 在 CAD 显示为空壳"**——定位：`BRepPrimAPI_MakePrism`（本 OCP 绑定）生成两个端面 cap 拓扑方向恒相同，必有一张 cap 朝内（侧板A parametric 168 面中 24 面与源 STL 法向反向、CAD 背面剔除显示空壳）。已尝试 `BRepLib.OrientClosedSolid_s`/`ShapeFix_Solid`/`BRepAlgoAPI_Sewing`（后者绑定不存在）均无效——OCC 认为壳 edge-consistent 而拒改。最终方案：新增 `_extrude_manual` **手工壳组装挤出**——BRep_Builder 共享顶点/边逐个构建底、顶与侧面平面（自然法向由 `BRepBuilderAPI_MakeFace` 自动化，引用方向由壳组装时按外法向选 FWD/REV；侧面外法向用"环走向符号"几何公式 `sgn>0:(dy,-dx)/sgn<0:(-dy,dx)`，修复形心判据对凹轮廓失效、相邻面方向错乱致体积偏大 29% 的问题）。`_extrude_solid` 外层环挤出改走新路径（失败回退旧 MakePrism）。实测：**侧板A parametric STEP 168 面朝外 168/168（100%）、与源 STL 反向 24→1、valid、体积误差 +0.18% 不变**；box POC 尺寸/体积精确、全朝外；14 测试全绿；批量回归正常（其余样本不受影响）。**用户 CAD 打开验收确认符合需求**。本地 Git 领先 origin 7 提交（推送因 GitHub 代理不通暂缓）。
- v0.6.1：修复 `detect_plate` 体积校验回退分支 `_wmed` **面积加权中位崩溃**（真实样本"修改为ECAS04鲍登头 x4"触发 `IndexError: index 873 out of bounds` 与维度不匹配 `(1170,) vs (1573,)`）：权重用面面积、值却错用顶点 z 数组，且 NaN/浮点累积可使 `searchsorted` 返回 n 越界。改为面级中心 z `cz=(v[faces].mean(1))@axis` 配 `area_faces`，`_wmed` 内部过滤非有限值并钳制索引。鲍登头 parametric 由崩溃→正常出 STEP（valid）；新增 11 个真实 STL 样本入 `stl/new_case`（侧板A 命中 plate 168 面，其余合理 fallback）；14 测试全绿。
- v0.6.2：`cli_batch.py` 新增 `--recursive` 递归批量（子目录 STEP 镜像输出目录结构），`process_file` 支持 `out_rel`。**49 件真实零件批量实测**（NEMA14/NEMA17 × MetalPlates/Printed）——parametric **命中 30/49**：板件 plate 21（Clamp/Plate 系列全部，面数 9→21 级、体积误差多数 <13%）、回转体 revolve 9（FrontMidBody 等 9-22 面）；回退 19（弹簧拉紧器/间隔圈等复杂件，全部 valid、体积误差 ~0%）。待关注 2 件体积误差 ≥25%（14_FrontMidBody +25.6%、14_Front_Plate_Spacer -26.0%，疑回转体误判）。**用户 logo 资产 LOGO/ 已从仓库移除并入 gitignore**（不上传 GitHub）。
- v0.6.3：`STLs/` 目录与批量日志文件加入 gitignore（用户 STL 不入库）；清理临时日志。
- v0.6.4：**GUI 接入参数化重建 + 结果对比面板**——导出设置新增「参数化重建（优先：板件/回转体/体素）」，与「面拟合 analytic」互斥；转换完成后分析面板展示增强：「重建效果对比」区块——重建方式 kind、源网格面数 vs 实体面数与降幅 %、无效形状数、网格体积 vs STEP 体积误差 %（导入 `_on_done` 实时计算）。offscreen 冒烟构建通过，14 测试全绿。