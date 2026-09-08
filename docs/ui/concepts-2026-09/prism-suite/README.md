# 晴空棱镜 · 完整软件 UI 预览

用户已选定 02 晴空棱镜。本目录用于整套软件的设计审阅，包含独立 HTML 交互原型与模拟数据；不修改正式 Python/Vue 程序，不读取用户 data、连接配置、密码、会话或真实日志。

打开 `index.html`。本地相对资源，无 CDN，无网络请求。普通浏览器双击可用；所有变更只保存在本页内存，刷新恢复。

## 覆盖清单与源代码依据

| 页面或组件 | 当前代码依据 | 设计覆盖 |
| --- | --- | --- |
| 软件边框 / 菜单 / 多标签 | main_window.py、ui/navigation_model.py | 品牌、最小化/最大化/关闭预览、图标栏/文字栏、二级菜单、搜索、标签页、托盘菜单 |
| 首页 | frontend/src/dashboard/DashboardApp.vue、panels/test_points_editor.py | 详细月度上线任务、完成与测试点、统计、工具入口 |
| Oracle / MySQL / OceanBase / 达梦 | main_window.py:1014、panels/ai_workbench_panel.py | 对象树、连接表单、SQL 编辑、查询结果/消息/历史、结构、AI 建议 |
| Redis | panels/db_redis_panel.py | Key 树、类型/TTL、值查看、重命名、过期时间、删除、命令演示 |
| MongoDB | panels/db_mongodb_panel.py | 集合树、JSON 过滤器、文档/表格、插入、删除、Shell |
| 模型聊天 / 工作 | panels/model_chat_panel.py、panels/agent_workbench_panel.py | 会话列表、模型选择、技能、聊天；绑定目录、任务步骤、运行/停止 |
| 需求管理 | panels/requirement_panel.py、panels/test_points_editor.py | 月份/状态检索、台账、详情、说明/文件/SQL/测试点、编辑、新建、删除确认 |
| 发版联动 | panels/sql_panel.py、panels/ticket_submit_dialog.py | 选择需求、SQL 检查、升级/回退/校验预览、提签表单与文档预览 |
| 接口文档更新 | panels/docx_panel.py | 文档列表、模板、SQL、预检、字段变更与生成结果 |
| 日报 / 自我学习 | panels/personal_panel.py | 日期、今日完成/问题/明日计划、预览与保存；资料检索、分类、阅读和新增 |
| 日志排查 | panels/ops_log_panel.py | 服务器/服务/文件、SSH 模拟会话、关键字、终端、批量导出预览 |
| 命令库 | panels/ops_panel.py | 分类检索、参数、命令生成和复制，无真实远程执行 |
| 加解密 | panels/gateway_panel.py | 报文输入、SM2+SM4 参数、请求/响应结果与格式工具跳转 |
| 接口排查 | panels/interface_debug_panel.py | 监听状态、请求列表、过滤、概览/请求/响应/请求测试、失败示例 |
| 格式工具 | panels/format_panel.py | JSON/XML/SQL/文本页签、编辑、格式化、校验和错误提示 |
| 证件类型 / VIN | panels/credit_panel.py、panels/vin_panel.py | 条件、个人/单位、数量、结果表与校验展示；只用显式标记的无效示例号 |
| 设置 | panels/settings_panel.py | 外观、密度、浮窗、提醒、模型、数据库、关闭行为等分区 |
| 悬浮框 | ui/quick_panel.py、ui/floating_shortcuts_editor.py | 可拖动胶囊、快捷工具/AI 对话、最多 6 入口、顺序调整、收起 |
| 图标 / 按钮 / 弹框 / 提示 | ui/design_system.py、ui/confirm_dialog.py | 独立设计展板、语义图标、按钮状态、表单/确认/错误/成功/空状态 |

## 设计原则与边界

- 冰白、薰衣草紫、淡青；外壳、浮窗、弹窗使用磨砂玻璃。SQL、终端和密集表格采用稳定底色。
- 各模块采用匹配任务的布局，不把数据库等密集工作台改成首页卡片。
- 当前源码的实际上线日期/上线月份保留；不重新引入已移除的“计划上线”业务字段。
- 查询、SSH、AI、证件、VIN、国密和文件导出展示均为模拟；不声称真实执行或算法校验通过。JSON 和文本基础转换在浏览器本地真实处理。
- 图标是可缩放 SVG；`brand.svg` 提供软件标志，展板展示 16/24/32/64 尺寸、窗口按钮和托盘图标。
- 正式落地时映射回 ThemeManager、layout_metrics 和既有 QWebChannel 适配；数据库等保留 PyQt 边界。HTML 不等于原生透明窗口实现。
- 初始工作区已有 `resources/style.qss` 修改，不属于本次交付，保留。初始 SHA-256：D1CEC1660DEB8FE040D44E609D52203D2BD7061BD6F73A9D985CA8F496ACABA9。

验证结果见 `verification.md`。最终审美选择由用户审阅。

## 2026-09-08 修订

- 首页主图形增加轻微浮动、旋转与呼吸；统计图标错峰轻动，常用图标悬停反馈；支持减少动态效果。
- 星期后的每日一句使用12条公共领域经典诗文，按本地日期轮换，也可点击换一句；不调用网络或业务服务。
- 收起的悬浮入口改为52×52单图标，点击展开、拖动移动；快捷工具保留在展开面板内。
- 详细落地要求见 [UI优化需求_晴空棱镜_Agent实施规范.md](UI优化需求_晴空棱镜_Agent实施规范.md)。最终原则：只处理UI，原功能逻辑不变；HTML模拟行为不是业务规范。

## V2.1 全面重构实施规格

[当前 UI 实施规格](UI优化需求_晴空棱镜_Agent实施规范.md)已升级到V2.1，纳入用户确认的单一晴空棱镜主题，包含技术可行性、零新增运行依赖决策、生产坐标、全模块与子窗口、8类Loading、组件接口、工单依赖、行为等价和验收命令。旧V1保留在archive，仅供追溯，不再用于施工。

项目背景、需求 AI 与开发 AI 的交接规则见 [项目入口](../../../project/README.md)。历史源码快照仅用于追溯，不代替当前分支。

Loading样式：在预览“组件与提示”点击“加载过程”。生产的延迟/驻留/token契约保持源码现状，演示时长不代表生产任务耗时。
