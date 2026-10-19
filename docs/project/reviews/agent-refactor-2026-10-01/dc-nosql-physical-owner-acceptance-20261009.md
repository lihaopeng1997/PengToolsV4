# 数据中心 NoSQL 查询所有权：源码与合成运行验收

本次限定范围接受。分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`；在原有 dirty 工作区按精确字节复核，未提交或推送。完整目标继续 active，本阶段为 progress，正式包和当前软件窗口尚未更新验收。

Redis/Mongo 的实际 ThreadPoolExecutor Future 现在纳入既有严格 QueryOwnerTracker。查询超时或取消后，逻辑返回仍保留未完成的同一 owner；后续工具、规划和模型请求被阻止。一次 release 保留 pending/failed owner，实际 Future 完成后自动退休，不恢复模型。附加 generation 是主机提交序号，不等同于数据库驱动 lease generation。

停止先于最终结果选定时，工作线程在 Future 完成前清理自有 lease/router；关闭线程池使用 wait=True、cancel_futures=False，保证排队清理能够完成。健康查询继续复用连接和 Redis 游标，外部注入的共享 router 不由 runtime 关闭。Future 完成只证明清理返回或尝试结束，不能保证原生 close 成功。

最终修复覆盖三个边界：阻塞的查询后 profile 检查跨越超时/取消；辅助 Future 回调设置异常发生在严格注册之前；成功已选定但 Future 尚未 done 时再次取消。已选定结果保持一致，pending owner 不因此变 idle。Runner 的 TIMEOUT/CANCELLED 终态仍只保存安全配对结果，迟到成功数据不进入会话、不触发下一轮模型。

结果已选成功的调度场景使用既有 Runner 迟到成功分支：canonical 工具配对为 CANCELLED、query_id=None，丢弃成功并不计入 RunResult.tool_results；整个 run 的状态仍为 timeout 或 cancelled。主代理第一份补充探针误要求公开 tool_results 条目及每个 deadline 配对为 TIMEOUT，导致四项 fixture 断言失败。该脚本、false receipt与说明原样保留，按受保护的源码契约改正后的 `settlement-probe-final/` 四场景通过，未为此修改产品源码。

作者最终五组测试 **59/59**，主代理八组 **93/93**，均为 0 failure/error/skip。39 源路径复核稳定，修复阶段 37 保护路径未变，五个真实 catalog/network 门禁均 0。主代理重新运行默认阻塞清理两种 provider、原三个失败边界，以及四种 provider×deadline/cancel 的结果选定竞争场景，全部通过。独立复核接受，复核者运行测试 0，作者和主代理证据分别归属。

第一候选作者56/主代理90虽通过，却被两个根边界拒绝；第二候选作者58/主代理92修复前两项，却被结果选定竞争拒绝。这些时间点的候选、拒绝、失败探针和 review 全部保留，不能重写为最终通过。最终证据见 `evidence/root-dc-nosql-physical-owner-20261009/settlement-final/`、作者 `evidence/dc-nosql-physical-owner-20261009/repair-settlement/`、独立 `evidence/dc-nosql-physical-owner-source-review-20261009/settlement-resolution.*`。

本任务增量仅两个生产路径（host_runtime.py、tool_registry.py）和一个 NoSQL 所有权回归测试；修复两轮只改 runtime 与新测试。4 条交接记录只追加当前检查点，旧正文按字节保留。最终44个源码/记录/报告 hash 与检查在 `settlement-final/delivery-final/delivery-check.json`，完整增量 patch 在同目录。

证据全部使用假模型、内存 lease/router、注入的阻塞回调和明确的调度故障缝，未访问真实配置、Key、catalog、网络、数据库、Qwen、已有窗口或正式包，也未测自然调度延迟。数据中心默认 planner 与可恢复查询证据尚待接入；投影64/256 KiB与文档12/48 KiB差异、累计24,000字符预算保持原状。qwen3.6仅为用户提供的模型家族，实际变体、服务端容量、协议未知；未据此写262,144或切换thinking。语义归档、容量/性能、DM、SSH/TLS、真实六类数据库/模型、发布和用户视觉签收仍待完成。

只读后续设计见 `evidence/dc-context-evidence-plan-20261009/`：1900字节纯规划器样例复现Q1事实丢失，合成摘要可保留样例事实，但未验证证书或Runtime接入。独立设计建议先做内建 active runner 的保守 planner；证据证书须等 canonical 成功提交才可生效，ledger总量、严格配对和8KiB候选摘要上限仍需定义，不能把设计算作实现。
