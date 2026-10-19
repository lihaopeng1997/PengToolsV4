# Luna 自绘标题栏与顶部原型差距审计

日期：2026-09-15
执行者：Luna（只读审计 / 方案）
当前分支：ui/prism-v1
审计基线：1545e0c7459f3d79eea302bdcee6b513bd12ee41
当前状态：方案待主代理审核；用户已明确批准“改成原型的自绘标题栏，并验证窗口行为”。

本文件把原型中“窗口标题栏”和工作区顶部控件拆开审查。它用于给主代理审核独占文件、接线边界和验收证据，不代表生产代码已完成。本轮没有修改生产代码、窗口 flags、Bridge、业务数据或运行中的体验包。

## 1. 需求决定与边界

docs/ui/prism-implementation-2026-09/PRISM_SHELL_REVISION_2026-09-15.md 是本轮最新范围记录。用户的自绘标题栏决定覆盖 V2.1 中“保留系统非客户区、排除自绘标题栏”的旧条款；单主题 calm、混合技术栈、现有业务行为、单实例和 WebEngine 安全策略继续有效。

本次标题栏明确包含：

- 品牌区，以及原型中的置顶、最小化、最大化/还原、关闭按钮。
- 标题栏拖动、双击最大化/还原、边缘与四角缩放。
- Windows Snap、系统菜单、Alt+F4、最小窗口尺寸、DPI 和多屏负坐标验证。
- 关闭按钮调用现有 MainWindow.close()，继续经过 closeEvent、关闭确认、隐藏到托盘或退出清理。

本次标题栏不自动授权以下生产功能：

- 原型中用于演示的 File / View / Help 假菜单动作。
- 全局 Ctrl+K 搜索、通知中心或未读计数。
- 全局打开模块标签（顶部 Tabs）。它与模块内部的 QTabWidget 是两套概念，另由 Tab 组提交真实生命周期方案。
- 图标展板、组件展板、窗口展板、导出预览、设计版状态文字和演示托盘菜单。
- 改动 ui/bridge.py、ui/web_shell.py、frontend/src/shared/bridge.ts、单实例键、WebEngine 初始化策略、悬浮工具栏置顶设置或业务面板生命周期。

## 2. 已核对的源证据

### 原型

原型源文件是 docs/ui/concepts-2026-09/prism-suite/suite.js 与同目录样式。shell() 构造以下区域：

- titlebar：品牌按钮、文件/视图/帮助菜单、PRISM EDITION / DESIGN PREVIEW 字样、pin / minimize / maximize / close。
- globalbar：导航折叠、面包屑、全局搜索、通知、头像。
- open-tabs：由演示状态 S.open 渲染的全局模块标签。
- statusbar 及设计展板/演示托盘入口。

act() 和 navigate() 操作的是演示状态与 CSS class。它们不代表 PengToolsHub 的现有 QActions、面板缓存、业务请求或退出生命周期。例如：

- window-pin 只切换 S.windowPinned 和演示样式。
- window-minimize、window-close 进入预览专用的 fake restore screen。
- maximize/restore 只切换 window.is-maximized CSS class。
- notifications、export、design boards 和若干快捷键没有生产处理函数。

### 生产

已核对的真实实现如下：

- main_window.py：MainWindow 继承 QMainWindow；设置窗口标题、最小尺寸 960x640、初始尺寸 1440x900；现有中心布局是横向“侧栏 + 内容”；顶部 ContextHeader 固定 52px；底部为真实 QStatusBar 与时钟。
- main_window.py：closeEvent() 根据设置调用关闭确认；取消时 event.ignore()，选择最小化时 self.hide()，选择退出时进入 _shutdown()；exit_application() 设置 _force_exit 后调用 self.close()。
- main_window.py：现有 _toggle_nav_collapse()、toggle_quick_panel()、showMaximized()、showNormal() 可作为真实动作的接线目标；本文件不增加新的业务动作。
- main_window.py：_user_menu 是真实的版本/语言/悬浮栏/关于/用户指南/退出菜单，挂在侧栏 user_chip 上；当前主窗口没有独立标题栏菜单。
- ui/page_chrome.py：ContextHeader 只有当前模块图标/名称和快速面板入口，没有全局搜索、通知、标签或窗口按钮。
- ui/quick_panel.py：悬浮工具栏已经使用独立的 FramelessWindowHint | Tool | WindowStaysOnTopHint，有自己的置顶与拖动逻辑；它不应与主窗口标题栏的置顶混用。
- ui/tray_service.py：有真实托盘菜单“打开 PengTools / 展开悬浮工具栏 / 退出”，退出与恢复窗口流程不能被标题栏替换或绕过。
- frontend/src/shared/bridge.ts、ui/web_shell.py：现有 Bridge 没有窗口控制、菜单、标签、通知或头像 DTO/slot。本轮不扩展 Bridge。

旧规范中关于“不新增假菜单/通知”“系统边框暂保留”的背景见：

- docs/ui/concepts-2026-09/prism-suite/UI优化需求_晴空棱镜_Agent实施规范.md 第 2、5 节及自绘标题栏风险说明。
- docs/ui/prism-implementation-2026-09/ACCEPTANCE_MATRIX.md 的既有边界条目。

## 3. 原型控件逐项映射

下表区分“可以复用的真实动作”和“原型演示动作”。“本轮处理”只覆盖用户已经明确批准的自绘标题栏。

| 原型控件 | 生产现状 | 可复用真实入口 | 本轮建议 | 新范围/业务边界 |
| --- | --- | --- | --- | --- |
| 品牌 PengToolsHub | 现在没有跨窗口客户区品牌；侧栏 Vue/Legacy 各自有品牌 | APP_NAME、现有品牌资源和主题 token | 放入 PrismTitleBar，只读显示品牌；不显示 DESIGN PREVIEW | 视觉整合，不新增业务 |
| 品牌旁“软件菜单” | 原型打开 fake File 菜单 | 侧栏 user_chip 的真实 _user_menu 不能直接变成 File 菜单 | 标题栏第一版不新增菜单按钮；若产品决定合并菜单，另列动作评审 | 新的菜单位置与菜单分组，需主代理/菜单组确认 |
| 文件 | 原型动作：新任务、日报、导出当前预览、关闭预览 | 新需求可复用 MainWindow._on_web_create_requirement() / 现有需求面板添加入口；日报通过现有 open_daily_report/导航链路；关闭可用 self.close() | 不把 fake File 菜单写进标题栏；关闭按钮单独走 self.close() | 导出当前页没有全局真实入口；新 File 菜单需要明确权限、参数、语言和回调次数 |
| 视图 | 原型动作：折叠导航、显示悬浮工具、最大化/还原、打开设计展板 | _toggle_nav_collapse()、toggle_quick_panel()、showMaximized() / showNormal() | 标题栏只实现窗口动作；导航折叠/快速入口仍由现有真实控件和菜单组评估 | 展板是开发预览，不进入业务导航；新菜单需要单独范围 |
| 帮助 | 原型动作：快捷键、关于、指南 | _show_about()、_show_user_guide()；快捷键目前主要由现有 tooltip/事件提供 | 不新增快捷键对话框；后续若菜单组获准，可复用现有关于/指南 QActions | 新的快捷键帮助页属于新增功能 |
| 置顶 pin | 原型只有演示状态和 CSS 样式 | 主窗口当前无置顶设置；QuickPanel 有独立置顶设置 | 标题栏按钮临时切换主窗口 WindowStaysOnTopHint；只保存本次窗口会话状态，不写设置 | 这是用户明确批准的新窗口行为；持久化 pin、影响启动默认值或悬浮栏仍属新范围 |
| 最小化 | 原型 fake restore screen | Qt 顶层窗口 showMinimized() | 按钮直接调用主窗口现有 Qt 最小化行为 | 不改变托盘关闭策略 |
| 最大化/还原 | 原型只切 CSS class | isMaximized()、showMaximized()、showNormal() | 按钮根据当前状态切换；更新按钮 accessible name/tooltip | 不重写业务布局；最大化尺寸交给 Qt/Windows |
| 关闭 | 原型 fake restore screen | MainWindow.close() → closeEvent() → ask_close_action() / _shutdown() | 按钮只调用 self.close()；绝不直接 QApplication.quit()、_shutdown() 或设置 _force_exit | 保留确认、取消、最小化到托盘、清理和 Alt+F4 语义 |
| globalbar 折叠 | 原型调用 fake expand-nav 状态 | _toggle_nav_collapse() | 由菜单/后续接线复用真实方法；标题栏组件只预留可选 signal，不自造状态 | 位置是否放到客户区由菜单组确认 |
| globalbar 搜索 | 原型为 fake 全局搜索并标注 Ctrl+K | 现有 openPalette Bridge/快速面板链路和 Ctrl+Shift+P 悬浮入口；无全局 Ctrl+K 搜索 | 本轮不添加搜索；不把按钮改名为无效 Ctrl+K | 新搜索入口、索引、快捷键和权限需独立需求 |
| 通知 bell | 原型 fake notification popover | TrayService.show_notification() 仅是事件系统通知，没有中心、未读数或列表 | 不新增通知按钮、popover 或未读状态 | 新通知中心需数据来源、已读规则和持久化，明确超出 UI-only |
| avatar P | 原型 fake 头像 | 侧栏 user_chip 的真实用户菜单；用户名来自现有配置/运行上下文 | 可在后续布局方案中复用同一 QMenu，不复制退出/语言逻辑 | 标题栏放置头像需要菜单组与布局确认；不能伪造账户信息 |
| open-tabs | 原型 fake S.open | 现有主窗口通过 _show_panel() 切换固定面板；部分面板有内部 QTabWidget | 本轮标题栏不实现；Tab 组独立定义入口记录、关闭和离页生命周期 | 全局标签是产品行为，涉及面板缓存、离页代理、SQL/SSH 所有权和权限 |
| edition 文案 | 原型为 DESIGN PREVIEW | app_version_text、版本/构建信息 | 不显示演示文案；如保留小字，读取真实版本信息 | 不允许把体验包或设计状态当成产品状态 |
| 底部状态/展板/演示托盘 | 原型为演示区域 | 生产有 QStatusBar、时钟和 TrayService | 本轮保持真实状态栏与托盘 | 展板和 fake tray 不进入生产 |

## 4. 自绘标题栏的最小实施方案

### 4.1 独占文件

主代理审核通过后，Luna 只负责以下新文件及其测试：

1. ui/prism_titlebar.py
   - PrismTitleBar(QFrame)：品牌、pin、min、max/restore、close 的视觉结构。
   - 公共信号：pin_toggled(bool)、minimize_requested()、maximize_requested()、close_requested()；拖动双击可通过 drag_double_clicked() 或内部回调通知宿主。
   - 只依赖 Qt6、现有 semantic theme token 和现有图标按钮工厂；不导入 panels、业务服务或 Bridge。
   - 按钮具备稳定 objectName、accessible name、tooltip、键盘焦点和 enabled 规则；不把浏览器 aria 属性当成 Qt 行为合同。
   - 可放置纯函数/小型工具：窗口按钮状态、八向边缘枚举、鼠标位置到 resize edge 的映射。实际顶层 native message 仍由主窗口接线。
2. tests/test_prism_titlebar.py
   - 在 offscreen 平台验证结构、对象名、信号、状态切换、按钮点击回调、拖动区域与子按钮隔离、主题变化。
   - 不把 offscreen 模拟当成 Windows Snap、DPI 或真实系统菜单证据。

主代理负责以下已知接线文件：

- main_window.py：这是唯一需要改造顶层窗口、中央根布局、native event 处理和现有动作接线的生产文件，必须由主代理控制合并。
- 如 Qt 版本/平台验证显示 native event 适配不宜放在 main_window.py，再由主代理决定是否增加独立 ui/prism_window_frame.py；在审核前不预先创建第二套窗口宿主。

本轮不修改：

- frontend/src/**、resources/webui/vue/**、ui/bridge.py、ui/web_shell.py。
- navigation_model.py、业务 panel、SQL/SSH/接口会话、QuickPanel 的 flags/设置。
- run.py、单实例 PengToolsHub.Private、WebEngine 开关与安全策略。

### 4.2 主窗口布局接线

当前中心布局是横向工作区。自绘标题栏应成为顶层客户区的第一行：

    MainWindow client area
    ┌──────────────────────────────────────────────┐
    │ PrismTitleBar  (固定高度，跨侧栏和内容)      │
    ├──────────────────────────────────────────────┤
    │ 现有侧栏 │ 现有 content frame                │
    │          │ ContextHeader 52px                │
    │          │ page body / WebEngine / panels    │
    └──────────────────────────────────────────────┘

main_window.py 的最小接线应是把现有横向 shell 放入一个纵向根布局，再在最上方插入 PrismTitleBar。侧栏、ContextHeader、页面栈、状态栏高度与现有页面生命周期继续由原对象管理。标题栏不能插入 WebEngine 页面内部，也不能放入侧栏，否则会出现仅半窗显示、WebEngine 覆盖或折叠导航时客户区漂移。

标题栏高度先按原型视觉目标建立 semantic metric；最终值由主代理根据现有 layout_metrics.py 和窗口截图校准。不得在标题栏中写死物理像素来推导屏幕坐标。

### 4.3 Qt 拖动与缩放优先级

1. 标题栏非按钮区域按下时，优先调用顶层 windowHandle().startSystemMove()。
2. Qt 系统移动成功时，把移动交给窗口管理器，以保留 Windows Snap、贴边、跨屏和 DPI 处理。
3. 窗口边缘/四角按下时，优先调用 windowHandle().startSystemResize(edges)，使用 Qt 的 Qt.Edge 组合，不在 Python 中自行累计物理像素。
4. Qt API 返回失败或平台不支持时，才考虑最小的宿主 fallback。fallback 必须只在标题栏/边缘命中区生效，并且不能吞掉子控件事件；不能用未经验证的 Win32 坐标算法替代系统行为。
5. 双击标题栏空白区时切换最大化/还原；按钮区域的双击不能触发标题栏切换。

Qt 一手文档明确提供 QWindow.startSystemMove()、QWindow.startSystemResize()，并说明它们把交互交给系统；参考：

- [Qt 6 QWindow](https://doc.qt.io/qt-6/qwindow.html)
- [Qt 6 WindowType](https://doc.qt.io/qt-6/qt.html)

### 4.4 Windows native hit-test 边界

Windows 自绘客户区不能只靠 Qt 鼠标事件来保证系统体验。主代理集成 nativeEvent() 时应遵循以下顺序：

1. 对 WM_NCHITTEST 先调用 DwmDefWindowProc，让 DWM 处理标准 caption 行为。
2. 仅当 DWM 未处理时，按窗口客户区坐标返回：
   - 标题栏空白区：HTCAPTION；
   - 最大化/还原按钮：HTMAXBUTTON；
   - 最小化按钮：HTMINBUTTON；
   - 关闭按钮：HTCLOSE；
   - 八向边缘：HTLEFT、HTRIGHT、HTTOP、HTTOPLEFT、HTTOPRIGHT、HTBOTTOM、HTBOTTOMLEFT、HTBOTTOMRIGHT；
   - 其他区域：HTCLIENT。
3. 命中返回值必须使用屏幕 DPI 下的真实窗口矩形，不从设备像素硬编码标题栏高度。
4. 返回 HTMAXBUTTON 是 Windows 11 Snap Layouts 可见性的必要检查项；按钮必须有足够的实际命中面积，视觉图标与 hit-test 区域不可错位。
5. 不拦截 WM_SYSCOMMAND、WM_CLOSE、Alt+F4 或系统菜单。关闭最终仍进入 Qt closeEvent()。

Windows 一手文档：

- [Microsoft: Custom Window Frame Using DWM](https://learn.microsoft.com/en-us/windows/win32/dwm/customframe)
- [Microsoft: Apply Snap Layouts Menu](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/ui/apply-snap-layout-menu)
- [Microsoft: WM_NCHITTEST](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-nchittest)
- [Microsoft: About Windows](https://learn.microsoft.com/en-us/windows/win32/winmsg/about-windows)

自绘标题栏本身不要求全窗透明。为保护 WebEngine 与原生页面命中，本轮优先使用普通不透明客户区和现有主题 token；圆角玻璃、阴影和透明背景只有在窗口行为通过后再评估。

### 4.5 按钮到现有生命周期

| 按钮 | 目标调用 | 必须保持的行为 |
| --- | --- | --- |
| Pin | setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked) 后按 Qt 要求恢复可见/激活状态 | 只作用于主窗口；重复切换不丢失 normal geometry，不写新配置，不改 QuickPanel |
| Minimize | MainWindow.showMinimized() | 任务栏/托盘恢复有效；不调用 closeEvent，不清理业务会话 |
| Max/Restore | isMaximized() 分支调用 showNormal() 或 showMaximized() | 使用 Qt 保存的 normal geometry；最小尺寸继续为 960x640 |
| Close | MainWindow.close() | 经过现有确认；取消保持可见；选择最小化执行既有隐藏流程；选择退出进入 _shutdown |
| 标题栏拖动 | windowHandle().startSystemMove() | 支持系统贴边/Snap、多屏负坐标和 DPI；不改变页面导航 |
| 标题栏双击 | showNormal() / showMaximized() | 只响应空白标题栏；按钮点击不冒泡成窗口双击 |
| 边缘/四角 | windowHandle().startSystemResize(Qt.Edge...) | 不小于最小尺寸；内容与 WebEngine 命中不受影响 |

置顶的行为需要主代理在接线评审中确认一项实现细节：默认建议是“当前窗口会话临时状态”，因为当前产品没有主窗口置顶配置合同。若要跨重启保存，必须新增设置键、默认值、迁移和验收，不能在标题栏补写。

## 5. 验收矩阵

### 自动化/离屏证据

tests/test_prism_titlebar.py 应覆盖：

- PrismTitleBar 创建后所有五个窗口控件及品牌对象存在，objectName/accessible name/tooltip 稳定。
- pin checked 状态与 pin_toggled(bool) 一一对应；重复点击不会发出多余的业务回调。
- maximize 按钮在 normal/maximized 状态间切换显示文案和 accessible name。
- min/max/close 信号各只发一次；close 信号由宿主测试 double 调用 MainWindow.close()，组件不直接退出应用。
- 空白标题栏拖动不会被按钮、菜单或快捷入口吞掉；按钮按下不会触发 drag/double-click。
- 主题 token 更新后标题栏背景、边框、hover/pressed/disabled 状态可重绘。
- 组件未导入 panels、业务服务或 Bridge。

自动化只证明组件合同与可调用路径。它不能证明 Windows 非客户区命中、Snap、DPI 或物理多屏行为。

### Windows 实机证据

主代理集成后需要在隔离配置下记录以下实机结果：

1. 960x640 与 1440x900 窗口均显示完整标题栏；最小尺寸不能继续缩小。
2. normal → maximize → restore 后 normal geometry 与内容布局恢复。
3. pin 开关只影响主窗口；QuickPanel 的置顶行为不变；切换期间窗口不跳屏。
4. 标题栏空白区拖动可以跨屏；Windows 11 贴左/右/顶端出现 Snap；主窗口在负坐标屏可拖动和恢复。
5. 双击空白标题栏最大化/还原；按钮区域不会误触发。
6. 左右上下及四角缩放均工作，光标方向正确；内容控件仍可点击；窗口不低于 960x640。
7. 最大化时工作区、ContextHeader、页面栈、状态栏和 WebEngine 均可见；无客户区遮挡。
8. 系统菜单、任务栏恢复、Alt+F4 与标题栏关闭按钮都进入同一 closeEvent；关闭确认取消/最小化/退出三种结果分别符合现状。
9. 100%、125%、150%、200% DPI，以及运行中跨屏，按钮命中与边缘缩放不漂移。
10. 单实例第二次启动仍由既有单实例路径处理；WebEngine policy、Bridge 和 native fallback 路径未改变。
11. 关闭退出后托盘、热键、接口服务等由既有 _shutdown() 清理；不会重复执行清理。

建议测试命令（集成完成后由主代理执行）：

    python -m unittest tests.test_prism_titlebar -v
    python -m unittest tests.test_prism_main_shell tests.test_prism_native_sidebar_geometry -v
    git diff --check

现有 tests.test_prism_main_shell 中若仍断言“不能有 FramelessWindowHint”，该断言属于旧范围合同；主代理需按最新用户决定改写为行为合同，同时保留单实例、WebEngine 和业务布局断言。不要用删除测试来掩盖行为变化。

## 6. 风险、决策和停止条件

### 风险

- FramelessWindowHint 会改变 Qt 的非客户区、窗口矩形、系统按钮和部分平台默认行为；必须先验证窗口 flag 与 normalGeometry，再接入 WebEngine。
- 缺少 WM_NCHITTEST 的 HTMAXBUTTON 可能使 Windows 11 Snap Layouts 不出现。
- startSystemMove() / startSystemResize() 在某些平台或窗口状态下可能返回失败；fallback 必须有边界，不能引入未审查的 Win32 逻辑。
- 透明窗口、圆角和阴影可能改变 WebEngine 合成与命中；行为稳定前只做普通客户区样式。
- 主窗口 pin 可能影响任务栏/窗口切换；保持临时会话语义，实测后再决定是否持久化。
- 现有 Qt 对话框和 QuickPanel 需要继续保留自己的窗口 flags；标题栏改造只针对 MainWindow。

### 需要主代理审核的选择

1. PrismTitleBar 是否只显示品牌 + pin/min/max/close，还是同时放入已存在的导航折叠/快速入口。建议首版只做批准的窗口控件，避免把未定菜单和 globalbar 混入。
2. pin 是否确认采用临时会话状态。建议不新增设置键。
3. Qt startSystemMove/Resize 失败时是否接受仅 Windows 的最小 native fallback，还是先将该平台记为未验收。建议先实现 Qt 路径并保留可诊断的失败证据，由主代理决定 Win32 适配。
4. Windows 命中测试接线放在 MainWindow.nativeEvent()，还是由主代理新增独立 top-level frame subclass。组件组不预先改变继承关系。

### 停止条件

- 若接入后必须改变 Bridge、单实例、WebEngine 策略或业务面板生命周期，立即停在方案阶段，转为新需求评审。
- 若只能通过绕过 closeEvent() 才能关闭，视为失败，不提交。
- 若真实 Windows 证据无法覆盖 Snap、DPI、负坐标或系统菜单，报告未验收项，不用模拟证据宣称完成。
- 不操作当前用户正在查看的体验包，不覆盖 resources/build_info.json 或五份阶段报告，不把诊断文件纳入标题栏提交。

## 7. 结论与交接

当前生产与原型的主要差异确实存在：原型有跨窗口客户区标题栏及其五个窗口控制，而生产仍使用 QMainWindow 系统非客户区；原型的 globalbar、全局 Tabs、通知和演示菜单也没有对应生产合同。用户明确批准的可立即进入实施的范围是自绘标题栏及窗口行为验证。

下一步应由主代理确认本文件第 4 节的文件边界和第 6 节的四项选择。确认后，Luna 才新增 ui/prism_titlebar.py 与 tests/test_prism_titlebar.py；main_window.py 的顶层接线、window flags、native hit-test 和实机验证由主代理统一操作与复核。
