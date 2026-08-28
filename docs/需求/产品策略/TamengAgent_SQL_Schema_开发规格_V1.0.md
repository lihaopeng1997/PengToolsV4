# TamengAgent 内部 Schema 证据编排器开发规格

**版本**：V1.1  
**状态**：开发交接冻结稿  
**适用模块**：`panels/ai_workbench_panel.py`（SQL 控制台）  
**产品名称**：PenngTools V4 Private  
**内部名称**：`TamengAgent` 是后台 Schema 证据编排器的代码/架构名称，不是任何用户可见的页签、面板、按钮、草案标签或提示语名称。  
**边界**：本文件定义 TamengAgent 的开发方案；不授权自动执行 SQL、联网获取 Schema 或扩大数据留存范围。SQL 控制台原有可见名称继续使用“AI 助手”。

## 给 Grok 的启动指令（直接复制）

```text
请严格按 `docs/需求/产品策略/TamengAgent_SQL_Schema_开发规格_V1.0.md` 实施后台 TamengAgent 的 P0-4A 至 P0-4D；同时遵循 `docs/需求/产品策略/PenngTools_V4_Private_全套UI需求文档_C方向_V1.2.md` 第 9.14、14.1、15.1–15.2。TamengAgent 仅是内部编排器名称，SQL 控制台用户可见名称继续为“AI 助手”。先阅读文档与其中列出的现有代码，再按批次开发、定向测试、敏感扫描、离线构建与产物核验。不得改动本批范围外模块，不得猜字段、联网获取 Schema 或自动执行 SQL。完成后汇报修改文件、测试结果、未完成项和内网待验证项。
```

> 本段只作为 Grok 的启动入口；所有详细实现、禁止项、验收用例和文件级改动清单均以本文件第 6–15 章为准。

---

## 1. 要解决的问题

现有 SQL 草案能力已可以读取本地 Schema 快照、选择对象/字段 Token、调用内网模型并通过 SQL Guard 做基础安全检查，但对“自然语言字段含义 → 真实字段”的证据链不够强。

典型问题：用户输入“查询 `prpcmain` 中创建日期倒序”，模型可能按语言习惯输出不存在的 `createddate`，而真实快照中字段可能是 `CREATED_DATE`。TamengAgent 的目标是让 SQL 使用的表、字段、索引和方言都有可回查的真实快照证据。

## 2. 目标与非目标

### 2.1 目标

1. 仅基于当前连接的有效 Schema 快照检索表、字段、字段注释、类型、主键和索引元数据。
2. 将自然语言意图转换成“候选对象/字段 → 用户确认或唯一证据确认 → SQL 草案”的受控流程。
3. 对模型返回的 SQL 再做本地对象/字段/方言/多语句校验；未知引用必须拦截。
4. 生成的结果只作为可编辑、可复制、可保存的 SQL 草案；执行与生成必须完全隔离。
5. 给用户显示连接、快照、扫描时间、字段证据、风险等级和拦截原因。

### 2.2 非目标

- 不连接公网，不自动连接或重新扫描数据库。
- 不读取表行数据，不把凭据、主机、端口、Token、Cookie、日志或接口报文放入模型上下文。
- 不自动执行、自动修复、自动应用、批量跨环境执行 SQL。
- 不用固定“中文字段名 → 某数据库字段名”的黑名单直接输出 SQL；同义词只允许用于候选检索，最终字段必须真实存在。
- P0 不做复杂 ER 图、跨库 Join 推断或索引创建建议；这些列为后续能力，且必须继续以快照证据为前提。

## 3. 现有可复用基础

| 现有文件 | 已有能力 | TamengAgent 使用方式 |
|---|---|---|
| `tools/schema_snapshot.py` | 本地 JSON 快照、连接指纹、扫描时间、对象、字段、类型、注释、主键、列级 indexed 标记 | 作为唯一 Schema 事实源；快照失效时拒绝生成。 |
| `tools/ai_object_context.py` | 表/字段 Token、快照 ID 与指纹一致性校验 | 用户显式选中的对象与字段是最高优先级证据。 |
| `tools/ai_sql_draft.py` | 模型 JSON 协议、最小 Schema 上下文、结构化草案、安全检查 | 只接受 TamengAgent 已确认的证据上下文；禁止将失效快照作为“参考”。 |
| `tools/sql_guard.py` | 语句分类、草案安全检查、错误脱敏 | 对草案做语句类别和执行前安全防线。 |
| `tools/intranet_llm.py` | 用户显式启用的内网模型调用 | 只发送最小的对象/字段/索引元数据与用户意图。 |
| `panels/ai_workbench_panel.py` | 连接选择、扫描线程、对象树、字段详情、Token 输入、`_AiWorker` | 保持可见名称“AI 助手”；展示候选/证据/状态，后台调用 TamengAgent 前后校验。 |

## 4. 总体架构

```text
用户自然语言
    │
    ▼
SQL 控制台“AI 助手”面板（用户可见）
    │  前置校验 / Loading / 候选选择
    ▼
TamengAgentOrchestrator（后台：tools/tameng_agent.py）
    ├── SnapshotGate：连接、指纹、状态、版本、截断校验
    ├── IntentExtractor：本地提取表、字段、排序、筛选、聚合候选
    ├── CandidateResolver：表/字段/索引检索与消歧
    ├── EvidenceBuilder：构造最小证据包
    └── OutputValidator：回查模型 SQL 中的对象、字段与方言
             │
             ▼
     ai_sql_draft.generate_sql_draft（内网模型，仅草案）
             │
             ▼
      SQL 草案标签页（未执行）
             │
             ├── 复制 / 编辑 / 保存草稿
             └── 用户另行点击“执行当前 SQL” → 原有执行与确认链路
```

## 5. 状态机

| 状态 | 进入条件 | UI 表现 | 可用操作 | 退出条件 |
|---|---|---|---|---|
| `NO_CONNECTION` | 未选连接 | “请先选择数据库连接” | 新建连接、选择连接 | 选中连接 |
| `SNAPSHOT_MISSING` | 无快照 | “尚未扫描结构，无法生成基于 Schema 的草案” | 扫描结构、查看连接 | 扫描成功 |
| `SNAPSHOT_STALE` | 指纹不一致、扫描失败、过期 | “快照已过期，请重新扫描” | 重新扫描、查看快照 | 新有效快照 |
| `SNAPSHOT_V1` | 快照版本低于 2 | 可字段草案；涉及索引能力显示“索引信息不完整” | 重新扫描、生成字段草案 | V2 快照 |
| `READY` | 快照有效、模型可用 | 展示连接/方言/扫描时间/快照 ID | 输入意图、选择字段、生成草案 | 发起生成 |
| `RESOLVING` | 本地检索表字段 | 局部“正在匹配表和字段…” | 取消 | 唯一匹配/需选择/失败 |
| `NEEDS_SELECTION` | 多候选或置信不足 | 显示候选与匹配依据 | 勾选对象/字段、返回编辑 | 用户确认 |
| `GENERATING` | 证据完整，模型请求已发送 | 局部阶段 Loading | 取消 | 返回草案/失败 |
| `VALIDATING` | 模型返回 | “正在复核对象和字段…” | 取消 | 通过/拦截 |
| `DRAFT_READY` | 本地复核通过 | 新建“SQL 草案 · 未执行”标签，并展示快照与字段证据 | 编辑、复制、保存草稿 | 用户切换/执行 |
| `BLOCKED` | 不明字段、多语句、方言不符等 | “草案被拦截”，展示原因 | 修改意图、选择候选、重新生成 | 新请求 |

### 5.1 Loading 规范

- `<150ms`：只展示按钮按压，不显示 Spinner。
- `400–799ms`：AI 助手面板内局部 Loading，例如“正在校验 Schema 字段…”。
- `≥2s`：展示四阶段 `校验快照 → 匹配表字段 → 生成草案 → 复核 SQL`、已耗时与取消入口。
- 不使用全局遮罩；不清空当前 SQL、历史草案、对象树、连接信息或字段证据。
- 同一 SQL Tab 的 TamengAgent 同时最多一个运行任务；重复点击明确提示“当前草案任务仍在运行”。

## 6. Schema 快照 V2

### 6.1 兼容的数据结构

```json
{
  "connection_id": "conn-001",
  "alias": "quote-test",
  "dialect": "oracle",
  "fingerprint": "oracle|...",
  "snapshot_id": "uuid",
  "version": 2,
  "scanned_at": "2026-08-26T15:00:00+08:00",
  "status": "ok",
  "truncated": false,
  "objects": [
    {
      "owner": "PRP",
      "name": "PRPCMAIN",
      "object_type": "TABLE",
      "comment": "保单主表",
      "columns": [
        {
          "name": "CREATED_DATE",
          "data_type": "DATE",
          "nullable": false,
          "position": 8,
          "comment": "创建日期",
          "primary_key": false,
          "indexed": true
        }
      ],
      "indexes": [
        {
          "name": "IDX_PRPCMAIN_CREATED_DATE",
          "unique": false,
          "index_type": "NORMAL",
          "columns": [
            {"name": "CREATED_DATE", "position": 1}
          ]
        }
      ]
    }
  ]
}
```

### 6.2 扫描规则

1. 保留当前对象和字段扫描；对象仍只存元数据。
2. 为每个关系型对象补充 `indexes`：索引名、唯一性、索引类型、字段名和字段顺序。
3. Oracle 使用系统字典中的索引与索引列信息；MySQL/OceanBase 使用 `information_schema.statistics`；Dameng 的系统视图必须由开发在目标版本验证后接入，不得照抄未验证 SQL。
4. 索引扫描异常不丢弃已有对象/字段；快照写入 `index_metadata_status: "unavailable"` 和脱敏 warning。
5. 快照 `version=1` 可展示与浏览，但任何“索引解释/索引建议”能力都要求重新扫描为 V2。
6. `truncated=true` 时，如果候选对象可能处于截断范围之外，TamengAgent 不得声称“没有该表/字段”，应提示“快照已截断，请扩大扫描范围后重试”。

## 7. 本地语义检索与消歧

### 7.1 标准化

`tools/tameng_agent.py` 新增纯函数：

```python
def normalize_identifier(value: str) -> str: ...
def normalize_text(value: str) -> str: ...
def extract_intent_terms(question: str) -> dict: ...
def resolve_candidates(snapshot: dict, intent: dict) -> dict: ...
def build_evidence_context(resolution: dict, snapshot: dict) -> dict: ...
def validate_generated_sql(sql: str, evidence: dict, dialect: str) -> dict: ...
```

标准化规则：

- 标识符统一大写，英文连字符/空格/下划线归一化后再比较；`createddate` 与 `CREATED_DATE` 可以成为“相似候选”，但不是直接确认。
- 中文字段词优先检索字段注释、对象注释；英文/拼音/别名可作为候选词扩展。
- `创建日期`、`创建时间`、`create date` 等只用于扩展检索候选，不能绕过“字段必须实际存在”的验证。
- SQL 字符串、注释、参数名和别名必须在生成后校验时排除，避免误把字面值当作字段。

### 7.2 候选排序

候选评分只决定展示顺序，不替代证据：

| 优先级 | 命中规则 | 示例 |
|---|---|---|
| 1 | 表/字段名精确匹配 | `PRPCMAIN` → `PRPCMAIN` |
| 2 | 下划线归一化精确匹配 | `createddate` → `CREATED_DATE` |
| 3 | 字段注释精确匹配 | “创建日期” → comment=`创建日期` |
| 4 | 对象注释 + 字段注释联合匹配 | “保单创建日期” → 保单主表 + 创建日期 |
| 5 | 同义词扩展匹配 | “创建时间” → `CREATED_DATE`，仅作为候选 |

### 7.3 自动确认与强制确认

- 仅在一个候选的表名和字段名/注释具有精确证据时自动确认。
- 出现多个字段候选，或仅有弱同义词命中时，进入 `NEEDS_SELECTION`，用户必须勾选。
- 多表查询没有明确 Join 证据时，不构造 Join；显示“请补充关联条件”并阻断生成可执行草案。
- 用户显式选中的 Token 高于自动候选，但 Token 对应快照 ID 或指纹不一致时立即失效。

### 7.4 示例：PRPCMAIN 创建日期倒序

输入：`查询 prpcmain 中创建日期倒序`

1. 识别对象候选 `prpcmain`、排序语义 `DESC`、字段语义 `创建日期`。
2. 在当前有效快照中命中 `PRPCMAIN`。
3. 在该对象字段中检索：
   - `CREATED_DATE / DATE / 创建日期 / 命中字段注释`
   - 如果唯一，则确认；如果还有 `CREATE_DATE / DATE / 创建时间`，则显示候选选择。
4. 证据包只包含 `PRPCMAIN.CREATED_DATE`、方言和必要索引信息。
5. 模型返回 SQL 后本地复核；只允许真实存在的 `PRPCMAIN` 和 `CREATED_DATE` 被引用。
6. 通过后生成：

```sql
SELECT *
FROM PRPCMAIN
ORDER BY CREATED_DATE DESC
```

草案标题必须为“SQL 草案 · 未执行”，并显示 `CREATED_DATE（创建日期）` 的匹配依据。

## 8. 模型上下文与输出协议

### 8.1 最小证据包

```json
{
  "dialect": "oracle",
  "snapshot_id": "...",
  "scanned_at": "...",
  "tables": [
    {
      "qualified_name": "PRP.PRPCMAIN",
      "object_type": "TABLE",
      "comment": "保单主表",
      "columns": [
        {"name": "CREATED_DATE", "data_type": "DATE", "comment": "创建日期", "indexed": true}
      ],
      "indexes": [
        {"name": "IDX_PRPCMAIN_CREATED_DATE", "columns": ["CREATED_DATE"]}
      ]
    }
  ],
  "confirmed_fields": ["PRP.PRPCMAIN.CREATED_DATE"]
}
```

- 发送给内网模型的上下文不得包含 host、port、username、password、连接 URL、行数据、执行结果、请求/响应正文、日志、Cookie、Token 或模型历史。
- 上下文按对象/字段裁剪，默认不超过既有安全上限；超出时要求用户减少对象或字段选择，而不是静默截断掉已确认字段。

### 8.2 模型返回 JSON

```json
{
  "summary": "按创建日期倒序查询保单主表",
  "intent": "查询",
  "objects_used": ["PRP.PRPCMAIN"],
  "selected_fields": ["CREATED_DATE"],
  "evidence": ["PRP.PRPCMAIN.CREATED_DATE · DATE · 创建日期"],
  "condition_interpretation": "未指定筛选条件",
  "join_assumptions": [],
  "risk_level": "read",
  "warnings": [],
  "sql": "SELECT * FROM PRP.PRPCMAIN ORDER BY CREATED_DATE DESC"
}
```

模型协议增加 `evidence`，但 TamengAgent 不信任模型自报的 evidence；必须重新以本地快照校验。

## 9. 生成后本地复核

### 9.1 必须拦截

- 快照不存在、失效、指纹不一致、状态失败、受截断影响。
- SQL 为多语句，或语句分类无法识别。
- `FROM/JOIN/UPDATE/INTO` 使用的对象不在证据对象集合。
- 直接字段引用不在当前对象字段集合，且无法被识别为 SQL 关键字、函数、别名、参数或字符串字面量。
- 模型生成未被确认的 Join 条件。
- 输出方言与当前连接方言不兼容。
- 写入/DDL 草案没有遵守现有 SQL Guard 的风险提示和执行确认边界。

### 9.2 复核产物

```python
{
  "allowed": False,
  "reason": "SQL 引用了当前快照不存在的字段 CREATEDDATE",
  "unknown_objects": [],
  "unknown_fields": ["CREATEDDATE"],
  "evidence_used": ["PRP.PRPCMAIN.CREATED_DATE"],
  "risk_level": "unknown"
}
```

`allowed=False` 时，不创建可执行 SQL Tab；保留用户意图和模型摘要，提供“选择字段后重试”“查看快照”“复制拦截详情”。

## 10. UI 规格

### 10.1 面板

- SQL 控制台右侧用户可见 Tab 名称保持 **AI 助手**；`TamengAgent` 仅用于后台模块、日志中的非敏感技术标识和开发文档，不替换任何用户可见名称。
- 顶部紧凑状态条：`连接别名 · 方言 · 快照有效性 · 扫描时间`。
- 输入区是主区域；次级区按需显示对象/字段 Token、候选列表和证据条，禁止拆成多张凸起大卡。
- “字段证据”显示为紧凑可复制条，例如：`PRPCMAIN.CREATED_DATE · DATE · 创建日期 · IDX_PRPCMAIN_CREATED_DATE`。
- 主按钮：`生成 SQL 草案`；次操作：`选择表和字段`、`查看快照`；低频操作进入“更多操作”。
- 草案结果新开 Tab，Tab 标题包含静态“未执行”状态；生成操作不得改变现有执行按钮文字或执行路径。

### 10.2 错误文案

| 原因 | 文案 | 下一步 |
|---|---|---|
| 无连接 | 未选择数据库连接，无法生成基于结构的 SQL 草案。 | 选择连接 |
| 无快照 | 尚未扫描当前连接的结构，TamengAgent 不会猜测表或字段。 | 扫描结构 |
| 快照过期 | 当前连接配置已变化，原快照不再适用。 | 重新扫描结构 |
| 字段歧义 | 找到多个“创建日期”候选，请选择要使用的字段。 | 选择字段 |
| 未找到字段 | 当前快照未找到“createddate”。可查看候选字段或修改描述。 | 查看候选 |
| 模型越界 | 草案引用了当前快照不存在的字段，已拦截且未写入 SQL 编辑器。 | 选择字段后重试 |

## 11. 研发实施批次

### P0-4A：快照 V2

1. 扩展 `schema_snapshot.py` 以保存对象 `indexes`。
2. 为 Oracle、MySQL/OceanBase 编写已验证的索引扫描；Dameng 先增加适配接口和目标环境验证用例。
3. 兼容读取 V1 快照，写入 V2；扫描失败不破坏已有快照。
4. 新增 `tests/test_schema_snapshot.py` 的 V2 与索引回归。

### P0-4B：本地解析与证据链

1. 新建 `tools/tameng_agent.py`，实现快照闸门、标准化、候选检索、消歧和证据包构建。
2. 先写纯函数测试，再接入 UI；禁止在该模块中调用数据库、模型或 Qt。
3. 修改 `ai_sql_draft.py`，使生成函数只接收有效 evidence，不允许 stale 快照走“仅供参考”路径。
4. 在 SQL 复核阶段阻断未知对象、字段、Join 和多语句。

### P0-4C：SQL 控制台集成

1. 保持 SQL 控制台右侧“AI 助手”显示名和原有用户心智不变；后台接入 `TamengAgentOrchestrator`，用户可见状态仅描述快照、候选、证据和草案状态。
2. 在 `_run_ai('generate')` 前调用快照闸门与候选解析；歧义时打开字段选择，不调用模型。
3. 复用 `_AiWorker`，但任务状态改为阶段化局部 Loading；完成后显示证据、风险与草案来源。
4. 通过后创建 `SQL 草案 · 未执行` 标签；执行仍走已有 `执行当前 SQL` 路径。

### P0-4D：安全与回归

1. 跑 TamengAgent 单测、现有 `test_schema_snapshot.py`、`test_ai_object_tokens.py`、`test_sql_guard.py`。
2. 用脱敏/模拟快照验证 UI：无快照、过期、字段歧义、字段不存在、索引元数据不可用、模型越界、取消与恢复。
3. 按项目规则执行敏感扫描、定向测试、离线构建和发布产物核验；不触碰真实生产数据进行验收。

## 12. 验收用例

| 编号 | Given | When | Then |
|---|---|---|---|
| TA-01 | 有效 Oracle V2 快照中含 `PRPCMAIN.CREATED_DATE` 注释“创建日期” | 输入“查询 prpcmain 中创建日期倒序” | 草案使用 `CREATED_DATE DESC`，显示字段证据，状态为未执行。 |
| TA-02 | 同表有 `CREATE_DATE` 与 `CREATED_DATE` | 输入“创建日期倒序” | 出现候选选择，不调用模型，不生成 SQL。 |
| TA-03 | 快照没有 `CREATEDDATE` | 模型返回 `CREATEDDATE` | 本地复核拦截，不创建草案标签。 |
| TA-04 | 当前连接指纹已变 | 点击生成 | 显示快照过期，提供重新扫描，不调用模型。 |
| TA-05 | 快照为 V1 且用户要求索引解释 | 生成 | 显示索引信息不完整，要求重新扫描 V2。 |
| TA-06 | 两张表无关联证据 | 要求联合查询 | 要求用户补充 Join 条件，不能标为安全 read 草案。 |
| TA-07 | 模型返回两条 SQL 或写入 SQL | 生成结束 | 草案被拦截；不自动执行、不自动写入现有编辑器。 |
| TA-08 | 生成任务超过 2 秒 | 生成中 | 显示阶段、耗时与取消；旧草案、连接、对象树仍可读。 |
| TA-09 | 用户取消任务 | 点击取消 | 保留输入和已选字段；未确认取消前显示“正在取消”。 |
| TA-10 | 同一 Tab 已有生成任务 | 再点击生成 | 不启动第二个任务，提示当前任务仍在运行。 |

## 13. 发布前检查

- [ ] TamengAgent 未读取行数据、密码、连接 URL、主机、端口、Token、Cookie、日志或接口报文。
- [ ] 无有效快照/字段证据时不调用模型、不生成草案。
- [ ] `created_date` 示例和歧义例均有自动化测试。
- [ ] 草案与执行按钮、调用路径、状态记录完全隔离。
- [ ] 取消、失败、模型格式错误、未知字段都不会留下伪成功状态。
- [ ] 快照、候选、布局以外不新增默认持久化；不保存自然语言问题、草案正文或模型输出历史。
- [ ] 主题、窄窗口、Splitter、Loading 同时存在时，连接/快照/证据/主操作仍可见。

## 14. 当前实现差异与精确改动清单

> 本节基于 2026-08-26 对现有源码的审阅编写。它区分“可复用基础”和“必须开发的缺口”；不得把现状代码误判为 TamengAgent 已完成。

| 位置 | 当前可复用基础 | 当前缺口 | P0-4 必须改动 |
|---|---|---|---|
| `tools/schema_snapshot.py` | 已保存连接指纹、快照 ID、扫描时间、对象、列、注释、主键和列级 `indexed`。 | `empty_snapshot()` 仍为 `version=1`；`_clean_object()` 会丢弃对象级索引定义，不能保留索引名、唯一性或联合列顺序。 | 升级快照 V2；为对象加入 `indexes` 与 `index_metadata_status`；兼容读取 V1，V1 只读浏览，涉及索引解释/建议时必须要求重新扫描。 |
| `tools/ai_object_context.py` | 已支持对象/字段 Token 绑定 `snapshot_id` 与连接指纹校验。 | 当前 Token 校验只覆盖已有用户选择，不能代替自然语言字段消歧。 | TamengAgent 的自动确认和候选确认均须产出/校验同一快照绑定的证据对象；快照 ID 或指纹变化即废弃证据。 |
| `tools/ai_sql_draft.py` | 已有内网模型调用、JSON 解析、单语句/风险基础检查。 | `build_safe_context(..., stale=True)` 仍会把“快照过期或缺失”作为参考说明后继续调用模型；`validate_draft()` 主要按用户已选字段检查，无法验证模型是否引用快照中真实对象/字段。 | 在调用模型前强制接收 `evidence`；缺失/过期/截断受影响快照直接 fail-closed；生成后以当前快照回查对象、字段、Join、方言和多语句，未知引用不创建草案标签。 |
| `panels/ai_workbench_panel.py` | 已有连接选择、结构扫描、对象/字段选择、`_AiWorker`、草案新标签页和独立 SQL 执行确认路径。 | UI 当前仍显示“AI 助手”；`_run_ai()` 仅在已有 Token 无效时拦截，未选择 Token 时可将 stale 上下文传入模型；草案标题仍是泛化“AI 草案”。 | 显示名改为 `TamengAgent`；`generate` 前先走快照闸门与候选消歧；无有效快照不创建 worker；通过本地复核后才创建 `SQL 草案 · 未执行`；现有“执行当前 SQL”路径不改。 |
| `tools/ptools_harness.py` 与 `tools/harness_project.py` | 已能加载项目级 SQL 约定，且本身不执行数据库。 | 通用 Harness 的项目上下文可能包含常见表名；它不是 Schema 事实源，也不会验证字段存在性。 | TamengAgent 不得用 Harness 项目上下文补全/确认表或字段。若后续复用项目约定，只能在快照证据确定后作为非事实性的方言/命名提示；任何表、字段、索引、Join 仍以当前有效快照为唯一事实源。 |

### 14.1 强制调用顺序

```text
用户意图
  → SnapshotGate（连接、指纹、status、version、truncated）
  → IntentExtractor / CandidateResolver（本地，不调用模型）
  → NEEDS_SELECTION（如有歧义，停止）
  → EvidenceBuilder（仅真实已确认对象/字段/索引/方言）
  → ai_sql_draft.generate_sql_draft（内网模型，仅草案）
  → OutputValidator（本地复核）
  → SQL 草案 · 未执行
  → 用户另行点击既有“执行当前 SQL”
```

以下任一步失败，必须停在当前步骤并展示原因/恢复入口；不得降级为“模型猜一个字段”或将通用 Harness 项目上下文当作证据。

## 15. 可直接交给 Grok 的实施指令

```text
请按以下顺序实现 PenngTools 的 TamengAgent，不做超出范围的功能扩展：

1. 先阅读：
   - docs/需求/产品策略/PenngTools_V4_Private_全套UI需求文档_C方向_V1.2.md（第 9.14、14.1、15.1–15.2）
   - docs/需求/产品策略/TamengAgent_SQL_Schema_开发规格_V1.0.md（全文，尤其第 11、12、14 节）
   - tools/schema_snapshot.py、tools/ai_object_context.py、tools/ai_sql_draft.py、tools/sql_guard.py、panels/ai_workbench_panel.py。

2. 按 P0-4A 至 P0-4D 逐批完成：
   - P0-4A：快照 V2。新增对象级 indexes（索引名、唯一性、类型、字段和顺序），保留 V1 读取兼容；不能扫描/验证的方言将索引状态标为 unavailable，不能伪造索引。
   - P0-4B：新增无 Qt、无网络、无数据库调用的 tools/tameng_agent.py；实现 SnapshotGate、候选检索、字段消歧、最小证据包和 SQL 本地复核。先完成纯函数单测。
   - P0-4C：集成 SQL 控制台。保持右侧“AI 助手”显示名不变；后台接入 TamengAgentOrchestrator，生成前必须先校验有效快照并处理候选选择；生成与执行路径完全隔离；草案标签命名为“SQL 草案 · 未执行”。
   - P0-4D：补齐无快照、快照过期、快照截断、字段歧义、未知字段、未知 Join、V1 索引、模型返回多语句、取消与重复提交的定向回归。

3. 绝对禁止：
   - 无有效快照时调用模型生成 SQL；
   - 以 createddate、项目常见表、模型记忆或 Harness 项目上下文猜测字段；
   - 读取表行数据或将主机、端口、用户名、密码、Token、Cookie、日志、请求响应传给模型；
   - 生成即执行、后台执行、自动修复、跨环境批量执行；
   - 改动现有“执行当前 SQL”的风险确认语义。

4. 最小验收：
   - 有效快照含 PRPCMAIN.CREATED_DATE（注释“创建日期”）时，“查询 prpcmain 中创建日期倒序”只能生成 ORDER BY CREATED_DATE DESC；
   - 若同时存在 CREATE_DATE 与 CREATED_DATE，必须要求用户选择；
   - 模型返回 CREATEDDATE 或未知 Join 时必须拦截，不能建草案 Tab；
   - 所有通过的草案均显示快照来源、字段证据和“未执行”；
   - 按项目规则执行定向测试、敏感扫描、build_release.ps1、产物和 build_info 核对后再提交。
```
