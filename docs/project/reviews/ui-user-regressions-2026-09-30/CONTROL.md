# 用户实际操作回归：已有文件夹导入与首页任务

用户在 UI5 验收时报告：选择已有文件夹后，需求管理不显示导入记录，并曾闪退；首页本月上线任务无法操作完成或勾选测试点。本轮以这些实际操作失败为验收目标，之前的布局完整性与合成保存测试不能代替它们。

## 现场与边界

- 实际工作区 `D:/PengTools`，分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。原未提交变化全部保留，不提交、推送、暂存、切分支或清理。
- 用户正在查看 UI5；不对其窗口执行输入、不终止进程、不操作真实需求或配置。全部复现使用 TEMP 合成文件夹、隔离存储与有界测试。
- 前置源码快照：`C:/Users/Lenovo/AppData/Local/Temp/pengtools-user-regressions-20260930-45b9a3e1c52b46a1b1249ee44f888837/before.json`，16 项输入原字节与哈希。13 个原保护文件继续使用前轮快照。
- AGY 必需 headless command 已有自动拒绝证据，不绕过权限；Grok 仍按用户要求暂停，Git-only 路线无法读取当前 dirty 源码。复用已授权 GPT-6 备用工作者，不新建用户可见聊天。

## 工作包

| 工作包 | 所有权 | 验收证据 | 当前状态 |
| --- | --- | --- | --- |
| 首页真实任务操作 | dashboard_completion | 生产 DOM 点击进入现有完成/测试点编辑器，实际勾选与取消勾选持久化，自然刷新，示例不可误当真实任务 | 示例提示与管理入口已改；合并冻结后 Windows QPA 两场景 2/2 通过，主代理检查最终示例及 checkbox 截图 |
| 已有文件夹导入 | module_completeness | 实际扫描按钮与 Qt QFileDialog → QThread → 合并保存 → 可见且选中的行；失败保留数据，事件循环存活 | 排队、扫描优先、最新选择合并、导入后清除阻挡筛选并定位已改；56 项定向测试通过，源码冻结 |
| 首页异常退出 | shell_completeness | 实际 HomeBridge 信号触发构造失败；进程与事件循环存活，成功路径保持 | 三个回调修复已接受；10 项定向测试通过 |
| 集成和交付 | 主代理 / shell_completeness | 前后差量、保护文件、冻结运行证据、新 UI6 隔离包及快照审计 | UI6 官方隔离构建 exit 0；主代理独立核对 258 输入、13 保护文件、EXE/ZIP 哈希和无 data/cache 归档 |

## 已知事实与未证明项

空需求存储会生成 `demo-req-*` 示例任务，Vue 隐藏完成/测试点动作并拒绝其 ID。首页现在明确提示示例不能编辑，并提供“去管理真实需求”。真实任务沿原有状态/测试点编辑区持久化，Bridge 不新增业务写入。前轮未覆盖已有点 checkbox 的缺口，本轮补上真实点击勾选及取消、持久化和自然刷新验证。

已复现两个独立缺陷：静默文件树 worker 运行时扫描按钮仍可点击，但原任务互斥检查会丢弃选中的目录；首页创建/打开需求时，构造失败后的再次构造或未保护构造会让异常逃出 Qt 回调，三个合成失败进程退出 `0xC0000409`。修复后扫描请求等待当前读取结束并优先执行，导入记录保存后清除搜索/系统/类型/状态筛选并选中；首页构造失败中止动作并显示错误。

扫描本地路径正常导入、过滤条件隐藏记录和后台忙时丢请求均有 TEMP 复现。没有收到用户选中的确切目录或当时错误文本，不能断言用户实际失败/闪退与上述合成条件完全相同。扫描自动回归使用真实 Qt `QFileDialog`（`DontUseNativeDialog`），与前置 Windows 系统对话框探针证据区分。新记录不从文件夹月份伪造实际上线日期；未填本月实际上线日期的记录可见于需求管理，但不会出现在首页本月真实任务列表。

## 差量与证据入口

- 产品源码仅本轮 `MonthlyTasks.vue`、`main_window.py` 三个首页回调、`panels/requirement_panel.py` 扫描排队/定位/结果提示及重新生成的嵌入资源发生变化。
- 16 项接手输入中，其余业务/Bridge/编辑器输入和既有扫描/保存/线程测试字节保持不变；live runtime 测试补上示例及 checkbox 覆盖，新增扫描运行和导航失败测试。
- [扫描验收](scan.md)、[首页验收](dashboard.md)、[构造失败验收](navigation-failure.md)。早期失败、测试条件与 TEMP 原始证据由各报告保留。
- 工作区保持 dirty、原分支和 HEAD；未提交、推送、暂存或复制用户数据。9 月 30 日最后只读核验 UI5 PID `9548`，旧 EXE 哈希匹配，无窗口输入；10 月 1 日续验前已无 PengToolsHub 进程。

## 最终验证与 UI6 交付

- 扫描/保存/精确定位/线程生命周期：56 项通过；真实 Qt 文件夹选择、扫描 QThread、TEMP 写盘、列表可见与选择的 6 个运行用例包含在内；Windows QPA 排队竞争补验通过。
- 首页构造失败及原动作/语言定向验证：10 项通过。受控失败条件的 Qt 信号进程由 `0xC0000409` 改为正常退出 0；不等于抓到了用户原始闪退堆栈。
- 最终冻结后的生产 Vue / MainWindow / RequirementPanel 集成：2/2 通过。示例 CTA 无假 ID 业务动作；真实状态保存、已有点勾选/取消及新增点自然刷新通过，checkbox 持久化 `true`/`false`，摘要 `1/1`/`0/1`，同一文档、返回导航零额外刷新；零外部请求、Python socket 连接或 renderer 终止。主代理已检查最终示例与 checked/unchecked 截图。
- 前端 typecheck、build:embedded、verify:embedded 通过；Python 编译和按仓库现有换行策略执行的 `git diff --check` 通过。
- UI6 从冻结的 258 项白名单生成新隔离源，不复制 data/用户配置/旧产物。主代理独立审计 live 输入哈希不匹配 0，隔离输入仅 `resources/build_info.json` 的 build_date/build_time 不同；13 个原保护文件不匹配 0。ZIP 共 2,068 项，data、bytecode/cache 项均为 0，dist/Installer EXE 哈希一致。详细官方构建与审计见 [ui6-package.md](ui6-package.md)。
- 新程序：[PengToolsHub.exe](D:/PengTools/build_new_20260930_UI6/source/dist/PengToolsHub/PengToolsHub.exe)，SHA-256 `4a0dbc1c278503cf3fa9f5b129c2efc0cd0f194d2a00c7ed6548e2d031ead7a9`。
- 完整程序包：[PengToolsHub_UI6_20260930.zip](D:/PengTools/build_new_20260930_UI6/PengToolsHub_UI6_20260930.zip)，SHA-256 `da6b27901a84d47035c86f8b8b37be0d2a70b5c75ca15e554d03fb4ca34ada94`。
- 9 月 30 日构建阶段未启动 UI6；10 月 1 日继续时旧版已经退出，已启动精确 UI6 程序并实际检查首页管理入口、需求管理和 Windows 文件夹选择器打开/取消后存活。UI6 PID `25856` 留在需求管理，`data/requirements.json` 尚不存在，新运行日志/配置保留；详见 [UI6 实窗续验](ui6-runtime-2026-10-01.md)。没有旧数据迁移，用户确切目录/真实 SVN、原始闪退同因与用户视觉签收仍待实测。

## 10 月 1 日追加：关闭弹窗整体缩小与 UI7

用户指出关闭弹窗仍太大，此前按钮收紧不能代替整个弹窗尺寸修复。已将 `CloseActionDialog` 目标宽度从 560 改为 400，减少边距/间距并将三个操作排列在同一行，按实际折行内容计算高度。相同文案 Windows QPA 测量由 560×211 改为 400×179；4 项定向回归通过，含 30 个中英文/字号/提示文案组合，保存动作返回与安全默认焦点保持。

UI7 已单次官方隔离构建 exit 0；258 输入、13 保护文件、EXE/ZIP 与无 data/cache 审计通过。已启动实际 UI7，并打开紧凑关闭弹窗供用户查看。UI7 是当前修复版本，旧候选和 data 全部保留，未提交/推送。新的源码差量、构建哈希、实际窗口证据与边界见 [关闭弹窗整体续修](close-dialog-2026-10-01.md)。
