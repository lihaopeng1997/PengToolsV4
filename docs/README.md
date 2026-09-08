# docs — 文档索引

当前项目与双 AI 接手先读 [project/README.md](project/README.md)，包含功能地图、已确认决定、开发协议及本轮审计。

## 分类

| 分类 | 位置 | 内容与权威性 |
|---|---|---|
| 开发约束 | [`../AGENTS.md`](../AGENTS.md) | 产品边界、发布、数据兼容与导航硬规则；代码和文档冲突时优先遵守。 |
| 架构 | [架构/](架构/) | 当前分层、依赖方向、数据流与测试约束。 |
| 方案 | [方案/](方案/)、[plans/](plans/) | HTML 设计稿、改造方案、技术分析与实施计划；代码描述现状，已确认需求描述目标；冲突按项目入口处理。 |
| 需求与设计 | [需求/](需求/)、[ui/](ui/)、[design.md](design.md) | 产品策略、UI/功能需求与历史改造计划。 |
| 提示词 | [提示词/](提示词/) | 人工或 Agent 接手使用的开发提示词；不属于运行时用户数据。 |
| 安全 | [SECURITY.md](SECURITY.md)、[SECURITY_TEST_BASELINE.md](SECURITY_TEST_BASELINE.md) | 离线边界、敏感数据限制与安全验证基线。 |
| 交接与历史 | [项目交接/](项目交接/)、[HANDOFF_LOG_OPS_2026-07-23.md](HANDOFF_LOG_OPS_2026-07-23.md) | 接手说明和已完成迭代记录；历史参考，当前接手入口为 `project/README.md`。 |
| 验收材料 | [验收/](验收/) | 验收报告、测试记录、截图；诊断脚本放在 `scripts/diagnostics/`，不混入文档。 |
| 用户说明 | [user_guide/](user_guide/) | 面向最终用户的 Word 使用说明；软件内置副本为 `resources/help/user_guide.html`。 |

提示词与技能分类：随程序分发的技能指令、任务模板和子 Agent 提示词位于 `resources/ai_skills/`；项目级 Harness 模板位于 `resources/harness/projects/`。两者都属于受版本控制的产品资源，改动时需记录用途、输入输出与安全边界。用户配置、连接信息和本机安装的技能只保留在 `data/ai_local.json`、`data/harness/` 等数据目录，严禁提交 Token、Cookie、SSH 密码或抓包明文。

代码与构建分类：`panels/` 为业务界面，`tools/` 为无界面逻辑，`ui/` 为公共界面能力，`resources/` 为随程序分发的静态资源（含 `ai_skills/` 与 Harness 模板），`tests/` 为定向验证，`scripts/` 只保留权威构建和开发工具。根目录的 `build_*.ps1` 仅是构建入口转发。

## 推荐阅读顺序

1. 仓库根 `AGENTS.md`
2. `project/README.md` 及其产品、开发、审计文档
3. 当前 UI 规格 V2.1（从项目入口进入），需要历史背景再读旧架构
4. 再按任务读 `需求/`、`方案/` 或 `ui/` 下对应材料（可能过时）
5. 终端用户：`user_guide/PengToolsHub_使用说明_V4.27.docx` 或软件内「使用说明」
