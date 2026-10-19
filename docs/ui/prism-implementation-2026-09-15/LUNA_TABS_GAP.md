# 全局模块 Tab 差距与最小实施方案

状态：草案，Tab 细节待主代理确认。本文是 2026-09-15 对用户最新反馈的只读审计与实现边界记录。自绘标题栏方向已经由用户确认，但全局模块 Tab 的关闭语义、生命周期和高度仍未完成实现确认。

用户反馈是：“实际软件与原型存在菜单、模块打开后的 Tab、顶部按钮缺失。” 本文只把这条反馈转成可评审的 UI 需求方向。没有把它解释成新增业务能力，也没有改变当前已有的关闭、连接、任务、数据或导航语义。

## 审计基准与范围

- 仓库：`D:\PengTools`。
- 分支：`ui/prism-v1`。
- 审计起点：`1545e0c7459f3d79eea302bdcee6b513bd12ee41`（`fix(packaging): include Prism UI resources and record preview delivery`）。
- 工作区已有 `resources/build_info.json` 修改、五份阶段报告和其他代理的诊断文件；本审计没有修改、覆盖或清理它们。
- 未启动业务面板、未访问数据库/SSH/模型/网络、未读写用户 `data`，也未触碰体验包进程 PID 38548。
- 对照对象：`docs/ui/concepts-2026-09/prism-suite/suite.js`、同目录 `model.js/pages.js`，以及生产 `main_window.py`、`ui/navigation_model.py` 和各 Panel 的 Tab 创建位置。

V2.1 第 5.1 节曾明确删除生产全局 36px 标签栏，并规定 `ContextHeader` 只展示当前模块和快速面板入口；这是旧的已发布 UI 约束。用户随后确认按原型改造自绘标题栏，并要求重新审查菜单、模块标签和顶部按钮；该新范围记录见 `docs/ui/prism-implementation-2026-09/PRISM_SHELL_REVISION_2026-09-15.md`。自绘标题栏已进入实施范围，模块 Tab 仍须先确认关闭与任务生命周期细节。上述范围调整不自动恢复原型中的设计展板、模拟数据、全局搜索或新的业务菜单。

## 原型实际提供了什么

原型的全局模块 Tab 是 `suite.js` 中的一套浏览器状态和 DOM 渲染：

1. `shell()` 创建 `<nav class="open-tabs" id="open-tabs" aria-label="已打开模块"></nav>`，位置在全局工具条下方、页面画布上方；同一个 `shell()` 还创建标题栏菜单 `文件 / 视图 / 帮助`、窗口按钮、搜索和通知入口。
2. `model.js` 的 `S.open` 初始为 `['home']`，是一个只存在于预览会话内存中的模块 ID 顺序；它没有接生产 Panel，也没有保存到应用设置。
3. `navigate(id)` 先保存原型输入框状态，再把唯一模块 ID 追加到 `S.open`，设置 `S.page`，最后调用 `render()`。它对四个数据库演示页重置的是模拟 SQL/结果字段；这段重置逻辑不能复制到生产。
4. `render()` 每次重建 `open-tabs`：当前页有 `active` 样式；首页没有关闭按钮，其余模块各有 `close-tab:<id>` 按钮。
5. `close-tab` 从 `S.open` 移除目标；如果关闭的是当前页，就把当前页切到移除后的 `S.open.at(-1)`，没有其他模块时回到首页。关闭非当前页不切换当前画布。
6. `contextmenu` 为标签提供“固定当前模块”“只保留当前模块”“关闭此标签”。其中 `tab-pin` 当前只是提示语，`S` 中没有持久的 pinned 集合；不能把它当成生产业务要求。

原型的 `MODULES` 还包含 `icons`、`components`、`windows` 三个设计展板 ID。它们没有生产导航索引，不得因为要补全 Tab 就出现在正式软件的 Tab 数据中。

## 生产现在做了什么

`main_window.py` 的主客户区是：侧栏 → `ContextHeader` → `page-body` → 一个 `QStackedWidget`。`_setup_ui_shell()` 创建固定数量的占位 host，`_mount_panel()` 把懒加载的 Panel 插入相应槽位；主窗口本身没有承载全局模块 Tab 的 `QTabWidget` 或 `QTabBar`，也没有 `open_tabs`/打开顺序集合。

`ContextHeader`（`ui/page_chrome.py`）只有当前模块图标、名称和快速面板按钮。生产没有原型标题栏里的浏览器式菜单；当前能找到的 `QMenu` 入口主要是底部 `user_chip` 绑定的 `_user_menu`（语言、悬浮栏、关于、使用说明、退出），以及各业务页自己的局部菜单。系统窗口标题栏仍由原生 `QMainWindow` 提供。

生产导航入口统一进入 `MainWindow._show_panel(index)`：

- 14（数据中心）和 15（模型）是父级折叠组，只展开/收起子菜单，不进入页面。
- 8（自我学习）和 9（日 报）都映射到同一个 `PersonalPanel` stack 槽位，但 `_show_panel()` 依据 nav ID 分别调用 `open_learning()` 与 `open_daily_report()`；Tab 身份必须使用 nav ID，不能用 stack 下标。
- 10→需求、11→格式工具、12→接口排查、13→日志排查、16→聊天、17→工作；18–23 是六个独立数据库导航 ID，分别映射到缓存的 DB Panel 槽位。
- 已存在的 Panel 会复用；首次进入才由 `_ensure_panel_for_nav()` 懒创建。页面切换后 `_show_panel()` 会更新 stack、侧栏选中态、状态栏和 ContextHeader。
- `_show_panel()` 在切换前会调用旧当前 Panel 的 `on_panel_deactivated()`。这不是“销毁 Panel”，但它可能清理页面 Loading；`AiWorkbenchPanel.on_panel_deactivated()` 还会停止/等待 DB worker 并把查询状态置为 `CANCELLED`，接口排查页会暂停抓包系统代理。这个现状与“模块 Tab 切换不取消任务”的期望存在必须先解决的边界冲突。

当前生产页内 Tab 是另一层概念，不能拿来当全局模块 Tab：

| 生产位置 | 现有 Tab | Tab 的真实职责 |
|---|---|---|
| `panels/sql_panel.py` | `self.tabs`：升级准备、发版联动、系统配置；`preview_tabs`：升级/回滚/验证 SQL | 同一发版/SQL 面板内的工作区和预览切换，已有 `currentChanged` 与数据刷新逻辑 |
| `panels/ai_workbench_panel.py` | `sql_tabs`：SQL 查询编辑标签；`side_tabs`：AI 助手/对象详情；`result_tabs`：结果/消息/历史 | 数据库工作台内部的编辑器、对象上下文和结果视图；`sql_tabs` 自身已有关闭逻辑 |
| `panels/format_panel.py` | `tabs`：JSON、XML、SQL、文本与开发辅助 | 格式工具内部不同处理器 |
| `panels/requirement_panel.py` | `detail_tabs`：文件库、SQL/发版、发布与联动、测试点 | 同一需求详情的功能分区，保存/测试点/联动信号不能由全局 Tab 接管 |
| `panels/db_redis_panel.py` | `side_tabs`、`value_tabs` | Redis 侧栏和 Key 值类型展示 |
| `panels/db_mongodb_panel.py` | `side_tabs`：文档视图、AI 助手、Shell | MongoDB 工作台内部视图 |
| `panels/interface_debug_panel.py` | `detail_tabs`、请求测试内部 `rt_tabs` | 接口记录详情和请求表单 |
| `panels/ops_log_panel.py` | `term_tabs`：SSH 终端会话；另有隐藏 `tabs` 兼容字段 | 终端会话自身有连接资源和关闭语义，不能由全局标签关闭按钮直接代替 |
| `panels/credit_panel.py`、`ui/json_viewer.py` | 证件类别、JSON 文本/树视图 | 页面内部视图切换 |

因此，当前“打开一个模块后没有模块 Tab”是生产与原型之间的真实差距；当前“页面内部有些 Tab”不是全局 Tab 缺失的证据，也不能通过移动/删除页内 Tab 来补差距。

## 最小恢复方案（待确认后开发）

### 组件边界

新增 `ui/workspace_tabs.py`，只负责全局模块标签的展示状态和交互：

- 使用轻量 `QFrame + QTabBar`（或同等 Qt 展示控件），提供当前 Tab、模块图标、名称、关闭按钮、水平滚动和键盘可达性。
- 组件只保存 `nav_index`、标题、图标角色、是否可关闭等展示元数据，不保存 Panel、连接、编辑器文本、任务状态、配置、凭据或业务对象。
- 数据由 `ui.navigation_model.py` 的 `get_nav_item()`、`display_name()`、`icon_role_for()`、`is_parent_nav()` 和 DB 导航判断派生；不复制一套模块名/图标表，不加入三个设计展板。
- 对外只发出“请求激活某 nav ID”“请求关闭某 nav ID”“请求上下文菜单”信号。Tab 点击最终仍由集成层调用已有 `_show_panel(nav_index)`，组件不直接操作 `QStackedWidget`。
- `ui/workspace_tabs.py` 不引入 `panels`，不读 `config`、数据库或本地文件；不新增运行时依赖。

拟新增测试 `tests/test_workspace_tabs.py`，本轮不创建实现文件和测试文件，待主代理确认后再做。

### 集成位置

唯一集成方是 `main_window.py`：在 `ContextHeader` 和 `page-body` 之间增加一个固定的全局 Tab 展示区；具体高度和窄屏策略需产品确认。原型约为 36px，但 V2.1 已删除该 36px 区域，不能把历史数字直接当成批准结果。

导航同步应集中在 `_show_panel()` 的成功路径，不在侧栏、首页、QuickPanel、Web Bridge、快捷键等每个入口各接一份：

1. 先保留已有 guard、父级判断、懒创建、异常处理、`on_panel_deactivated()`、stack 切换、状态栏和 ContextHeader 更新。
2. 叶子导航成功显示后，把 nav ID 去重加入 Tab 顺序并标记 active；再次点击同一入口只激活已有 Tab，不创建第二个 Panel 或第二个标签。
3. Tab 点击仅发出 nav ID，并回到 `_show_panel()`；不得直接 `stack.setCurrentIndex()`，否则会漏掉日报/学习分流、接口恢复、状态栏、ContextHeader 和懒创建逻辑。
4. 侧栏、首页、Web、QuickPanel 触发的原有 `_show_panel()` 也必须同步标签，保证 Tab 不是另一套导航状态。父级 14/15、非法 ID 和锁定的 8 不添加 Tab。
5. 8 与 9 要保留两个独立的 Tab 元素，即使它们共享一个 `PersonalPanel`；激活它们必须继续走 `_show_panel(8)` / `_show_panel(9)`，不能通过 stack 下标猜测当前工作区。

在 `_show_panel()` 当前返回值不区分“成功切换”和“异常回退”的情况下，集成层需要先确认如何避免失败打开留下假标签。推荐为集成阶段增加一个局部成功判定/返回契约，由主代理统一修改；本轮不偷偷改变 `_show_panel()` 的行为。

### Tab 顺序与关闭候选

建议先按原型保留会话内打开顺序：首次打开按追加顺序排列，首页常驻且不可关闭；重复打开只切换 active。是否支持“固定”、是否保存跨重启、是否限制标签数量都不是当前生产行为，需单独确认。

若产品确认沿用原型，关闭规则可定义为：

- 关闭非当前标签：只移除该标签，当前页面和当前 Panel 不变。
- 关闭当前标签：从打开顺序移除后，切换到剩余顺序最后一个标签；没有其他标签时回首页。
- 关闭动作只删除 Tab 元数据，不调用 Panel 的 `close()`、`deleteLater()`、`_mount_panel()`、SSH 断开、数据库连接关闭、模型停止或编辑器清空。
- 首页不显示关闭按钮；关闭按钮必须有“关闭 XXX 标签”的 accessible name 与 tooltip，键盘焦点和 Esc 行为不抢占业务控件。

这里有一个必须在开发前裁决的生命周期问题：按照原型关闭当前标签需要调用 `_show_panel(备用 nav)`，但现有 `_show_panel()` 在切换前会触发旧 Panel 的 `on_panel_deactivated()`；对 AI 数据库工作台它会取消正在执行的查询，对接口排查它会暂停系统代理。若产品要求“标签切换/关闭不取消后台任务”，不能仅在 `workspace_tabs.py` 绕开这一点；需要主代理先把“纯视觉隐藏”与“业务暂停/取消”边界拆清楚并补充测试。当前文档不把“继续任务”写成已实现，也不授权修改这些业务生命周期行为。

## 顶部菜单和按钮的相邻差距

用户反馈同时提到菜单和顶部按钮，但它们不应塞进全局 Tab 组件：

- 原型 `shell()` 的文件/视图/帮助菜单、窗口置顶/最小化/最大化/关闭按钮属于标题栏/全局 Chrome。
- 生产基线保留原生 `QMainWindow` 系统非客户区；客户区只有 `ContextHeader` 和快速面板按钮，菜单主要在用户芯片和各页面局部 `QMenu`。新范围已经确认按原型进入自绘标题栏实施，但该组由指定的标题栏代理集成，本文只记录它与 Tab 的边界。
- 生产补齐这些展示前，应另建 Shell/标题栏需求，逐个把原型动作映射到已有 QAction/槽，先区分已存在的功能和原型模拟动作。不能因为原型有通知、搜索、标签固定或设计展板，就新造业务能力。
- 此相邻工作应与 `workspace_tabs.py` 分开评审，以免一个展示组件同时改变窗口关闭、系统 Snap、托盘、快捷键或业务菜单。自绘标题栏的关闭必须沿原 `closeEvent`、确认及托盘流程；Tab 组件不拥有窗口关闭按钮。

## 验收与测试计划

### 组件级

`tests/test_workspace_tabs.py` 在实现阶段应覆盖：

- 有效叶子 nav 去重、追加顺序、active 同步；14/15、非法值、未解锁 8 不可添加。
- 首页不可关闭；关闭非当前不改变 active；关闭当前按已确认的回退规则工作；最后回首页。
- 8/9 即使共享 stack 仍显示两个 nav 身份；18–23 每个数据库 nav 独立。
- Tab 点击、关闭、右键菜单信号各触发一次；不会构造或销毁 Panel，不访问真实数据。
- 13/16 字号、窄宽度、超长模块名、横向滚动、禁用/选中/焦点态、中文/英文 accessible name。

### 主窗口集成级

待集成后在现有 `tests/test_prism_main_shell.py` 或独立定向测试中覆盖：

- 侧栏、首页、Web Bridge、QuickPanel 和 Tab 点击都最终进入同一个 `_show_panel()`；重复进入保持 Panel 对象身份。
- 8/9 分流仍调用对应 `open_learning()` / `open_daily_report()`；数据库 18–23 仍按方言创建和复用正确面板。
- 打开/切换 Tab 不改变输入文本、选中项、splitter 偏好、SQL 标签、SSH 会话、模型历史或需求编辑状态。
- 当前/非当前/首页关闭后的 active、ContextHeader、侧栏选中态、状态栏和 loading 清理与确认的生命周期策略一致。
- `_show_panel()` 懒创建失败时不遗留假 Tab；父级和锁定入口不产生标签。

### 视觉与运行级

在主代理完成集成后，用隔离演示数据检查 1440×900、1280×800、1100×720、960×640，展开/图标栏、13/16 字号；记录 PNG 与脱敏 JSON 的 Tab 数量、active ID、内容边界和 overflow。保留系统边框时不声明自绘标题栏已完成；标题栏若单独获批，另按窗口拖拽、Snap、DPI、托盘和关闭确认验收。

不使用 HTTP 绕过本地文件浏览器限制，不用原型模拟数据证明生产业务正确，不以静态 HTML 截图替代实际 Qt 信号/Panel 生命周期测试。

## 待确认决策

1. 是否恢复生产全局模块 Tab；若恢复，目标高度是否沿用原型约 36px，窄屏是否横向滚动。自绘标题栏已确认，Tab 仍需单独确认。
2. 首页是否常驻且不可关闭；是否需要“固定”与“只保留当前”菜单。原型的固定目前没有真实状态，不能直接照搬为产品能力。
3. 当前 Tab 关闭后的回退是否确认采用“移除后打开顺序最后一个/首页”；关闭当前时对运行中查询、抓包、Agent、SSH 的生命周期以什么规则为准。
4. 是否只保留当前会话的打开顺序；暂不建议把它写入现有设置，除非另有需求说明数据格式、升级兼容、失败回退和隐私边界。
5. 自绘标题栏方向已确认；菜单与按钮仍须按 Shell 方案逐项映射到当前已有动作，原型中的模拟/展板入口不进入正式导航。

在这些决定明确前，最小安全停点是：交付本审计文档，由主代理确认范围后再新增 `ui/workspace_tabs.py` 与定向测试，最后由唯一集成方修改 `main_window.py`。本轮没有生产代码变更。
