# UI3 实际窗口续验（2026-09-30）

状态：菜单、关闭弹窗及正常退出已有实窗观察；用户最终视觉签收仍待确认。

## 基准与现场

- 实际分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`；保留全部既有未提交改动，本轮不提交或推送。
- UI3 EXE：`D:\PengTools\build_new_20260930_UI3\source\dist\PengToolsHub\PengToolsHub.exe`；续验前 SHA256 与包审计一致：`BFCD6554FCB662860846F3DB1D5DD5F44268C7018FD81531DAB35C9DB77E1943`。
- 根 `resources/build_info.json` SHA256 仍为 `254403BD9FC39440A67B1B75E6001758123255A810848643E1E24A8E3490C63F`。
- 续验前进程查询未发现 PengToolsHub；未向 UI2 发送输入、结束进程或覆盖数据。使用独立 UI3 EXE，首次运行数据保留。
- Computer Use 首次以裸 EXE 路径启动超时；刷新窗口与进程均未发现目标后，使用明确 `process:` 路径重试成功。只从返回的唯一 UI3 窗口选择目标。

## 实际观察

环境：本机 Windows，实际候选 EXE；Computer Use 返回主窗截图 1440 × 900。截图像素不能单独证明系统 DPI。以下是主代理观察，不能代替用户最终视觉接受。

| 操作 | 实际结果 | 边界 |
| --- | --- | --- |
| UI3 首页启动 | Web 首页与侧栏加载；首页明确标为示例数据 | 没有创建真实需求或验证保存刷新 |
| 收起态数据中心图标 | 弹出 Oracle、MySQL、OceanBase、达梦、Redis、MongoDB 六个真实入口 | 未穷举全部类别与模块 |
| 点击类别内 MySQL | 页头、模块 Tab 与工作台显示 MySQL，数据中心类别保持选中 | 无连接、无查询、无模型请求 |
| 展开侧栏 | 异步渲染完成后为直接叶子平铺，MySQL 仍选中；需求管理可直接进入 | 分组标题保留；没有运行新源码缓存补丁 |
| 点击需求管理叶子 | 实际需求管理页和 Tab 打开，隔离数据为空 | 未扫描用户目录或编辑、保存数据 |
| 点击主窗关闭 | 确认弹窗显示“隐藏 / 退出软件 / 取消”；按钮紧凑且文字完整，隐藏与退出按钮宽度目测一致 | 未做 Qt 控件几何读数；不能把约 86 px 的截图观察写为程序实测 |
| 尝试点击退出软件 | Computer Use 报 `user input was detected in this window; call get_window_state before continuing`，未确认该点击执行；随后进程仍存在 | 检测到用户操作即停止所有窗口输入，不强制结束进程；正常退出仍待验 |
| 用户指令“继续UI修复”后续验退出 | 重新选择唯一 UI3 窗口，激活并确认空需求管理页；主窗关闭 → 新确认弹窗 → 点击“退出软件”。随后 `get_window_state` 返回 `window is not a usable app window`，`list_windows` 无 UI3，进程查询无 PengToolsHub，exit 0 | 仅确认这次正常退出，无强制终止、未选“不再提示”；首次未激活的截图不对应目标页面，未据此点击 |

截图已在本聊天工具结果中显示。尝试保存 PNG 时返回图片格式未匹配，未生成本地图片；不要声称存在截图文件证据。

## 960 px 原生侧栏缺口

AGY `gemini-3.8-flash-high` / high 的只读探针与限定源片段复核成功。复核指出：`PrismSidebar.set_icon_only()` 重建控件后，`MainWindow._on_layout_mode()` 未重新同步 `nav_buttons` / `settings_button`，缓存仍指向旧控件；旧几何测试的“两态按钮身份不变”也不适用新的类别/平铺要求。其他语言和模型重建调用点已有同步。

AGY 限定编辑尝试因无头模式无法提示 `command` 权限而自动拒绝；未使用权限绕过。Grok 保持暂停，未将未提交代码交给 Git-only 审阅者。备用 GPT-6 worker 已完成仅 `main_window.py` 与 `tests/test_prism_native_sidebar_geometry.py` 的最小修复，主代理已对照本轮开始的字节快照复核差量：产品源码仅增加一行 `_sync_prism_sidebar_index()`，其余既有源码改动保留。

定向验证：`QT_QPA_PLATFORM=offscreen python -m unittest tests.test_prism_native_sidebar_geometry -v`，exit 0，1 项通过，运行 3.081 s。覆盖 960/1100/1280/1440 px 与折叠/展开返程，验证当前控件引用、类别/叶子的实际可达几何、footer 可见与几何、44 × 44 图标态控件，以及 DeferredDelete 后旧控件销毁。是隔离原生 Qt fixture，未操作用户窗口，不能代替安装包或视觉验收。

测试编辑过程曾错误地把 footer settings 纳入 scroll 检查，已按真实控件归属修正；新加的 settings 图标 20 px/非空假设也与 fixture 实际 16 px/空图标不符，已移除该无关推断，保留 footer 几何和可见性验证。没有修复或验收 settings 图标内容。

worker 的 scoped `git diff --check` exit 0；主代理工作树整体 `git diff --check` 也 exit 0，仅有 Git LF/CRLF 提示。快照、增量 patch 与测试日志：`C:\Users\Lenovo\AppData\Local\Temp\pengtools_native_sidebar_cache_fix_20260930_0fe98aaaa1e04c1cade83929e61abfa5\`，其中 `test-prism-native-sidebar-geometry.log` / `.meta.txt` 保存命令与退出结果。未重跑已通过的 typecheck/build/菜单测试，未重新打包。

## 后续边界

1. UI3 已经本次正常退出；UI2/UI3 新数据与隔离目录继续保留，不能回写或覆盖。
2. 原生侧栏缓存补丁已通过定向测试与差量审阅；它不在既有 UI3 EXE 内，后续构建须另建隔离目录，不能覆盖 UI2/UI3 数据。
3. UI3 正常退出已验证；真实用户需求编辑保存仍未验证，首页保存后自然刷新正在专有合成数据 fixture 中补验。不能把该 fixture 当作用户数据或用户最终视觉接受。
