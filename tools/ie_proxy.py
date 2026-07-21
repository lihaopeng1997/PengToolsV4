# -*- coding: utf-8 -*-
"""本机抓包配套：WinINet 代理备份/恢复、mitm CA 证书。

HTTP/HTTPS 数据包抓取引擎见 tools.http_capture（Fiddler 同款 MITM）。
本文件保留历史 API（IeProxyWorker / flow_to_record）供面板与测试兼容。

安全：抓包会改系统代理；必须保证 stop/退出/崩溃后/再次启动能恢复，
避免「代理指着已死的 127.0.0.1:端口」导致其它接口全挂。
"""

from __future__ import annotations

import atexit
import ctypes
import os
import socket
import subprocess
import threading
import time
from typing import Optional

from config import local_data_dir
from tools.interface_debug_store import (
    load_interface_debug_config,
    save_interface_debug_config,
)
from tools.http_capture import (
    HttpCaptureError,
    HttpCaptureWorker,
    flow_to_record,
    flow_to_url_record,
)

# 兼容旧符号
IeProxyError = HttpCaptureError
IeProxyWorker = HttpCaptureWorker

INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37

# 进程内：是否由本工具设置了系统代理（atexit 兜底）
_CAPTURE_PROXY_ACTIVE = False
_ATEXIT_REGISTERED = False
_PROXY_LOCK = threading.Lock()


def _wininet_refresh():
    try:
        internet = ctypes.windll.Wininet
        internet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        internet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
    except Exception:
        pass


def read_proxy_settings() -> dict:
    """读取当前用户 Internet Settings 代理配置（含 PAC/自动检测）。"""
    result = {
        'ProxyEnable': 0,
        'ProxyServer': '',
        'ProxyOverride': '',
        'AutoConfigURL': '',
        'AutoDetect': 0,
    }
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Internet Settings',
        )
        for name in ('ProxyEnable', 'ProxyServer', 'ProxyOverride', 'AutoConfigURL', 'AutoDetect'):
            try:
                val, _ = winreg.QueryValueEx(key, name)
                result[name] = val
            except OSError:
                pass
        winreg.CloseKey(key)
    except OSError:
        pass
    return result


def write_proxy_settings(settings: dict):
    """写入代理设置并刷新 WinINet。"""
    try:
        import winreg
    except ImportError as exc:
        raise IeProxyError(f'无法访问注册表：{exc}') from exc
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r'Software\Microsoft\Windows\CurrentVersion\Internet Settings',
        0,
        winreg.KEY_SET_VALUE,
    )
    try:
        enable = int(settings.get('ProxyEnable') or 0)
        winreg.SetValueEx(key, 'ProxyEnable', 0, winreg.REG_DWORD, enable)
        server = str(settings.get('ProxyServer') or '')
        override = str(settings.get('ProxyOverride') or '')
        winreg.SetValueEx(key, 'ProxyServer', 0, winreg.REG_SZ, server)
        winreg.SetValueEx(key, 'ProxyOverride', 0, winreg.REG_SZ, override)
        # PAC / 自动检测：监听期间必须关闭，否则 Chrome/Edge 会忽略 ProxyServer
        if 'AutoConfigURL' in settings:
            pac = str(settings.get('AutoConfigURL') or '')
            if pac:
                winreg.SetValueEx(key, 'AutoConfigURL', 0, winreg.REG_SZ, pac)
            else:
                try:
                    winreg.DeleteValue(key, 'AutoConfigURL')
                except OSError:
                    winreg.SetValueEx(key, 'AutoConfigURL', 0, winreg.REG_SZ, '')
        if 'AutoDetect' in settings:
            try:
                winreg.SetValueEx(key, 'AutoDetect', 0, winreg.REG_DWORD, int(settings.get('AutoDetect') or 0))
            except OSError:
                pass
    finally:
        winreg.CloseKey(key)
    _wininet_refresh()


def _normalize_proxy_server(text: str) -> str:
    return str(text or '').strip().lower().replace(' ', '')


def is_loopback_capture_proxy(settings: Optional[dict] = None, ports: Optional[list] = None) -> bool:
    """当前系统代理是否指向本机抓包（127.0.0.1 / localhost + 可选端口）。"""
    settings = settings if isinstance(settings, dict) else read_proxy_settings()
    if int(settings.get('ProxyEnable') or 0) != 1:
        return False
    server = _normalize_proxy_server(settings.get('ProxyServer'))
    if not server:
        return False
    if '127.0.0.1' not in server and 'localhost' not in server:
        return False
    if not ports:
        return True
    return any(f':{int(p)}' in server for p in ports if p)


def _port_listening(port: int, host: str = '127.0.0.1') -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def _register_atexit_once():
    global _ATEXIT_REGISTERED
    if _ATEXIT_REGISTERED:
        return
    atexit.register(_atexit_restore_proxy)
    _ATEXIT_REGISTERED = True


def _atexit_restore_proxy():
    """进程退出兜底（正常 exit / 多数崩溃路径）；强杀进程仍依赖下次启动清理。"""
    try:
        ensure_system_proxy_safe(reason='atexit')
    except Exception:
        pass


def backup_proxy_to_config() -> dict:
    snap = read_proxy_settings()
    # 禁止把「已是本机抓包代理」当作可恢复快照，否则嵌套污染
    if is_loopback_capture_proxy(snap):
        cfg = load_interface_debug_config()
        existing = cfg.get('proxy_restore_snapshot')
        if isinstance(existing, dict):
            return existing
        # 无干净快照时记一份「关闭代理」的安全默认
        snap = {
            'ProxyEnable': 0,
            'ProxyServer': '',
            'ProxyOverride': '',
            'AutoConfigURL': '',
            'AutoDetect': 0,
        }
    cfg = load_interface_debug_config()
    cfg['proxy_restore_snapshot'] = {
        'ProxyEnable': int(snap.get('ProxyEnable') or 0),
        'ProxyServer': str(snap.get('ProxyServer') or ''),
        'ProxyOverride': str(snap.get('ProxyOverride') or ''),
        'AutoConfigURL': str(snap.get('AutoConfigURL') or ''),
        'AutoDetect': int(snap.get('AutoDetect') or 0),
        'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    save_interface_debug_config(cfg)
    return cfg['proxy_restore_snapshot']


def restore_proxy_from_snapshot(snapshot: Optional[dict] = None) -> bool:
    """恢复代理；返回是否执行了恢复。"""
    global _CAPTURE_PROXY_ACTIVE
    with _PROXY_LOCK:
        snap = snapshot
        if snap is None:
            cfg = load_interface_debug_config()
            snap = cfg.get('proxy_restore_snapshot')
        if not isinstance(snap, dict):
            return False
        write_proxy_settings({
            'ProxyEnable': int(snap.get('ProxyEnable') or 0),
            'ProxyServer': str(snap.get('ProxyServer') or ''),
            'ProxyOverride': str(snap.get('ProxyOverride') or ''),
            'AutoConfigURL': str(snap.get('AutoConfigURL') or ''),
            'AutoDetect': int(snap.get('AutoDetect') or 0),
        })
        cfg = load_interface_debug_config()
        cfg['proxy_restore_snapshot'] = None
        save_interface_debug_config(cfg)
        _CAPTURE_PROXY_ACTIVE = False
        return True


def disable_orphan_loopback_proxy(ports: Optional[list] = None) -> bool:
    """无快照但系统仍指向本机抓包端口且端口已死：直接关闭代理。"""
    global _CAPTURE_PROXY_ACTIVE
    current = read_proxy_settings()
    if not is_loopback_capture_proxy(current, ports=ports):
        return False
    # 若抓包端口仍在监听，不要乱动（可能是本进程正在抓）
    cfg = load_interface_debug_config()
    port = int(cfg.get('ie_proxy_port') or 8899)
    check_ports = list(ports or [port])
    if any(_port_listening(p) for p in check_ports):
        return False
    write_proxy_settings({
        'ProxyEnable': 0,
        'ProxyServer': '',
        'ProxyOverride': '',
        'AutoConfigURL': '',
        'AutoDetect': 0,
    })
    _CAPTURE_PROXY_ACTIVE = False
    return True


def ensure_system_proxy_safe(reason: str = '') -> str:
    """启动/停止后的统一安全检查。

    返回：ok | restored_snapshot | disabled_orphan | cleared_stale_snapshot
    """
    global _CAPTURE_PROXY_ACTIVE
    _register_atexit_once()
    with _PROXY_LOCK:
        cfg = load_interface_debug_config()
        port = int(cfg.get('ie_proxy_port') or 8899)
        current = read_proxy_settings()
        snap = cfg.get('proxy_restore_snapshot')
        our_ports = [port]

        # 1) 有快照 + 当前仍是本机抓包代理 → 恢复用户原设置
        if isinstance(snap, dict) and is_loopback_capture_proxy(current, ports=our_ports):
            # 仅当「进程内标记抓包中且端口仍存活」才保留；端口已死则立即恢复
            if _CAPTURE_PROXY_ACTIVE and _port_listening(port):
                return 'ok'
            write_proxy_settings({
                'ProxyEnable': int(snap.get('ProxyEnable') or 0),
                'ProxyServer': str(snap.get('ProxyServer') or ''),
                'ProxyOverride': str(snap.get('ProxyOverride') or ''),
                'AutoConfigURL': str(snap.get('AutoConfigURL') or ''),
                'AutoDetect': int(snap.get('AutoDetect') or 0),
            })
            cfg = load_interface_debug_config()
            cfg['proxy_restore_snapshot'] = None
            save_interface_debug_config(cfg)
            _CAPTURE_PROXY_ACTIVE = False
            return 'restored_snapshot'

        # 2) 有快照但当前已不是抓包代理 → 清掉陈旧快照，避免误恢复
        if isinstance(snap, dict) and not is_loopback_capture_proxy(current):
            cfg['proxy_restore_snapshot'] = None
            save_interface_debug_config(cfg)
            _CAPTURE_PROXY_ACTIVE = False
            return 'cleared_stale_snapshot'

        # 3) 无快照但代理指向本机抓包端口且端口已死 → 强制关代理
        if is_loopback_capture_proxy(current, ports=our_ports) and not _port_listening(port):
            if not _CAPTURE_PROXY_ACTIVE:
                write_proxy_settings({
                    'ProxyEnable': 0,
                    'ProxyServer': '',
                    'ProxyOverride': '',
                    'AutoConfigURL': '',
                    'AutoDetect': 0,
                })
                return 'disabled_orphan'

        return 'ok'


def apply_local_proxy(port: int = 8899) -> dict:
    """备份并设置 127.0.0.1 代理（Fiddler 式：关 PAC/自动检测，强制显式代理）。"""
    global _CAPTURE_PROXY_ACTIVE
    _register_atexit_once()
    port = max(1, min(65535, int(port or 8899)))
    with _PROXY_LOCK:
        # 若当前已是本机抓包端口（上次异常未恢复），先恢复再备份，避免快照嵌套污染
        current = read_proxy_settings()
        if is_loopback_capture_proxy(current):
            cfg = load_interface_debug_config()
            if isinstance(cfg.get('proxy_restore_snapshot'), dict):
                try:
                    # 直接写回，避免死锁（已在锁内）
                    snap0 = cfg.get('proxy_restore_snapshot')
                    write_proxy_settings({
                        'ProxyEnable': int(snap0.get('ProxyEnable') or 0),
                        'ProxyServer': str(snap0.get('ProxyServer') or ''),
                        'ProxyOverride': str(snap0.get('ProxyOverride') or ''),
                        'AutoConfigURL': str(snap0.get('AutoConfigURL') or ''),
                        'AutoDetect': int(snap0.get('AutoDetect') or 0),
                    })
                    cfg['proxy_restore_snapshot'] = None
                    save_interface_debug_config(cfg)
                except Exception:
                    pass
            else:
                try:
                    write_proxy_settings({
                        'ProxyEnable': 0,
                        'ProxyServer': '',
                        'ProxyOverride': '',
                        'AutoConfigURL': '',
                        'AutoDetect': 0,
                    })
                except Exception:
                    pass

        snap = backup_proxy_to_config()
        # Fiddler 经典：单地址对全部协议；Chrome/Edge/IE 识别最稳
        write_proxy_settings({
            'ProxyEnable': 1,
            'ProxyServer': f'127.0.0.1:{port}',
            # <-loopback>：本机环回也走代理（Win10+）；不要写 <local> 排除内网
            'ProxyOverride': '<-loopback>',
            'AutoConfigURL': '',
            'AutoDetect': 0,
        })
        _CAPTURE_PROXY_ACTIVE = True
        return snap


def mark_capture_proxy_inactive():
    """抓包引擎已释放系统代理后调用。"""
    global _CAPTURE_PROXY_ACTIVE
    _CAPTURE_PROXY_ACTIVE = False


def mitm_cert_dir() -> str:
    path = os.path.join(local_data_dir(), 'mitmproxy')
    os.makedirs(path, exist_ok=True)
    return path


def mitm_ca_cert_path() -> str:
    return os.path.join(mitm_cert_dir(), 'mitmproxy-ca-cert.cer')


def ensure_mitm_ca_exists() -> str:
    """确保 mitmproxy CA 存在；返回 .cer 路径。"""
    cer = mitm_ca_cert_path()
    if os.path.isfile(cer):
        return cer
    confdir = mitm_cert_dir()
    try:
        from mitmproxy.certs import CertStore
        store = CertStore.from_store(confdir, 'mitmproxy', 2048)
        pem = os.path.join(confdir, 'mitmproxy-ca-cert.pem')
        if os.path.isfile(pem) and not os.path.isfile(cer):
            with open(pem, 'rb') as src, open(cer, 'wb') as dst:
                dst.write(src.read())
        elif not os.path.isfile(cer):
            ca_path = getattr(store, 'default_ca_path', None) or pem
            if ca_path and os.path.isfile(ca_path):
                with open(ca_path, 'rb') as src, open(cer, 'wb') as dst:
                    dst.write(src.read())
    except Exception:
        try:
            subprocess.run(
                [
                    os.environ.get('PYTHON', 'python'),
                    '-c',
                    (
                        'from mitmproxy.certs import CertStore; '
                        f'CertStore.from_store(r"{confdir}", "mitmproxy", 2048)'
                    ),
                ],
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            pem = os.path.join(confdir, 'mitmproxy-ca-cert.pem')
            if os.path.isfile(pem):
                with open(pem, 'rb') as src, open(cer, 'wb') as dst:
                    dst.write(src.read())
        except Exception as exc:
            raise IeProxyError(f'生成抓包证书失败：{exc}') from exc
    if not os.path.isfile(cer):
        pem = os.path.join(confdir, 'mitmproxy-ca-cert.pem')
        if os.path.isfile(pem):
            with open(pem, 'rb') as src, open(cer, 'wb') as dst:
                dst.write(src.read())
    if not os.path.isfile(cer):
        raise IeProxyError('未找到 mitmproxy CA 证书文件')
    return cer


def cert_sha1_thumbprint(cer_path: str) -> str:
    """计算证书 SHA-1 指纹（无冒号大写十六进制）。"""
    import hashlib
    data = open(cer_path, 'rb').read()
    if b'-----BEGIN' in data:
        import base64
        lines = [
            ln for ln in data.decode('ascii', errors='ignore').splitlines()
            if ln and not ln.startswith('-----')
        ]
        data = base64.b64decode(''.join(lines))
    return hashlib.sha1(data).hexdigest().upper()


def install_user_root_cert(cer_path: Optional[str] = None) -> str:
    """安装到当前用户 Root 存储，返回 thumbprint。"""
    path = cer_path or ensure_mitm_ca_exists()
    thumb = cert_sha1_thumbprint(path)
    proc = subprocess.run(
        ['certutil', '-user', '-addstore', 'Root', path],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or '').strip()
        raise IeProxyError(f'安装证书失败：{err or proc.returncode}')
    cfg = load_interface_debug_config()
    cfg['ie_certificate_thumbprint'] = thumb
    save_interface_debug_config(cfg)
    return thumb


def remove_recorded_cert(thumbprint: Optional[str] = None) -> bool:
    """仅删除配置中记录的指纹对应证书。"""
    cfg = load_interface_debug_config()
    thumb = (thumbprint or cfg.get('ie_certificate_thumbprint') or '').strip().upper()
    if not thumb:
        return False
    proc = subprocess.run(
        ['certutil', '-user', '-delstore', 'Root', thumb],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    cfg['ie_certificate_thumbprint'] = ''
    save_interface_debug_config(cfg)
    return proc.returncode == 0
