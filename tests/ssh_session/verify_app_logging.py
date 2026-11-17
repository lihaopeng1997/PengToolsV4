"""File log redaction and last-chance hooks (offscreen, no network)."""
import logging
import os
import sys
import threading
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from PyQt6.QtWidgets import QApplication

app = QApplication([])

from tools.app_logging import (  # noqa: E402
    get_logger,
    install_exception_hooks,
    setup_logging,
)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


def flush():
    for handler in logging.getLogger().handlers:
        handler.flush()


log_dir = os.path.join(_isolation.temp_dir(), 'app-log')
log_path = setup_logging(log_dir)
again = setup_logging(log_dir)
marked = [h for h in logging.getLogger().handlers if getattr(h, '_pengtools_app_log', False)]
check('日志落在隔离临时目录', os.path.abspath(log_path).startswith(os.path.abspath(_isolation.temp_dir())))
check('重复初始化不增加 handler', again == log_path and len(marked) == 1, str(len(marked)))
check('paramiko 实际级别是 WARNING',
      logging.getLogger('paramiko').getEffectiveLevel() == logging.WARNING)

logger = get_logger('verify.app')
logger.info('token Bearer abc123')
logger.info('password=xx')
logger.info('http://u:p@h')
logger.info('host 10.1.2.3')
try:
    raise RuntimeError('stack Bearer abc123 host 10.1.2.3')
except RuntimeError:
    logger.exception('boom')
flush()
text = Path(log_path).read_text(encoding='utf-8')
check('Bearer 被遮掉', 'abc123' not in text and 'Bearer' in text)
check('password 被遮掉', 'password=xx' not in text)
check('URL 凭据被遮掉', 'u:p@' not in text)
check('私网地址被遮掉', '10.1.2.3' not in text and '10.x.x.x' in text, text[-500:])

install_exception_hooks()


def _crash():
    raise RuntimeError('thread-boom-marker')

thread = threading.Thread(target=_crash, name='verify-thread')
thread.start()
thread.join()
try:
    raise RuntimeError('main-boom-marker')
except RuntimeError:
    sys.excepthook(*sys.exc_info())
flush()
hooked = Path(log_path).read_text(encoding='utf-8')
check('sys.excepthook 记下堆栈', 'main-boom-marker' in hooked and 'Traceback' in hooked)
check('threading.excepthook 记下堆栈', 'thread-boom-marker' in hooked)

from PyQt6.QtCore import qWarning  # noqa: E402

install_exception_hooks()
qWarning('qt-warning-marker')
flush()
check('Qt 警告写入日志', 'qt-warning-marker' in Path(log_path).read_text(encoding='utf-8'))


def _bad_dialog():
    raise RuntimeError('dialog-should-not-escape')


install_exception_hooks(show_dialog=_bad_dialog)
sys.excepthook(RuntimeError, RuntimeError('dialog-hook'), None)
check('对话框失败时钩子不抛出', True)

chunk = 'rotate-marker ' * 400
rotated = False
for index in range(2500):
    logger.info(chunk)
    if index % 25 == 0:
        flush()
        if os.path.exists(log_path + '.1'):
            rotated = True
            break
if not rotated:
    logger.info('after-rotate')
    flush()
    rotated = os.path.exists(log_path + '.1')
check('轮转生效', rotated, log_path)

import tools.app_logging as app_logging

shown = []
app_logging._state['dialog_open'] = False
app_logging._state['dialog_at'] = 0.0
install_exception_hooks(show_dialog=lambda: shown.append(1))
app_logging._sys_excepthook(RuntimeError, RuntimeError('rate-1'), None)
app_logging._sys_excepthook(RuntimeError, RuntimeError('rate-2'), None)
check('连续两次异常只弹一次窗', len(shown) == 1, str(len(shown)))

app_logging._state['handler'] = None
app_logging._state['path'] = ''
real_handler = logging.handlers.RotatingFileHandler

def _deny_file(*args, **kwargs):
    raise OSError('unwritable')

logging.handlers.RotatingFileHandler = _deny_file
try:
    denied = setup_logging(os.path.join(_isolation.temp_dir(), 'denied-log'))
    check('目录不可写时返回空字符串', denied == '')
finally:
    logging.handlers.RotatingFileHandler = real_handler

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
