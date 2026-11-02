# 模型对话接入 Harness —— 给 Grok 的开发提示词

> 项目：PengToolsHub V4.27 Private（Python 3.12 + PyQt6，离线桌面工具台）
> 目标：让「模型对话」入口在回答 SQL / Linux 类问题时，先走字段证据链与只读白名单，做到「表名可模糊、字段名必须精准」，不再让模型凭空猜测。
> 请先完整读完本文再动手。改完必须自测，不要只改不验。

---

## 一、铁律（违反任何一条都算改错）

1. **只允许改一个文件**：`panels/model_chat_panel.py`。意图识别需要**新建** `tools/chat_intent.py`（注意：这个文件**当前不存在**，是新建，不是修改已有文件），且不得被其它文件 import。
2. **只允许调用既有函数，禁止修改它们的签名或内部逻辑**：
   - `tools/tameng_agent.py`：`prepare_request` / `validate_generated_sql` / `format_evidence_bar`
   - `tools/ai_sql_draft.py`：`generate_sql_draft`
   - `tools/linux_guard.py`：`inspect_commands`
   - `tools/schema_snapshot.py`：`load_snapshot`
   - `tools/db_connect.py`：`load_connections`
3. **禁止改**：`tools/db_connect.py`（连接/查询执行）、`tools/ptools_harness.py`、`tools/intranet_llm.py`、`tools/secure_store.py`（凭据加密）、`requirements.txt`、`scripts/build_release.ps1`。
4. **通用聊天能力必须原样保留**：非取数意图的消息走原有 `_send → _ChatWorker.run → chat_completions` 链路，不得拦截、不得降级。
5. **证据链里绝不注入敏感信息**：只注入表名、字段名、字段类型、字段注释、索引等结构元数据。禁止出现密码、Token、Cookie、完整连接串、任何业务行数据。
6. **绝不自动执行**：生成的 SQL 和 Linux 命令只展示给用户，不写入执行器、不自动发送。

---

## 二、现状（先理解再改）

模型对话面板 `panels/model_chat_panel.py` 现在的发送链路是纯通用聊天：

```
_send() → append_message() → _start_worker(payload) → _ChatWorker.run()
        → chat_completions(messages, cfg) → completed.emit(text)
```

`messages` 里只有 `SYSTEM_PROMPT` + 历史消息，**没有**当前连接、Schema 快照、字段证据、Linux 白名单的任何概念。所以问「查车险 main 表的创建日期」时模型会凭自己猜表名和字段名。

而「数据中心」面板 `panels/ai_workbench_panel.py` 已有一条完整可复用的证据链：
`_browse_conn()` 拿当前连接 → `load_snapshot(conn_id)` 拿快照 → `prepare_request()` 做门禁 + 候选解析 → `generate_sql_draft()` 调模型 → `validate_generated_sql()` 校验拦截。

本次任务就是把这套链路「接入」模型对话入口，不是新写一套。

---

## 三、要实现的功能

### 3.1 意图分流

在 `_send()` 里、`append_message` 之前插入意图识别：

```
if detect_take_data_intent(text) == 'sql'   → 走 SQL 证据链（3.2）
elif detect_take_data_intent(text) == 'linux' → 走 Linux 只读门禁（3.3）
else → 走原通用聊天链路（保持不动）
```

意图识别放**新建的** `tools/chat_intent.py`（当前不存在），轻量关键字规则即可（示例，可按需微调，但不要过度设计）：

```python
def detect_take_data_intent(text: str) -> str:
    t = (text or '').lower()
    linux_keys = ('日志', '查日志', 'tail', 'grep', '进程', '磁盘', '内存',
                  'ps', 'df', 'free', '主机名', 'cpu', 'uptime')
    sql_keys = ('查', '查询', 'select', '表', '字段', '列', '数据库',
                '创建日期', '创建时间', '统计', '合计', 'count', '索引', '关联', 'join')
    if any(k in t for k in linux_keys):
        return 'linux'
    if any(k in t for k in sql_keys):
        return 'sql'
    return 'none'
```

### 3.2 SQL 证据链

按以下顺序实现：

**① 拿当前连接和快照**（已拍板：模型对话面板自带连接下拉框，方案 B）：

```python
from tools.db_connect import load_connections
from tools.schema_snapshot import load_snapshot

# 模型对话工具栏新增一个连接下拉框 self.conn_combo，数据源 load_connections()。
# 参考 ai_workbench_panel.py:772 的 _reload_connections 填充逻辑：
rows = load_connections()                    # list[dict]，每条含 id/name/dialect/host 等
for item in rows:
    self.conn_combo.addItem(str(item.get('name') or item.get('id')), item)

# 发送时读当前选中项：
conn = self.conn_combo.currentData()
conn = dict(conn) if isinstance(conn, dict) else None

# 拿快照：
snap = load_snapshot(str(conn['id'])) if conn else None
```

连接下拉框硬约束：**只读展示 + 可切换，不提供新增/编辑/删除**；无连接时显示「未配置数据库连接」；**不得渲染密码字段**。**不要**去复用数据中心的「最后浏览连接」，也**不要**改 `db_connect.py`（只调用 `load_connections()`）。

**①-补充 快照缺失/过期兜底（已拍板，按顺序执行）**：

- `snap is None`（该连接从没扫过）→ 先回显「该连接尚未扫描结构，正在扫描…」，然后**在后台线程**调一次 `scan_schema(conn)`（`schema_snapshot.py` 的入口）现场扫描，成功则继续；扫描失败则回显「扫描失败：{原因}，请先到数据中心确认连接可用」，**不调模型**，结束。
- 快照过期（`snapshot_gate` 返回 `SNAPSHOT_STALE`）→ 提示「结构可能已变化，建议到数据中心重新扫描」，但**仍按旧快照继续**，把「可能过期」作为警告回显。
- 现场扫描只调 `scan_schema` 这一个既有函数，**不改**其内部实现，扫描是只读结构读取。

**② 调 `prepare_request` 做门禁 + 候选解析**：

```python
from tools.tameng_agent import prepare_request

prepared = prepare_request(question, snap, conn)
# 返回 dict：ok / state / reason / next_action / evidence / resolution / call_model
```

**③ 按 `state` 分流**：

| state | 处理 |
|---|---|
| `READY` 且 `ok=True` | 进入 ④ 调模型 |
| `NEEDS_SELECTION` | 把 `resolution['fields']` 在气泡内渲染成可点击候选，用户点选后带 `confirmed=[...]` 重新调 `prepare_request` |
| `BLOCKED` / `NO_CONNECTION` / `SNAPSHOT_MISSING` / `SNAPSHOT_STALE` | 把 `reason`（或 `reason + next_action`）作为助手消息直接回显，**不调模型** |

**④ 调模型生成**：

```python
from tools.ai_sql_draft import generate_sql_draft

draft = generate_sql_draft(
    question,
    action='generate',
    dialect=str((conn or {}).get('dialect') or 'oracle'),
    alias=str((conn or {}).get('name') or ''),
    snapshot=None,                    # generate 模式走 evidence，不需要 snapshot
    evidence=prepared.get('evidence'),
    cfg=当前选中的模型配置 dict,       # 复用面板里 _current_model()
)
```

**⑤ 校验拦截**：

```python
from tools.tameng_agent import validate_generated_sql, format_evidence_bar

checked = validate_generated_sql(draft.get('sql'), evidence, dialect)
if not checked.get('allowed'):
    # 回显 checked['reason'] + checked['unknown_fields'] 等，不落盘、不执行
else:
    # 回显 SQL + 字段证据条 format_evidence_bar(evidence)，标记「草案 · 未执行」
```

### 3.3 Linux 只读门禁

用轻量方案（不要改 `ptools_harness`）：

```python
from tools.linux_guard import inspect_commands

# 1. 让模型把自然语言转成只读命令（system 提示词约束只输出查看命令）
# 2. 过滤：
allowed, rejected = inspect_commands(command_list)
# 3. 回显 allowed；rejected 必须标注「已拦截：原因」，绝不执行
```

**白名单口径已拍板**：统一用 `tools/linux_guard.py` 的 `inspect_commands` 这一套。**禁止**引入或参考 `tools/ops_commands.py`（运维助手）那套白名单来「对齐」——两套在 find/awk/sed/systemctl 等命令放行口径不一致，混用会导致拦截行为分叉。分叉问题本轮不处理，留后续单独统一。

---

## 四、已拍板的 5 项决策（照做，不必再问）

1. **当前连接来源**：模型对话面板**自带连接下拉框**（数据源 `load_connections()`，只读 + 可切换，不继承数据中心选中项，不碰 `db_connect.py`）。
2. **快照缺失/过期兜底**：缺失→后台现场 `scan_schema` 一次；失败→提示去数据中心、不调模型；过期→警告但按旧快照继续。
3. **取数模式开关**：默认开启，但只对 `detect_take_data_intent` 命中的消息生效，不影响普通聊天。
4. **Linux 白名单口径**：统一用 `linux_guard.inspect_commands`，禁止引入 `ops_commands` 那套对齐。
5. **候选字段交互**：`NEEDS_SELECTION` 时在气泡内渲染可点击候选（简单按钮列表即可），点击后带 `confirmed` 重发。

以上 5 项已定，**不得自行更改默认值、不得因拿不准而停滞**。遇到文档未覆盖的细节，按「只读、不执行、不碰红线、不注入敏感信息」四条底线取最保守做法，并在交付说明里注明。

---

## 五、自测要求（改完必须逐条验证并汇报结果）

1. 问「写一段周报」→ 走通用聊天，行为与改造前一致。
2. 无连接时问「查车险主表创建日期」→ 回复「请先选择连接并扫描结构」，不调模型。
3. 有连接+快照时问「查 main 表创建日期」→ 生成的 SQL 用快照里真实表名（如 `prpmain`）和真实字段名（如 `CREATE_DATE`），字段名必须精准。
4. 让模型故意返回不存在的字段 → 被 `validate_generated_sql` 拦截并回显原因。
5. 命中多个「创建日期」候选 → 气泡内出现候选列表可点选。
6. 问「查最近错误日志」且命令含 `rm`/`kill`/`sudo`/重定向 → 被 `inspect_commands` 拦截并标注原因。
7. 检查发往模型的 messages，确认无密码/Token/完整连接串/行数据，只有结构元数据。
8. 生成的 SQL 和 Linux 命令都只展示，不自动执行。
9. 连接未扫描过快照时问取数问题 → 能触发后台现场 `scan_schema`，成功后正常出结果；连接不通时回显失败提示、不调模型。

---

## 六、交付要求

1. 只提交改动文件（`model_chat_panel.py`，以及新增的 `tools/chat_intent.py`），附一段不超过 200 字的改动说明。
2. 列出你实际验证过的自测项（第 1–8 条分别打勾/说明）。
3. 自测第 2 条改为：无连接（下拉框为空/未选中）时问「查车险主表创建日期」→ 回复「请先选择连接并扫描结构」，不调模型；且「未扫描过该连接」时能触发后台现场扫描。
4. 若遇到必须改「禁止改」文件才能完成的情况，**停下并说明原因**，不要硬改。
