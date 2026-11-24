# 文档导航

当前行为以对应分支源码和已确认需求为准。先读根目录 [AGENTS.md](../AGENTS.md)，核对实际分支、提交和工作区，再按本次任务读相关源码。历史材料不作为现行实现或验收证明，也不要根据历史文档里的分支名切换工作区。

## 源码地图

```text
run.py → main_window.py → panels/ → tools/、ui/、config.py
```

`panels/` 组合页面流程，`tools/` 提供无 QWidget 的业务逻辑，`ui/` 提供公共界面和桥接。前端源码在 `frontend/`，正式离线资源在 `resources/webui/vue/`。

| 事项 | 入口 |
|---|---|
| 版本更新日志 | [CHANGELOG.md](../CHANGELOG.md)（与 `resources/help/release_notes.json` 启动公告同步） |
| 产品功能与源码定位 | [PRODUCT_AND_SOURCE.md](project/PRODUCT_AND_SOURCE.md)；导航以 `ui/navigation_model.py` 为准 |
| 开发、复核与上下文 | [DEVELOPMENT_AND_AI.md](project/DEVELOPMENT_AND_AI.md) |
| 数据中心 | [DATA_CENTER.md](requirements/data-center/DATA_CENTER.md) |
| 日志与系统排查 | [LOG_ANALYSIS.md](requirements/log-analysis/LOG_ANALYSIS.md) |
| 需求状态与进度流程 | [需求流程说明](requirements/workflow/REQUIREMENT_WORKFLOW_2026-10-29.md) |
| UI 规格与原型 | [Prism V2.1](design/prism/README.md) |

## 当前约定

- 单一 Prism `calm` 主题；旧多主题方案属于历史材料。
- Vue 用于轻量全局界面；数据库、SQL、SSH、抓包和文件操作等工作台保留原生 PyQt。
- 主题、响应式布局和导航分别以 `ui/theme_manager.py`、`ui/layout_metrics.py`、`ui/navigation_model.py` 为准。
- 用户数据路径由 `config.local_data_dir()` 定义。目录整理不改变用户数据、连接或会话位置。
- 当前分支不发布开发测试、诊断脚本、测试报告和验收记录；这些文件保留本地。功能实现、静态检查、实际集成和人工验收必须分别说明。

## 按目录查阅

| 目录 | 内容 |
|---|---|
| [architecture/](architecture/) | 项目架构与分层说明 |
| [design/prism/](design/prism/README.md) | 现用 Prism V2.1 规格与离线交互原型 |
| [design/prototypes/](design/prototypes/) | 其他视觉原型与生成模板 |
| [requirements/data-center/](requirements/data-center/DATA_CENTER.md) | 数据中心目标、已落地边界，以及仍待处理的 Redis / Mongo 事项 |
| [requirements/log-analysis/](requirements/log-analysis/LOG_ANALYSIS.md) | 日志分析与 Linux 系统观察的当前行为 |
| [requirements/workflow/](requirements/workflow/) | 需求状态、进度节点和工作流配置 |
| [guides/](guides/) | 用户手册与版本说明 |
| [project/](project/DEVELOPMENT_AND_AI.md) | 开发约定、双 AI 交接和上下文规则 |
| [archive/](archive/README.md) | 历史设计，按用途归档 |

功能是否可用，要结合当前源码、运行环境和用户验收。实施记录不等于完整功能验证。

[安全与分发说明](SECURITY.md)适用于源码和发布资源。开发测试、诊断脚本、测试报告和验收记录仅保留本地，不随当前 Git 分支发布；软件中的测试点管理和接口请求测试功能仍是产品的一部分。

历史交接已归入 [archive/](archive/README.md)。其中记录的路径、版本、分支及验证结果仅适用于当时上下文。报告当前状态前重新检查源码，不将历史报告视为现行验证。
