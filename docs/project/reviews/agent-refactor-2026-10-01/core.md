# 共用 Agent 执行层与编程宿主回执

状态：本写者包已冻结，等待主代理联合验收。此报告只覆盖共用执行层与编程宿主，不代表 DC04–DC06 或完整用户目标完成。

## 基准与范围

- 仓库：`D:/PengTools`，分支 `dev/2026-09-23`，基准 `818fba72bf437de9bd49fcd5b198fb1270096a53`。
- 使用的工作树包含大量并行 dirty 变更；本包只在分配范围内实现，没有暂存、提交、推送、切换分支或清理工作树。
- 共用执行层：`tools/agent_core/`；兼容入口：`tools/data_center/agent.py`、`contracts.py`、`budget.py`、`streaming.py`、`model_adapter.py`、`model_config.py`；编程宿主：`tools/agent_runtime.py`；编程会话保存兼容：`tools/agent_store.py`。
- 本包新增/扩展验证：`tests/test_agent_core_contracts.py`、`tests/test_programming_agent_runtime.py`、`tests/test_agent_runtime.py`、`tests/test_agent_store.py`。

## 接口与行为

- `AgentRunner`、`CancellationToken`、模型流式适配器、预算、事件及运行契约集中在 `tools.agent_core`。公共 `ModelAdapter` 是注入协议；具体 SSE 类名为 `StreamingModelAdapter`；选定配置宿主为 `AgentModelHostAdapter`。
- `run_agent_loop` 保留旧位置参数和 `(answer, messages, tool_calls)` 返回形状；可选 `model_adapter`、`model_transport`、`event_cb`、`cancellation` 支持无网络测试与外层生命周期注入。共享执行循环只有一份。
- `CancellationToken()` 无参构造；调用 `cancel(reason)`，用 `is_cancelled()`、`reason`、`wait(timeout)` 查询/等待。模型 deadline 由模型适配器负责；工具调用前后检查取消，过期/取消运行不会回放写入。
- `progress_cb(kind, text)` 的 `assistant_delta` / `reasoning_delta` 是本轮 body 字符串增量；工具和状态也是字符串。每轮开始发 `status=turn_started:<零起始索引>`，结构化事件 `TURN_STARTED` 含 `{turn_index, status: "model_pending", budget}`，工作台据此清理上一轮临时文本。原生工具调用助手消息不作为最终答复气泡保存；最终答案只持久化一次。
- 运行历史按完整 assistant-call/tool-result 交换分组后做最多 30 条尾部裁剪，不能产生孤立结果；存储清洗只保留参数完整、ID 唯一且结果一一配对的工具交换。保存后重载再经真实 `StreamingModelAdapter` FakeTransport 发起的第二轮请求已验证配对完整。编程模型输入预算是域专用 `384,000` 字符；数据库预算未因此变化。
- Wire 层只发送 OpenAI 消息允许字段 `role/content/name/tool_calls/tool_call_id`，本地存储的 `id/created_at` 等元字段不会泄露。`native_tools` 保持原生调用与结果消息；`strict_json` / `text_only` 的历史工具交换在请求中降级为 assistant JSON envelope + user `tool_observations`，保留 `tool_call_id`、name、arguments 和结构化结果；内存历史及存储格式仍保留原始配对。
- `strict_json` 只接受完整 envelope；自由文本中的 JSON 不会被当作工具调用。明确 `TEXT_ONLY` 模型不发布或执行工具，并在用户可见终答中声明本轮仅提供建议、未执行文件操作。数据库严格模式的只读/查询授权没有因编程工具而放宽。
- 编程 registry 固定绑定调用方工作区，模型不能提供 workspace/connection。文件读取有边界和可继续分页；TEMP 实测读取 39KB 文件。写入、编辑、删除、SVN 提交均需要确认完整差异；确认后校验原文件哈希、编辑唯一匹配并在写前再次检查取消。UTF-8 BOM、CRLF 与 GB18030 编辑回归通过；原格式不可可靠往返则拒绝。已有定向测试/SVN工具保留，没有新增任意 shell 工具。
- RunContext `scope` 深拷贝为递归不可变 JSON 快照，`as_dict()` 返回可独立修改的副本；DB scope / policy 保持 data-center host 自有。现有 `data_center` 包面测试要求旧 `__module__` 路径，兼容 shim 因而保留这些类型的旧模块归属元信息；具体共用实现仍定义在 `tools.agent_core`。

## 验证证据

以下命令均在本地 TEMP 合成项目及假 SSE transport 上运行，没有触网、真实模型、数据库或真实会话：

```powershell
python -m py_compile tools/agent_runtime.py tools/agent_store.py tools/agent_core/__init__.py tools/agent_core/budget.py tools/agent_core/contracts.py tools/agent_core/history.py tools/agent_core/host_model_adapter.py tools/agent_core/model_adapter.py tools/agent_core/model_config.py tools/agent_core/runner.py tools/agent_core/streaming.py tools/data_center/agent.py tools/data_center/budget.py tools/data_center/contracts.py tools/data_center/model_adapter.py tools/data_center/model_config.py tools/data_center/streaming.py tests/test_agent_core_contracts.py tests/test_agent_runtime.py tests/test_agent_store.py tests/test_programming_agent_runtime.py
```

退出码 `0`，编译通过。

```powershell
python -m unittest tests.test_agent_core_contracts tests.test_programming_agent_runtime tests.test_agent_runtime tests.test_agent_store tests.test_data_center_agent_runner tests.test_data_center_agent_contracts tests.test_data_center_streaming tests.test_data_center_host_model_adapter tests.test_data_center_model_config tests.test_data_center_package_surface -v
```

退出码 `0`，`Ran 111 tests ... OK`。覆盖共用契约/预算、真实 SSE FakeTransport 的读/搜/确认编辑、取消后不写、格式往返、自然语言 JSON 不执行、TEXT_ONLY、strict JSON 第二轮、会话保存续聊、超过 30 条历史的整组裁剪、数据库 runner/model/config 兼容与工具隔离。

`git diff --check` 退出码 `0`，无空白错误；Git 对并行 dirty 工作树中的多份 CRLF/LF 混合文本打印换行转换提示。没有做全量发现、真实服务联调或视觉验收。

## 待联合验收

- 工作台 worker/UI 回合展示、主窗口退出与真实 Qt 生命周期由并行工作台包负责，本包没有更改这些文件；需主代理集成验证。
- 数据中心其余面板装配和 DC04–DC06 留在后续包；本包只保留既有数据库只读 runner 政策及兼容入口。
- 真实模型网关对不同 strict JSON 供应商接受文本化历史的兼容性未在无网络环境验证；FakeTransport 已验证请求契约和完整数据。
- 兼容模块类的 `__module__` 仍显示旧 data-center 路径以通过现有 package surface API 测试。
