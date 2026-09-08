# 2026-09-08 分支、设计与源码偏离审计

## 1. 核对范围与证据边界

本轮针对用户所说“代码与设计越来越偏离”核对本地 `D:\PengTools`、`origin/ui/prism-v1`、正式 UI 规格、原型和首页相关生产调用链。审计起点 HEAD 为 `a3a3a4a`；执行 fetch 后与远端同名分支比较为 `0 0`，未发现该时点本地提交落后或超前。main 为 `32d7880`，不等于当前 UI 开发分支。

工作区起点有五份未跟踪阶段报告：`scripts/p09a_fix1_report.txt`、`p09a_fix2_report.txt`、`p09b_fix1_report.txt`、`p09b_fix2_report.txt`、`p09b_report.txt`。本轮保留它们，不将其纳入修复文件。

这是源码与部分定向运行审计，不是全软件逐屏视觉验收。用户已授权将本轮修复与文档提交并推送到 `ui/prism-v1`。需求 AI 应确认其读取提交包含本文件，实际结果 SHA 从 Git 历史与交接消息获取，不能把本报告的起点 SHA 当作包含修复的提交。

## 2. 结论与处理

| 项目 | 已核实事实 | 分类与处理 |
|---|---|---|
| 分支差异 | 起点本地与 origin/ui/prism-v1 同步 | 不是本地漏拉代码导致的问题 |
| 单主题 | 分支有相关收口提交；旧规格仍要求 calm/black；用户明确确认保留单主题 | 已批准目标变更。保留实现，V2.1 正文与验收同步修订 |
| 最近需求卡片 | Vue MonthlyTasks 与原生 DashboardPanel 仍显示旧卡片；与用户替换要求冲突 | 展示偏离。Vue 移除该区；原生隐藏兼容卡片，保留既有引用和信号 |
| 首页卡片高度 | Vue 固定 height、原生 setFixedHeight 锁定 176 | 布局约束偏离。改成最小高度176、允许内容自然增高；完整视觉仍待验收 |
| 汇总层反向依赖 | tools/dashboard_summary.py 为默认快捷工具导入 ui.navigation_model | 分层偏离。导航信息改由 MainWindow/原生面板装配注入 |
| 前端产物是否陈旧 | 修复前重新构建 dist，与正式资源按文件及哈希比较，7个文件一致 | 此怀疑未成立，不记作修复成果；本轮改源码后另重新生成正式产物 |
| README 技术描述 | 仍写迁移骨架、首页 legacy、12个业务面板；文档索引优先指旧交接 | 交接信息陈旧。更新根目录和文档入口，新增产品与协作规范 |
| 阶段完成情况 | 历史有 P00–P09B 与修订提交 | 不能等同全部展示面完成；P09C/D/E/P10 仍需依当前代码逐项核对和实施 |

没有因本轮审计恢复深色主题、重写业务计算、改变数据库/SSH/Agent 生命周期或引入新框架。

## 3. 修复路径和兼容方式

首页任务展示由 `frontend/src/dashboard/components/MonthlyTasks.vue` 负责。移除“最近需求”展示后，继续使用现有本月任务、测试点与进度数据；点击仍进入原需求操作链。增加月度任务语义定位属性，集成测试不再依赖“第二张卡片”这种易碎的布局序号。

原生 `panels/dashboard_panel.py` 隐藏旧卡片，保留其属性、信号与兼容刷新路径，不删除需求数据。宽窄布局中月度卡片仍显示。两种首页 Hero 均不再锁死最大高度；这只解决固定高度限制，不宣称所有长内容排版都已人工验证。

`tools/dashboard_summary.py` 保留原可选 tools 参数。未注入时返回空展示工具列表，不再加载 UI。两个正式装配调用者都注入 `get_dashboard_quick_tools()`；Vue 的快捷入口仍以 navModel 为权威，未新增第二套导航目录。相应测试按显式注入契约更新。

## 4. 本轮验证记录

| 检查 | 结果 | 范围 |
|---|---|---|
| `npm --prefix frontend run typecheck` | 通过 | 曾发现移除旧卡片后遗留未使用函数，已清理再通过 |
| `npm --prefix frontend run build:embedded` | 通过 | 正式双入口与引用资源重新生成 |
| `npm --prefix frontend run verify:embedded` | 通过 | 入口、相对资源、离线引用和现有桥接检查 |
| `python -m pytest -q tests/test_prism_drift_repair.py tests/test_dashboard_summary.py tests/test_prism_dashboard.py --tb=short` | 16 passed | 新增依赖隔离/原生卡片行为，加既有汇总与布局契约检查；其中部分既有测试是静态断言 |
| `python -m unittest tests.test_dashboard_summary tests.test_round4_v2e_review_fix1 -v` | 17 tests OK | 汇总、导航、日期兼容及相关原有行为；与上一组有重叠，不相加冒充独立用例总量 |
| `python -m pytest -q tests/test_web_dashboard_runtime.py --tb=short` | 未通过验证：进程提前退出，退出码 -1073740791 | 没有完整测试结果，不能证明真实 WebEngine 链路通过 |
| 单独运行 production_chain_and_interactions | 退出码1、无完整结果 | 未解决运行证据缺口；不能据此归因为本次修改或原有缺陷 |
| 用户视觉、真实连接、安装包 | 未执行 | 不宣称整套UI或发布验收完成 |

Vite 提示未来 native configLoader 与当前配置格式的兼容提醒；本次构建成功，未顺带调整工具链。交付前 `git diff --check` 通过，34 个本地文档链接检查无断链。

## 5. 给下一位接手者

1. 阅读项目入口和 V2.1，确认单主题决定，不能再按 V2.0 回退。
2. 获取包含本轮修复的实际提交；本地未发布文件不属于远端可读证据。
3. 在隔离且可运行 WebEngine 的环境补齐首页 DOM/Bridge 集成验证，再给用户查看实际首页，包括长文本、窄宽和动画。
4. 按开发规范逐模块建立“原动作 → 新位置 → 同一回调”的核对表，再推进 P09C/D/E；不要一口气凭原型重写所有面板。
5. P10 单独报告全量展示面、原生回退、加载、DPI、多屏与必要发布验证，不把此次有限修复报告当作替代品。
