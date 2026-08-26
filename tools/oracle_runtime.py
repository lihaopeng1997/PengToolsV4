# -*- coding: utf-8 -*-
"""Oracle 客户端模式：auto / thin / thick。进程内只允许一次 init_oracle_client。"""

from __future__ import annotations

import os

ORACLE_MODES = ('auto', 'thin', 'thick')

_STATE = {
    'initialized': False,
    'mode': None,
    'lib_dir': None,
    'error': '',
}


class OracleRuntimeError(Exception):
    pass


def normalize_mode(value: str) -> str:
    text = str(value or 'auto').strip().lower()
    return text if text in ORACLE_MODES else 'auto'


def client_state() -> dict:
    return dict(_STATE)


def diagnose_instant_client(lib_dir: str) -> dict:
    path = os.path.normpath(str(lib_dir or '').strip())
    result = {
        'path': path,
        'exists': False,
        'has_oci': False,
        'hint': '',
        'files': [],
    }
    if not path:
        result['hint'] = '未指定 Instant Client 目录'
        return result
    result['exists'] = os.path.isdir(path)
    if not result['exists']:
        result['hint'] = '目录不存在'
        return result
    names = []
    try:
        names = os.listdir(path)
    except OSError as exc:
        result['hint'] = f'无法读取目录：{exc}'
        return result
    markers = ('oci.dll', 'oraociei', 'libclntsh')
    matched = [name for name in names if any(mark.lower() in name.lower() for mark in markers)]
    result['files'] = matched[:8]
    result['has_oci'] = bool(matched)
    result['hint'] = '已找到 Instant Client 库' if result['has_oci'] else '目录存在，但未发现 oci.dll / libclntsh'
    return result


def ensure_oracle_client(mode: str = 'auto', lib_dir: str = '') -> dict:
    """按用户配置初始化。thin/auto 不调用 init；thick 只初始化一次。"""
    wanted = normalize_mode(mode)
    path = str(lib_dir or '').strip()
    if _STATE['initialized']:
        if wanted == 'thick' and _STATE['mode'] != 'thick':
            raise OracleRuntimeError(
                'Oracle 客户端已按 Thin 初始化。切换 Thick 或 Instant Client 路径后请重启应用再生效。'
            )
        if wanted in ('thin', 'auto') and _STATE['mode'] == 'thick' and wanted == 'thin':
            raise OracleRuntimeError('Oracle 已按 Thick 初始化。改回 Thin 请重启应用。')
        return client_state()
    if wanted in ('auto', 'thin'):
        _STATE['initialized'] = True
        _STATE['mode'] = 'thin'
        _STATE['lib_dir'] = path
        _STATE['error'] = ''
        return client_state()
    try:
        import oracledb
    except ImportError as exc:
        raise OracleRuntimeError('未安装 oracledb，请安装依赖后重试') from exc
    kwargs = {}
    if path:
        diag = diagnose_instant_client(path)
        if not diag['exists']:
            raise OracleRuntimeError(f'Instant Client 目录无效：{path}')
        kwargs['lib_dir'] = path
    try:
        oracledb.init_oracle_client(**kwargs)
    except Exception as exc:
        text = str(exc)
        if 'already been initialized' in text.lower() or 'DPI-1015' in text:
            _STATE['initialized'] = True
            _STATE['mode'] = 'thick'
            _STATE['lib_dir'] = path
            _STATE['error'] = ''
            return client_state()
        raise OracleRuntimeError(f'Thick 模式初始化失败：{text}') from exc
    _STATE['initialized'] = True
    _STATE['mode'] = 'thick'
    _STATE['lib_dir'] = path
    _STATE['error'] = ''
    return client_state()


def thick_required_message(original: str) -> str:
    return (
        '当前为 Thin 模式，Oracle 11.2 及更早版本会报 DPY-3010。'
        '请在连接中选择 Thick，指定 Instant Client 目录，然后重启应用。'
        '不会改系统 PATH，也不会自动下载 Client。'
        f' 原始错误：{original}'
    )
