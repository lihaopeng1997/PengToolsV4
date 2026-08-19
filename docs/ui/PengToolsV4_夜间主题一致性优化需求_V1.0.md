# PengToolsV4 夜间主题一致性优化需求 V1.0

## 目标

修复「夜间安读」主题中白色卡片、浅色 Loading、浅色风险标签与深色界面割裂的问题。夜间主题应是低眩光、低对比冲突、适合长时间工作的深墨绿灰界面；不是将浅色主题简单改成黑底。

本次只改视觉 token、绘制与控件状态，不改变任何业务数据、模块入口和功能行为。

## 视觉方向

- 页面底色：`#1B211E`，用于窗口空白和滚动区底层。
- 侧栏：`#222A26`，仅比页面底色亮一阶，保持导航边界清晰。
- 普通卡片：`#29332E`；悬浮/二级卡片：`#303B35`；禁止出现纯白卡片。
- 输入框和代码区：`#202823`，与卡片形成轻微内凹，不使用纯黑。
- 常规文本：主文本 `#EDF2EE`，次文本 `#BAC5BD`，提示文本 `#919E95`；避免大面积纯白文字。
- 品牌与选中：保留柔和鼠尾草绿，不使用高饱和荧光蓝；主色 `#9ABAA6`，选中底 `#35483E`。
- 分割线：`#3C4942`，仅负责结构提示，不能形成亮边框网格。

## Token 扩展

在 `ui/theme_manager.py` 为所有主题补充以下 token；浅色主题填入其语义对应色，夜间主题按下列数值实现：

| Token | 夜间值 | 用途 |
|---|---|---|
| `ELEVATED_SURFACE` | `#303B35` | 弹窗、悬浮卡、浮层主体 |
| `CODE_BG` | `#202823` | JSON/XML/SQL、命令和日志区域 |
| `OVERLAY_BG` | `rgba(9, 14, 11, 150)` | Loading、模态遮罩 |
| `INFO_BG` / `INFO_BORDER` | `#263B3D` / `#3B5D60` | 普通提示 |
| `SUCCESS_BG` / `SUCCESS_BORDER` | `#263D31` / `#4D765D` | 成功状态 |
| `WARNING_BG` / `WARNING_BORDER` | `#423923` / `#75633B` | 警告状态 |
| `DANGER_BG` / `DANGER_BORDER` | `#432E30` / `#765055` | 风险、删除和失败状态 |
| `SEARCH_MATCH` / `SEARCH_CURRENT` | `#4A5532` / `#68753D` | 搜索命中和当前命中 |
| `LOADING_TRACK` | `#425047` | Loading 轨道 |

`SURFACE`、`INPUT_BG`、`TABLE_ALT`、`DISABLED_BG` 也必须保持深色层级；任何 night token 均不得使用 `#FFFFFF`、`#FFF*` 或接近纯白的背景色。

## 必须修复的硬编码位置

1. `ui/aurora_progress.py`
   - 改为从 `ThemeManager` 读取 surface、overlay、状态和 loading token。
   - 取消当前白色渐变主体、浅蓝描边、浅色成功/失败胶囊；夜间 Loading 应为深色浮层与柔和主色进度。
   - Loading 继续是独立浮层，不能改变原页面按钮位置。

2. `resources/style.qss`
   - 将普通按钮的 `color: white` 换为语义 token，例如 `__ON_PRIMARY__`；为全部主题补齐该 token。
   - 将 `#FFF8F9`、`#FFF0F1`、`#F4C9CE`、`#FFF5E9` 等风险/提示硬编码替换为对应 token。
   - 补充 `QToolTip`、`QMenu`、`QDialog`、`QMessageBox`、`QCalendarWidget`、表头、空状态、滚动条、代码区在 night 下的完整背景/边框/文字状态。
   - 对禁用、hover、pressed、selected、focus 都使用 token，不允许回退系统白色样式。

3. `panels/requirement_panel.py`
   - 月份标题背景/前景以及升级标记颜色改用 token 或 palette，不能固定 `#F0F3FA`、`#1E2A44`、`#B24A24`。

4. `panels/personal_panel.py`、`ui/json_viewer.py`、`panels/requirement_panel.py`
   - 搜索命中颜色改用 `SEARCH_MATCH` / `SEARCH_CURRENT`，夜间状态下不得使用刺眼的亮黄。

5. `ui/confirm_dialog.py`
   - 图标 tint、卡片和按钮均使用主题 token；删除确认可保留红色语义，但背景必须使用 `DANGER_BG`，不可出现白底红框。

6. 继续全仓扫描 `#FFF`、`#FFFFFF`、`white`、`QColor('#...')`、`setStyleSheet`。
   - 所有运行 UI 的硬编码颜色均要逐项判断：品牌图标内部白色可保留；Excel 原始单元格样式和学习资料内容不改；其他应用 UI 全部 token 化。

## 设置页主题预览

- 夜间主题卡必须真实展示：页面底、侧栏、卡片、输入区、主按钮、正文和边框六层信息。
- 不得出现空白预览框。
- 主题名称改为「夜间安读」，副说明为「低眩光的深色工作界面」；仅改展示文案，持久化 key 仍是 `night`。
- 用户点击主题时即时预览并保存；切换主题不得改变布局、数据、文件路径或当前页面。

## 验收

1. 逐页切换夜间主题：工作台、需求管理、升级准备、格式工具、加解密、运维、设置、自我学习、日报、弹窗与 Loading。
2. 在任何页面不允许出现无业务意义的纯白/近白大块背景；表格内容、Excel/Word 原始文件预览例外。
3. 验证按钮、下拉框、日期控件、右键菜单、Tooltip、禁用态、焦点态、成功/警告/风险提示、搜索高亮均可读且协调。
4. 切回三套浅色主题，验证文字、主按钮与命令示例仍有足够对比度。
5. 仅运行主题相关定向测试；构建并启动 Private 版，标准安装包不得变化；更新 handoff、提交并推送 GitHub。
