---
name: pengtools-lean-test
description: PengToolsHub 精益测试规范。在任何修改任务需要确定最低充分验证等级与执行验证时触发，避免过度测试与全量套件浪费。
---

# PengToolsHub Lean Testing SOP

## 1. 核心原则 (Core Principle)

**Minimum Sufficient Evidence**：只针对当前修改带来的真实风险进行最低充分验证，不扩大测试面，不以过度测试拖慢迭代。

## 2. 验证等级矩阵 (Test Levels)

| 等级 | 适用范围 | 验证动作 | 期望输出 / 状态 |
| :--- | :--- | :--- | :--- |
| **L0** | 文档、规则、Governance | `git diff`、`git diff --check`、`git status`。**禁止**跑产品测试、Build、启动 GUI。 | `PASS` / `TASK_LOCK_FAILED` |
| **L1** | Vue、CSS、UI presentation | Typecheck（`vue-tsc` / `tsc`）、必要 build、嵌入产物变更校验。视觉效果交由人工验收。 | 通常为 `READY_FOR_UI_REVIEW` |
| **L2** | 普通 Python 业务、Panel 页面 | 仅运行最相关的 targeted tests。明确的 regression 场景最多新增 1–2 个精准测试。 | `PASS` / `FAIL` |
| **L3** | Runtime、并发、IPC、WebEngine、SSH 终端核心、抓包生命周期 | Targeted regression + 必要的最小 runtime smoke（如无头/轻量化状态校验）。 | `PASS` / `FAIL` |
| **L4** | 依赖变更、安全审计、打包、发布 | 仅在发布/安全专项执行：隔离环境 full suite、`pip-audit` 依赖扫描、密钥扫描、发布构建及包 smoke。 | `PASS` / `FAIL` |

## 3. 测试约束与纪律 (Discipline)

- **未修改不测**：未修改的子系统默认不执行回归测试。
- **无回归不增**：无明确 regression 缺陷默认不新增自动化测试用例。
- **已有失败隔离**：发现无关的既有测试失败时记录为 `EXISTING_FAILURE`，禁止借故扩大当前任务范围。
- **禁止全量滥用**：常规任务禁止盲目执行 `unittest discover`、全量 build 或构建大型 GUI 桌面自动化。
- **自动化非全能**：自动化测试不能替代人工对 UI 视觉、层级与交互体验的人工验收。
