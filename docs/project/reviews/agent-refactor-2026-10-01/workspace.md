# DC04：统一数据中心工作区

日期：2026-10-01
分支：`dev/2026-09-23`
HEAD：`818fba72bf437de9bd49fcd5b198fb1270096a53`

## 本阶段交付

导航模型把数据中心固定为唯一顶层入口 14。旧入口 18–23 仍能定位相应引擎类型，但都收敛到同一个 `DataCenterPanel`；全局模块标签只保留 canonical 14。编程 Agent 仍在导航 17、stack 槽位 14。本阶段把 Data Center 放在独立 stack 槽位 47。

面板使用一个可搜索、可收起和可拖动的原生树组织项目、环境、引擎和连接。目录投影只显示连接摘要与 ID，完整配置继续由 catalog 持有；未知字段及已保存的 token 字段在编辑保存时保留。加载错误保留上次成功目录，坏记录局部提示，重复 ID 会显示为不可打开的歧义节点，不会任取一条连接。

默认 reader 直接只读解析现有连接文件中的 raw rows，不做归一或写回；默认 reader 首次读取时文件缺失视作空目录，任何一次成功读取后文件缺失则作为读取错误保留 last-good 并显示提示。合法的 `items: []` 仍会清空目录。现有文件的 JSON、shape 或读取错误会抛给 catalog；错误状态持续期间 catalog 会拒绝保存和删除，避免旧写入路径把无法读取的文件当作空表覆盖。

只有显式打开连接才按 `connection_id` 懒创建并复用内部工作标签。SQL、Redis 和 MongoDB 仍使用各自原生宿主；宿主的连接选择被固定到打开时的 ID，初始构造、显示和树选择不会触发驱动连接。显式刷新仍进入原宿主的工作路径。切换内部或全局标签不会清理已打开宿主的任务。窄窗口工具栏会换行，按钮保留完整标签。

旧 SQL、Redis、MongoDB 面板目前是工作宿主组合层，既有引擎工具仍可用。此阶段没有接入 manual session、显式事务生命周期或 typed result grid；这些由后续包承接，不能把本阶段视作事务化数据工作台已经完成。Data Center 右侧 Agent 使用现有 SQL 宿主的 Agent 面板与布局开关，本次没有单独重做其双语界面。

## 修改范围

- 新增 `panels/data_center_panel.py`、`tools/data_center/workspace_catalog.py`、`ui/data_center/workspace_tree.py`。
- 接线 `main_window.py`、`ui/navigation_model.py`、`ui/module_tabs.py`。
- 新增 `tests/test_data_center_workspace.py` 和 `tests/test_data_center_navigation.py`。
- 仅更新 `tests/test_prism_module_tabs.py`、`tests/test_prism_sidebar_chrome.py` 中与 canonical 14/旧入口兼容路由冲突的断言。
- 前端源与嵌入资源未修改。Python 导航 DTO 继续作为 Web 消费端的数据源。

## 验证

以下定向检查通过：

- `.venv-build/Scripts/python.exe -m unittest tests.test_data_center_workspace -v`：10 项通过，覆盖坏记录隔离、默认 reader 的 JSON/shape/读取错误及读取后文件缺失时保留 last-good、首次缺失为空与合法 empty 清空、重复 ID 禁止打开、敏感字段不进入树、连接 ID 固定、宿主懒创建与退出清理未完成时保留引用。默认 reader 回归将配置路径替换为 TEMP 文件，并在错误后验证不会走 saver。
- `.venv-build/Scripts/python.exe -m unittest tests.test_data_center_navigation -v`：2 项通过，覆盖 Python/Web 导航投影、14 与 18–23 汇聚到同一生产面板、17 和 Agent stack 槽位保持不变。
- `.venv-build/Scripts/python.exe -m unittest tests.test_prism_module_tabs tests.test_prism_sidebar_chrome -v`：21 项通过。
- `.venv-build/Scripts/python.exe -m py_compile ...`：本阶段 Python 源与测试文件编译通过。
- `git diff --check -- main_window.py ui/navigation_model.py ui/module_tabs.py tests/test_prism_module_tabs.py tests/test_prism_sidebar_chrome.py`：通过。
- `npm --prefix frontend run typecheck`：通过；它验证当前 Vue/QWebChannel 消费侧类型，不代表本阶段修改了前端。

Windows QPA 下用隔离 catalog、fake host、13pt 字体及 `WA_DontShowOnScreen` 生成并目视复核了 960×760 与 1160×760 的中英文截图。预览期间将 `config.save_settings` 和 `config.save_layout_splitter` 替换为 no-op；没有打开用户主窗口、连接数据库或访问真实模型。

截图保存在：

- `%TEMP%\pengtools-dc04-20261001\dc04-zh-960x760.png`
- `%TEMP%\pengtools-dc04-20261001\dc04-zh-1160x760.png`
- `%TEMP%\pengtools-dc04-20261001\dc04-en-960x760.png`
- `%TEMP%\pengtools-dc04-20261001\dc04-en-1160x760.png`

## 未验证边界

本地验收没有覆盖 960×640 尺寸；Agent 内部的中英文 caption/阶段文本仍待后续界面包验证。没有实际连接 SQL、Redis 或 MongoDB，没有调用模型，也没有对运行中的用户 UI 做操作。manual session、dirty-close 流程、事务提交/回滚与 typed grids 均未包含在本阶段。
