# -*- coding: utf-8 -*-
"""本机抓包配套：WinINet 代理备份/恢复、mitm CA 证书。

HTTP/HTTPS 数据包抓取引擎见 tools.http_capture（Fiddler 同款 MITM）。
本文件保留历史 API（IeProxyWorker / flow_to_record）供面板与测试兼容。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
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


def backup_proxy_to_config() -> dict:
    snap = read_proxy_settings()
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
    return True


def apply_local_proxy(port: int = 8899) -> dict:
    """备份并设置 127.0.0.1 代理（Fiddler 式：关 PAC/自动检测，强制显式代理）。"""
    port = max(1, min(65535, int(port or 8899)))
    snap = backup_proxy_to_config()
    write_proxy_settings({
        'ProxyEnable': 1,
        # 同时写 http/https，兼容只认分协议代理的客户端
        'ProxyServer': f'http=127.0.0.1:{port};https=127.0.0.1:{port}',
        # <-loopback>：允许经代理访问本机环回（Windows 默认绕过 127.0.0.1）
        'ProxyOverride': '<-loopback>',
        'AutoConfigURL': '',
        'AutoDetect': 0,
    })
    return snap


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
