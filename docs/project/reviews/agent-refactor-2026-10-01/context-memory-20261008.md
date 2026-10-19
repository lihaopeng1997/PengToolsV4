# Agent 上下文记忆与会话版本：2026-10-08

本文为阶段二冻结点。后续预算提示、只读冲突结果查看器与 calm 字体修复见 [UI 最终候选](context-ui-final-20261009.md)，Qwen3.6 部署能力边界见 [接入记录](qwen-context-20261008.md)。旧 121 项证据不覆盖后续 UI 源；正文中的 UI 待办属于当时状态。

当前分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`；已有 dirty 改动保留，无 stage、commit、push、新包或现有软件窗口操作。这是上下文阶段二，不代表完整 Agent/数据中心/UI 目标完成。

## 当前实现

- `tools/agent_core/memory.py` 提供可序列化、默认最多 8192 UTF-8 字节的确定性历史记忆。只保留 assistant 原文摘录、成功文件工具的相对路径与 SHA256 回执，以及测试工具的返回码；不推测已完成决策、测试数量或未执行的操作。记忆是普通 assistant 历史资料，明确不可信，不能当成指令、权限或工具重放依据。
- 记忆绑定 workspace/conversation ID、两个 revision 和所选模型公开配置 fingerprint。源 hash 计公开角色/内容/原生工具 ID 与结果，不计展示用 ID/日期。`decode_memory` 从原始公开消息重建摘要与证据，不只检查 `memory_hash`。前缀改变、工作区改变、profile 改变或 revision 回退会失效。失败的工具不会产生成功文件证据；未知 hash 明确为 unknown。
- 正常无遗漏的增量只提取新完成交换并核对旧前缀。此前已省略 note/artifact/validation 记录、且新增完整单元时，执行完整源前缀重建，以保证重新可用的旧证据与解码一致；这条 fallback 不是纯 tail-only。它覆盖已保留回执变短，以及旧 artifact 已被整项淘汰而无法比较尺寸的情况。恢复解码本身也会重建源证据，当前仍有 O(n) 校验开销。
- `memory_context.py` 先用阶段一 planner 判断是否需要裁剪，仅在裁剪时插入并固定这条普通历史记忆。最新工具交换及全部 user/system 要求仍固定，交换不拆分。记忆放不下时使用原有有效裁剪请求并记录 `memory_omitted`。合成记忆不会追加到原始对话，也不成为新的真实用户要求。供应商窗口未知时仍只执行本机输入预算。
- Runtime 的 read 回执包含原始文件完整字节 hash，包括 BOM/换行；分页不把 hash 错当页内容 hash。write/edit/delete 回执提供修改前后与确认前提 hash，不包含新的完整文件副本。确认、取消、路径边界和文件变化拒绝继续沿用。历史回执不能证明文件当前仍相同，模型被要求先重新读取并走原预览确认。
- Store 在既有 workspace JSON 中增加 revision/context_memory/conflicted_runs；没有新增 userdata 路径。保存不再用最近 5000 条的原始尾切片，以保留早期限制和完整原生工具交换。旧数据没有 revision/memory 时按 0/None 兼容；有限工具审计保留原 200 条上限，剔除不属于公开工具记录的顶层字段。rename/选择对话不会推进 transcript revision，workspace 路径/计划确认策略变化会推进 workspace revision。
- Worker 捕获保存后的版本、完整原文和模型绑定；完成结果经过 `commit_conversation_result` 重新加载当前文件并核对版本。竞争结果只有一个能写入对话；过期结果单独保存在原 workspace JSON 并显示提示。现有 CRUD read-modify-write 与 CAS 使用同进程 RLock，避免新要求被异步旧结果覆盖。Qt 线程引用仍在真实 finished 后释放。
- Runtime 公共入口遇到顶层 `reasoning_content` 会在模型/监控线程/取消路径前拒绝，返回公开副本，不修改原输入。文本正文中的这个词不被当作私有字段。模型内部私有续接仍由 adapter 管理，不进入记忆、事件或 Store。缓存回调异常不会丢弃已完成 run 结果。
- Profile fingerprint 只含公开 ID、模型/能力/输出/TLS 等行为与 URL 的公开 routing 部分；不含 token、API Key、用户名、密码或 query。含 user-info/query/fragment 的 URL 用每次运行的新标识，避免复用无法安全表达的配置。

## 验证与审查状态

根侧 `evidence/root-agent-memory-20261008/verify_memory.py`：最终 **121/121、0skip**，15 源文件哈希前后稳定。正常真实配置/catalog 读取和 urlopen/socket 建连门调用均 0；实际 Qt fixture 使用 TEMP Store、fake adapter，settings 写入在整个 Qt 删除生命周期被重定向。包含实际 Worker 等待时追加新要求的冲突保存、两个并发 CAS、append 与 CAS 的可控竞争、5003 条完整原文、BOM/CRLF 文件哈希、源重建拒绝伪造摘要、private-history 正常与取消入口、缓存回调失败、预算放不下记忆的回退等。

根侧额外纯合成探针 `probe_incremental.py`：58 个字节上限、修复前 14 处 self-decode 失败，修复后 0。独立 reviewer 继续指出整项 artifact 淘汰后同路径更新的缺口，根 `probe_evicted_artifact.py` 复现 96 个有效边界检查中的 2 处失败（1020/1030 字节上限）；第二修复后 0。作者独立窄 memory 测试最终 16/16；这些是不同的执行记录，不能把作者测试称作根独立执行。最初根组合测试 65 项中旧 tail-30 断言失败；该断言改为验证早期用户要求与完整工具配对，未改生产逻辑来恢复旧尾切片。

冻结证据：`source-manifest-reviewed.json`、15 份 `source-after-reviewed/` 原字节、7 份 phase2 `source-before/` 原字节，以及 `incremental-source-reviewed.patch`。最终 manifest SHA256 `8a1b12285310089bd3bc532b8171e07e77dff0f90407b395be7e1a0f6319e1b9`。根全 dirty tracked 树 `git diff --check` exit0；行尾警告留在回执。四个新文件另做 no-index whitespace check。115 初候选回执、120 首修复候选完整源包、失败测试、两种 probe 和作者两次修复前文件未被改写成最终通过证据。packet helper 首次误用 flat before 路径，修正后成功冻结，没有产品源变化。

独立只读 source review 最终接受；根再次核对当前15源与冻结manifest、测试与审查receipt一致后接受本阶段。结论记录于 `evidence/agent-memory-source-review-20261008/`，根接受状态以根 review 为准。reviewer 未运行测试，不把根 receipt 当成自己的执行证据。

## 尚待处理

阶段二接受后的独立补充：`tests.test_agent_memory_private_continuation` 1/1 通过，TEMP实际 read_file 与 fake DeepSeek SSE 串接 MemoryContextPlanner。两个假 transport 请求均含压缩后的普通记忆；第二个请求保留实际原生 read exchange 并只在 adapter 的 outbound assistant 上回填私有 reasoning，公开对话/工具记录/记忆不含 marker，run 结束缓存为空。十个相关源 hash 在补充执行期间稳定，真实所选profile/catalog/URL/socket计数均0。证据独立保存在 `root-agent-memory-20261008/private-memory-supplement/`，没有覆写121项的冻结回执；不代表真实供应商窗口计量。

真实供应商 tokenizer/window 配置，以及 adapter 注入私有续接后的最终请求计量；任务/决策/待办的语义摘要、原文检索和冲突结果 UI 查看器；长期原文/冲突审计的归档与磁盘增长策略；性能专项；数据中心 query/lease/owner 安全空闲边界专测后再启用压缩。本次数据中心默认路径未启用 planner。

同进程 RLock 没有新增跨进程写入保证；直接调用 load→任意外部改动→save 的调用方仍应使用已有事务 API 或 CAS。当前 profile fingerprint 是公开投影，不是对私有凭据内容的版本承诺。本次没有真实模型调用、供应商缓存声明、权重量化、现有窗口重启、新包或用户视觉签收。之前 DeepSeek 真实验收属于其当时冻结源码，不能改称本次新源码的真实验收。

直接 Runtime API 的一个已披露边界：私有历史拒绝时，不补入尚未存在于 messages 的独立 user_message 参数；实际 Workbench 在启动前已保存该消息，产品路径不受影响。
