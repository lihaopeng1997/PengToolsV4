"""Process-wide file log and last-chance exception hooks.

The file handler redacts messages before they hit disk. Hook failures are
swallowed so a logging problem cannot replace the original crash.
"""
from __future__ import annotations

import faulthandler
import inspect
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback

_LOG_BYTES = 2 * 1024 * 1024
_LOG_BACKUPS = 5
_FORMAT = '%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s'
_HANDLER_MARK = '_pengtools_app_log'

_QUIET_LOGGERS = ('paramiko', 'urllib3', 'asyncio', 'PIL')
_DIALOG_INTERVAL = 30.0

_state = {
    'path': '',
    'handler': None,
    'hooks': False,
    'show_dialog': None,
    'on_interrupt': None,
    'interrupt_scheduled': False,
    'fault_file': None,
    'dialog_open': False,
    'dialog_at': 0.0,
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
    if _state.get('handler') is not None:
        return _state.get('path') or ''
    _quiet_libraries()
    root = logging.getLogger()
    root.setLevel(_level())
    directory = _choose_dir(log_dir)
    path = os.path.join(directory, 'app.log') if directory else ''
    try:
        if not path:
            raise OSError('no writable log directory')
        handler = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=_LOG_BYTES,
            backupCount=_LOG_BACKUPS,
            encoding='utf-8',
        )
    except OSError:
        handler = logging.NullHandler()
        path = ''
    else:
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.addFilter(RedactingFilter())
        setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    _state['handler'] = handler
    _state['path'] = path
    return path


def install_exception_hooks(show_dialog=None, on_interrupt=None) -> None:
    """Record uncaught exceptions. A dialog is optional and must not raise."""
    if show_dialog is not None:
        _state['show_dialog'] = show_dialog
    if on_interrupt is not None:
        _state['on_interrupt'] = on_interrupt
        _state['interrupt_scheduled'] = False
    if _state.get('handler') is None:
        setup_logging()
    path = _state.get('path') or ''
    if not _state.get('hooks'):
        _state['hooks'] = True
        sys.excepthook = _sys_excepthook
        threading.excepthook = _thread_excepthook
        if path:
            _enable_fault_log(os.path.dirname(path))
    _install_qt_handler()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def _level() -> int:
    name = str(os.environ.get('PENGTOOLS_LOG_LEVEL') or 'INFO').upper()
    value = getattr(logging, name, None)
    return value if isinstance(value, int) else logging.INFO


def _quiet_libraries() -> None:
    for name in _QUIET_LOGGERS:
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET:
            logger.setLevel(logging.WARNING)


def _choose_dir(log_dir) -> str:
    primary = log_dir or _default_dir()
    chosen = _usable_dir(primary)
    if chosen:
        return chosen
    fallback = _fallback_dir()
    if not fallback:
        return ''
    return _usable_dir(fallback) or ''


def _fallback_dir() -> str:
    local_app = str(os.environ.get('LOCALAPPDATA') or '').strip()
    if not local_app:
        return ''
    return os.path.join(local_app, 'PengToolsHub', 'logs')


def _default_dir() -> str:
    try:
        from config import local_data_dir
        return os.path.join(local_data_dir(), 'logs')
    except Exception:
        return _fallback_dir()


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


def redact_text(text: str) -> str:
    from tools.sql_guard import redact_error
    from tools.log_analysis.events import Redactor
    return Redactor().clean(redact_error(str(text or '')))


_redact = redact_text


def log_failure(logger, code, exc, *, level=logging.ERROR, with_message=False) -> None:
    """Record a swallowed failure without logging the raw exception message."""
    try:
        text = f'{code}: {type(exc).__name__}'
        if with_message:
            text += ': ' + redact_text(str(exc))
        text += '\n' + ''.join(traceback.format_tb(exc.__traceback__))
        logger.log(level, '%s', text)
    except Exception:
        return


def current_log_dir() -> str:
    """Directory of the active app.log, or '' when file logging is not set up."""
    path = str(_state.get('path') or '')
    if not path:
        return ''
    return os.path.dirname(path)


def mask_private_ipv4(text: str) -> str:
    import re
    pattern = re.compile(
        r'(?<![A-Za-z0-9_])(?:'
        r'10\.\d{1,3}\.\d{1,3}\.\d{1,3}'
        r'|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}'
        r'|192\.168\.\d{1,3}\.\d{1,3}'
        r')(?![A-Za-z0-9_])'
    )

    def repl(match):
        return match.group(0).split('.', 1)[0] + '.x.x.x'

    return pattern.sub(repl, text)


def mask_private_ipv6(text: str) -> str:
    """Mask RFC 4193 ULA (fc00::/7), link-local (fe80::/10), and private IPv4-mapped IPv6."""
    import re
    raw = str(text or '')
    if ':' not in raw:
        return raw
    # IPv4-mapped private IPv6: ::ffff:10.x / 172.16-31.x / 192.168.x
    raw = re.sub(
        r'(?i)(?<![A-Za-z0-9_])::ffff:(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![A-Za-z0-9_])',
        '::ffff:x.x.x.x',
        raw,
    )
    # ULA (fc00::/7 -> fc.. or fd..) and link-local (fe80::/10 -> fe80..febf)
    raw = re.sub(
        r'(?i)(?<![A-Za-z0-9_:])(?:f[cd][0-9a-f]{2}|fe[89ab][0-9a-f]):(?:[0-9a-f]{0,4}:){1,6}[0-9a-f]{0,4}(?:%[A-Za-z0-9_.-]+)?(?![A-Za-z0-9_:])',
        '[私网IPv6已隐藏]',
        raw,
    )
    return raw


def _is_exit_signal(exc_type) -> bool:
    try:
        return isinstance(exc_type, type) and issubclass(exc_type, (KeyboardInterrupt, SystemExit))
    except Exception:
        return False


def _format_error_summary(exc_type, exc) -> str:
    name = getattr(exc_type, '__name__', '') or type(exc).__name__
    msg = ''
    try:
        raw = str(exc or '').strip()
        first_line = raw.splitlines()[0].strip() if raw else ''
        msg = redact_text(first_line) if first_line else ''
    except Exception:
        msg = ''
    if len(msg) > 160:
        msg = msg[:157] + '…'
    if name and msg:
        return f'{name}: {msg}'
    return name or msg


def _invoke_dialog(dialog, exc_type, exc) -> None:
    accepts_detail = False
    try:
        sig = inspect.signature(dialog)
        accepts_detail = any(
            p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            )
            for p in sig.parameters.values()
        )
    except (TypeError, ValueError):
        accepts_detail = False
    if accepts_detail:
        dialog(_format_error_summary(exc_type, exc))
    else:
        dialog()


def _handle_interrupt_exit(exc_type) -> None:
    """Handle Ctrl+C / SystemExit quietly without logging an error or popping a crash dialog."""
    try:
        logging.getLogger('pengtools').info(
            '收到中断/退出信号（%s），正常退出',
            getattr(exc_type, '__name__', 'Interrupt'),
        )
    except Exception:
        pass
    if threading.current_thread() is not threading.main_thread():
        return
    if not (isinstance(exc_type, type) and issubclass(exc_type, KeyboardInterrupt)):
        return
    if _state.get('interrupt_scheduled'):
        return
    _state['interrupt_scheduled'] = True

    def _finish():
        try:
            handler = _state.get('on_interrupt')
            if callable(handler):
                handler()
                return
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:
            pass
        finally:
            _state['interrupt_scheduled'] = False

    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is not None:
            QTimer.singleShot(0, _finish)
            return
    except Exception:
        pass
    _finish()


def _sys_excepthook(exc_type, exc, tb):
    if _is_exit_signal(exc_type):
        _handle_interrupt_exit(exc_type)
        return
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
        now = time.monotonic()
        if _state.get('dialog_open'):
            return
        last = float(_state.get('dialog_at') or 0.0)
        if last and now - last < _DIALOG_INTERVAL:
            return
        _state['dialog_open'] = True
        _state['dialog_at'] = now
        try:
            _invoke_dialog(dialog, exc_type, exc)
        finally:
            _state['dialog_open'] = False
    except Exception:
        _state['dialog_open'] = False
        return


def _thread_excepthook(args):
    if _is_exit_signal(getattr(args, 'exc_type', None)):
        return
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
