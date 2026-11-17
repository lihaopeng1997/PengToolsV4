"""Swallowed data-center failures are logged without their secrets."""
import logging
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from tools.app_logging import get_logger, log_failure, setup_logging  # noqa: E402
from tools.data_center.manual_session import ManualSession, ManualSessionTarget  # noqa: E402
from tools import db_connect  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


def flush():
    for handler in logging.getLogger().handlers:
        handler.flush()


SECRET = "password=SECRETPW 10.9.8.7 select 'SECRETLIT'"

log_dir = os.path.join(_isolation.temp_dir(), 'swallowed-logs')
log_path = setup_logging(log_dir)


class _Redis:
    def type(self, key):
        message = SECRET
        raise RuntimeError(message)


class _MongoDoc:
    def find_one(self):
        message = SECRET
        raise RuntimeError(message)


class _Mongo:
    def __getitem__(self, name):
        return _MongoDoc()


check('redis 列字段仍返回空', db_connect.list_columns(_Redis(), 'redis', 'k') == [])
check('mongo 列字段仍返回空', db_connect.list_columns(_Mongo(), 'mongodb', 't') == [])
flush()
text = Path(log_path).read_text(encoding='utf-8')
check('记录 DB_LIST_COLUMNS_FAILED', text.count('DB_LIST_COLUMNS_FAILED') >= 2)
check('记录异常类型和 list_columns 堆栈', 'RuntimeError' in text and 'list_columns' in text)
for secret in ('SECRETPW', 'SECRETLIT', '10.9.8.7'):
    check(f'列字段日志不含 {secret}', secret not in text)


class _Driver:
    def request_interrupt(self):
        raise RuntimeError('interrupt-down')


session = ManualSession(
    ManualSessionTarget('tab', 'conn', 'rev'),
    lambda: None,
    lambda sql: None,
)
session._active_job = object()
session._driver = _Driver()
check('中断失败仍返回进行中', session.cancel_current() is True)
flush()
hooked = Path(log_path).read_text(encoding='utf-8')
check('记录 MANUAL_INTERRUPT_FAILED', 'MANUAL_INTERRUPT_FAILED' in hooked)

plain = get_logger('verify.plain')
try:
    message = SECRET
    raise RuntimeError(message)
except RuntimeError as exc:
    saved = exc
log_failure(plain, 'PLAIN_CODE', saved, with_message=False)
log_failure(plain, 'REDACT_CODE', saved, with_message=True)
flush()
logged = Path(log_path).read_text(encoding='utf-8')
plain_at = logged.rfind('PLAIN_CODE')
redact_at = logged.rfind('REDACT_CODE')
plain_part = logged[plain_at:redact_at] if plain_at >= 0 and redact_at > plain_at else ''
redact_part = logged[redact_at:] if redact_at >= 0 else ''
check('with_message=False 不含消息', plain_part and 'SECRETPW' not in plain_part and 'SECRETLIT' not in plain_part)
check('with_message=True 已脱敏', 'REDACT_CODE' in redact_part and 'SECRETPW' not in redact_part and '10.9.8.7' not in redact_part)


class _BoomLogger:
    def log(self, *args, **kwargs):
        raise RuntimeError('logger down')


raised = False
try:
    log_failure(_BoomLogger(), 'SHOULD_NOT_ESCAPE', saved, with_message=True)
except Exception:
    raised = True
check('logger 抛错时 log_failure 不抛出', not raised)

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
