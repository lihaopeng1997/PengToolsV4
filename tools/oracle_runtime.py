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


def resolve_oci_lib_dir(home: str = '', oci_lib: str = '', lib_dir: str = '') -> str:
    """OCI 库文件优先；否则用 Oracle 主目录。"""
    oci = os.path.normpath(str(oci_lib or '').strip()) if str(oci_lib or '').strip() else ''
    if oci:
        if os.path.isfile(oci) or os.path.splitext(oci)[1]:
            return os.path.dirname(oci) or oci
        return oci
    home_path = os.path.normpath(str(home or '').strip()) if str(home or '').strip() else ''
    if home_path:
        return home_path
    return os.path.normpath(str(lib_dir or '').strip()) if str(lib_dir or '').strip() else ''


def diagnose_instant_client(lib_dir: str, *, home: str = '', oci_lib: str = '') -> dict:
    oci = str(oci_lib or '').strip()
    home_path = str(home or '').strip()
    folder = resolve_oci_lib_dir(home_path, oci, lib_dir)
    result = {
        'path': folder,
        'home': home_path,
        'oci_lib': oci,
        'exists': False,
        'has_oci': False,
        'hint': '',
        'files': [],
    }
    if oci:
        oci_norm = os.path.normpath(oci)
        result['oci_exists'] = os.path.isfile(oci_norm)
        base = os.path.basename(oci_norm).lower()
        result['has_oci'] = bool(result['oci_exists'] and 'oci' in base)
        if not result['oci_exists']:
            result['hint'] = f'OCI 库文件不存在：{oci_norm}'
            return result
        result['exists'] = os.path.isdir(os.path.dirname(oci_norm) or oci_norm)
        result['files'] = [os.path.basename(oci_norm)]
        result['hint'] = f'已指定 OCI 库：{oci_norm}'
        if home_path and not os.path.isdir(home_path):
            result['hint'] += '；Oracle 主目录无效'
        return result
    if not folder:
        result['hint'] = '未指定 Oracle 主目录或 oci.dll'
        return result
    result['exists'] = os.path.isdir(folder)
    if not result['exists']:
        result['hint'] = 'Oracle 主目录不存在'
        return result
    try:
        names = os.listdir(folder)
    except OSError as exc:
        result['hint'] = f'无法读取目录：{exc}'
        return result
    markers = ('oci.dll', 'oraociei', 'libclntsh')
    matched = [name for name in names if any(mark.lower() in name.lower() for mark in markers)]
    result['files'] = matched[:8]
    result['has_oci'] = bool(matched)
    result['hint'] = (
        '主目录下已找到 ' + ', '.join(matched[:3])
        if result['has_oci'] else
        '主目录存在，但未发现 oci.dll，请单独指定 OCI 库文件'
    )
    return result


def ensure_oracle_client(mode: str = 'auto', lib_dir: str = '', home: str = '', oci_lib: str = '') -> dict:
    """按用户配置初始化。thin/auto 不调用 init；thick 只初始化一次。"""
    wanted = normalize_mode(mode)
    path = resolve_oci_lib_dir(home, oci_lib, lib_dir)
    home_path = str(home or '').strip()
    if _STATE['initialized']:
        if wanted == 'thick' and _STATE['mode'] != 'thick':
            raise OracleRuntimeError(
                'Oracle 客户端已按 Thin 初始化。切换 Thick 或 OCI 路径后请重启应用再生效。'
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
    if home_path:
        os.environ['ORACLE_HOME'] = home_path
    kwargs = {}
    if path:
        diag = diagnose_instant_client(path, home=home_path, oci_lib=oci_lib)
        if oci_lib and not diag.get('has_oci'):
            raise OracleRuntimeError(diag.get('hint') or f'OCI 库无效：{oci_lib}')
        if not diag.get('exists') and not os.path.isdir(path):
            raise OracleRuntimeError(f'Oracle 主目录或 OCI 目录无效：{path}')
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


def load_oracle_client_config(settings=None) -> tuple[str, str]:
    """全局 Oracle 客户端。返回 (mode, lib_dir)，lib_dir 由主目录或 oci.dll 解析。"""
    data = settings
    if not isinstance(data, dict):
        try:
            from config import load_settings
            data = load_settings()
        except Exception:
            data = {}
    data = data or {}
    mode = normalize_mode(str(data.get('oracle_client_mode') or 'auto'))
    home = str(data.get('oracle_home') or data.get('oracle_client_lib_dir') or '').strip()
    oci_lib = str(data.get('oracle_oci_lib') or '').strip()
    lib_dir = resolve_oci_lib_dir(home, oci_lib, str(data.get('oracle_client_lib_dir') or ''))
    return mode, lib_dir


def load_oracle_paths(settings=None) -> dict:
    data = settings
    if not isinstance(data, dict):
        try:
            from config import load_settings
            data = load_settings()
        except Exception:
            data = {}
    data = data or {}
    home = str(data.get('oracle_home') or data.get('oracle_client_lib_dir') or '').strip()
    oci_lib = str(data.get('oracle_oci_lib') or '').strip()
    mode, lib_dir = load_oracle_client_config(data)
    return {'mode': mode, 'home': home, 'oci_lib': oci_lib, 'lib_dir': lib_dir}


def thick_required_message(original: str) -> str:
    return (
        '当前为 Thin 模式，Oracle 11.2 及更早版本会报 DPY-3010。'
        '请到「设置 → Oracle 兼容」选择 Thick，填写 Oracle 主目录并指定 oci.dll，然后重启应用。'
        '该配置对所有 Oracle 连接共用。不会改系统 PATH，也不会自动下载 Client。'
        f' 原始错误：{original}'
    )
