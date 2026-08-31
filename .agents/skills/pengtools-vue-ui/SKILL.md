---
name: pengtools-vue-ui
description: PengToolsHub Hybrid Web UI 开发规范。在涉及 frontend/、Vue 3、TypeScript、Vite、Chrome/Sidebar、Dashboard、QWebChannel Bridge、主题或响应式适配时触发。
---

# PengToolsHub Vue UI SOP

## 1. 混合架构边界 (Hybrid UI Architecture)

- **Vue 3 + TS + Vite** 默认负责：
  - Chrome / Sidebar 框架
  - Dashboard 首页
  - 通知与概览面板
  - 轻展示型全局 UI
- **PyQt QWidget** 默认保留：
  - 数据库工作台与 SQL 控制台
  - SSH / 终端重交互页面
  - 接口排查与抓包控制台
  - 大型树（Tree）、表格（Table）、Splitter 密集型工作台
  - 本地文件与系统级桌面交互
  - 重业务流程工具
- **禁止主动全量迁移**：不得因为引入了 Vue 就将 QWidget 工具全量重构为 Web。

## 2. Bridge 通信与安全规则 (Bridge Contract)

- **定位**：`Bridge` 是 UI Adapter，**不是** Business Service。
- **目录与依赖**：Bridge 文件置于 `ui/`，严禁 `import panels`。
- **数据注入**：由 `MainWindow`、provider 或 callback 单向注入业务数据。
- **契约对齐**：Python producer 与 TypeScript adapter 严格保持 DTO 定义对齐；按领域拆分（如 `ChromeBridge` / `DashboardBridge`），避免 God Bridge。
- **Web 运行边界**：
  - Vue 不得直接读写本地文件或数据库。
  - Vue 不得直接任意调用业务 Python 函数。
  - 严禁加载在线 CDN、远程字体、外部 JS/CSS。
  - 严禁自建公网暴露服务。

## 3. UI 权威源 (UI Authorities)

- **Theme 权威**：Python `ThemeManager` 的语义 token 为产品主题唯一权威。Frontend 不维护冲突独立色板；Chrome 与 Dashboard 共享前端 semantic CSS 变量。
- **Responsive 权威**：产品断点以 `ui/layout_metrics.py` 为唯一标准。Vue 不建立冲突断点体系。
- **Navigation 权威**：导航项以 `ui/navigation_model.py` 为唯一事实源。菜单项遵循 `icon + 1 primary label` 规则。

## 4. 产物生成与交付 (Artifacts & Acceptance)

- **源码与生成物**：
  - 源码目录：`frontend/**`
  - 嵌入式生产构建物：`resources/webui/vue/**`
  - 生产产物若有更新，必须由前端源码 build 产生，且源码与产物必须在**同一 commit** 提交；禁止手改 bundle。
- **UI 验收标准**：
  - 自动化负责：Typecheck、Build 编译、Contract 检查、产物一致性校验。
  - 最终视觉：排版层级、空间密度与桌面交互体验交由用户人工视觉验收，任务状态通常返回 `READY_FOR_UI_REVIEW`。
