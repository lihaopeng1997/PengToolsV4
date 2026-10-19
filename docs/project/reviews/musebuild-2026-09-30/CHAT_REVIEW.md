# MuseBuild 模型聊天 / 草稿 / token 差异审查

- 目标：2019e867acceea5aa63026b6ebf5cd111d80bc43（干净 detached worktree）
- 基线：origin/main = 32d7880af3d7246d0e586e3d7b2e16854e712cce
- 范围：指定四个产品文件及直接调用链；没有修改源码、提交、推送或读取真实模型/数据库数据。
- 说明：本 worktree 不存在 docs/project/README.md；已读当前 AGENTS.md，以当前代码和用户任务为准。

## P1

1. **SQL 证据链成功后没有向用户显示 SQL 草稿。** panels/model_chat_panel.py:1059-1080 的成功回调只追加 format_explanation(draft) 和字段证据；tools/ai_sql_draft.py:236-252 的解释函数不包含 draft["sql"]。因此有有效快照、模型返回并通过校验时，聊天气泡仍只有摘要/风险/“草案未执行”，用户拿不到草稿。无外部 I/O 复现：将 {"ok": true, "draft": {"sql": "SELECT POLICYNO FROM PRP.PRPCMAIN"}} 传给 _on_sql_draft_ok，得到 SQL_IN_REPLY False。最小修复：成功回调以明确的 SQL 代码块展示校验后的 SQL，并保留未执行说明；增加成功路径回归，断言回复包含原 SQL 且没有调用执行函数。

2. **聊天 SQL 新入口绕开 sql.draft 停用开关。** 聊天面板的技能管理器可停用内置 sql.draft（panels/model_chat_panel.py:1198,1358-1365），原有统一门禁在 tools/ptools_harness.py:83-87。新增 tools/ai_sql_draft.py:314-358 直接走 generate_sql_draft / chat_completions（第 300 行），从未查询 is_task_enabled 或 find_task。停用后提问仍能调用模型并生成草稿。无外部 I/O 复现：mock prepare_request、模型响应和 is_task_enabled=False，结果为 RESULT_OK True MODEL_CALLED True DISABLE_CHECK_CALLED False。最小修复：在聊天 SQL 草稿入口、调用模型之前检查该任务启用状态并 fail-closed；增加“停用后模型未调用”的新入口测试。现有 tests/test_skill_disable_flag.py 只覆盖旧三个入口，不能覆盖此路径。

## P2

3. **普通聊天被宽泛关键字误导至数据库链路。** tools/chat_intent.py:15-20 把单字“列”“查”“表”等一律判为 sql；panels/model_chat_panel.py:883-889 立即改走 _start_sql_draft。例如“请列出你支持的功能”被实测判为 sql，没有取数连接时直接回复“未选择取数连接”，不会回答普通聊天。最小修复：以词组/对象上下文识别取数意图，低置信度保持普通聊天；测试该例和真实取数句各走正确分支。

4. **所选聊天模型与 SQL 草稿实际调用模型不一致。** panels/model_chat_panel.py:538-547 的下拉选择只写 model_chat_last_model_id；普通聊天用选中模型（第 922-925 行），SQL 草稿在 panels/model_chat_panel.py:1043-1052 却调用 load_ai_local()。后者读取全局活动模型（tools/intranet_llm.py:208-215,278-282）。当聊天选 B、全局活动为 A 时，待定气泡标记 B，但提问与结构证据发给 A；若 A 停用则即使 B 可用也 fail-closed。最小修复：将本次选中的模型配置传入 SQL worker，并测试双模型选择/停用组合。

5. **运行中切换会话会让原 SQL 请求永远停在 pending。** panels/model_chat_panel.py:645-651 允许加载 B 会话；新增 _on_sql_draft_ok 调 _reply_local（第 1059-1080 行），后者读取可变的 self._session 和 self._pending_id（第 966-973 行）。从 A 发起草稿后切到 B，完成时实测调用 update_message("session-B", "pending-A")；tools/model_chat_store.py:237-248 对未命中 ID 静默保存，A 的待定消息无法完成。停止时同样读取当前会话（panels/model_chat_panel.py:1097-1118）。最小修复：worker 的完成/失败/停止处理使用发送时捕获的 session ID、message ID，且仅当该会话仍显示时刷新视图；测试 A→B 切换及停止。旧普通聊天回调也有同类可变状态风险，本条聚焦新增 SQL worker。

6. **运行中切换取数连接会把旧连接证据显示在新连接选择之下。** _SqlDraftWorker 在 panels/model_chat_panel.py:118-129 捕获 A 的连接/快照，但 _on_sql_conn_changed（第 580-581 行）不阻止 A 任务继续，也不标记来源；_on_sql_draft_ok（第 1059-1080 行）只写证据解释，无连接 ID/快照 ID。用户从 A 发问、等待时选择 B，完成后气泡显示 A 的证据，而工具栏显示 B。最小修复：结果携带并展示来源连接/快照；连接切换时取消或通过请求 generation ID 拦截不匹配结果。用两个合成快照测试 A→B 切换，不能把 A 当作 B 的草稿。

7. **Linux 建议门禁的提取边界同时漏检命令并误拒日志。** 新调用 panels/model_chat_panel.py:890-896,937-945 只审 extract_command_candidates 的结果；tools/linux_guard.py:120-146 仅识别指定小写围栏和 $ 前缀行。实测“看看日志，执行 rm -rf /fictional”、Bash/zsh 围栏中的同一命令均得到空候选，因此聊天路径照常调用模型，回复如仍是普通单行危险命令也不会附加拒绝提示。反向实测 text 围栏中的 “Service started successfully” 被当命令拒绝，导致分析日志请求直接中止。**此路径没有自动执行远端命令**；影响是聊天建议的只读提示漏标和文本日志误拦，不应表述为自动执行绕过。最小修复：区分日志文本和 shell 命令、支持常见大小写/围栏形式及明确的单行命令；测试上述正反例，并断言没有执行函数被触发。

## 已通过的验证与可复用部分

- D:\PengTools\.venv-build\Scripts\python.exe -B -m unittest tests.test_model_chat_intent tests.test_token_rebind -v（环境 QT_QPA_PLATFORM=offscreen、PYTHONDONTWRITEBYTECODE=1）：22/22 通过。
- D:\PengTools\.venv-build\Scripts\python.exe -B -m unittest tests.test_skill_disable_flag -v（同环境）：13/13 通过；仅证明旧入口有效，不覆盖新聊天路径。
- git diff --check origin/main...HEAD -- panels/model_chat_panel.py panels/ai_token_edit.py tools/ai_object_context.py tools/ai_sql_draft.py tests/test_model_chat_intent.py tests/test_token_rebind.py：无输出，通过。
- git status --short --branch：## HEAD (no branch)，worktree 无修改。
- 可复用：prepare_request 和 validate_generated_sql 在无证据/过期/未知对象时 fail-closed；成功路径只生成不执行。token 的快照/指纹重绑定与可视片段移除在现有测试中通过；run_task 旧入口停用门禁在测试中通过。

## 未验证与边界

- 未连接真实模型、数据库或 SSH；未执行 GUI 交互、打包、视觉验收或真实流式/超时测试。现有 skill-disable 测试主体重定向到 TEMP，但其模块导入时 ptools_harness 调用 list_tasks()，可能读取用户级 skills.json；没有输出或改写该内容，不能宣称此项测试绝对零用户配置读取。
- SQL 证据链的实际模型质量与跨方言生成效果未验证；本报告的 P1/P2 依据源码调用链和无外部 I/O 复现。
- 普通聊天使用的非流式面板回调和停止后的底层请求持续时间属既有实现，未将其误报为此次差异新增缺陷。
