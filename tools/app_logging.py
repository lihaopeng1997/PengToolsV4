"""Process-wide file log and last-chance exception hooks.

The file handler redacts messages before they hit disk. Hook failures are
swallowed so a logging problem cannot replace the original crash.
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
import traceback

_LOG_BYTES = 2 * 1024 * 1024
_LOG_BACKUPS = 5
_FORMAT = '%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s'
_HANDLER_MARK = '_pengtools_app_log'

_state = {
    'path': '',
    'handler': None,
    'hooks': False,
    'show_dialog': None,
    'fault_file': None,
}


class RedactingFilter(logging.Filter):
    """Mask secrets and private addresses on one handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _redact(record.getMessage())
            record.args = ()
            if record.exc_info and record.exc_info[0] is not None:
                stack = ''.join(traceback.format_exception(*record.exc_info))
                record.exc_text = _redact(stack)
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = _redact(record.exc_text)
        except Exception:
            record.msg = '[日志脱敏失败]'
            record.args = ()
            record.exc_info = None
            record.exc_text = '[日志脱敏失败]'
        return True


def setup_logging(log_dir=None) -> str:
    """Attach one rotating file handler. Repeat calls keep the same handler."""
    existing = _state.get('handler')
    if existing is not None and _state.get('path'):
        return _state['path']
    directory = _choose_dir(log_dir)
    path = os.path.join(directory, 'app.log')
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=_LOG_BYTES,
        backupCount=_LOG_BACKUPS,
        encoding='utf-8',
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(RedactingFilter())
    setattr(handler, _HANDLER_MARK, True)
    root = logging.getLogger()
    root.setLevel(_level())
    root.addHandler(handler)
    _state['handler'] = handler
    _state['path'] = path
    return path


def install_exception_hooks(show_dialog=None) -> None:
    """Record uncaught exceptions. A dialog is optional and must not raise."""
    if show_dialog is not None:
        _state['show_dialog'] = show_dialog
    path = _state.get('path') or setup_logging()
    if not _state.get('hooks'):
        _state['hooks'] = True
        sys.excepthook = _sys_excepthook
        threading.excepthook = _thread_excepthook
        _enable_fault_log(os.path.dirname(path))
    _install_qt_handler()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def _level() -> int:
    name = str(os.environ.get('PENGTOOLS_LOG_LEVEL') or 'INFO').upper()
    value = getattr(logging, name, None)
    return value if isinstance(value, int) else logging.INFO


def _choose_dir(log_dir) -> str:
    primary = log_dir or _default_dir()
    chosen = _usable_dir(primary)
    if chosen:
        return chosen
    fallback = os.path.join(os.environ.get('LOCALAPPDATA') or '', 'PengToolsHub', 'logs')
    chosen = _usable_dir(fallback)
    return chosen or primary or fallback


def _default_dir() -> str:
    try:
        from config import local_data_dir
        return os.path.join(local_data_dir(), 'logs')
    except Exception:
        return os.path.join(os.environ.get('LOCALAPPDATA') or '', 'PengToolsHub', 'logs')


def _usable_dir(path: str) -> str:
    if not path:
        return ''
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, 'app.log')
        with open(probe, 'a', encoding='utf-8'):
            pass
        return path
    except OSError:
        return ''


def _redact(text: str) -> str:
    from tools.sql_guard import redact_error
    from tools.log_analysis.events import Redactor
    cleaned = redact_error(str(text or ''))
    cleaned = Redactor().clean(cleaned)
    return _mask_private_ipv4(cleaned)


def _mask_private_ipv4(text: str) -> str:
    import re
    pattern = re.compile(
        r'\b(?:'
        r'10\.\d{1,3}\.\d{1,3}\.\d{1,3}'
        r'|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}'
        r'|192\.168\.\d{1,3}\.\d{1,3}'
        r')\b'
    )

    def repl(match):
        return match.group(0).split('.', 1)[0] + '.x.x.x'

    return pattern.sub(repl, text)


def _sys_excepthook(exc_type, exc, tb):
    try:
        logging.getLogger('pengtools').error(
            '未捕获的异常',
            exc_info=(exc_type, exc, tb),
        )
        if threading.current_thread() is not threading.main_thread():
            return
        dialog = _state.get('show_dialog')
        if dialog is None:
            return
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            return
        dialog()
    except Exception:
        return


def _thread_excepthook(args):
    try:
        logging.getLogger('pengtools.thread').error(
            '线程未捕获异常',
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
    except Exception:
        return


def _install_qt_handler():
    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return
    noisy = {QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg}

    def _qt(mode, _context, message):
        try:
            if mode in noisy:
                logging.getLogger('qt').warning('%s', message)
        except Exception:
            return

    try:
        qInstallMessageHandler(_qt)
    except Exception:
        return


def _enable_fault_log(directory: str) -> None:
    if _state.get('fault_file') is not None:
        return
    try:
        os.makedirs(directory, exist_ok=True)
        handle = open(os.path.join(directory, 'crash.log'), 'a', encoding='utf-8')
        faulthandler.enable(handle, all_threads=True)
        _state['fault_file'] = handle
    except Exception:
        return
