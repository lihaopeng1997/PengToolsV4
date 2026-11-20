"""Redacted diagnostics zip. No Qt import and no business data files."""
from __future__ import annotations

import os
import platform
import sys
import zipfile

_TAIL_BYTES = 512 * 1024

_EXCLUDED = (
    'data/ 业务文件',
    '*.sqlite3 数据库',
    'secure_store 凭证',
    'ai_local.json',
    'harness connections.json',
    'ops_servers.json',
    'known_hosts',
    '模型地址与 token',
    '设置项的值',
    '完整内网地址',
)


class DiagnosticsError(Exception):
    """Export failed. The message is safe to show; it has no traceback."""


def collect_manifest() -> dict:
    """What a bundle will and will not contain. No file contents."""
    return {
        'included': [
            'version.txt',
            'system.txt',
            'settings_keys.txt',
            'manifest.txt',
            'logs/app.log',
            'logs/app.log.1',
            'logs/crash.log',
            'logs/agent_diag.log',
            'logs/agent_diag.log.1',
            'logs/agent_diag.log.2',
            'logs/webengine_startup.log',
        ],
        'excluded': list(_EXCLUDED),
    }


def build_bundle(dest_path, *, tail_bytes=_TAIL_BYTES, log_paths=None, settings=None) -> str:
    """Write a redacted zip. Failures leave neither the zip nor its temp file."""
    dest_path = os.path.abspath(str(dest_path))
    tmp_path = dest_path + '.tmp'
    try:
        parent = os.path.dirname(dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload, notes = _payload(tail_bytes=tail_bytes, log_paths=log_paths, settings=settings)
        with zipfile.ZipFile(tmp_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, text in payload.items():
                archive.writestr(name, text)
        os.replace(tmp_path, dest_path)
    except Exception:
        _remove_quiet(tmp_path)
        raise DiagnosticsError(f'无法导出诊断包：{dest_path}') from None
    return dest_path


def _payload(*, tail_bytes, log_paths, settings):
    notes = []
    files = {
        'version.txt': _version_text(),
        'system.txt': _system_text(),
        'settings_keys.txt': _settings_keys(settings),
    }
    for arcname, text, note in _log_entries(tail_bytes, log_paths):
        notes.append(note)
        if text is not None:
            files[arcname] = text
    files['manifest.txt'] = _manifest_text(notes)
    return files, notes


def _version_text() -> str:
    from config import APP_BUILD_DATE, APP_EDITION, APP_NAME, APP_VERSION, app_version_text
    lines = [
        f'APP_NAME={APP_NAME}',
        f'APP_VERSION={APP_VERSION}',
        f'APP_EDITION={APP_EDITION}',
        f'APP_BUILD_DATE={APP_BUILD_DATE}',
        f'app_version_text={app_version_text()}',
    ]
    return '\n'.join(lines) + '\n'


def _system_text() -> str:
    qt_version, pyqt_version = 'unknown', 'unknown'
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        qt_version = str(QT_VERSION_STR)
        pyqt_version = str(PYQT_VERSION_STR)
    except Exception:
        pass
    lines = [
        f'platform={platform.platform()}',
        f'python={sys.version.split()[0]}',
        f'qt={qt_version}',
        f'pyqt={pyqt_version}',
        f'frozen={bool(getattr(sys, "frozen", False))}',
    ]
    return '\n'.join(lines) + '\n'


def _settings_keys(settings) -> str:
    from config import DEFAULT_SETTINGS
    if settings is None:
        from config import load_settings
        settings = load_settings()
    if not isinstance(settings, dict):
        settings = {}
    names = list(DEFAULT_SETTINGS.keys())
    for key in settings:
        if key not in DEFAULT_SETTINGS:
            names.append(key)
    lines = []
    for key in names:
        if key in settings:
            value = settings[key]
        else:
            value = DEFAULT_SETTINGS.get(key)
        kind = type(value).__name__
        extra = '' if key in DEFAULT_SETTINGS else ' (非默认)'
        lines.append(f'{key}: {kind}{extra}')
    return '\n'.join(lines) + '\n'


def _manifest_text(notes) -> str:
    manifest = collect_manifest()
    lines = ['included:']
    lines.extend(f'- {item}' for item in manifest['included'])
    lines.append('excluded:')
    lines.extend(f'- {item}' for item in manifest['excluded'])
    lines.append('logs:')
    lines.extend(f'- {note}' for note in notes)
    return '\n'.join(lines) + '\n'


def _log_entries(tail_bytes, log_paths):
    from tools.app_logging import redact_text
    roots = _log_roots()
    entries = _candidate_logs() if log_paths is None else list(log_paths)
    seen = set()
    for raw in entries:
        path = os.path.abspath(str(raw or ''))
        base = os.path.basename(path)
        if not path or not _inside_log_dir(path, roots):
            if base:
                yield None, None, f'{base}: 已跳过'
            continue
        arcname = _arc_name(base, seen)
        if not os.path.isfile(path):
            yield None, None, f'{base}: 无'
            continue
        try:
            original = os.path.getsize(path)
            with open(path, 'rb') as handle:
                if original > tail_bytes:
                    handle.seek(original - int(tail_bytes))
                    raw_bytes = handle.read(int(tail_bytes))
                    taken = len(raw_bytes)
                else:
                    raw_bytes = handle.read()
                    taken = len(raw_bytes)
        except OSError:
            yield None, None, f'{base}: 无'
            continue
        text = raw_bytes.decode('utf-8', errors='replace')
        redacted = '\n'.join(redact_text(line) for line in text.splitlines())
        if text.endswith('\n'):
            redacted += '\n'
        yield arcname, redacted, f'{base}: 原始 {original} 字节，截取 {taken} 字节'


def _candidate_logs():
    paths = []
    directory = _primary_log_dir()
    if directory:
        for name in ('app.log', 'app.log.1', 'crash.log'):
            paths.append(os.path.join(directory, name))
    try:
        from tools.agent_diag import log_file_path
        agent = log_file_path()
        paths.extend([agent, agent + '.1', agent + '.2'])
    except Exception:
        pass
    try:
        web_dir = _primary_log_dir()
        if web_dir:
            paths.append(os.path.join(web_dir, 'webengine_startup.log'))
    except Exception:
        pass
    return paths


def _primary_log_dir() -> str:
    from tools.app_logging import current_log_dir
    current = current_log_dir()
    if current:
        return current
    try:
        from config import local_data_dir
        return os.path.join(local_data_dir(), 'logs')
    except Exception:
        return ''


def _log_roots():
    roots = []
    primary = _primary_log_dir()
    if primary:
        roots.append(primary)
    try:
        from tools.agent_diag import log_file_path
        roots.append(os.path.dirname(log_file_path()))
    except Exception:
        pass
    unique = []
    for root in roots:
        if root and root not in unique:
            unique.append(root)
    return unique


def _inside_log_dir(path, roots) -> bool:
    try:
        real_path = os.path.realpath(path)
    except OSError:
        return False
    for root in roots:
        try:
            real_root = os.path.realpath(root)
            if not real_root:
                continue
            if os.path.normcase(os.path.commonpath([real_path, real_root])) == os.path.normcase(real_root):
                return True
        except (OSError, ValueError):
            continue
    return False


def _arc_name(base, seen) -> str:
    name = base or 'log.txt'
    arc = f'logs/{name}'
    index = 2
    while arc in seen:
        stem, ext = os.path.splitext(name)
        arc = f'logs/{stem}_{index}{ext}'
        index += 1
    seen.add(arc)
    return arc


def _remove_quiet(path) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        return
