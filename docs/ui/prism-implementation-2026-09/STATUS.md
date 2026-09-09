# 晴空棱镜代码实施检查点

更新：2026-09-10。这是进行中的实施记录，不是完整验收结论。

## 2026-09-10 学习、日报和聊天续修

本批基于已推送的 `edc0c50647d39f49e61f6c63ba92d44485ba6718`。以下证据补充并更新下方历史表中的对应待查项，不代替其余模块的完整验收。

| 页面/组件 | 实际修复与证据 | 当前图 |
|---|---|---|
| 日报 | 原日期行按钮超出视口；日期、辅助操作分行，保存/复制/删除固定底部。低高度下历史与编辑区不再把共享页面撑高。问题与计划编辑区最小120，保留今日完成优先伸展。窗口切换保留草稿，点击保存仍调用原保存方法；昨日计划、富文本和图片回归通过 | [864×520](daily-verified-864.png) |
| 学习 | 检索与分类移入260资料栏；16分隔；保留实际既有左右布局。搜索防抖、选择结果和拖拽分栏在布局变化后保留，搜索测试使用隔离样例 | [1144×740](learning-verified-1144.png) |
| 聊天 | 低高度时收缩可滚动消息区，输入框仍至少120；原风险提示不隐藏。25行样例的完整文本、内容高度、滚动和复制、未发送草稿、发送按钮可达均通过 | [864×520](chat-verified-864.png) |
| SQL/通用分隔 | 四SQL工作台上下/左右改16分隔；带prismGutter的垂直分隔改为16命中区域中的细线，避免原粗横条。实际控件测量横向宽16、垂直高16；日志/发版/数据库布局回归通过 | [Oracle 1440×740](oracle-verified-1440.png) |

最终代码下定向验证共51项通过：`tests.test_daily_report_upgrade` 12、`tests.test_p09a_delivery_panels` 6、`tests.test_p09b_database_panels` 5、`tests.test_splitter_prefs` 14、`tests.test_model_chat_bubbles` 10、`tests.test_prism_learning_layout` 1、`tests.test_prism_release_layout` 1、`tests.test_prism_log_layout` 1、`tests.test_prism_database_layout` 1。分别使用 `PYTHONUTF8=1 python scripts/diagnostics/prism_web_runtime.py --test-module <模块>` 在临时配置下运行，均退出0。

两个旧测试预期已按批准规格更新：日报不再要求所有按钮挤在同一行，改为可达性及真实保存回调验证；旧black主题在ThemeManager中必须归一到calm，不恢复双主题。纯调色函数的显式色板输入测试仍保留。

四张当前截图实际渲染尺寸均等于请求尺寸，未报告可见控件横向溢出。截图使用原生控件、临时配置和演示内容，无数据库/模型连接。`*-audit-before.png` 是故障对照。其余阶段图不因本次修复自动成为最新证据。

仍需完成：其他页面的原动作/长内容/状态矩阵、全部子窗口、整窗集成及DPI/性能要求。以上进展不代表整个UI目标完成。

当前基础提交：`80861f0bccba22eb1d36a3e09a497d8949ba3ebf`，分支 `ui/prism-v1`。用户已明确要求将本轮修复和交接材料提交并推送，故本次作为可读取的实施检查点交付。包含本记录的提交才是本次交付，完整 SHA 以 `git log -1 --format=%H -- docs/ui/prism-implementation-2026-09/STATUS.md` 为准；上述基础 SHA 不包含本次新增改动。此次发布不代表全套 UI 已通过最终视觉验收。

## 本次提交前复核（2026-09-09）

- 远端 `ui/prism-v1` 与基础提交一致，无需合并。
- 使用 `python scripts/diagnostics/prism_web_runtime.py --test-module <模块>` 隔离运行：`test_p09a_delivery_panels` 6项、`test_p09b_database_panels` 5项、`test_prism_sidebar_chrome` 11项、`test_visual_native_surfaces` 21项、`test_splitter_prefs` 14项、`test_prism_settings_navigation` 1项、`test_prism_compact_tools` 2项、`test_dashboard_release_monthly` 12项、`test_prism_database_layout` 1项、`test_redis_panel_layout` 2项、`test_prism_release_layout` 1项、`test_prism_log_layout` 1项，均退出0。模块名均以 `tests.` 为前缀。这些检查包含源码契约和实际控件检查，不全部属于视觉检查。
- 最新正式资源下，Windows 平台 `python -u scripts/diagnostics/prism_web_runtime.py --integration-tests` 3项通过。以上合计80项，不含更早批次的重复计数。
- 本次 `npm --prefix frontend run typecheck` 和 `verify:embedded` 均通过；正式资源已由上一轮 `build:embedded` 生成。
- 最新动效修复：Vue scoped 样式的完整后代选择器移入 `:global(...)`，避免编译后丢失后代部分。此前运行采样确认正常动画变化、隐藏时暂停、减弱动态时静止。三种样例视口864×520、1020×592、1440×740已取得无横向溢出的DOM记录，见同目录 `web-runtime-*.json`。
- 当前材料优先查看网页首页 `web-dashboard-1144-740.png`、三种尺寸的 `web-dashboard-*.png`、原生首页 `home-sample.png`、实际原生动效 `native-motion.gif`。其他图片与下表对应；带 before/current 的图片是历史对照，不能冒充最终效果。

业务边界：本次 `tools/` 仅调整接口排查的默认显示分栏比例；主窗口仅为原生首页快捷入口接入既有导航方法。原业务计算、数据库操作、SSH会话及生产启动策略不在本次修改中。完整状态覆盖和人工视觉确认仍按下文待验收清单执行。

## 本轮已经落实的代码与证据

| 展示面 | 当前改动 | 已取得证据 | 仍需检查 |
|---|---|---|---|
| 原生首页 | 左侧欢迎/统计/任务、右侧上线总览/工具；窄窗堆叠；短任务列表自然高度；快捷入口中英切换 | `home-sample.png`，实际1144×740；月份、完成同步及列表行为测试 | 窄窗、长文本、英文完整页面；动效运行证据 |
| 设置 | 180侧栏、六类分区、1040居中、窄窗选择器、固定保存操作 | 真实控件导航测试，保留输入、语言、保存按钮可见 | 所有分区最终截图 |
| 聊天、Agent、命令 | 初始侧栏与间距；保留用户分栏；聊天/Agent输入区高度 | 分栏基础行为回归；部分页面截图 | 最终截图、聊天长消息、上下文开关和窄窗 |
| SQL发版 | 左320上下文，右输入/预览；窄窗上下排列且页内滚动；导出固定底部；恢复窄窗清空/检查/预览入口 | `release-sql-after.png`，1144×740；`release-narrow.png`，680×740；两尺寸渲染回归通过 | 原按钮与字段交互、切换模式保留输入 |
| 接口排查 | 请求测试表单页内滚动；52/48默认比例；16分栏间距 | `interface-after.png`，1144×740，实际左右52/48 | 原有偏好及各页签交互、窄窗 |
| 日志排查 | 左260；服务器/抓取/目录/导出按钮重排；导出结果表自然高度 | `logs-after.png`、`logs-export.png`，1144×740；`logs-narrow.png`，680×740；三种隔离渲染均无控件横向溢出 | 长路径、样例结果、多会话保留与低高度 |
| Oracle/MySQL/OceanBase/达梦 | 结果/字段表局部最小高度修复，AI按钮两列排列 | 四引擎从936恢复740高；`oracle-1440.png`检查三列工作区 | 样例查询/对象状态、AI取消显示、窄窗、最终间距 |
| 接口文档 | 左侧文档选择/输出树，右侧编辑；修正文案 | `documents-after.png`，1144×740 | 最终文案截图、窄窗与文件选择回归 |
| Redis | 左240；局部表格最小高度修复；值区仍min280；命令区保留右下 | `redis-after.png`，1144×740；页签、输入保留、侧栏几何测试 | Key类型样例/长内容、低高度与窄窗 |
| MongoDB | 左240；过滤96；结果自然伸展；原Shell控件放入工作区页签 | `mongodb-after.png`、`mongodb-shell.png`，1144×740；页签、输入保留、侧栏几何测试 | 过滤96的最终截图；查询/JSON与Shell模拟回调验证 |
| 证件、VIN、加解密、格式化 | 小高度表格下限、VIN两行筛选、加解密/格式化页内滚动 | 864×520渲染；VIN指定条件不撑大窗口、参数继续进入原生成方法的测试通过 | 长文本、各格式页签、复制和导出动作复核 |
| Vue首页 | 浅色Hero与系统字体；常用工具两列最小宽修复；正式资源重建 | typecheck/build/verify通过；Windows实际绘制 `web-dashboard.png`；3项真实WebEngine集成测试通过 | 多宽度样例状态及网页动效证据 |
| 原生动效 | 使用既有首页棱镜、AuroraProgress、ThinkingIndicator真实动画 | `native-motion.gif`，60帧，各组件60个不同帧；隐藏后暂停；减弱动态下两次间隔180ms截图完全一致；40项加载状态检查通过 | 全局减弱动态设置与各业务调用位置的集成核对 |
| 悬浮框 | 复核既有展开与聊天展示，无本轮业务修改 | `floating-0.png`：52×52；`floating-1.png`：含阴影316×260，内容宽300；`floating-2.png`：含阴影356×456，内容340×440；无横向溢出 | 学习搜索、快捷入口、屏幕边缘锚点交互 |
| 后续AI协作 | 完整需求AI提示词，包含开发任务与提交后代码复核循环 | `../../project/AI_REQUIREMENTS_PROMPT.md` | 最终交付SHA及材料索引补齐 |

本目录 `*-before`、`*-current` 以及部分较早 `*-after` 图片是过程材料，不应一起作为最终验收截图。最终交付需清理/整理为明确的当前图集。所有新截图由 `scripts/diagnostics/prism_preview.py` 使用临时配置与数据生成，并禁止外部连接；样例任务和对话不是生产数据。该脚本关闭动画，所以 PNG 不证明动态效果。

## 最近运行的验证

续轮新增：`python -m unittest tests.test_prism_log_layout tests.test_splitter_prefs tests.test_prism_database_layout tests.test_dashboard_release_monthly -v`：28项通过，退出0。共享分栏修复了“构造期被最小宽度挤压后，实际显示仍沿用失真比例”的问题；新增回归在构造宽100、最终宽1144时验证保存52/48恢复正确。四SQL工作台修改后又跑14项分栏测试，通过。

本次继续验证：`python -m unittest tests.test_prism_loading tests.test_loading_feedback -v`：40项通过；`python -m unittest tests.test_prism_release_layout tests.test_prism_log_layout -v`：2项通过，含5个实际页面/尺寸场景。`scripts/diagnostics/prism_motion_preview.py`正常退出，使用Qt实际计时器采样并验证动态/暂停/静态三种行为。它不启动业务流程，也不伪造业务进度。

WebEngine诊断已取得新证据：单事件循环探针最初退出1是因测试使用 `QApplication([])`，Qt明确报告缺少程序名；探针修正为非空参数后可运行。offscreen仍有GPU绘制错误与旧集成测试访问冲突，因此实际绘制验收使用Windows平台，未修改生产启动、GPU或sandbox策略。绘制在pageReady后等待1000ms采样，早先150ms采样为空白，不作为有效图片。Windows真实截图现已取得。

`QT_QPA_PLATFORM=windows PYTHONUTF8=1 python -u -X faulthandler scripts/diagnostics/prism_web_runtime.py --integration-tests`：3项通过，退出0。测试运行前重定向配置常量和目录到临时目录、禁止Python外部连接。覆盖MainWindow+需求面板自然信号同步、navModel权威，以及生产HTML+WebChannel+Vue与DOM点击链路。

实际DOM探针曾发现常用工具按钮使文档宽1307超过视口1144；修复QuickTools网格minmax及按钮min-width后，最终文档宽与视口同为1144，溢出元素为空。截图还揭示欢迎区错误使用浓色品牌渐变及未声明全局字体，现已按浅色玻璃风格修复。

- `python -m unittest tests.test_dashboard_release_monthly tests.test_prism_settings_navigation tests.test_splitter_prefs -v`：26项通过，退出0。
- `python -m unittest tests.test_prism_database_layout tests.test_redis_panel_layout -v`：3项通过，退出0。覆盖实际窗口尺寸、页签切换、命令输入保留及240侧栏；不连接数据库。
- `npm --prefix frontend run typecheck`：通过。
- `npm --prefix frontend run build:embedded`：通过。Vite提示未来默认配置加载方式的兼容警告，本次构建未失败。
- `npm --prefix frontend run verify:embedded`：通过，双入口资源与WebChannel契约检查通过。
- `git diff --check`：通过；仅有Git换行转换提示。

这些结果不能证明全部22个叶子页面、子窗口、动效与业务合同已完成验收。

## 下一步按此收尾，不能缩减目标

1. 复核4种SQL工作台、需求、学习/日报、格式化、证件/VIN、命令、聊天和Agent的规格、原有动作与窄窗。未实际核验的模块保持待验收。共享分栏算法修复后，旧截图需按最终代码更新。
2. 补齐SQL分栏切换与输入保留、接口排查偏好与各页签交互；检查文档、数据库、日志页的长内容和空/错误/禁用状态。新增UI缺陷由本轮修复，不能推给后续AI当新需求。
3. 核对全局菜单、主窗、图标、弹窗、悬浮框和所有加载呈现。核对既有源码与V2.1中的尺寸、暂停、减弱动态、隐藏与销毁要求，补动态证据。
4. WebEngine的Windows绘制和3项集成检查已通过；继续验证网页多视口样例状态和动效。offscreen GPU异常作为测试环境限制记录，不改生产策略去绕过。
5. 最终以逐条需求矩阵审计代码、动作合同、运行证据和图集；检查后续AI提示词及索引；检查业务层未意外改动。
6. 按用户本次明确指令先发布实施检查点，后续完整验收仍需补齐上述证据。每次提交前fetch并核对远端；保留原有五份未跟踪阶段报告，不加入本任务提交。

原有五份报告：`scripts/p09a_fix1_report.txt`、`p09a_fix2_report.txt`、`p09b_fix1_report.txt`、`p09b_fix2_report.txt`、`p09b_report.txt`。
