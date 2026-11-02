# PengToolsHub 开发入口

先阅读根目录 [AGENTS.md](../../AGENTS.md)，核对实际分支、提交和工作区，再按本次任务读取相关源码。不要根据历史文档中的分支名切换工作区。

## 源码地图

```text
run.py → main_window.py → panels/ → tools/、ui/、config.py
```

`panels/` 组合页面流程，`tools/` 提供无 QWidget 的业务逻辑，`ui/` 提供公共界面和桥接。前端源码在 `frontend/`，正式离线资源在 `resources/webui/vue/`。

| 事项 | 入口 |
|---|---|
| 产品功能与源码定位 | [PRODUCT_AND_SOURCE.md](PRODUCT_AND_SOURCE.md)；导航以 `ui/navigation_model.py` 为准 |
| 开发与交接协议 | [DEVELOPMENT_AND_AI.md](DEVELOPMENT_AND_AI.md) |
| 上下文与工作区 | [AI_CONTEXT_WORKFLOW.md](AI_CONTEXT_WORKFLOW.md) |
| 需求与开发提示词 | [需求提示词](AI_REQUIREMENTS_PROMPT.md)、[开发提示词](AI_DEVELOPMENT_PROMPT.md) |
| 数据中心 | [功能需求](../requirements/data-center/DATA_CENTER_AGENT_REQUIREMENTS.md) |
| 日志与系统排查 | [Linux 排查设计](../requirements/log-analysis/LINUX_OPS_AI_DESIGN_2026-10-29.md)、[日志分析需求](../requirements/log-analysis/LOG_ANALYSIS_AGENT_REQUIREMENTS_2026-10-28.md) |
| 需求状态与进度流程 | [需求流程说明](../requirements/workflow/REQUIREMENT_WORKFLOW_2026-10-29.md) |
| UI 规格与原型 | [Prism V2.1](../design/prism/README.md) |

## 当前约定

- 单一 Prism `calm` 主题；旧多主题方案属于历史材料。
- Vue 用于轻量全局界面；数据库、SQL、SSH、抓包和文件操作等工作台保留原生 PyQt。
- 主题、响应式布局和导航分别以 `ui/theme_manager.py`、`ui/layout_metrics.py`、`ui/navigation_model.py` 为准。
- 用户数据路径由 `config.local_data_dir()` 定义。目录整理不改变用户数据、连接或会话位置。
- 当前分支不发布开发测试、诊断脚本、测试报告和验收记录；这些文件保留本地。功能实现、静态检查、实际集成和人工验收必须分别说明。

历史交接已归入 [archive/](../archive/README.md)。其中记录的路径、版本、分支及验证结果仅适用于当时上下文。报告当前状态前重新检查源码，不将历史报告视为现行验证。
