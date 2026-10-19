# 数据中心默认上下文规划：限定范围验收

本次源码与合成运行范围接受。实际分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`；原 dirty 工作区保留，未提交或推送。完整目标继续 active，本阶段为 progress，正式包与当前软件窗口未更新验收。

内建 active、非 draft 的 DataCenterAgentRuntime 现在注入共享 ContextPlanner：本地输入上限64,000，未知服务端上下文窗口，输出预留4,096。64,000使用保守UTF-8字节启发式，不是提供商token计数，不保证实际Qwen窗口可容纳。现有累计24,000输入字符、投影64/256 KiB和最终组装请求字节门禁保持不变。用户提供qwen3.6模型家族，具体变体、服务端容量与协议仍未知，没有据此写入262,144或切换thinking。

规划只在完整工具交换边界删减请求视图，保留系统/用户锚点与最近完整交换；canonical会话和调用者输入保留全部历史。实际未完成Future、failed或未知查询owner继续在规划和模型请求前阻断。过大的锚点在模型和查询前阻断。draft及严格自定义runner factory保留原构造参数，没有新增context_planner参数。

作者五组41/41、主代理十组85/85，均0失败/错误/跳过。新增六个fake-runtime测试覆盖默认公开BUDGET标量、完整交换裁剪、draft/自定义factory兼容、实际提交Future与未知owner屏障、累计字符停止及超大锚点。主代理三个独立探针14项检查全通过：合成历史旧交换整组移除而canonical保留；超大锚点零模型/查询；真实标准库提交Future pending时零planner/模型/查询，一次逻辑release后实际完成才退休。历史工具数据仅为合成夹具，不是已认证数据库证据。

45源路径精确hash复核，43保护路径与35个host方法/类AST片段不变。生产增量仅host_runtime.py的imports和构造组合，另新增test_data_center_context_planner.py。五个真实profile/catalog/network门禁均0。独立源码复核接受，复核者测试0；作者、主代理与独立证据分别保留。交付不重复运行测试，4条记录只追加当前检查点并保留旧正文/BOM/换行；既有NoSQL正式验收报告按字节不变。

证据：作者 `evidence/dc-planner-stage-a-20261009/`；主代理 `evidence/root-dc-planner-stage-a-20261009/candidate/` 与 `runtime-probe/`；独立 `evidence/dc-planner-stage-a-source-review-20261009/`；最终增量、51项源码/记录/报告hash和diff检查在 `evidence/root-dc-planner-stage-a-20261009/delivery-final/`。

Stage A尚无查询事实恢复、证书ledger或overlay。Stage B设计 `evidence/dc-context-evidence-plan-20261009/plan-revision.md` 需独立写入GO才能实施：只认证canonical成功提交的投影消息，严格run范围/epoch与工具配对，有界ledger和8KiB请求引用视图，既有预算和最终字节门禁仍有效。未访问真实配置、Key、catalog、网络、数据库、Qwen、现有软件窗口或正式包。实际服务验证、语义归档/性能、DM、SSH/TLS、六类数据库、发布与用户视觉签收仍待完成。
