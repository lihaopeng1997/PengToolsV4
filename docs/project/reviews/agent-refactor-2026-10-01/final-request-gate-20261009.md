> **2026-10-09 有限返工修正：** 按独立 source review 的 FW-1/FW-2 收敛修改。`ContextPlan.max_request_bytes` 现在与 `AgentTurnRequest` 共用 exact positive `int` / `None` 校验；默认 planner 仅将新派发的 byte cap 正规化为内置 `int`，保留旧 `ContextLimits` 整数子类契约。schema 2 中英文提示改为本机 JSON 字节限额不能保证符合模型上下文窗口限制，不再无条件称窗口未知；`budget_exhausted` 瞬态状态也改为通用预算上限文案。四文件备份、前后哈希及窄测试回执见 `evidence/agent-final-wire-20261009/repair-contract-tooltip/`。最终定向命令 20 项通过；根侧独立复核仍待进行。

# 最终出站请求字节 gate

状态：实现完成，待根侧独立复核与验收。工作区上下文按 WRITE GO 提供的分支 dev/2026-09-23、HEAD 818fba72bf437de9bd49fcd5b198fb1270096a53 记录；本任务未运行 Git 命令。

## 行为

AgentTurnRequest 新增可空的单轮 max_request_bytes。只有 ContextPlanner 使用精确的默认 ConservativeTokenEstimator 且 kind 为 heuristic_utf8_bytes 时，ContextPlan 才从 _input_budget() 派发 byte cap；已知窗口时，cap 使用扣除输出、私有续接和安全预留后的较小额度。自定义 estimator 与 planner=None 都保持 None。cap 随单个请求传递，不存于共享 adapter 状态。

ModelAdapter 在组装完整 _request_body（包括 system、tools、response format、可选参数及 DeepSeek 私有 continuation）后调用根侧 request_budget.measure_model_request。正好达到 cap 可发送，超过一个字节即本地拒绝。默认 HTTP transport 与 gate 共用 encode_model_request，保持原 JSON 空格和 UTF-8 格式。gate 位于 transport/open 之前，取消或截止状态在计量后复核；本地超限不进入可重试传输分支。DeepSeek cache 在拒绝路径释放，既不删 continuation，也不重放工具。

超限映射到稳定的 final_request_input_bytes / BUDGET_EXHAUSTED。Core BUDGET 事件只带 request_budget_status、measurement_kind 和 local_byte_limit；运行时将其转换为严格 schema 2，UI 只显示 cap 和本机 UTF-8 JSON 限制说明，不显示实测 input_bytes、body 或 private 内容。schema 1 context-budget DTO 保持原形。状态说明“后续模型请求未发送”，不声称整次 run 没有发过请求。快速结束的 Worker 会在 completed 回调中、任务身份仍有效时排空进度队列，保证瞬态 schema 2 状态到达 UI；QThread 引用仍由原 finished 回调释放。

Data Center 的 planner=None 路径不获得意外 cap。MemoryContextPlanner 与 Host 的 dataclasses.replace 保留请求字段，因此未修改对应受保护文件。

## 修改范围

六个生产文件：tools/agent_core/context.py、contracts.py、runner.py、model_adapter.py、tools/agent_runtime.py、panels/agent_workbench_panel.py。

四个既有测试文件：tests/test_deepseek_private_reasoning.py、test_agent_context_integration.py、test_agent_memory_private_continuation.py、test_agent_workbench_runtime.py。另新增 tests/test_agent_final_request_gate.py。

根侧所有的 tools/agent_core/request_budget.py 与 tests/test_agent_final_request_measurement.py 未修改；二者 SHA-256 与 WRITE GO 冻结值一致。未修改 host_model_adapter.py、memory_context.py、Data Center 源码、配置、Store 或其他应用路径。

## 验证

定向命令共 40 项测试通过，包含根侧计量辅助模块的 3 项测试。覆盖共享序列化格式、exact fit / +1、错误 cap、header/network 前 gate、取消、并发 request-local cap、默认/自定义/无 planner、私有续接导致的第二请求超限、三次临时 TEMP read_file 的回执及 canonical history、memory 兼容、Worker schema 2 进度与完成生命周期。具体命令与机器可读结果见 evidence/agent-final-wire-20261009/test-receipt.json。

工作台测试中的 catalog 与网络 guard 均为 0；真实 catalog reader 替换成失败 guard，配置访问 mock，模型传输为合成 fake，TEMP 文件只在临时目录内创建。guard 细节见 evidence/agent-final-wire-20261009/guard-receipt.json。十个已授权路径 before/after 哈希及两个受保护根侧路径哈希见 evidence/agent-final-wire-20261009/after-manifest.json。

旧方案的 before-probe 错误措辞已先备份并修正：根侧 probe 当时的 fake transport 确实发出两次请求，第二个 body 为 117408 bytes 且大于 60000；那份 before probe 本身没有本产品 gate。旧 plan 精确副本位于 evidence/agent-final-wire-20261009/before-proof-wording-plan.md。

## 限制

该限制计量本机最终 JSON UTF-8 bytes，不是 provider token fit。服务端 context window 仍未知；没有加入 tokenizer、Qwen thinking-history 协议或 run 累计预算。没有真实 provider、网络或视觉验收。此交接提交根侧独立 review；本任务不自行宣布整体目标验收完成。
