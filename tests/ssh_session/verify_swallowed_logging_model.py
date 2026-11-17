"""Swallowed model and log-analysis failures are logged without prompts."""
import logging
import os
import sys
import time
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from tools.agent_core.contracts import AgentStatus, AgentTurn, RunContext  # noqa: E402
from tools.agent_core.runner import AgentRunner  # noqa: E402
from tools.app_logging import log_failure, setup_logging  # noqa: E402
from tools.log_analysis.service import LogService  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


def flush():
    for handler in logging.getLogger().handlers:
        handler.flush()


SECRET = 'SECRETPROMPT Bearer SECRETTOKEN123 reasoning_content SECRETREASON'

log_dir = os.path.join(_isolation.temp_dir(), 'model-logs')
log_path = setup_logging(log_dir)


class _Tools:
    def definitions(self):
        return ()


class _BoomModel:
    def complete_turn(self, request, *, cancellation, on_delta):
        message = SECRET
        raise RuntimeError(message)


class _OkModel:
    def complete_turn(self, request, *, cancellation, on_delta):
        on_delta(type('Delta', (), {'kind': 'text', 'text': '你好', 'arguments_delta': '', 'usage': None})())
        return AgentTurn(text='你好')


def _context():
    return RunContext('run-1', 'tab-1', 'model-1')


failed = AgentRunner(_BoomModel(), _Tools()).run(_context(), '问题')
check('模型失败结果不变', failed.status is AgentStatus.FAILED and failed.error == 'MODEL_FAILED:RuntimeError', str(failed.error))
flush()
text = Path(log_path).read_text(encoding='utf-8')
check('记录 MODEL_FAILED 和堆栈', 'MODEL_FAILED: RuntimeError\n' in text and 'File ' in text)
for secret in ('SECRETPROMPT', 'SECRETTOKEN123', 'SECRETREASON'):
    check(f'模型日志不含 {secret}', secret not in text)


def _sink(_event):
    raise RuntimeError('sink-down')


sunk = []
ok = AgentRunner(_OkModel(), _Tools(), on_event=_sink).run(_context(), '问题')
sunk.append(ok.status)
flush()
sunk_text = Path(log_path).read_text(encoding='utf-8')
check('事件回调失败不改变结果', sunk[0] is AgentStatus.COMPLETED, str(sunk[0]))
check('EVENT_SINK_FAILED 只出现一次', sunk_text.count('EVENT_SINK_FAILED') == 1, str(sunk_text.count('EVENT_SINK_FAILED')))


class _Task:
    id = 't'
    thread = None

    def snapshot(self, blocking=True):
        return {'id': 't', 'events': []}

    def stop(self):
        return None

    def read_range(self, *args, **kwargs):
        return None


def _analyzer(snapshot, question, model_id, token, **kwargs):
    message = SECRET
    raise RuntimeError(message)


service = LogService(store=None, analyzer=_analyzer)
service.tasks['t'] = _Task()
service.enqueue('t', '问题', 'model', kind='auto')
deadline = time.time() + 5
while time.time() < deadline:
    with service.lock:
        ready = service.active is None and not service.queue and any(row[1] == 'result' for row in service._ordered)
    if ready:
        break
    time.sleep(0.02)
events = service.drain_events()
service.close()
service.join(1)
result_text = next(payload[1] for bucket, payload in events if bucket == 'result')
flush()
analysis = Path(log_path).read_text(encoding='utf-8')
check('分析失败文案不变', result_text == '分析失败：RuntimeError；已保留证据，可手动重试。', result_text)
check('LOG_ANALYSIS_FAILED 只出现一次', analysis.count('LOG_ANALYSIS_FAILED') == 1)
check('分析日志不含提示词', 'SECRETPROMPT' not in analysis and 'SECRETREASON' not in analysis)

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
