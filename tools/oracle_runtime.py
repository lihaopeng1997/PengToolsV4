# -*- coding: utf-8 -*-
"""Oracle 客户端模式：auto / thin / thick。进程内只允许一次 init_oracle_client。"""

from __future__ import annotations

import os
import re
import struct
import sys

ORACLE_MODES = ('auto', 'thin', 'thick')
# python-oracledb 2.x Thick 要求 Oracle Client 19 及以上
MIN_THICK_CLIENT_MAJOR = 19
_PE_I386 = 0x14C
_PE_AMD64 = 0x8664

_STATE = {
    'initialized': False,
    'mode': None,
    'lib_dir': None,
    'error': '',
}


class OracleRuntimeError(Exception):
    pass


def python_is_64bit() -> bool:
    return sys.maxsize > 2**32


def has_non_ascii(path: str) -> bool:
    return any(ord(ch) > 127 for ch in str(path or ''))


def windows_short_path(path: str) -> str:
    """把含中文的路径转成 8.3 短路径，避免 OCI LoadLibrary 加载失败后落到 PATH 里的旧 oci.dll。"""
    text = os.path.abspath(os.path.normpath(str(path or '').strip())) if str(path or '').strip() else ''
    if os.name != 'nt' or not text:
        return text
    try:
        import ctypes
        GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        GetShortPathNameW.restype = ctypes.c_uint
        buf = ctypes.create_unicode_buffer(520)
        if GetShortPathNameW(text, buf, 520):
            return buf.value or text
    except Exception:
        return text
    return text


def find_oci_on_path() -> list[str]:
    hits = []
    for folder in os.environ.get('PATH', '').split(os.pathsep):
        candidate = os.path.join(folder, 'oci.dll')
        if os.path.isfile(candidate):
            hits.append(os.path.abspath(candidate))
    return hits


def _is_reparse_point(path: str) -> bool:
    if os.name != 'nt' or not path:
        return os.path.islink(path)
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
        if attrs == 0xFFFFFFFF:
            return False
        return bool(attrs & 0x400)
    except Exception:
        return os.path.islink(path)


def ascii_client_link_path() -> str:
    try:
        from config import local_data_dir
        base = local_data_dir()
    except Exception:
        base = os.path.join(os.path.expandvars('%LOCALAPPDATA%'), 'PengToolsHub', 'data')
    return os.path.join(base, 'oracle_thick_lib')


def _make_ascii_junction(target: str) -> str:
    """把含中文的 Instant Client 目录联到 data/oracle_thick_lib，供 OCI 用纯英文路径加载。"""
    if os.name != 'nt':
        return ''
    target_abs = os.path.abspath(target)
    if not os.path.isdir(target_abs):
        return ''
    link = ascii_client_link_path()
    parent = os.path.dirname(link)
    os.makedirs(parent, exist_ok=True)
    if os.path.lexists(link):
        try:
            if os.path.samefile(link, target_abs):
                return link
        except OSError:
            pass
        if _is_reparse_point(link):
            os.rmdir(link)
        else:
            return ''
    import subprocess
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    completed = subprocess.run(
        ['cmd', '/c', 'mklink', '/J', link, target_abs],
        capture_output=True,
        text=True,
        encoding='mbcs',
        errors='replace',
        creationflags=flags,
    )
    if completed.returncode == 0 and os.path.isdir(link):
        return link
    return ''


def ensure_ascii_lib_dir(lib_dir: str) -> str:
    """OCI LoadLibrary 走 ANSI 路径；含中文时先试 8.3，再试目录联接。"""
    raw = str(lib_dir or '').strip()
    folder = os.path.abspath(os.path.normpath(raw)) if raw else ''
    if not folder:
        return folder
    if os.path.isdir(folder) and not has_non_ascii(folder):
        return folder
    short = windows_short_path(folder)
    if short and os.path.isdir(short) and not has_non_ascii(short):
        return short
    if not has_non_ascii(folder):
        return folder
    link = _make_ascii_junction(folder)
    if link and os.path.isdir(link) and not has_non_ascii(link):
        return link
    raise OracleRuntimeError(
        'Instant Client 路径含中文，OCI 无法加载你选的 19.24，会误报 DPI-1072。\n'
        f'当前路径：{folder}\n'
        '请把整个 instantclient_19_24 文件夹拷到纯英文路径，例如 C:\\oracle\\instantclient_19_24，'
        'OCI 库改选该目录下的 oci.dll，然后重启 PengTools。'
        '不要只拷 oci.dll，同目录必须有 oraociei19.dll。'
    )


def detect_pe_machine(path: str) -> str:
    """读 Windows PE 机器类型：x64 / x86 / unknown。"""
    try:
        with open(path, 'rb') as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b'MZ':
                return 'unknown'
            pe_offset = struct.unpack_from('<I', header, 60)[0]
            stream.seek(pe_offset)
            sig_machine = stream.read(6)
            if len(sig_machine) < 6 or sig_machine[:4] != b'PE\x00\x00':
                return 'unknown'
            machine = struct.unpack_from('<H', sig_machine, 4)[0]
    except OSError:
        return 'unknown'
    if machine == _PE_AMD64:
        return 'x64'
    if machine == _PE_I386:
        return 'x86'
    return 'unknown'


def detect_client_major(lib_dir: str) -> int | None:
    """从 Instant Client 目录的 oraocieiNN.dll 推断主版本。"""
    folder = str(lib_dir or '').strip()
    if not folder or not os.path.isdir(folder):
        return None
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    found = []
    for name in names:
        match = re.match(r'oraociei(\d+)', name.lower())
        if match:
            found.append(int(match.group(1)))
    return max(found) if found else None


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
        result.update(_compat_fields(os.path.dirname(oci_norm) or oci_norm, oci_norm))
        result['hint'] = _compat_hint(result, prefix=f'已指定 OCI 库：{oci_norm}')
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
    result.update(_compat_fields(folder, oci))
    if result['has_oci']:
        result['hint'] = _compat_hint(result, prefix='主目录下已找到 ' + ', '.join(matched[:3]))
    else:
        result['hint'] = '主目录存在，但未发现 oci.dll，请单独指定 OCI 库文件'
    return result


def _compat_hint(diag: dict, prefix: str = '') -> str:
    parts = [prefix] if prefix else []
    major = diag.get('client_major')
    arch = diag.get('oci_arch') or 'unknown'
    if major is not None:
        parts.append(f'客户端 {major}c')
    if arch != 'unknown':
        parts.append(arch)
    if not diag.get('arch_ok'):
        parts.append(
            f'位数不匹配：oci.dll 是 {arch}，PengTools 是 {diag.get("python_arch")}。'
            'PL/SQL Developer 常用 32 位客户端，这里必须用 64 位 Instant Client 19 的 oci.dll。'
        )
    elif major is not None and major < MIN_THICK_CLIENT_MAJOR:
        parts.append(
            f'python-oracledb Thick 需要 Instant Client {MIN_THICK_CLIENT_MAJOR} 及以上。'
            f'PL/SQL Developer 能用 {major}c，这里不行。请下载 64 位 Instant Client 19，连 11.2 库也用 19 客户端。'
        )
    elif not diag.get('bundle_ok'):
        parts.append('同目录缺少 oraociei19.dll。只拷 oci.dll 会加载 PATH 里 PL/SQL 的旧库，从而报 DPI-1072。请使用完整 Basic 包。')
    if diag.get('non_ascii'):
        parts.append('路径含中文。请把 instantclient_19_24 整夹拷到纯英文路径，例如 C:\\oracle\\instantclient_19_24。')
    return '；'.join(part for part in parts if part)


def _compat_fields(lib_dir: str, oci_lib: str = '') -> dict:
    oci_path = str(oci_lib or '').strip()
    if not oci_path and lib_dir:
        candidate = os.path.join(lib_dir, 'oci.dll')
        if os.path.isfile(candidate):
            oci_path = candidate
    arch = detect_pe_machine(oci_path) if oci_path else 'unknown'
    major = detect_client_major(lib_dir)
    py64 = python_is_64bit()
    bundle_ok = major is not None
    return {
        'oci_arch': arch,
        'client_major': major,
        'python_arch': 'x64' if py64 else 'x86',
        'arch_ok': arch == 'unknown' or arch == ('x64' if py64 else 'x86'),
        'version_ok': major is None or major >= MIN_THICK_CLIENT_MAJOR,
        'bundle_ok': bundle_ok,
        'non_ascii': has_non_ascii(lib_dir) or has_non_ascii(oci_path),
    }


def tns_config_dir(home: str = '', lib_dir: str = '') -> str:
    for root in (home, lib_dir):
        if not root:
            continue
        candidate = os.path.join(root, 'network', 'admin')
        if os.path.isdir(candidate):
            return candidate
    return ''


def prepare_thick_environment(lib_dir: str, home: str = '') -> str:
    """进程内让 Instant Client 优先生效，避免和 PL/SQL Developer 的旧 ORACLE_HOME 混用。

    不改系统 PATH；只调整当前进程环境。TNS 可通过 config_dir 指向主目录的 network/admin。
    """
    folder = windows_short_path(str(lib_dir or '').strip()) if str(lib_dir or '').strip() else ''
    home_path = windows_short_path(str(home or '').strip()) if str(home or '').strip() else ''
    if folder and os.path.isdir(folder):
        current = os.environ.get('PATH', '')
        parts = current.split(os.pathsep) if current else []
        if not parts or os.path.normcase(parts[0]) != os.path.normcase(folder):
            os.environ['PATH'] = folder + os.pathsep + current if current else folder
        try:
            os.add_dll_directory(folder)
        except (AttributeError, OSError, FileNotFoundError):
            pass
        try:
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(folder)
        except Exception:
            pass
        home_ok = bool(
            home_path
            and (
                os.path.normcase(home_path) == os.path.normcase(folder)
                or os.path.normcase(folder).startswith(os.path.normcase(home_path) + os.sep)
            )
        )
        if home_ok:
            os.environ['ORACLE_HOME'] = home_path
        else:
            os.environ.pop('ORACLE_HOME', None)
    return tns_config_dir(home_path, folder)


def thick_client_error(exc, *, lib_dir: str = '', home: str = '', oci_lib: str = '') -> str:
    text = str(exc or '')
    diag = diagnose_instant_client(lib_dir, home=home, oci_lib=oci_lib)
    extra = diag.get('hint') or ''
    if 'DPI-1072' in text or 'unsupported' in text.lower():
        others = find_oci_on_path()
        conflict = ''
        if others:
            conflict = '\n系统 PATH 里还有这些 oci.dll：\n- ' + '\n- '.join(others[:6])
        return (
            'Thick 模式初始化失败：实际加载的 Oracle Client 版本不受支持（DPI-1072）。\n'
            '常见原因：路径含中文（例如「AI辅助编程」）导致没加载你选的 19.24，'
            '或 instantclient 目录不完整（缺 oraociei19.dll），于是用了 PATH 里 PL/SQL 的旧库。\n'
            '请把整个 instantclient_19_24 文件夹拷到纯英文路径，例如 C:\\oracle\\instantclient_19_24，'
            'OCI 库选该目录下的 oci.dll，然后重启 PengTools。\n'
            f'当前诊断：{extra or lib_dir or oci_lib or "未指定"}'
            f'{conflict}'
        )
    return f'Thick 模式初始化失败：{text}' + (f'\n{extra}' if extra else '')


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
    kwargs = {}
    if path:
        diag = diagnose_instant_client(path, home=home_path, oci_lib=oci_lib)
        if oci_lib and not diag.get('has_oci'):
            raise OracleRuntimeError(diag.get('hint') or f'OCI 库无效：{oci_lib}')
        if not diag.get('exists') and not os.path.isdir(path):
            raise OracleRuntimeError(f'Oracle 主目录或 OCI 目录无效：{path}')
        if diag.get('arch_ok') is False:
            raise OracleRuntimeError(diag.get('hint') or 'oci.dll 位数与 PengTools 不匹配')
        if diag.get('version_ok') is False:
            raise OracleRuntimeError(diag.get('hint') or 'Oracle Client 版本过低')
        if diag.get('bundle_ok') is False:
            raise OracleRuntimeError(diag.get('hint') or 'Instant Client 不完整，缺少 oraociei19.dll')
        path = ensure_ascii_lib_dir(path)
        if home_path:
            try:
                home_path = ensure_ascii_lib_dir(home_path)
            except OracleRuntimeError:
                home_path = windows_short_path(home_path)
        kwargs['lib_dir'] = path
        config_dir = prepare_thick_environment(path, home_path)
        if config_dir:
            kwargs['config_dir'] = windows_short_path(config_dir)
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
        raise OracleRuntimeError(thick_client_error(exc, lib_dir=path, home=home_path, oci_lib=oci_lib)) from exc
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
        '请到「设置 → Oracle 兼容」选择 Thick，OCI 库请指定 64 位 Instant Client 19 的 oci.dll，然后重启应用。'
        '不要直接套用 PL/SQL Developer 的 11g/32 位客户端。该配置对所有 Oracle 连接共用。'
        f' 原始错误：{original}'
    )
