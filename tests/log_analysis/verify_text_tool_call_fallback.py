"""Text tool-call fallback and unfinished-work continuation (fake model, no network)."""
import json
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from tools.agent_core.budget import BudgetLimits
from tools.agent_core.contracts import AgentStatus, AgentTurn, RunContext, ToolResult
from tools.agent_core.runner import AgentRunner, unfinished_work_announcement
from tools.agent_core.text_protocol import EVIDENCE_CONTINUATION_INSTRUCTION, prepare_text_tool_calls
from tools.agent_runtime import build_tool_system_prompt, recover_text_tool_calls
from tools.data_center.host_runtime import _TextProtocolAdapter, build_data_center_system_prompt
from ui.data_center.agent_panel import empty_answer_text

PASS, FAIL = [], []

ANNOUNCE = "让我搜索商业险出险次数的获取来源。"
CONTINUE = "请直接调用工具继续执行，不要只描述计划。"
ALLOWED = ("search_objects", "describe_object", "query_readonly")


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


class ScriptModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def complete_turn(self, request, *, cancellation, on_delta):
        self.seen.append([dict(item) for item in request.messages])
        if not self.replies:
            raise AssertionError('model called too many times')
        return AgentTurn(text=self.replies.pop(0))


class Registry:
    def __init__(self, names):
        self.names = list(names)
        self.calls = []

    def definitions(self):
        return [{"name": name} for name in self.names]

    def execute(self, call, *, context, cancellation):
        self.calls.append((call.name, dict(call.arguments)))
        return ToolResult(ok=True, code="OK", data={"ok": True})


def run(replies, names=ALLOWED):
    model = ScriptModel(replies)
    registry = Registry(names)
    result = AgentRunner(
        model,
        registry,
        budget_limits=BudgetLimits(
            max_model_turns=8,
            max_duration_s=60,
            max_output_tokens=None,
            max_input_chars=None,
            max_bytes=None,
        ),
    ).run(RunContext("run-text", "tab-text", "fake-model"), "查商业险出险次数")
    return result, model, registry


def user_text(messages):
    return [item.get("content") for item in messages if item.get("role") == "user"]


string_args = json.dumps(
    {"name": "search_objects", "arguments": json.dumps({"keyword": "prpcmain"}, ensure_ascii=False)},
    ensure_ascii=False,
)
both_formats = (
    "先查对象。\n"
    f"<tool_call>\n```json\n{string_args}\n```\n</tool_call>\n"
    '<tool_call>{"name":"describe_object","arguments":{"name":"PRPCMAIN"}}</tool_call>'
)
parameter_form = (
    "<tool_call><function=search_objects><parameter=keyword>prpcmain</parameter></function></tool_call>"
)

print('--- 解析并执行 ---')
result, model, registry = run([
    both_formats,
    '<function=query_readonly>{"sql":"select 1 from dual"}</function>',
    "商业险出险次数是 3 次。",
])
check('两种标签都执行了', [name for name, _args in registry.calls] == [
    "search_objects", "describe_object", "query_readonly",
], str(registry.calls))
check('字符串 arguments 解成对象', registry.calls and registry.calls[0][1].get("keyword") == "prpcmain", str(registry.calls[:1]))
check('围栏和前后文字不影响第二个调用', len(registry.calls) > 1 and registry.calls[1][1].get("name") == "PRPCMAIN")
check('function 标签的 JSON 是参数', len(registry.calls) > 2 and registry.calls[2][1].get("sql") == "select 1 from dual")
check('执行后给出最终回答', result.final_text == "商业险出险次数是 3 次。" and not result.failure_reason)

result, model, registry = run([parameter_form, "旧格式仍然可用。"])
check('旧 parameter 标记仍执行', registry.calls == [("search_objects", {"keyword": "prpcmain"})], str(registry.calls))

print('--- 别名 ---')
result, model, registry = run([
    '<tool_call>{"name":"get_columns","arguments":{"name":"PRPCMAIN"}}</tool_call>',
    '<function=search_tables>{"keyword":"prpcmain"}</function>',
    '<tool_call>{"name":"describe_table","arguments":{"name":"PRPCMAIN"}}</tool_call>',
    '<tool_call>{"name":"get_table_schema","arguments":{"name":"PRPCMAIN"}}</tool_call>',
    '<tool_call>{"name":"list_tables","arguments":{"keyword":"prpcmain"}}</tool_call>',
    "别名已核对。",
])
check('别名都映射到允许的工具', [name for name, _args in registry.calls] == [
    "describe_object", "search_objects", "describe_object", "describe_object", "search_objects",
], str([name for name, _args in registry.calls]))
check('别名参数原样保留', registry.calls[0][1].get("name") == "PRPCMAIN" and registry.calls[1][1].get("keyword") == "prpcmain")

prepared, failure = prepare_text_tool_calls(
    '<tool_call>{"name":"list_tables","arguments":{"keyword":"a"}}</tool_call>',
    ["query_readonly"],
)
check('不允许的别名不改写成白名单外的工具', prepared is None and failure == "tool_rejected", str((prepared, failure)))

result, model, registry = run(
    ['<tool_call>{"name":"list_tables","arguments":{"keyword":"a"}}</tool_call>'] * 4,
    ["query_readonly"],
)
check('不允许的别名重试两次', len(model.seen) == 3, str(len(model.seen)))
check('不允许的别名没有执行', registry.calls == [], str(registry.calls))
check('不允许的别名得到 tool_rejected', result.failure_reason == "tool_rejected" and result.status is AgentStatus.COMPLETED)
check('拒绝原因是中文且非空', result.final_text == "模型调用了不允许的工具" and "tool_rejected" not in result.final_text)

print('--- 坏 JSON ---')
result, model, registry = run(
    ['<tool_call>{"name":"search_objects","arguments":</tool_call>'] * 4,
)
check('坏 JSON 重试两次', len(model.seen) == 3, str(len(model.seen)))
check('坏 JSON 没有执行工具', registry.calls == [])
check('坏 JSON 得到 parse_failed', result.failure_reason == "parse_failed")
check('parse_failed 的回答非空且不显示代号',
      result.final_text == "模型工具调用格式无法识别" and "parse_failed" not in (result.final_text or ""))
check('修复提示进了下一轮', any("无法识别" in text for text in user_text(model.seen[1])))
check('坏标记仅进入本轮修复请求', any('<tool_call>' in str(item.get('content')) for item in model.seen[1])
      and not any('<tool_call>' in str(item.get('content')) for item in result.messages))
check('修复提示不留在返回历史', all(
    "无法识别" not in str(item.get("content") or "")
    for item in result.messages if item.get("role") == "user"
))

print('--- 宣告句续跑 ---')
check('宣告句本身被识别', unfinished_work_announcement(ANNOUNCE))
check('普通回答不是宣告句', not unfinished_work_announcement("商业险出险次数是 3 次。"))
check('无标点结尾也是宣告句', unfinished_work_announcement("我先查一下"))
check('感叹号结尾不是宣告句', not unfinished_work_announcement("让我搜索来源！"))
check('继续读取冒号是宣告', unfinished_work_announcement("继续读取 config.py："))
check('句中让我加冒号是宣告', unfinished_work_announcement("好的，让我看下表结构："))
check('我再搜一下冒号是宣告', unfinished_work_announcement("我再搜一下："))
check('接下来句号不是宣告', not unfinished_work_announcement("接下来建议重建索引。"))
check('下面句号不是宣告', not unfinished_work_announcement("下面是结果。"))

result, model, registry = run([
    ANNOUNCE,
    "商业险出险次数来自承保库，共 3 次。",
])
check('宣告句触发了一次续跑', len(model.seen) == 2, str(len(model.seen)))
check('续跑提示原文正确', CONTINUE in user_text(model.seen[1]), str(user_text(model.seen[1])))
check('续跑后采用后一条回答', result.final_text == "商业险出险次数来自承保库，共 3 次。")
check('宣告句续跑没有失败代号', not result.failure_reason)

result, model, registry = run([ANNOUNCE] * 6)
check('宣告句最多续跑两次', len(model.seen) == 3, str(len(model.seen)))
check('续跑两次后停在宣告句上', result.status is AgentStatus.COMPLETED and result.final_text == ANNOUNCE)
check('续跑不会死循环', len(model.seen) < 6)

same_line = ANNOUNCE + '<tool_call>{"name":"search_objects","arguments":{"keyword":"prpcmain"}}</tool_call>'
result, model, registry = run([same_line, "查完了。"])
check('同一轮有工具调用时不把宣告句当结束', registry.calls[:1] == [("search_objects", {"keyword": "prpcmain"})], str(registry.calls[:1]))

print('--- 普通回答 ---')
plain = "商业险出险次数是 3 次。"
result, model, registry = run([plain])
check('无标签回答只调用一次模型', len(model.seen) == 1)
check('无标签回答原样返回', result.final_text == plain and result.status is AgentStatus.COMPLETED)
check('无标签回答不追加续跑', all(CONTINUE not in text for text in user_text(model.seen[0])))
check('无标签回答没有 failure_reason', not result.failure_reason)

print('--- 代码 Agent 与数据中心适配器 ---')
recovered = recover_text_tool_calls(
    '先看文件\n<tool_call>{"name":"read_file","arguments":{"path":"README.md"}}</tool_call>'
)
check('代码 Agent 能执行 JSON tool_call', recovered == [{"tool": "read_file", "args": {"path": "README.md"}}], str(recovered))
function_recovered = recover_text_tool_calls('<function=read_file>{"path":"src/a.py"}</function>')
check('代码 Agent 能执行 function 标签', function_recovered == [{"tool": "read_file", "args": {"path": "src/a.py"}}], str(function_recovered))
check('代码 Agent 不把数据中心别名放进编程白名单', recover_text_tool_calls(
    '<tool_call>{"name":"search_tables","arguments":{"keyword":"a"}}</tool_call>'
) == [])


class _Inner:
    def __init__(self, text):
        self.text = text

    def complete_turn(self, request, *, cancellation, on_delta):
        return AgentTurn(text=self.text)


adapted = _TextProtocolAdapter(
    _Inner('<tool_call>{"name":"get_table_schema","arguments":{"name":"T"}}</tool_call>'),
    frozenset({"describe_object"}),
).complete_turn(None, cancellation=None, on_delta=lambda _delta: None)
check('数据中心适配器把别名映射后交给执行', adapted.tool_calls and adapted.tool_calls[0].name == "describe_object"
      and dict(adapted.tool_calls[0].arguments).get("name") == "T")
rejected_turn = _TextProtocolAdapter(
    _Inner('<function=list_tables>{"keyword":"a"}</function>'),
    frozenset({"query_readonly"}),
).complete_turn(None, cancellation=None, on_delta=lambda _delta: None)
check('数据中心适配器拒绝白名单外的别名', not rejected_turn.tool_calls)

print('--- 系统提示与空回答文案 ---')
check('代码 Agent 系统提示要求基于证据继续', EVIDENCE_CONTINUATION_INSTRUCTION in build_tool_system_prompt('workspace'))
check('数据中心系统提示要求基于证据继续', EVIDENCE_CONTINUATION_INSTRUCTION in build_data_center_system_prompt(
    provider='oracle', mode='oracle', database='db', schemas=('APP',), text_only=False,
))
check('parse_failed 显示中文', empty_answer_text('parse_failed') == '模型工具调用格式无法识别')
check('tool_rejected 显示中文', empty_answer_text('tool_rejected') == '模型调用了不允许的工具')
check('empty_output 显示中文', empty_answer_text('empty_output') == '模型没有输出内容')
check('空回答不显示英文代号', all(
    code not in empty_answer_text(code)
    for code in ('parse_failed', 'tool_rejected', 'empty_output')
))
check('没有代号时仍用原来的空回答', empty_answer_text('') == '这次没有返回查询内容。')

print()
try:
    _isolation.assert_untouched()
    check('未触碰真实数据目录与用户配置', True)
except AssertionError as exc:
    check('未触碰真实数据目录与用户配置', False, str(exc))

print(f'RESULT: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('FAILED:', FAIL)
sys.exit(1 if FAIL else 0)
