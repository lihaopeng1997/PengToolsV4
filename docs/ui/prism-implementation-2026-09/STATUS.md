# 晴空棱镜代码实施检查点

更新：2026-09-11。这是进行中的实施记录，不是完整验收结论。

## 2026-09-11 格式工具、文档与设置逐页检查

本批基于 `44a9316fe1062a9d475c7ef0bad406de4c991840`。检查格式工具四页签、设置六分区和接口文档的960×640客户区，并直接修正发现的展示偏差：XML和SQL格式页的工具栏按实际按钮宽度换行，避免文字挤压；XML、文本辅助、接口文档编辑分栏统一16间隔和细线；Oracle客户端模式选择器加宽，完整显示原选项。未改转换算法、SQL执行/校验规则、文档更新流程或设置保存键。

隔离回归：`tests.test_prism_workbench_overflow` 7项通过，新增格式四页签宽窄切换保留长文本及SQL、工具栏完整按钮宽度/无重叠、XML原格式化回调、Base64编码解码还原原文、文档SQL与作者保留及原多文件选择回调；`tests.test_prism_settings_navigation` 1项通过，遍历六分区并检查修改值/固定保存入口；`tests.test_theme_format_tools` 7项通过；`tests.test_xml_formatter` 12项通过。合计27项，均退出0。

主窗口探针新增 `--tab`，记录请求页签与实际页签并校验一致。当前图包括 [接口文档](shell/nav-3-960-640.png)、格式工具 [JSON](shell/nav-11-960-640.png)/[XML](shell/nav-11-960-640-tab-1.png)/[SQL](shell/nav-11-960-640-tab-2.png)/[文本辅助](shell/nav-11-960-640-tab-3.png)，以及设置 [外观](shell/nav-7-960-640.png)/[悬浮工具栏](shell/nav-7-960-640-tab-1.png)/[提醒](shell/nav-7-960-640-tab-2.png)/[安全](shell/nav-7-960-640-tab-3.png)/[Oracle](shell/nav-7-960-640-tab-4.png)/[模型](shell/nav-7-960-640-tab-5.png)。原生页面区域已逐图查看。

证据边界：部分Qt客户区抓图中WebEngine侧栏呈白色或仍显示首页选中态，即使JSON renderer为web；这些图只支持本轮原生页面布局核对，不能当成侧栏绘制和选中同步通过的证据。延长导航后采样到1000ms未彻底消除，后续需要区分采样限制与真实绘制/同步问题。没有因此修改生产WebEngine策略、导航或Bridge契约。设置模型列表和时间控件的视觉一致性仍待细查；各页长内容、错误/禁用状态及全DPI/性能验收仍按完整目标继续。

## 2026-09-10 发版验证适配问题已解决

本批基于 `3101f58b8f7fc346b1671118d69f66d4987cd20b`，只改测试和隔离诊断，不改正式业务源码。上一节历史记录中的两项未通过结果由本节更新：

- SQL导入按钮的固定两级parent断言改为检查按钮确实属于原SQL页签和滚动工作区；增加点击按钮到原多文件选择器的回调检查，模拟取消，不打开或导入真实文件。页签顺序、日期候选自动加载及生成前日期同步仍按原用例验证。
- 诊断的 `local_data_dir` 替代函数接受原参数。默认应用路径仍指向临时目录；显式exe路径参数交给原函数进行纯路径计算（已检查原实现不访问文件系统）。升级测试继续检查同目录不同exe共用data，并增加不同安装目录不应得到同一路径的反例，避免把恒定临时路径误当成升级兼容证明。

最终执行 `QT_QPA_PLATFORM=windows PYTHONUTF8=1 python -u scripts/diagnostics/prism_web_runtime.py --test-module tests.test_release_ui`：28项通过，退出0，无跳过。初次追加回调检查误用了单文件选择器补丁，已停止该独立测试进程并改为原代码实际调用的多文件选择器后重新运行；不采用中断运行作为通过证据。数据路径、SQL导入、候选加载和发版生成的正式实现保持不变。完整UI的其余视觉、DPI及性能验收仍未完成。

## 2026-09-10 Agent、命令库与需求目录续修

本批基于 `49105c915af257d71a25f56a9934ba5c558c536a`。检查960×640真实主窗口后修正以下展示偏差：

- 新创建页面和侧栏折叠时沿用 `LayoutModeController.low_height`，不再固定传入False。低高度下打开Agent等页面可立即应用已有紧凑规则；只修正展示状态传递，不改变导航索引、面板创建顺序或启动策略。
- Agent输入区最小高度由100调整为规格120，消息/输入与文件树/预览两个垂直分栏采用16间隔及细线呈现。项目文件开关和原草稿保持。
- 命令库预览区由125恢复规格240，仍在既有右侧滚动区中；原固定底部复制入口可见，生成/复制业务未改。
- 需求目录全选、删除、提签、展开、折叠的原控件按可用宽度换行，消除窄栏按钮重叠；目录/详情分隔统一16。工作区增加无边框滚动容器，低高度时详情和文件区内容完整可达，不依赖窗口外的裁切区域。

最新真实主窗口图：[Agent](shell/nav-17-960-640.png)、[命令库](shell/nav-6-960-640.png)、[需求管理](shell/nav-10-960-640.png)，均960×640逻辑客户区，探针退出0。Agent和需求图已检查对应修复；截图仅展示当前滚动位置，不是整个内容拼图。

隔离验证：`tests.test_prism_workbench_overflow` 5项通过（包括新增的Agent输入/项目文件开关保留草稿、需求目录按钮不重叠与原展开/折叠行为、命令预览高度及复制入口可达）；`tests.test_prism_main_shell` 19项通过（新增低高度创建页面、侧栏折叠和恢复大窗）；`tests.test_agent_workbench_layout` 10项通过。

`tests.test_release_ui` 实际运行28项，26项通过、1项失败、1项错误，不能报告整组通过：`test_release_page_is_first_and_date_auto_loads_candidates`仍直接断言SQL页面按钮两级parent等于页签，与已有滚动容器层级不符；`test_upgrade_reuses_data_directory_and_accepts_legacy_requirement`对local_data_dir传入两个参数，与隔离脚本的无参替代函数不兼容。本批未修改SQL发版源文件或这份测试文件，已用Git确认二者与基线相同。未通过项保留记录，未更改正式数据目录逻辑来让测试通过。合计本批60项通过、2项未通过；无真实模型执行、SVN提交或生产数据操作。

## 2026-09-10 工作台窄窗重叠修复

本批基于 `168dce13805b0ce31a3baa69f9a7b57cdcce27c0`。真实960×640主窗口检查发现：SQL连接栏虽然未撑大窗口，固定宽连接选择器与目标提示/按钮仍发生重叠；接口排查上下布局的空列表提示越过左侧容器，压住详情页签。宽窗切回窄窗的SQL定向测试还确认工作区最小高度将独立页面从528撑到764。

- 四种SQL工作台的连接工具栏使用按控件实际尺寸换行的布局。保留原控件、信号绑定、显隐和启用状态；长连接提示允许换行。工作区放入无边框滚动容器，高度不足时保留完整编辑器/结果区及原分栏。
- 接口排查保留原上下/左右分栏和原请求测试页签，在上下布局时按子区域实际最小高度为工作区留空间，由外层滚动容器承担低高度滚动，空列表提示不再覆盖详情页签。恢复宽窗时清除上下布局专用最小高度。
- 主窗口诊断改为等待WebChannel就绪后再导航，避免截图中页面已切换但初始化侧栏仍选中首页。此修改仅在诊断脚本，不改变正式导航或启动策略。

当前图：[SQL 960×640](shell/nav-18-960-640.png)、[SQL 1440×900](shell/nav-18-1440-900.png)、[接口排查960×640](shell/nav-12-960-640.png)、[聊天960×640](shell/nav-16-960-640.png)。低高度的工作区需要滚动，截图不是完整滚动内容的拼接。SQL与接口截图已检查对应重叠问题；聊天本轮仅补主窗口运行记录。

定向验证均使用临时配置隔离运行：`tests.test_prism_workbench_overflow` 2项通过（四方言、中英文、13/16字体、宽窄切换、控件边界与相互重叠、SQL输入保留和原测试连接回调、接口请求体保留与滚动可达）；`tests.test_sql_workbench_query` 21项通过；`tests.test_interface_fiddler_workbench` 31项通过；`tests.test_splitter_prefs` 14项通过。合计68项通过，均退出0。四份最新主窗口探针退出0。

未连接实际数据库或启动实际抓包。本批没有更改查询、连接、过滤、监听、代理、分页或数据保存逻辑。其余页面状态、全部子窗口、DPI和性能证据仍按后文清单继续，不以此宣布完整UI验收。

## 2026-09-10 主窗口组合预览检查点

本批基于 `836321d5c21fbbbc85ed974f7e378ad1865037c1`，只补充隔离诊断与截图，不改正式软件代码。`scripts/diagnostics/prism_web_runtime.py --shell` 使用真实 MainWindow、正式 WebEngine 侧栏和首页；配置重定向到临时目录，禁用本次诊断进程的托盘/全局快捷键服务及 Python 外部连接。首页数据为明确标记的示例数据。

| 逻辑窗口尺寸 | 侧栏初始偏好 | 页面实际尺寸 | 截图 |
|---|---|---|---|
| 1440×900 | 展开 | 1144×772 | [整体预览](shell/nav-0-1440-900.png) |
| 1440×900 | 折叠 | 1308×772 | [折叠预览](shell/nav-0-1440-900-collapsed.png) |
| 1280×800 | 展开 | 1020×680 | [整体预览](shell/nav-0-1280-800.png) |
| 1100×720 | 展开，窄窗自动收缩 | 984×608 | [整体预览](shell/nav-0-1100-720.png) |
| 960×640 | 展开，窄窗自动收缩 | 856×528 | [整体预览](shell/nav-0-960-640.png) |

五份 JSON 均记录窗口未被内部最小尺寸撑大、当前导航为首页、侧栏和首页 renderer 均为 web、上下文栏52和状态栏28。`collapsed` 字段表示传入的初始偏好，不代表窄窗最终有效侧栏宽度。截图为 Qt 客户区抓图，像素尺寸受当前屏幕缩放影响，不证明其他 DPI 或系统非客户区效果。已查看1440展开及960截图；其余截图已生成，仍需视觉核对。

本轮重新执行隔离的 `tests.test_prism_main_shell`：18项通过，退出0。1440展开/折叠、1280、1100的真实主窗口探针均退出0；960已有相同格式的运行记录。未执行真实数据库、网络连接或完整业务流程，未以本次首页组合检查替代全部页面验收。

## 2026-09-10 子窗口与消息显示续修

本批基于 `2a9cb230f286c1d6ed78aa5ae985d0835b6e2b23`。可打开 [子窗口检查图集](dialogs/index.html)，在13/16像素全局字体设置间切换，点击查看原图。

- 修复确认框在布局建立前按空sizeHint确定180高度，导致短正文被压缩的问题。现在按实际带样式的文字高度确定初始大小；超长内容进入滚动区，固定取消与确认按钮不被挤走。
- 通知与后续操作窗口采用相同的长正文保留/滚动策略；原 accepted/rejected、确认默认焦点、Esc取消、selected_action返回值未改。回归验证了长正文、实际可滚动范围、操作按钮与返回结果。
- 修复确认/标准弹窗按钮的QSS最小高度叠加padding后超过32的问题；实际带主题渲染的按钮尺寸检查通过。
- 分类、技能、对象/字段选择、提签配置与需求列表采用统一白色圆角列表、选中底色与行间距。只增加展示属性和QSS，不改列表数据、选择规则或回调。

`scripts/diagnostics/prism_dialog_preview.py` 在临时配置和禁止Python外部连接的条件下生成真实Qt客户区截图；显式装入本机中文字体。模拟可用屏960×640，最终24类窗口加3个长文场景，各以13/16像素字体运行，共54份PNG及对应JSON。全部退出0，尺寸未超过可用屏减48，固定按钮未越过窗口或父容器；这不代表所有滚动内容、业务状态、真实多显示器和DPI已经验收。渲染探针不执行提交、连接、检出或删除。

定向测试使用 `PYTHONUTF8=1 python scripts/diagnostics/prism_web_runtime.py --test-module <模块>`：`tests.test_prism_dialogs` 13项通过；`tests.test_connection_dialog` 13项通过；`tests.test_ai_object_tokens` 7项通过；`tests.test_ticket_submit` 11项通过、2项跳过（临时配置下无ECIF样例签）。合计44项通过、2项跳过，不将跳过项计为通过。不使用生产模板补齐测试。

图集覆盖各类初始状态，连接认证分支和对象选择由上述定向测试补充部分交互证据。整窗集成、其余状态和附录尚未覆盖子窗仍在完整目标内，未宣布全UI验收完成。

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
