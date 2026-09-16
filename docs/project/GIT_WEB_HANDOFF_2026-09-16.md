# Git 分支核对与网页端 GPT 接手

审计日期：2026-09-16。仓库：`lihaopeng1997/PengToolsV4`。目的：让网页端 GPT 读取已发布代码，不涉及合并、删除历史分支或改变默认分支。

## 代码基准与一致性

本轮审计基准为 `ui/prism-v1` 的 `03fc20e9b84489761d1a9f8c3d51e382eebbca3a`。本轮一次 origin fetch 成功，随后重复联网查询发生 GitHub TLS 握手失败。成功获取的远端引用与本地开发分支相同；后续网络失败不能解释为远端没有变化。

相对该远端引用，tools、panels、ui、frontend、tests 下已跟踪源码无差异。整个工作区并非完全一致：README.md、DEVELOPMENT_AND_AI.md、resources/build_info.json 有此前未提交改动；另有 AI_CONTEXT_WORKFLOW.md、三份 Luna 菜单/标题栏/Tab 报告、一个诊断预览脚本和五份 p09 阶段报告未跟踪。它们不是网页端已发布内容。本记录和检查点修正属于本轮新增交付，实际发布状态以交付回执为准。

## 分支清单

以下为本轮成功 fetch 后保留的引用；差异数字相对审计基准，不是质量或验收结论。

|本地分支|远端对应分支|提交|与当前开发分支关系|
|---|---|---|---|
|ui/prism-v1|ui/prism-v1|03fc20e|相同，双方独有提交 0/0|
|main|main|32d7880|一致，但比当前开发分支少 71 个提交|
|review/final-test-gate-fix|review/final-test-gate-fix|32d7880|一致，比当前少 71 个提交|
|review/correction-round-4|review/correction-round-4|7c4e0e3|一致，比当前少 72 个提交|
|release-stabilization-candidate|review/correction-round-3|11cd9f3|一致，本地与远端名称不同，比当前少 118 个提交|
|backup/release-stabilization-c4942b0|无 upstream|c4942b0|分叉：当前独有 158，备份独有 4|

备份独有提交为 4be9e85、0ec44be、9f07e57、c4942b0。git cherry 未发现这四个提交与当前分支的等价补丁；这不等于相关行为一定缺失，合并前需逐项源码复核。本轮保留全部分支及现有 worktree。

## 网页端读取入口

明确读取 [ui/prism-v1](https://github.com/lihaopeng1997/PengToolsV4/tree/ui/prism-v1)，不要仅凭仓库名默认读取 main。先报告实际分支和完整 SHA；只读到 main 时不得声称已检查最新数据中心 Agent。

按顺序读取：

1. 根目录 AGENTS.md 和 docs/project/README.md。
2. docs/project/DATA_CENTER_AGENT_REQUIREMENTS.md。
3. docs/project/DATA_CENTER_AGENT_DEVELOPMENT.md。
4. docs/project/DATA_CENTER_AGENT_CHECKPOINT_2026-09-16.md。
5. 与具体任务有关的源码和测试。

上述核心入口在审计基准提交中已存在。README 开头的早期审计日期以及旧提示词不能覆盖最新数据中心功能授权；检查点中的完成/未完成项仍须对照当前源码核实。Agent 尚未完成真实数据库、真实模型与界面端到端验收，不能以“代码已发布”代替功能可用。

可发给网页端 GPT：

> 请读取 lihaopeng1997/PengToolsV4 的 ui/prism-v1 分支。先报告你实际读取的完整 SHA，并确认包含 03fc20e9b84489761d1a9f8c3d51e382eebbca3a。按 docs/project/GIT_WEB_HANDOFF_2026-09-16.md 的顺序读取项目入口、数据中心需求、开发文档和检查点，然后用 tools/data_center/host_relational.py 与 host_nosql.py 的源码证明读取成功。区分已实现、假驱动验证、真实环境待验收；缺少仓库权限或只能读取旧分支时明确说明，不根据聊天内容补造源码结论。

## 验证边界

本轮已通过 Vue typecheck 和 verify:embedded 基础检查；用户澄清任务是网页端 GPT 读取代码，因此未追加桌面运行、打包或业务测试。Git 引用一致不证明 ChatGPT 连接器权限、索引刷新或实际会话读取成功；需网页端回报实际 SHA 和上述文件内容才能完成该端验收。本轮没有修改账户连接器配置。
