# 编程 Agent 工作台实现记录

状态：本工作台包已完成定向实现与本地合成验证；Agent 重构整体仍在实现中，本文不代表数据中心或完整项目验收完成。

## 基准与范围

- 仓库：`D:/PengTools`，分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。
- 独占改动：`panels/agent_workbench_panel.py`、`tests/test_agent_workbench_layout.py`、`tests/test_agent_workbench_runtime.py`、`tests/test_agent_application_shutdown.py`、`main_window.py` 的真实退出清理接缝，以及本报告。
- `main_window.py` 仅新增已实例化 Agent 面板的异步退出轮询；托盘隐藏路径、应用启动和面板所有权未改。
- 测试把 Agent store、设置和 splitter 读写都指向 TEMP/桩函数。没有读写真实 data/config/credentials/session，也没有操作正在运行的 UI7。未暂存、提交或推送。仓库其他 dirty 项保留原样。

## 实现

- 工作台任务捕获提交时的 workspace、conversation、真实目录、模型 profile 和取消令牌。附件只保留工作区内路径引用，提示模型先调用真实 `read_file`；文件树预览也经 Agent runtime 的路径校验和读取工具。
- Worker 复用 `run_agent_loop` 兼容接口与共用取消令牌。确认桥用可取消 `threading.Event` 与 GUI queued signal，取消后即拒绝；活动确认引用独立于等待响应表，覆盖对话框刚打开、第一轮 40ms watcher 尚未触发的窗口期。长差异继续交给滚动确认对话框完整显示。
- `QThread.finished` 使用独立完成槽而非重名信号；worker 引用只在 Qt 报告线程结束后释放。停止、关窗、换工作区/对话会取消任务并使迟到的消息与结果失效；异步 `agent_shutdown_cleanup() -> bool` 不等待、不 terminate。
- 流式预览按 40ms 批次刷新并限制队列/文本缓冲；每轮模型开始时清理上一轮暂存；正文与摘要分区显示、摘要不入历史，最终回答只持久化一次。上滚阅读时不强拉到底。
- 长绑定路径不再撑大最小宽度；可见标签明确用省略号表示路径前段已收起，只显示末段，完整目录保留在 tooltip。执行模式框按当前语言最长选项文本测量并额外留出箭头、边框和留白，不再把英文选项截成半句。发送提示按实际换行高度调整 composer 最小高度，避免窄屏覆盖输入框；动作按钮根据当前语言保持文本所需宽度。

## 验证

- `python -m unittest tests.test_agent_workbench_layout tests.test_agent_workbench_runtime tests.test_agent_application_shutdown -v`：20 项通过。
- `python -m py_compile panels/agent_workbench_panel.py main_window.py tests/test_agent_workbench_layout.py tests/test_agent_workbench_runtime.py tests/test_agent_application_shutdown.py`：通过。
- `git diff --check -- main_window.py panels/agent_workbench_panel.py tests/test_agent_workbench_layout.py`：通过；Git 仅提示文件行尾转换提醒。
- 隔离 Windows QPA 检查使用 `.venv-build/Scripts/python.exe`、`QT_QPA_PLATFORM=windows`、Microsoft YaHei UI 13pt，组合 zh/en × 960/1160。四种请求宽度均保持不变，最小宽度提示分别为 725px/747px；模式框中英文宽度分别为 163px/186px，最长选项测量为 119px/142px；按钮均达到各自 sizeHint，长路径标签明确省略且完整路径可由 tooltip 查看，composer 输入框与发送提示没有几何重叠。窗口设为 `WA_DontShowOnScreen`。
- TEMP 截图：`C:/Users/Lenovo/AppData/Local/Temp/pengtools-agent-workbench-zh-960-13pt.png`、`...-zh-1160-13pt.png`、`...-en-960-13pt.png`、`...-en-1160-13pt.png`。英文 960px 截图已按模式框和路径标签修正重新生成。尺寸与控件指标在 `C:/Users/Lenovo/AppData/Local/Temp/pengtools-agent-workbench-qpa-metrics.json`。
- 真实生产 `ConfirmActionDialog` 的首个 watcher tick 前取消测试通过，确认弹窗按时关闭且未写文件。完整读/改/写路径使用 TEMP 项目与共用 fake model adapter；真实模型网络未调用。

## 未验范围

- 未使用真实内网模型或真实项目目录；未在运行中的 UI7 上验收。
- 退出清理通过 fake MainWindow panel 和 QPA 合成线程验证，未在整套运行应用中执行真正退出。
- Agent 重构总目标仍包括数据中心范围、真实模型/连接环境以及用户整体视觉签收；本工作台包的定向通过不替代这些验收。
