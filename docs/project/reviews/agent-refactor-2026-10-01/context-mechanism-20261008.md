阶段二当前进展：确定性记忆、文件哈希与Store/Worker CAS已接入；根121项通过，独立源码复核与根阶段接受已完成。详见 [阶段二记录](context-memory-20261008.md)。下文为阶段一的当时实施/待办记录，不应覆盖阶段二当前状态。

# Agent 上下文机制：阶段一实施与后续边界

分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。本次处理用户新增的上下文方向，保留此前 Agent/数据中心/UI 重构目标。无新提交、推送或软件包。

## 已实现

- 每个模型请求单独规划输入，与整次 run 的累计字符/字节/轮数预算分开。公开 system prompt 与工具 schema 全部计入。默认使用严格 JSON UTF-8 字节及消息开销的保守估值，不是真实 tokenizer；来源字段为 `heuristic_utf8_bytes`。
- 编程工作台采用本机估算输入上限 64,000，供应商窗口为未知。可注入显式窗口和估算器，已知窗口扣除输出、私有续接预留及安全边际。软件不按模型名称猜窗口。
- 原始 user/system 消息、最近完整消息和最近完整工具交换保留。旧 assistant 消息和旧工具交换按完整单元从早到晚省略，调用 ID、参数、结果原文不改；请求裁剪不裁掉 `AgentRunResult.messages` 或工作台原始记录。
- 锚点自身超限时，在模型请求前停止并显示原因；不会为了凑预算静默省略约束。未完成/孤立/重复/ID 不匹配工具交换拒绝请求，显式 pending 规划只返回 deferred。编程工作台只在同步工具及确认返回后的 turn 边界规划。
- 默认长历史裁剪使用精确差量，选定结果再做一次完整序列化复核。5000 条历史用例共两次完整计量，选择与逐步全量参考一致；这不是实际 UI 性能 benchmark。
- 每轮输出受本轮预留、整次 run 剩余额度限制；工作台读取所选模型的公开 snapshot.max_tokens 作为预留上限。恰好耗尽输出额度也不会发起下一次请求。
- 私有 DeepSeek reasoning continuation 继续只在 adapter 内部。公共 Runner 拒绝顶层 reasoning_content 输入，终态 transcript 也不回传该字段；工具结果正文提到这个词可以正常读取。

## 验证

根独立命令：`.venv-build/Scripts/python.exe -B docs/project/reviews/agent-refactor-2026-10-01/evidence/root-agent-context-20261008/verify_phase1.py`。

结果：84/84、0skip，覆盖新 planner 20 项、新 integration 11 项、共享数据中心 Runner、编程 runtime、旧 Agent runtime、DeepSeek 私有续接。16 个 source hash 在验收期间不变；所选配置读取、catalog 读取、urlopen、socket.create_connection、socket.connect 禁止门调用计数全为 0。回执和日志在 `evidence/root-agent-context-20261008/`。首次验收脚本 guard 名误写导致测试开始前退出，修正到实际 API 后验收；不算产品失败或先前通过。

首 candidate 83 项通过后，独立 source review 找到显式 planner 可能增大 profile 输出限额的接缝。Runner 已统一先按公开 snapshot.max_tokens 收紧每次请求，再与 planner 预留和 run 剩余预算取 min；新增注入 planner.reserve=4096 / snapshot.max_tokens=128 的实际请求回归为128。旧83项源码/回执原样保留在 `first-candidate/`，最终5路径原字节副本与增量 patch 为 `source-after-reviewed/` 和 `incremental-source-reviewed.patch`。未改 ModelAdapter 或真实 profile 来规避问题。

独立 reviewer 最终只读复核通过，原输出 finding 标为 resolved，5 路径 hash 与根实际当前文件一致；证据 `evidence/agent-context-source-review-20261008/resolution.md` 和 `resolution-receipt.json`。reviewer 未运行测试，84项来自根独立执行，两个边界分开记录。5 个源增量 whitespace check 无发现；no-index exit1为文件有差异，行尾LF/CRLF警告已留回执。此次源级阶段一接受，不等于整个上下文机制或整项目完成。

没有发真实模型请求、读取真实配置/Key、操作现有软件窗口或重新打包。本次不重跑已完成的 DeepSeek 真实验收；其 `root-deepseek-live-20261008` 是旧冻结源上的独立实际证据，不能改称本次新源真实模型验收。

## 阶段一时未实施（当前进展见页首）

1. 增量结构化摘要：只总结新增完成交换，固定任务、用户限制、决策、验证、待办等章节，原始约束优先；摘要是普通历史资料，不能升级为系统指令或工具授权。
2. 文件证据索引：相对路径、读/改类型、修改前后 SHA、patch 前提、对应工具交换和验证状态。过期证据必须重新读取；摘要不能直接触发或重放修改。
3. Store/Worker 的 workspace、conversation、profile revision 绑定与 compare-and-swap；摘要失效回退、旧数据迁移、完整原文查询和 UI 状态显示。
4. 所选 profile 的明确供应商窗口/实际 tokenizer 配置，以及 adapter 注入私有续接后的最终请求计量。当前默认只估公开负载，不能宣称完整供应商 token-fit。
5. 数据中心活动 query/lease/owner 的安全空闲边界专测后再启用裁剪；本次数据中心默认没有 planner，数据库作用域及所有权保持原样。

应用当前通过远程 API 适配器调用模型。此次没有本地权重压缩或量化实现，也未承诺供应商 KV-cache/提示缓存能力。后续需根据实际内网模型服务能力另行确定服务器侧方案。
