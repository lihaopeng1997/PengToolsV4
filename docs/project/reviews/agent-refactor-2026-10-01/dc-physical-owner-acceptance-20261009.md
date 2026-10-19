# 数据中心 SQL 查询所有权与终态历史：源码及合成运行验收

本包限定范围接受。实际分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`；使用精确 dirty before/after 字节复核，原有无关修改保留，未提交、推送、打包或操作现有软件窗口。完整目标仍 active，本阶段为 progress。

SQL 查询通过 QueryExecutor 的真实 submit Future 跟踪本轮/目标/会话 generation 所有权；Future 尚未完成或状态不能核实时，共享 Runner 阻止后续工具及模型/规划调用。默认查询超时仍停止整轮；逻辑会话关闭不提前关闭 worker 的驱动，release 后仍保留未完成 Future。Future 完成仅证明 worker 走完 finally；现有 close 异常被吞掉，不能据此保证真实原生连接关闭成功。

本轮 Runner 自己追加的工具调用在提前终止时配对完整：安全 TIMEOUT/CANCELLED 保留实际 query_id（非查询可为 None），剩余调用为 NOT_STARTED且不执行、不重试、不计为验证证据；取消后的迟到成功继续抑制。旧输入中的畸形历史不自动修补。界面通过严格标量显示“本轮终止时 worker 尚未完成”的历史快照，不冒充实时 cleanup 完成通知。控制器源码保持原样，真实 owned runtime.close→shutdown(wait=True) 的 offscreen fake-driver 测试验证引用持有到清理结束。

根最终15组 **150/150**，0 failure/error/skip，32源 hash 首尾稳定；五个 catalog/network 禁止门均0。覆盖数据中心 runner/证据/controller/panel/物理 owner/readonly connectors/host relational与NoSQL，以及共享context/final gate/private continuation/file workbench。根冻结 before 独立复现、最终两分支实阻塞探针和4个契约探针均有证据。默认路径没有第三次模型请求；另一个局部 query token 分支明确属于风险测试，修复后同样不再执行第三工具或请求模型。合成完成查询仍能正常接续。

原作者72项和根118项是初候选时间点；首修根150项通过但独立复核发现callback失败后的退休缺口，也保留为待返工时间点，不改称最终源码验证。根追加4个契约检查发现 query_id被改写/制造与无身份Future被视作idle，独立初审拒绝；4路径返工后作者窄20项通过，但首修独立resolution仍拒绝退休缺口。再修两路径补齐一次release后的自动退休，根最终重跑全部回归、阻塞/契约/退休探针，独立retirement-resolution接受最终源码。旧候选、初拒绝、首修resolution及失败探针保留。

证据：`evidence/root-dc-physical-owner-20261009/retirement-final/`，作者 `evidence/dc-physical-owner-20261009/repair-retirement/`，独立 `evidence/dc-physical-owner-source-review-20261009/retirement-resolution.*`。独立审查未跑测试，150项和根探针仅归根执行。

本包改动10个源码/测试路径，精确清单及32源hash见 `evidence/root-dc-physical-owner-20261009/retirement-final/delivery-final/delivery-check.json`；原root-review.json保留首次记录检查器误判的时间点。生产6处为共享Runner、QueryExecutor、AgentQueryTool、DC registry/runtime和native panel；其余为定向测试。首修仅Runner/registry及两个测试，28条保护路径未变；退休再修仅registry和一个测试，30条保护路径未变。

后续先补齐 NoSQL Future 空闲边界，再连接DC保守planner和有界可恢复查询证据，避免裁剪丢失先前事实；当前尚未开启DC planner或证据索引/读取工具。实际ScopedResultProjector的64/256 KiB与文档12/48 KiB差异保持记录，本包未变更限额；累计24,000字符预算未变。用户仅提供qwen3.6，具体变体/部署窗口/协议仍未知，不能自动设262,144或启用历史thinking。真实Qwen/六库/原生close、语义摘要/归档/性能、DM、SSH/TLS、正式包和用户视觉签收未完成。
