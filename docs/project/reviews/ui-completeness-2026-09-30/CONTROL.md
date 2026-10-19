# UI 重构完整性收尾（2026-09-30）

用户当前指令：继续完成 UI 重构任务，保证 UI 的完整。本轮按现有功能可达、真实保存刷新、统一公共控件及响应式完整性收尾，不增加或改写其他模块业务。

当前结论：本轮发现的 UI 缺口已修复并完成定向复核，UI5 已按冻结源码构建、通过快照审计并完成生产包启动、两态菜单导航和点击正常退出观察。交付包为 [PengToolsHub_UI5_20260930.zip](D:/PengTools/build_new_20260930_UI5/PengToolsHub_UI5_20260930.zip)。用户最终视觉签收和下述实际环境验收边界仍保留。

## 基准与边界

- 本地 `D:\PengTools`，实际分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。现有工作区刻意保留未提交变更，当前源码比 HEAD 更晚；Git-only 复核无法覆盖它。
- 以根 AGENTS、项目入口、V2.1 单主题 calm 规格及当前源码为依据。旧验收矩阵属于历史基准，不直接当成本轮完成证据。
- 保留所有既有改动、用户数据、配置和 UI/UI2/UI3/UI4/UI4_Final/UI5 隔离目录，包括候选包运行后产生的数据；不提交、暂存、推送、切分支或清理。无新用户可见聊天。
- AGY headless 编辑因必要 command 权限自动拒绝，既有具体失败证据有效，不绕过权限；Grok 按用户要求暂停。本轮本地 GPT-6 Luna/max 备用工作者承接可控隔离验证。没有向 Git-only Web 评审发送未提交源码。

## 分工与验收

| 工作包 | 所有权 | 必需证据 | 状态 |
| --- | --- | --- | --- |
| 首页完整链路 | dashboard_completion；新 test 与 dashboard-live.md，TEMP | 生产 WebEngine DOM → 精确需求编辑/测试点保存 → 原刷新信号 → 同页自然更新；合成数据隔离 | 已通过，主代理已审阅 JSON/截图；每次保存后 1 个自然刷新、返首页 0 个额外刷新 |
| 22 个模块 | module_completeness；diagnostic 与 modules.md，TEMP | 当前真实路由、实际页面构造、主要动作可达与窄布局；逐项标清静态/运行边界 | Windows QPA 的 22 个入口 × 四档尺寸通过；最后 Gateway 单文件增量另补 zh/en × 字号设置 12/16 × 四档尺寸，零内部按钮 fit/路径重叠失败 |
| 公共 UI | shell_completeness；test 与 shell-dialogs.md，TEMP | 窗口/侧栏/弹框/浮窗/加载状态现有契约、公共 UI 定向测试、具体缺口 | 独立定向套件已通过；取消修复回归 54/54；组合 Qt 进程中止及旧源码断言失败另行记录 |
| 原生重建瞬间 | dashboard_completion；PrismSidebar 与 retirement test/report | 延迟销毁前旧控件立即隐藏；新控件可见可点、正常延迟销毁，不改复用页脚身份 | 已修复并复核；相关三个套件 8/8 |
| Redis/Mongo 等待反馈 | dashboard_completion；两 panel 与 QueryStatusBand、独立 test/report | 现有 worker 回调、重叠任务、错误、窄宽度与字号变化；TEMP 合成结果 | 最终隔离集成通过；固定宽度 12→16→12 省略显示回归通过，无 socket 尝试 |
| 集成与交付 | 主代理 | 审阅具体增量；缺陷分文件授权修复；最终差量/验证/交接；独立新候选包 | UI5 官方构建 exit 0，258 项快照与 13 个保护文件审计通过；实窗首页、展开直达、收起分类、Docx/Gateway 及点击正常退出已观察，见 ui5-runtime.md |

各工作者初始只可新增自己拥有的验证/报告，不编辑产品。发现真实缺陷后由主代理明确分配产品文件；同一文件串行。已明确分配 `ui/aurora_progress.py` + `AiWorkbenchPanel._cancel_scan` 的取消状态呈现修复，以及 `PrismSidebar._clear_layout` 的旧控件立即隐藏修复。主代理不将构造 fixture、静态枚举或旧截图当成模块完整交互验收。

模块最初 offscreen 渲染缺少 CJK 字体，不能作为中文文字宽度/视觉证据；Windows QPA 使用现有 Microsoft YaHei UI，已观察实际 150% DPR。旧控件待 DeferredDelete 的重叠图与正常事件循环后的稳态图分别保留。公共控件组合进程曾原生中止，后续采用逐模块独立进程分辨失败；不把没有 unittest 汇总的组合运行记作通过。

## 既有有效增量

- 原生侧栏重建后同步 MainWindow 缓存；四档宽度往返的定向测试通过。
- 设置叶子仅原生 role `gear` → `settings` 适配；三态真实点击与图标定向测试通过。
- UI3 实窗菜单、关闭弹窗、正常退出已观察；全部本轮修复由独立 UI5 候选包承载，旧候选保留。

## 本轮局部修复与验证入口

| 修复 | 当前文件 | 定向证据 |
| --- | --- | --- |
| 侧栏旧控件重建后立即隐藏，保留正常延迟销毁 | `ui/prism_sidebar.py` | [sidebar-retirement.md](sidebar-retirement.md)，8/8；此前设置图标和 MainWindow 缓存同步补丁保留 |
| 扫描取消显示中性终态，退休旧 token 与动画 | `ui/aurora_progress.py`、`panels/ai_workbench_panel.py` 取消呈现调用 | [shell-dialogs.md](shell-dialogs.md)，6/6 独立、54/54 定向组合；业务取消 flag 不变 |
| Redis/Mongo 查询与刷新沿原 worker 显示等待/成功/失败，Redis 同时运行任务不会漏提示 | `panels/db_redis_panel.py`、`panels/db_mongodb_panel.py` | [nosql-loading.md](nosql-loading.md)，真实 panel 与合成 worker 回调隔离集成通过 |
| 长错误保留全文，按实际 label 宽度和字号省略 | `ui/query_status_band.py` | 同一报告；完整 API、tooltip、accessibility 保留，固定宽度字体往返通过 |
| 文档路径行和网关结果区保留布局实际最小高度；文档英文大字号标题完整换行；网关横向工具栏按操作行所需宽度分配空间 | `panels/docx_panel.py`、`panels/gateway_panel.py` | [modules.md](modules.md)，7/7 定向测试，四档尺寸与语言/字体往返，真实合成复制/折叠/清空回调；`ui/json_viewer.py` 原字节不变 |
| 默认英文启动时首次创建页面，首页按钮立即跟随当前语言 | `main_window.py` 的 `_apply_panel_chrome` | [lazy-page-home-locale.md](lazy-page-home-locale.md)，修复前真实 Docx 路由失败，修复后 Docx/Gateway、语言往返和单次真实导航点击通过 |

首页使用生产 Vue bundle 和真实保存信号，详见 [dashboard-live.md](dashboard-live.md)。本轮没有再编辑 Vue 源码或 bundle；已有 typecheck、embedded build/verify 证据沿用。模块构造检查关闭真实服务启动与页面激活副作用，记录 button 连接/菜单/Tab/overflow 与滚动可达，不代表每个业务动作的联网执行通过。

## 最终复核补充

UI4 已在当时冻结的 258 项源码快照上构建成功，零 live-source 变化，13 个保护文件匹配，ZIP 2,068 项且无 data。随后的英文大字号截图复核发现真实冷启动语言缺口：已设为英文时首次创建的页面 header home 按钮仍沿默认中文；显式切换语言才会修正。该缺口已以 MainWindow 公共呈现同步的五行增量修复，冷启动回归通过；不改变导航、设置写入或业务。最新 MainWindow SHA-256 为 `C21AE9A4913683A32A25E040A476386DF80F7E2CE6CFC8307E843C311C43F4E0`，UI4 不包含这最后一项。

原诊断 driver 的 `qt_platform` 字段曾硬编码为 offscreen；已改为记录实际 `QApplication.platformName()`、字体与 DPR，旧报告保留，不用于最终平台结论。语言修复后的 Windows 四档页面证据已生成。随后对 Gateway 英文字号设置 12/16 的截图作文字/图标复核，发现横向两栏布局会把 JSON 工具栏压到 72 DIP，路径输入与操作重叠，部分按钮文字截断。祖先几何裁切为零不能证明按钮内部文字完整；该缺口已在 Gateway 容器局部修复，7/7 定向测试与四组语言/字号的四档 Windows 补验通过。字号设置 12/16 对应实际 QApplication 字体 10/14 pt，不能写成 12/16 pt。

UI4_Final 的 258 项快照、保护文件和无 data ZIP 审计已通过。桌面工具三次裸路径启动请求超时均未产生进程；项目已有自动退出 smoke 入口实际启动后，主代理观察到生产 Web Chrome/dashboard 正常显示（1440×900）。该首轮 PowerShell GUI 启动未取得子进程退出码，之后窗口/进程已消失，不据此声称点击退出或退出码通过。UI4_Final 新产生的独立 data/logs 保留。

最终 UI5 使用新隔离目录、同一运行时白名单和原官方构建脚本，258 项输入与 live source 一致，仅隔离 build_info 由构建器盖时间戳。ZIP 有 2,068 项，data 和 bytecode/cache 路径均为零；依赖检查、配置范围的资源扫描与构建通过。包和快照审计见 [ui4-package.md](ui4-package.md)。UI5 使用桌面工具实际支持的 `process:` 应用标识启动成功；主代理观察生产首页、展开菜单直达 Docx、收起开发分类进入 Gateway，最后点击关闭弹窗“退出软件”，窗口清单为空且 PengToolsHub 进程数为 0。该观察没有 OS 子进程退出码，也不是用户视觉签收，详见 [ui5-runtime.md](ui5-runtime.md)。

交付前再次核对：实际分支/完整 HEAD 未变，258 个 live input 和 257 个非时间戳复制文件零哈希差异，13 个保护文件零差异，dist/Installer EXE 哈希相同且 ZIP 哈希匹配报告。`git diff --check` exit 0，仅有 36 项既有 LF/CRLF 规范化提示；本轮主代理新增/编辑交接记录无尾部空白。工作区仍保留原 dirty 变化，没有提交、暂存或推送。

## 仍需区分的验证边界

物理多屏、负坐标、真实 Windows DPI/透明度与用户最终视觉签收不由 offscreen fixture 推断。真实 DB/SSH/AI 服务、生产需求数据不用于本轮合成验证。Redis/Mongo AI 等待线程方案及 SQL 原结果清空行为不因 UI 收尾而自动获得业务修改授权。
