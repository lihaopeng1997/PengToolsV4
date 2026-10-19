# 达梦手动关系库工作台：有限后端计划（2026-10-07）

## 基线与决策状态

root 2026-10-07 修订决策：下文原八路径首阶段仍是历史提案，未 GO。后续官方 service-file 补充明确 `dmsvc_path` 为每次 connect 的目录参数，但文件/所选 service 项缺失会 fallback；应用侧私有目录预检及连接后属性核对不能证明 native 并发隔离或 stalled-login deadline。普通 UI/provider 继续明确 unsupported、默认 connector 继续 `DAMENG_SERVICE_TIMEOUT_UNVERIFIED`，只读 gate 不变。另收敛了新专用 DM driver/factory、纯方言及专有 fake 测试的准备方案，不加入测试启用开关，也不声称生产 DM 已可用；尚未授予实现 GO。两个作者提案完整 hash 校验副本在 `evidence/dameng-plan-revision-20261007/`，参见 copy-manifest.json。真实达梦能力仍是完整目标的必需项，不因有限准备包而删除。

这是当前脏工作树字节的只读审查结论；**HEAD 仅作仓库锚点，不能代替源码基线**。审查时分支为 `dev/2026-09-23`，HEAD 为 `818fba72bf437de9bd49fcd5b198fb1270096a53`。工作树已有大量未提交改动，且 history 产品由另一作者负责；实施前必须重新核对本计划涉及的实时后端文件和该作者冻结后的调用边界。未核实 upstream/remote 一致性。

本文件是提案，等待 root 明确首阶段边界后才可实现。此轮没有改实现、其他计划/验收/交接文件，没有提交或推送，也没有读取或写入用户配置、连接凭据、真实数据。当前环境 `importlib.util.find_spec("dmPython")` 为 `None`；没有导入驱动、建立连接或运行真实达梦测试。

## 已核对缺口

| 区域 | 当前脏工作树行为 | 对达梦的最小要求 |
|---|---|---|
| 手动 provider 门 | `manual_relational_providers.py` 仅放行四个精确组合：MySQL、Oracle、OceanBase MySQL 模式、OceanBase Oracle 模式；达梦返回 `MANUAL_PROVIDER_UNSUPPORTED`。 | 增加一个达梦专用、精确 provider/mode/dialect 检查；保留现有四组原样。SQL 分类不能因 `dameng` 被映射到 Oracle 就宣称两者语义相同。 |
| 手动连接 | `RelationalManualDriverFactory` 只接受上述四组并调用 `connect_relational`。后者的 `_connect_dameng()` 明确以 `DAMENG_SERVICE_TIMEOUT_UNVERIFIED` 失败关闭。legacy `tools/db_connect.py` 有独立 `dmPython.connect(...)` 路径，但不是本手动 session 的固定目标、owner-thread 接线或超时证明。 | 新增专用手动连接分支；只在 session owner 线程延迟加载 dmPython、重载并比对精确 profile revision/choice、解析密钥、创建/使用/关闭连接。不要打开只读 lease 的达梦 gate。 |
| 手动提交 | `ManualRelationalDriver.configure_manual_commit()` 检查通用 `autocommit`/`get_autocommit`，没有 DM 的驼峰属性 `autoCommit`。dmPython 文档默认自动提交开启，并以整数 `1/0` 与 `DSQL_AUTOCOMMIT_OFF` 表示状态。 | 按文档设置 `autoCommit = dmPython.DSQL_AUTOCOMMIT_OFF`，再严格读回；只接受 typed `int(0)` 或 `bool(False)`。拒绝 `None`、字符串、`0.0`、其他真假对象；首条 SQL 前失败关闭。 |
| SQL 绑定、标识符、保存点 | ChangeSet dialect 只有 MySQL 百分号位置绑定、Oracle 命名/数字绑定。通用 driver 对非 MySQL 保存点采用未加引号名称，release 路径也不能代表 DM 的完整语法。 | 达梦绑定使用 DB-API `qmark` 的 `?` 和位置序列；标识符用 DM 双引号规则并把内部 `"` 写成 `""`；保存点名称须受限并双引号引用，使用文档中的 `SAVEPOINT`、`ROLLBACK TO SAVEPOINT`、`RELEASE SAVEPOINT`。 |
| DDL 状态 | `ManualSession` 已在成功或失败语句的分类 effect 为 `UNKNOWN` 时转为 `UNKNOWN`。达梦分类尚不存在。DM 文档说明手动模式 DDL 可先提交当前事务，DDL 自身提交受 `DDL_AUTO_COMMIT` 影响；部分 DDL 总是自动提交。 | 达梦 DDL 在未经真实环境验证前分类为 `UNKNOWN`。成功不能落成 `CLEAN`；失败也不能假定此前状态可回滚。会话当前要求 DDL 只能从 `CLEAN` 开始，这一安全约束保留。 |
| endpoint、schema 与服务文件 | `DataCenterProfileHost._database_from_profile()` 会把 `service_name`、`service`、`sid` 排在 `schema`/`owner` 前作为 `ConnectionChoice.database`；此通用 Oracle/MySQL 取值不构成 DM endpoint/schema 约定。 | 达梦专用规范须将网络 endpoint/service 与登录后默认 schema/owner 分开；拒绝猜测 `service` 或 `sid` 是 schema。无明确支持时，对 SSH、TLS、DSN、服务别名等路线失败关闭。 |
| 元数据、分页与导入 | `host_metadata.py` 没有 DM catalog template，`object_tree.py` 显示 DM 目录未接线，`table_source.py` 仅支持 MySQL/Oracle。`import_session.py` 通过 `create_changeset_dialect()` 取得 SQL policy；单加 dialect 不会使 DM 表引用变得可信。 | 第一阶段不开放对象目录、表编辑或导入；后续需 DM 原生 catalog 与签名表引用，再用变更集。不得把 Oracle `ALL_*` catalog 直接套用到达梦。 |
| Agent/只读连接 | `readonly_relational_connectors._connect_dameng()` 仍拒绝连接；只读适配矩阵中存在 DM 的策略描述，但未形成可用 driver lease/timeout gate。 | 手动用户 SQL 与 Agent 只读 lease 是两条权限边界。保持 `_connect_dameng()` 拒绝状态和现有只读测试；本计划不授权 Agent 使用达梦。 |

项目需求要求首版含达梦，并要求真实达梦集成；同一需求也明确假 driver 不能替代真实引擎。这里的 fake/TEMP 证据只用于结构和安全边界，**不能把达梦标成已验收**。

## 官方证据与边界

- [达梦 dmPython 接口文档](https://eco.dameng.com/document/dm/zh-cn/pm/dmpython-interface.html)：DB-API 属性和调用，包括 `threadsafety=1`、`paramstyle='qmark'`、`autoCommit`、`commit()`/`rollback()`、`login_timeout`（毫秒）、`connection_timeout`（秒）及 `dmsvc_path`。按线程安全声明，连接不得跨线程共享。
- 同一接口文档 §3.2.2.7 还列出只读 `Connection.current_schema`（对应 `DSQL_ATTR_CURRENT_SCHEMA`）、只读 `server`/`port`、`login_timeout` 与 `connection_timeout`；连接参数 `schema` 仅在创建 cursor 时生效。post-connect 属性核对能发现连接结果不匹配，但不证明 login 一定受限时，也不证明没有 service-file fallback。
- [达梦 dmPython 介绍](https://eco.dameng.com/document/dm/zh-cn/pm/dmpython-introduction.html)：连接参数/部署背景。文档事实不证明本机已安装驱动或任意现存 profile 字段适用于 DM。
- [达梦事务管理](https://eco.dameng.com/document/dm/zh-cn/pm/management-affairs.html)：手动事务下 DDL 可能先提交当前事务；DDL 自身行为依 `DDL_AUTO_COMMIT`；文档列出的 `ALTER TABLESPACE`、`ALTER USER` 有特别自动提交行为。计划不假设服务器当前取值，也不从 Oracle 行为推断。
- [达梦一致性与并发控制](https://eco.dameng.com/document/dm/zh-cn/pm/consistency-concurrency.html)：达梦保存点及回滚/释放语法依据。实施应再次定位原文并以目标 DM 版本验证 SQL。
- [达梦 SQL 介绍](https://eco.dameng.com/document/dm/zh-cn/pm/dm_sql-introduction.html)：DM SQL 语法参考；目标对象的大小写、双引号定界标识符和 schema 语义仍应由后续 catalog/集成测试锁定。
- [达梦用户模式关系](https://eco.dameng.com/document/dm/zh-cn/start/dm-user-mode-relation)：用户、模式关系参考；不能据此把 Oracle owner 规则视为完全兼容。

dmPython 文档描述 service 配置可覆盖 API 设置，并描述 `dmsvc_path`/服务项缺失时的 fallback 路径。现有只读 connector 因无法证明最终采用的服务文件与连接超时而拒绝 DM。实施时不得把 `login_timeout`/`connection_timeout` 的 API 入参当成已生效证明；在服务文件存在时须验证所选文件、唯一服务项、选项覆盖优先级及实际 endpoint。私有 TEMP 服务文件只可用于合成测试或经 root 确认的实现边界，不能覆盖用户全局文件、写凭据、静默落回另一 endpoint，且其生命周期须覆盖连接存活期。

目前上述官方资料**没有在本计划审阅范围内证明** `DDL_AUTO_COMMIT` 可由 dmPython API 或 `dm_svc.conf` 针对单个连接安全覆盖。因此该参数和当前服务端值都视为未知。即使参数被设置，DDL 自身的结果仍先按 `UNKNOWN` 处理，直到真实目标版本测试、错误路径与目标参数取值都纳入验收；不能因语句成功或 `rollback()` 成功报告事务 `CLEAN`。

## 建议首阶段：有限 backend GO（待 root 确认）

目标限于“可安全打开一个 DM 手动关系库 session、执行受支持的单语句 SQL、显式管理 DML 事务、为未来变更集提供方言 contract”。它不交付完整对象浏览/表编辑，也不改变只读 Agent 能力。历史产品作者的八个文件冻结前，不改调用 UI/工作台接线，不从那些实时文件推断最终入口。

### 拟拥有文件与函数边界

1. `tools/data_center/manual_relational_providers.py`
   - 保持 `inspect_manual_provider(choice: ConnectionChoice) -> ManualProviderCapability`、`classify_manual_sql(choice: ConnectionChoice, sql: str) -> StatementClassification`、`create_manual_session(target: ManualSessionTarget, choice: ConnectionChoice, *, table_scope: BoundManualTableScope | None = None, **factory_options: Any) -> ManualSession` 的外部签名。
   - 增加唯一规范 DM tuple `("dameng", "dameng", "oracle")` 的 capability 分支和 `classify_dameng_statement(sql: str) -> StatementClassification`。只读调用 `DataCenterProfileHost.capture_connection()` 得到：合成 profile 原 dialect 为 `dameng` 或 `oracle` 时，choice 均规范为该 tuple；实施测试固定这一归一化契约，不得通过忽略 dialect 或任意 alias 放宽。对已识别 DML 标 `DIRTY`，常见只读查询 `NO_CHANGE`，DM DDL `UNKNOWN`；控制语句、多语句仍拒绝，未能安全识别的语句保守归 `UNKNOWN`。
   - `supports_native_cancel=False`；timeout scope 只有在服务文件解析与 driver 选项已验证后才可报受支持。owner 无法被证明的连接不创建。

2. `tools/data_center/manual_driver.py`
   - 保持现有 `RelationalManualDriverFactory` 的四组 provider 允许集及调用行为不变；新增独立 `DamengManualDriverFactory(choice, *, profile_host=None, connector=None, secret_resolver=None, connect_timeout=8.0, read_timeout=15.0, table_scope=None)`，实现 `__call__(target: ManualSessionTarget) -> ManualDriver`。`ManualRelationalProviderFactory` 仅在精确 DM capability 成立时选此 factory。
   - 保留当前完整顺序：先比较 target、重载精确 revision、重捕获 choice 并逐字段比对，再验证可选 `BoundManualTableScope`，然后才解析 secret/调用 connector。每个步骤及任何 `dmPython` 导入、连接、cursor、fetch、commit、rollback、close 都在 `ManualSession` owner 线程。scope 与 `(connection_id, revision, provider, mode, dialect, database, tab_id, generation)` 绑定；不能把签名对象引用换成未签名表名。
   - DM driver 仅设置 `connection.autoCommit = dmPython.DSQL_AUTOCOMMIT_OFF`。读回严格接受 `type(value) is int and value == 0` 或 `type(value) is bool and value is False`；拒绝 `None`、字符串、`0.0`、其他整数及自定义真假对象。假驱动应使用整数 `autoCommit` 属性形状，同时覆盖明确拒绝值；不得静默改用 `autocommit`。
   - connect 时将精确捕获的 `target.schema` 传作 DM `schema`。因它只在创建 cursor 时生效，必须由 owner 线程创建验证 cursor 后读取只读 `connection.current_schema`，与 captured schema 精确比较，再关闭该 cursor 并允许首条用户 SQL。缺少 captured schema、属性不可读或不相等都失败关闭；禁止用 Oracle `SYS_CONTEXT`/`DUAL` 查询代替 DM 原生属性核验。
   - 可在连接后核对只读 `connection.server`/`connection.port` 与已捕获 endpoint，以及可读 timeout 属性与传入值；这些只是连接结果核验，不证明 login 已被 bounded，也不能证明隔绝 service-file fallback。native timeout/service 语义仍须真实 dmPython 验证，未验证前不得宣称已建立超时安全 gate。绑定只接受位置 `Sequence`，不接受命名 mapping；超时换算为 dmPython 文档单位。
   - 对保存点使用经文档确认的 DM `SAVEPOINT "name"`、`ROLLBACK TO SAVEPOINT "name"`、`RELEASE SAVEPOINT "name"`。保存点名由内部生成并严格限制字符，标识符双引号转义。`ddl_transaction_behavior(sql)` 对 DM 返回 `UNKNOWN`。

3. `tools/data_center/dameng_manual_connector.py`（新增）
   - 提议 connector 入口 `connect_manual_dameng(profile: Mapping[str, Any], secret: Any, *, connect_timeout: float, read_timeout: float) -> DamengConnectionLease`；lease 持有连接和私有服务配置生命周期，由 owner 线程释放。
   - lazy import `dmPython`；将 connect timeout 映射至 `login_timeout` 毫秒、statement/connection timeout 至 `connection_timeout` 秒；只接收经明确定义的 host/port 或显式受控 DM service endpoint。对超时/endpoint 选项不能证实生效、服务文件 fallback、或不支持的 DSN/SSH/TLS profile 字段一律失败关闭。临时配置不得承载密码。
   - 这里只是手动 connector，不调用或修改 `readonly_relational_connectors._connect_dameng()`，不构造只读 lease，也不触达真实用户 service 文件。

4. `tools/data_center/changeset_dialects.py`
   - 增加 `DamengChangeSetDialect(choice: ConnectionChoice, target: ManualSessionTarget)` 与 factory union；用 `?` 生成参数 marker 并保持值为 tuple；标识符和限定对象名采用 DM 双引号规则。只有 exact choice/target 绑定才创建 dialect。
   - `_validate_binding()` 和 ChangeSet 防护继续保留；不在此阶段宣称 importer 已经可用，因为当前 DM 没有可信 `VerifiedTableRef` / metadata provider。

5. 测试，仅使用既有测试面/新增 backend 测试，不跑全量发现：
   - `tests/test_data_center_manual_relational_providers.py`：四组既有 provider 回归、唯一规范 DM tuple、错误 mode/dialect 在 connector 前失败、精确 profile drift/signed-scope 拒绝。
   - 新增 `tests/test_data_center_dameng_manual_integration.py`：fake dmPython/connection/cursor 记录 thread id；连接、验证 cursor/current_schema、查询、fetch、savepoint、commit/rollback、close 只能同一个 owner。fake `autoCommit` 使用整数 0 属性；同时断言只接受整数 0/布尔 False，`None`、字符串、`0.0` 等在任何 SQL 前失败。DML 成功为 `DIRTY`；DM DDL 成功与失败都不会误报 `CLEAN`，成功 DDL 显示 `UNKNOWN`；schema 不匹配、readonly 事务控制和多语句在用户 SQL I/O 前拒绝。
   - `tests/test_data_center_changeset_dialects.py`：`?` 与有序参数，双引号/嵌入引号 escaping、schema/table 组合和跨 target 拒绝；暂不走实际 DM import。
   - 新增 `tests/test_data_center_dameng_manual_connector.py`：用 TEMP 合成服务文件及假 driver 验证 alias 精确匹配、timeout 单位、post-connect server/port/timeout 属性核对、API/service precedence、失效 alias 不 fallback、不碰全局文件、密钥不落盘、连接关闭后删除临时文件。没有任何测试配置可连接真实主机；这些 fake/TEMP 断言仍不能证明原生 login deadline。
   - 保留/运行现有 `tests/test_data_center_readonly_relational_connectors.py` 对 `_connect_dameng()` 的 fail-closed 断言；不扩展只读 connector 或 adapter 矩阵。


首阶段精确 proposed owned paths：`tools/data_center/manual_relational_providers.py`、`tools/data_center/manual_driver.py`、新增 `tools/data_center/dameng_manual_connector.py`、`tools/data_center/changeset_dialects.py`、`tests/test_data_center_manual_relational_providers.py`、`tests/test_data_center_changeset_dialects.py`、新增 `tests/test_data_center_dameng_manual_connector.py`、新增 `tests/test_data_center_dameng_manual_integration.py`。达梦 owner-thread 集成检查放新专用测试文件；不改通用 `tests/test_data_center_manual_integration.py`。此清单仍待 root 在 history 最终验收后批准，不代表 backend 已 GO。

本阶段不拥有 `tools/data_center/table_source.py`、`host_metadata.py`、`object_tree.py`、`readonly_relational_connectors.py`、`readonly_driver_adapters.py`、`tools/db_connect.py`、`manual_session.py`，也不修改配置、UI、历史、CONTROL/ACCEPTANCE/handoff。现有 `ManualSession` 对 `TransactionEffect.UNKNOWN` 已会置状态 `UNKNOWN`，因此 DM classifier 先给 DDL/无法判定语句返回 UNKNOWN；如果测试暴露当前 session 状态路径不满足该 contract，再由 root 单独扩大边界，避免无必要 session 重构。

## 后续真实集成 gate

fake/TEMP tests 全绿后仍须在专用非生产 DM 库、已批准驱动版本上验证：连接配置最终生效的 endpoint 与登录默认 schema；dmPython 真实 `autoCommit` 读回及显式 commit/rollback；绑定类型和中文/NULL/decimal/date 值；DM DDL 与 `DDL_AUTO_COMMIT` 实际设置在成功、失败和断连时的状态；保存点释放/回滚语法；超时和连接关闭行为。达不到这些证据前，保留 `UNKNOWN` DDL 状态与真实验收待定标记。

对象树/表编辑/导入另开阶段：先增加 DM 原生且版本有证据的 catalog query、分页和字段类型映射，再通过现有 `ObjectTreeService.resolve_table_source()` 产生并校验 `VerifiedTableRef`；按 DM 所有者/schema 语义固定签名 scope，再接现有 `ManualSession.open_table_source()` 与 ChangeSet/import。该阶段的建议边界至少涉及 `host_metadata.py`、`object_tree.py`、`table_source.py`、`manual_session.py` 与对应 contract tests；不得直接复用 Oracle `ALL_*` 查询或从 profile service 字段猜 owner。
