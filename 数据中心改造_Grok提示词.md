# 任务：PengToolsHub「数据中心」改造 + Redis/MongoDB 扫描展示修复 + Redis 键值管理

你是资深全栈开发，请对仓库 PengToolsV4（Python 3.12 + PyQt6 离线桌面工具台，工作目录 D:\development\workspace\WorkBuddy\PengTools）执行以下四项改造。**严格按边界执行，禁止扩大改动范围，改完必须自测。**

## ⚠️ 铁律（违反任何一条即返工）
1. 只允许改四处：① 文案「SQL 控制台→数据中心」；② Redis 对象树与字段详情展示；③ MongoDB 对象树与字段推断展示；④ Redis 键值管理交互（DB 切换、新增 Key、模糊搜索、值查看/格式切换、删除、刷新、TTL）。
2. 禁止改动：连接建立（tools/db_connect.py 的 open_connection）、查询执行核心逻辑（_run_redis 的只读分支 / _run_mongo / run_read_query）、写命令分类门禁（sql_guard.classify_statement 与 REDIS_WRITE 集合）、SQL 草案生成（ai_sql_draft / tameng_agent）、凭据加密（secure_store）、requirements.txt 与 scripts/build_release.ps1（redis/pymongo 依赖与 hidden-import 已含，勿动）。
3. 只改「显示文案字符串」，不改类名/函数名/变量名/导航 index/图标。AiWorkbenchPanel 类名、ai_workbench_panel 属性名、导航 index 14、图标 database 一律保持不变。
4. Redis 扫描只能用只读命令（TYPE/HKEYS/HLEN/LLEN/SCARD/ZCARD/STRLEN/TTL/GET）。写操作（新增 Key 的 SET/HSET/RPUSH/SADD/ZADD、删除 DEL、设置 TTL EXPIRE）必须复用现有 sql_guard.classify_statement 的 REDIS_WRITE 门禁，弹「需确认写操作」对话框（danger=True），默认焦点取消，禁止绕过。**禁止新增 FLUSHDB/FLUSHALL/CONFIG/SHUTDOWN 入口按钮**。Mongo 采样只能用 find().limit(N)，禁止 aggregate/mapReduce，禁止回显完整文档内容。
5. Oracle/MySQL 原有展示不得回归。
6. **不为 Redis 另起独立导航/面板/Stack 页**——键值管理复用现有数据中心面板的中栏（替换 sql_tabs 位置，用 QStackedWidget 或 setVisible 按 dialect 切换），值查看复用下栏 result 表格，严格遵守现有页面骨架四层规范（L1 页头/L2 工具栏 ≤8 按钮/L3 筛选条不放写按钮/L4 ds-card 内容区）。

## 一、文案改名「数据中心」（共 6 处字符串，精确替换）
1. ui/navigation_model.py 第 21 行：`('SQL 控制台', 'SQL Console', ...)` → `('数据中心', 'Data Center', ...)`
2. ui/navigation_model.py 第 84 行：`'SQL 控制台：连接、结构快照与多标签编辑'` → `'数据中心：连接、结构快照与多标签编辑'`
3. main_window.py 第 1013 行：`'SQL 控制台 · 多标签编辑与结构快照'` → `'数据中心 · 多标签编辑与结构快照'`
4. main_window.py 第 1027 行：`'SQL console · multi-tab editor and schema snapshot'` → `'Data center · multi-tab editor and schema snapshot'`
5. panels/ai_workbench_panel.py 第 275 行：页头 title `'SQL 控制台'` → `'数据中心'`
6. panels/ai_workbench_panel.py 第 565 行（set_language）与第 655 行（_title 返回）：`'SQL 控制台'`→`'数据中心'`、`'SQL Console'`→`'Data Center'`

可选：ai_workbench_panel.py 第 2 行 docstring 同步改为「数据中心」，不影响功能。
不要改 tools/sql_guard.py、tools/db_connect.py、panels/sql_panel.py 等含 "sql" 但非本页面的模块。

## 二、Redis 扫描展示修复
根因：展示层把 Redis key 的「数据类型」当成了库名分组，且给每个 key 塞了假字段 `{'name': kind, 'data_type': kind}`，导致层级错乱、点开详情像没数据。

改法：
1. panels/ai_workbench_panel.py 的 `_rebuild_tree`（约 1101-1129 行）：对 dialect in ('redis','mongodb') 时改为「单层平铺」——根节点下直接挂每个 key/collection 节点，不套 schema 分组；oracle/mysql/dameng/oceanbase 保持原有 owner 分组逻辑不变。
2. tools/schema_snapshot.py 的 `_scan_redis`（约 560-582 行）：为每个 key 生成真实摘要字段替换假字段。按 key 类型（conn.type 只读）：
   - string → 字段 [{'name':'value','data_type':'string','comment': value 前200字符}]
   - hash → 用 HKEYS 取 field 列表（截断前若干）作为字段
   - list → [{'name':'length','data_type':'int','comment': llen}]
   - set → [{'name':'cardinality','data_type':'int','comment': scard}]
   - zset → [{'name':'cardinality','data_type':'int','comment': zcard}]
   - none/未知 → 空列表或提示
   异常一律吞掉降级为空，不要抛错中断扫描。返回结构仍是 (objects, truncated)，字段须满足 _clean_object 的清洗字段结构（name/data_type/nullable/position/comment/primary_key/indexed），不要改 _clean_object 本身。
3. 可选增强：扫描完成提示里体现当前 Redis DB 序号（如 conn_meta 追加 DB=0），避免用户误判连错库。

## 三、MongoDB 扫描展示修复
根因：`_scan_mongo`（tools/schema_snapshot.py 约 531-557 行）只用 find_one() 取第一条文档顶层 key，嵌套/数组/其他文档独有字段全漏，空集合 columns 为空；且 object_type='COLLECTION' 被当成分组名导致层级错乱。

改法：
1. `_scan_mongo`：采样 find().limit(20) 多条文档，递归合并所有 key（嵌套用点号路径如 a.b.c，数组元素用 a[] 或 a.0 标记），data_type 用 type(value).__name__；空集合 columns 兜底为 [{'name':'_id','data_type':'object',...}]，不要返回空。
2. 树分组修复复用「二、改法 1」（_rebuild_tree 对 mongodb 同样单层平铺），不要重复实现。
3. 保持返回 (objects, truncated)、object_type='COLLECTION'、inferred=True 不变。

## 四、Redis 键值管理（按 dialect 差异化功能区，不新起面板）
目标：当前连接 dialect=='redis' 时，中栏从「SQL 多标签编辑器」切换为「Redis 键值管理区」；关系型方言仍显示 sql_tabs，一字不改。参考 Another Redis Desktop Manager 的交互，但**复用现有面板骨架，不另立面板**。

挂载点（已核对源码）：
- 中栏当前为 `middle → editor_row + sql_tabs`（panels/ai_workbench_panel.py 约 390-413 行）。用 QStackedWidget 包一层（index0=sql_tabs，index1=Redis 功能区），连接切换时按 dialect 切 index。
- 「选中 key 后展示 value」复用下栏 result 表格（约 554 行 `self.result`，`_fill_result` 已能接收 {columns, rows}），不新造结果控件。

功能清单与实现要点：
1. DB 下拉：QComboBox 列出 DB0-DB15（默认选中连接元信息 database 字段，默认 0）。切换后自动重扫（open_connection 已按 db=db_index 连接；面板层切 DB 用 conn.select(db) 或重建连接后重新 scan_schema + _rebuild_tree）。
2. 新增 Key：按钮 + 小表单（key 名 / 类型 string|hash|list|set|zset / 初值）。按类型发 SET/HSET/RPUSH/SADD/ZADD，走 REDIS_WRITE 门禁确认。
3. 模糊搜索：复用左栏已有 object_filter + _filter_tree（前端过滤），服务端 SCAN MATCH 已在 _run_redis，不要重复实现。
4. 选中 key 展示 value：按 key 类型发只读命令——string→GET、hash→HGETALL、list→LRANGE 0 -1、set→SMEMBERS、zset→ZRANGE 0 -1 WITHSCORES，结果喂给 _fill_result 渲染到下栏 result。
5. 格式切换 TXT/JSON/XML：QComboBox 或 QToolButton 菜单三选。纯前端视图转换（TXT 原样、JSON 缩进/解析、XML 转义包裹），不写回 Redis、不改底层 value。
6. 删除 key：按钮 + 确认对话框（二次确认含 key 名）→ 执行 DEL（已在 REDIS_WRITE）→ 删除后自动刷新键列表。
7. 刷新：重新读取当前 key 的 value 或重扫键列表。
8. 设置 TTL：按钮 + 输入框（秒，-1 表示持久化）→ 执行 EXPIRE（已在 REDIS_WRITE）→ 门禁确认。

类型覆盖（全部常见类型）：string / hash / list / set / zset，五种都要能在下栏正确展示。

重要：底层只读能力 _run_redis（tools/db_connect.py 380-450 行）已全部具备，本次只在面板层新增「按类型发只读命令 + 格式化视图」的方法，**不要改 _run_redis 内部逻辑**。写命令分类 sql_guard.REDIS_WRITE 已含 set/del/expire 等，直接复用，不要改 sql_guard。

## 五、完成后的自测（必须做，并在回复里汇报结果）
1. 全量搜索「SQL 控制台」，确认 UI 可见字符串已全部替换，仅剩代码注释/文档。
2. 语法检查：python -m py_compile tools/schema_snapshot.py panels/ai_workbench_panel.py ui/navigation_model.py main_window.py（用项目 venv：C:\Users\Lenovo\.workbuddy\binaries\python\envs\pengtools\Scripts\python.exe）。
3. 若环境允许，跑定向测试：tests/test_ai_object_tokens.py、tests/test_page_skeleton.py、tests/test_sql_guard.py（不强制全量）。
4. 代码自查：确认未改 open_connection、_run_redis（只读分支）、_run_mongo、run_read_query、sql_guard（classify_statement 与 REDIS_WRITE）、ai_sql_draft、tameng_agent、secure_store、requirements.txt、build_release.ps1。
5. 逻辑自查：dialect=='redis' 时中栏显示键值管理区、切 DB 自动重扫、五种类型值查看正常、三种写操作均弹确认门禁、关系型方言 sql_tabs 不回归、未新增独立导航/面板。

## 交付要求
- 只改上述明确列出的文件与函数，改动最小化，不重构无关代码。四项改造按「一、二、三、四」顺序落地。
- 回复中列出：改了哪些文件、每个文件改了什么（精确到函数/行）、自测结果、以及任何你判断有风险或不确定需要我确认的点。
- 若某处源码行号与你看到的不符，以函数名和字符串内容为准，不要硬套行号。
