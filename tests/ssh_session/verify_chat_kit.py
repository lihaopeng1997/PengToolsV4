"""ChatKit widgets and the model-chat migration. Offscreen, no network."""
import ast
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401

from PyQt6.QtWidgets import QApplication, QWidget

_app = QApplication([])
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402

from tools.ai_events import AiEvent
from tools.app_logging import current_log_dir, get_logger, setup_logging
from ui.chat_kit import (
    ChatController, ChatView, CodeBlock, Composer, PhaseLine, ThinkingBlock, ToolStepLine,
)
from ui.log_analysis.chat_view import ChatView as LogChatView
from ui.theme_manager import ThemeManager, unresolved_qss_tokens
import tools.intranet_llm as llm
import tools.model_chat_store as chat_store
import config
from panels.model_chat_panel import ModelChatPanel, _ChatWorker

chat_store.MODEL_CHAT_DIR = os.path.join(_isolation.temp_dir(), 'model_chat')
config.MODEL_CHAT_DIR = chat_store.MODEL_CHAT_DIR

PASS, FAIL = [], []
ROOT = Path(__file__).resolve().parents[2]
MARKER = 'THINK_MARKER_9f3'


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


def _has_cjk(text: str) -> bool:
    return any('\u4e00' <= ch <= '\u9fff' for ch in text)


def _ascii_letters(text: str) -> str:
    return ''.join(ch for ch in text if ch.isascii() and ch.isalpha())


def _show(widget, width, height):
    widget.resize(width, height)
    widget.show()
    QTest.qWait(40)


print('--- 代码块与换行 ---')
code_view = ChatView()
_show(code_view, 360, 220)
code = 'a' * 400
code_view.append_message({
    'id': 'code-1',
    'role': 'assistant',
    'content': '```\n' + code + '\n```',
    'status': 'complete',
    'created_at': '2026-11-18T08:05:00',
})
QTest.qWait(60)
code_bar = code_view.horizontalScrollBar()
blocks = code_view.findChildren(CodeBlock)
editor_max = blocks[0].editor.horizontalScrollBar().maximum() if blocks else -1
code_height = blocks[0].height() if blocks else -1
check('消息区横向滚动条不可见', not code_bar.isVisible() and code_bar.maximum() == 0, f'max={code_bar.maximum()} visible={code_bar.isVisible()}')
check('代码块内部横向可滚', editor_max > 0, str(editor_max))
check('代码块高度不超过约 320', 0 < code_height <= 330, str(code_height))

wrap_view = ChatView()
_show(wrap_view, 280, 200)
wrap_view.append_message({
    'id': 'wrap-1',
    'role': 'assistant',
    'content': '测' * 80,
    'status': 'complete',
})
QTest.qWait(60)
browsers = [item for item in wrap_view.findChildren(QWidget) if item.objectName() == 'chat-markdown']
doc_height = browsers[0].document().size().height() if browsers else 0
wrap_bar = wrap_view.horizontalScrollBar()
check(
    '无空格中文自动换行且消息区不横向滚动',
    doc_height > 28 and not wrap_bar.isVisible() and wrap_bar.maximum() == 0,
    f'height={doc_height} max={wrap_bar.maximum()}',
)

print('--- 跳到底部 ---')
jump = ChatView()
_show(jump, 360, 160)
jump.append_message({
    'id': 'tall-1',
    'role': 'assistant',
    'content': '这是用来把消息区撑高的一行。\n' * 40,
    'status': 'complete',
})
QTest.qWait(80)
bar = jump.verticalScrollBar()
check('在底部时按钮隐藏', bar.maximum() > 32 and not jump._jump.isVisible(), f'max={bar.maximum()} shown={jump._jump.isVisible()}')
bar.setValue(0)
QTest.qWait(30)
check('滚到顶后按钮显示', jump._jump.isVisible() and not jump._dot.isVisible(), f'btn={jump._jump.isVisible()} dot={jump._dot.isVisible()}')
jump.append_message({
    'id': 'tall-2',
    'role': 'assistant',
    'content': '新到的一条。',
    'status': 'complete',
})
QTest.qWait(80)
check('不在底部追加时出现红点', jump._jump.isVisible() and jump._dot.isVisible())
jump._jump.click()
QTest.qWait(40)
bar = jump.verticalScrollBar()
check(
    '点击后到底并清掉红点',
    bar.maximum() - bar.value() <= 32 and not jump._dot.isVisible() and not jump._jump.isVisible(),
    f'gap={bar.maximum() - bar.value()} dot={jump._dot.isVisible()} btn={jump._jump.isVisible()}',
)

print('--- 思考、阶段、工具 ---')
think_view = ChatView()
think_view.resize(420, 280)
bubble = think_view.append_message({'id': 't1', 'role': 'assistant', 'content': '', 'status': 'pending'})
controller = ChatController(think_view)
controller.attach(bubble)
for piece in ('甲', '乙', '丙'):
    controller.handle(AiEvent(kind='reasoning_delta', text=piece))
title = bubble.thinking.title.text()
check('三段思考时展开且字数正确', bubble.thinking.is_expanded() and '3 字' in title, title)
controller.handle(AiEvent(kind='text_delta', text='可见正文'))
check('定时器触发前思考仍展开', bubble.thinking.is_expanded())
QTest.qWait(120)
check('第一段正文节流后收起思考', not bubble.thinking.is_expanded() and '可见正文' in bubble.markdown.source())

huge = ThinkingBlock()
tail = 'TAIL64_END'
huge.add_delta(('H' * 80) + ('中' * 40000) + tail)
kept = huge.text()
check(
    '思考超过 64KB 只留末尾',
    kept.endswith(tail) and len(kept.encode('utf-8')) <= 64 * 1024 and not kept.startswith('H' * 80),
    f'bytes={len(kept.encode("utf-8"))} head={kept[:8]!r}',
)

for state in ('model_turn_reset', 'tool_call_started', 'xyz_unknown_state'):
    line = PhaseLine()
    line.set_state(state)
    shown = line.display_text()
    letters = _ascii_letters(shown)
    check(
        f'阶段 {state} 只显示中文',
        '_' not in shown and not letters and _has_cjk(shown) and shown == line.caption.text(),
        shown,
    )

known = ToolStepLine('search_objects', 'users', 'ok', 2, call_id='call_SHOULD_NOT_SHOW')
unknown = ToolStepLine('drop_database', 'secret_table', 'failed', 1, call_id='call_HIDDEN')
check('已知工具映射且不显示 call_id', known.mark.text() == '✓' and known.text().startswith('搜索对象 users') and 'call_SHOULD_NOT_SHOW' not in known.text() and 'search_objects' not in known.text(), known.text())
check('未知工具是执行工具', unknown.mark.text() == '!' and unknown.text().startswith('执行工具') and 'drop_database' not in unknown.text() and 'call_HIDDEN' not in unknown.text(), unknown.text())
running = ToolStepLine('read_file', 'main.py', 'running', 0, call_id='cid')
check('运行中工具不显示内部名', running.mark.text() == '…' and running.text().startswith('读取文件') and 'cid' not in running.text())

print('--- 输入区 ---')
composer = Composer()
_show(composer, 480, 220)
idle_width = composer.send_btn.width()
composer.set_running(True)
check(
    '运行中变成停止且宽度不变',
    composer.send_btn.text() == '停止'
    and bool(composer.send_btn.property('danger'))
    and composer.send_btn.property('actionRole') == 'danger'
    and composer.send_btn.width() == idle_width
    and idle_width > 0,
    f'text={composer.send_btn.text()} w={composer.send_btn.width()} idle={idle_width}',
)
composer.set_running(False)
check('结束后恢复发送', composer.send_btn.text() == '发送' and not bool(composer.send_btn.property('danger')))
sent = {'n': 0}
composer.send_requested.connect(lambda: sent.__setitem__('n', sent['n'] + 1))
QTest.keyClick(composer.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
shift_count = sent['n']
QTest.keyClick(composer.input, Qt.Key.Key_Return)
check('Enter 发送且 Shift+Enter 不发送', shift_count == 0 and sent['n'] == 1, str(sent))

print('--- P5 Composer 与日志流 ---')
compact = Composer(compact=True)
_show(compact, 320, 130)
button = compact.send_btn
stops = []
compact.stop_requested.connect(lambda: stops.append(True))
compact.set_language('en')
check('紧凑输入框使用英文发送，隐藏附件和重试',
      button.text() == 'Send' and compact.add_attachment_btn.isHidden() and compact.retry_btn.isHidden())
compact.set_running(True)
button.click()
check('原按钮原位切为英文停止', compact.send_btn is button and button.text() == 'Stop' and stops == [True])
compact.set_running(True, stopping=True)
check('等待停止时禁用同一按钮', button.text() == 'Stopping…' and not button.isEnabled())
compact.set_language('zh')
check('停止过程中切换语言', button.text() == '停止中…' and not button.isEnabled())
compact.set_language('en')
compact.set_running(False)
check('停止完成恢复英文发送', button.text() == 'Send' and button.isEnabled())

log_chat = LogChatView()
_show(log_chat, 340, 190)
for index in range(12):
    log_chat.append_message({'role': 'user', 'text': f'测试消息 {index}'})
QTest.qWait(40)
log_chat.verticalScrollBar().setValue(0)
log_chat.begin_stream('思考过程', role='thinking')
log_chat.append_stream('只用于显示')
log_chat.begin_stream('AI 助手（生成中）', role='assistant')
log_chat.append_stream('工具轮临时正文')
QTest.qWait(40)
jump = log_chat.findChild(QWidget, 'chat-jump-bottom')
dot = log_chat.findChild(QWidget, 'chat-jump-dot')
check('日志聊天浏览旧消息时显示跳转按钮及红点',
      log_chat.verticalScrollBar().value() == 0 and jump.isVisible() and dot.isVisible())
log_chat.discard_answer_preview()
check('流重置只删除临时正文气泡',
      log_chat.stream_role == ''
      and all('工具轮临时正文' not in bubble.visible_text() for bubble in log_chat._bubbles.values())
      and any(bubble.thinking.text() == '只用于显示' for bubble in log_chat._bubbles.values()))
jump.click()
check('点击跳转后回到底部并清除红点',
      log_chat.verticalScrollBar().value() == log_chat.verticalScrollBar().maximum()
      and not dot.isVisible())

print('--- 对话页不落思考 ---')
panel = ModelChatPanel()
renders = {'n': 0}


def _count_render():
    renders['n'] += 1


panel._render_messages = _count_render
answer = '可见回答甲乙丙'
calls = {'n': 0}


def _fake(messages, cfg=None, model_config_id=None, on_delta=None):
    calls['n'] += 1
    if on_delta:
        on_delta('reasoning', MARKER)
        on_delta('text', answer)
    return answer


original = llm.chat_completions
llm.chat_completions = _fake
try:
    session = chat_store.create_session(model='fake')
    session = chat_store.append_message(session['id'], 'assistant', '', status='pending', model='fake')
    panel._session = session
    panel._pending_id = session['messages'][-1]['id']
    panel._show_chat()
    live = panel.chat.append_message(session['messages'][-1])
    panel._bind_bubble(live)
    panel.controller.attach(live)
    worker = _ChatWorker([{'role': 'user', 'content': 'hi'}], {'enabled': True, 'model': 'fake'})
    worker.ai_event.connect(panel._on_ai_event)
    worker.completed.connect(panel._on_chat_ok)
    worker.run()
    QTest.qWait(80)
finally:
    llm.chat_completions = original

saved = chat_store.load_session(session['id'])
saved_text = ''
session_dir = Path(chat_store.MODEL_CHAT_DIR)
for path in session_dir.rglob('*'):
    if path.is_file():
        saved_text += path.read_text(encoding='utf-8', errors='replace')
log_path = setup_logging()
get_logger('pengtools.chat_kit').info('marker-only')
log_file = Path(current_log_dir() or os.path.dirname(log_path)) / 'app.log'
log_text = log_file.read_text(encoding='utf-8', errors='replace') if log_file.is_file() else ''
message = next(item for item in (saved or {}).get('messages') or [] if item.get('id') == panel._pending_id)
check('fake worker 被调用', calls['n'] == 1, str(calls))
check('会话文件没有思考标记', MARKER not in saved_text and answer in saved_text, saved_text[-180:])
check('日志没有思考标记', MARKER not in log_text and 'marker-only' in log_text)
check('正文完整且状态为 complete', message.get('content') == answer and message.get('status') == 'complete', str(message))
check('流式过程没有整页重绘', renders['n'] == 0, str(renders))
check('思考只留在这条气泡的内存里', MARKER in live.thinking.text() and MARKER not in live.visible_text())

print('--- 样式 ---')
qss = (ROOT / 'resources' / 'style.qss').read_text(encoding='utf-8')
check('chat-user-bubble 只定义一次', qss.count('chat-user-bubble') == 1, str(qss.count('chat-user-bubble')))
check('ChatKit 样式只有一块', qss.count('/* ChatKit */') == 1)
for theme in ('calm', 'black'):
    try:
        rendered = ThemeManager().render(theme)
    except RuntimeError as exc:
        check(f'{theme} 无未解析令牌', False, str(exc))
        continue
    leftover = unresolved_qss_tokens(rendered)
    check(f'{theme} 无未解析令牌', leftover == (), ','.join(leftover[:12]))

print('--- 语法 ---')
changed = [
    ROOT / 'panels' / 'model_chat_panel.py',
    ROOT / 'tests' / 'ssh_session' / 'verify_chat_kit.py',
]
changed.extend((ROOT / 'ui' / 'chat_kit').glob('*.py'))
for path in changed:
    try:
        ast.parse(path.read_text(encoding='utf-8'))
        check(f'语法 {path.name}', True)
    except SyntaxError as exc:
        check(f'语法 {path.name}', False, str(exc))

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
