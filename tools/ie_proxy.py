# -*- coding: utf-8 -*-
"""IE 本机 MITM 代理：仅 127.0.0.1，备份/恢复 WinINet 代理，证书指纹管理。

抓包数据只在内存；禁止修改/重放请求。
"""

from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import threading
import time
import uuid
from typing import Callable, Optional
from urllib.parse import urlparse

from config import local_data_dir
from tools.browser_debug import STATIC_EXTENSIONS, empty_record, is_static_url
from tools.interface_debug_store import (
    load_interface_debug_config,
    save_interface_debug_config,
)

INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37


class IeProxyError(ValueError):
    pass


def _wininet_refresh():
    try:
        internet = ctypes.windll.Wininet
        internet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        internet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
    except Exception:
        pass


def read_proxy_settings() -> dict:
    """读取当前用户 Internet Settings 代理配置（含 PAC/自动检测，Fiddler 同理需处理）。"""
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
        'ProxyServer': f'127.0.0.1:{port}',
        # 勿把全部 <local> 排除业务网；仅绕过本机，避免误伤内网抓包
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
    # 触发 mitmproxy 生成证书
    confdir = mitm_cert_dir()
    try:
        from mitmproxy.certs import CertStore
        store = CertStore.from_store(confdir, 'mitmproxy', 2048)
        # pem 路径
        pem = os.path.join(confdir, 'mitmproxy-ca-cert.pem')
        if os.path.isfile(pem) and not os.path.isfile(cer):
            # 复制为 .cer 供 certutil
            with open(pem, 'rb') as src, open(cer, 'wb') as dst:
                dst.write(src.read())
        elif not os.path.isfile(cer):
            # 部分版本直接写 .pem
            ca_path = getattr(store, 'default_ca_path', None) or pem
            if ca_path and os.path.isfile(ca_path):
                with open(ca_path, 'rb') as src, open(cer, 'wb') as dst:
                    dst.write(src.read())
    except Exception:
        # 退回：用 dump 一次空 master 生成
        try:
            subprocess.run(
                [
                    os.environ.get('PYTHON', 'python'),
                    '-c',
                    (
                        'from mitmproxy.certs import CertStore; import os; '
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
    # 若是 PEM，提取 DER
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


def flow_to_record(flow) -> dict:
    """将 mitmproxy HTTPFlow 转为与 CDP 相同的内存记录。"""
    rec = empty_record(uuid.uuid4().hex)
    rec['source'] = 'ie_proxy'
    try:
        req = flow.request
        rec['method'] = (req.method or 'GET').upper()
        rec['url'] = req.pretty_url or req.url or ''
        parsed = urlparse(rec['url'])
        rec['path'] = parsed.path or '/'
        rec['query'] = parsed.query or ''
        headers = {}
        try:
            for k, v in req.headers.items(multi=True):
                headers[str(k)] = str(v)
        except Exception:
            try:
                headers = {str(k): str(v) for k, v in dict(req.headers).items()}
            except Exception:
                headers = {}
        rec['request_headers'] = headers
        try:
            rec['request_body'] = req.get_text(strict=False) or ''
        except Exception:
            try:
                raw = req.content or b''
                rec['request_body'] = raw.decode('utf-8', errors='replace') if raw else ''
            except Exception:
                rec['request_body'] = ''
        # 耗时起点
        try:
            ts = getattr(flow, 'timestamp_start', None) or getattr(req, 'timestamp_start', None)
            if ts:
                rec['started_at'] = float(ts)
        except Exception:
            pass
        if getattr(flow, 'response', None) is not None:
            resp = flow.response
            rec['status'] = resp.status_code
            rec['mime_type'] = resp.headers.get('content-type', '') or ''
            rh = {}
            try:
                for k, v in resp.headers.items(multi=True):
                    rh[str(k)] = str(v)
            except Exception:
                try:
                    rh = {str(k): str(v) for k, v in dict(resp.headers).items()}
                except Exception:
                    rh = {}
            rec['response_headers'] = rh
            try:
                rec['response_body'] = resp.get_text(strict=False) or ''
            except Exception:
                try:
                    raw = resp.content or b''
                    rec['response_body'] = raw.decode('utf-8', errors='replace') if raw else ''
                except Exception:
                    rec['response_body'] = ''
            try:
                start = getattr(flow, 'timestamp_start', None) or rec.get('started_at') or 0
                end = getattr(flow, 'timestamp_end', None)
                if end is None and hasattr(resp, 'timestamp_end'):
                    end = resp.timestamp_end
                if end and start:
                    rec['duration_ms'] = int(max(0, (float(end) - float(start)) * 1000))
            except Exception:
                pass
        else:
            err = getattr(flow, 'error', None)
            if err:
                rec['failure'] = str(getattr(err, 'msg', err))
        # 资源类型粗判：Document / XHR / 静态
        path = (rec.get('path') or '').lower()
        accept = ''
        for k, v in headers.items():
            if str(k).lower() == 'accept':
                accept = str(v).lower()
                break
        if any(path.endswith(ext) for ext in STATIC_EXTENSIONS):
            rec['resource_type'] = 'Other'
        elif 'text/html' in accept or path.endswith(('.html', '.htm')) or path in ('', '/'):
            rec['resource_type'] = 'Document'
        elif not path or path.endswith('/'):
            rec['resource_type'] = 'Document'
        else:
            # 默认按业务 XHR 保留，避免被默认过滤误杀
            rec['resource_type'] = 'XHR'
    except Exception as exc:
        rec['failure'] = str(exc)
    return rec


class _CaptureAddon:
    """request + response 两阶段均产出记录；不修改流量。

    捕获层默认全部入库，静态资源由 UI 筛选隐藏（对齐 Fiddler「先抓再滤」）。
    """

    def __init__(self, out_queue: queue.Queue, show_static: bool = True, id_map: Optional[dict] = None):
        self.out_queue = out_queue
        self.show_static = show_static
        self.id_map = id_map if id_map is not None else {}

    def _should_emit(self, url: str) -> bool:
        # 捕获层始终放行；UI 再按 show_static 过滤
        return True

    def requestheaders(self, flow):
        """尽早登记，防止仅有 CONNECT/失败时无记录。"""
        self._emit(flow)

    def request(self, flow):
        self._emit(flow)

    def response(self, flow):
        self._emit(flow)

    def error(self, flow):
        try:
            rec = flow_to_record(flow)
            prev = self.id_map.get(id(flow))
            if prev:
                rec['id'] = prev
            if not rec.get('failure'):
                rec['failure'] = 'HTTPS 未解密：请安装本机抓包证书' if (
                    (rec.get('url') or '').startswith('https')
                ) else 'proxy error'
            self.id_map[id(flow)] = rec['id']
            self.out_queue.put(rec)
        except Exception:
            pass

    def _emit(self, flow):
        try:
            rec = flow_to_record(flow)
            prev = self.id_map.get(id(flow))
            if prev:
                rec['id'] = prev
            self.id_map[id(flow)] = rec['id']
            self.out_queue.put(rec)
        except Exception:
            pass


class IeProxyWorker:
    """后台线程运行 mitmproxy DumpMaster，仅监听 127.0.0.1。

    通用本机代理与 IE 兼容模式共用此 worker。
    """

    def __init__(
        self,
        port: int = 8899,
        on_record: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_stopped: Optional[Callable[[], None]] = None,
        on_ready: Optional[Callable[[], None]] = None,
        show_static: bool = False,
        source_label: str = 'ie_proxy',
        apply_system_proxy: bool = True,
    ):
        self.port = max(1, min(65535, int(port or 8899)))
        self.on_record = on_record
        self.on_error = on_error
        self.on_stopped = on_stopped
        self.on_ready = on_ready
        self.show_static = show_static
        self.source_label = source_label or 'ie_proxy'
        self.apply_system_proxy = bool(apply_system_proxy)
        self._thread = None
        self._poll_thread = None
        self._master = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self.records: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._proxy_applied = False
        self.ready = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self.ready = False
        # 先启动监听，就绪后再写系统代理，避免“代理已改但端口未开”
        self._thread = threading.Thread(target=self._run_master, name='ie-mitm', daemon=True)
        self._thread.start()
        self._poll_thread = threading.Thread(target=self._poll_queue, name='ie-poll', daemon=True)
        self._poll_thread.start()

    def wait_ready(self, timeout: float = 12.0) -> bool:
        ok = self._ready.wait(timeout)
        return bool(ok and self.ready and not self._stop.is_set())

    def stop(self):
        self._stop.set()
        self.ready = False
        try:
            if self._master is not None:
                self._master.shutdown()
        except Exception:
            pass
        if self._proxy_applied:
            try:
                restore_proxy_from_snapshot()
            except Exception:
                pass
            self._proxy_applied = False
        self.clear_session()
        if self.on_stopped:
            try:
                self.on_stopped()
            except Exception:
                pass

    def clear_session(self):
        with self._lock:
            self.records.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _poll_queue(self):
        while not self._stop.is_set():
            try:
                rec = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            rec = dict(rec)
            rec['source'] = self.source_label
            with self._lock:
                self.records[rec['id']] = rec
            if self.on_record:
                try:
                    self.on_record(rec)
                except Exception:
                    pass

    def _mark_ready(self):
        self.ready = True
        self._ready.set()
        if self.on_ready:
            try:
                self.on_ready()
            except Exception:
                pass

    def _run_master(self):
        """运行 mitmproxy。DumpMaster.run 为 async，必须用事件循环驱动。"""
        try:
            from mitmproxy import options
            from mitmproxy.tools.dump import DumpMaster
        except ImportError as exc:
            if self.on_error:
                self.on_error(f'缺少 mitmproxy 依赖：{exc}')
            self._cleanup_proxy()
            self._ready.set()
            return
        confdir = mitm_cert_dir()
        # 确保证书目录存在（HTTPS 解密）
        try:
            ensure_mitm_ca_exists()
        except Exception:
            pass
        try:
            import asyncio
            import socket

            listen_host = '127.0.0.1'
            opts = options.Options(
                listen_host=listen_host,
                listen_port=self.port,
                confdir=confdir,
            )
            # 尽量兼容不同 mitmproxy 版本的可选参数
            for key, val in (
                ('ssl_insecure', True),  # 上游自签证书不阻断抓包
                ('http2', True),
                ('websocket', True),
            ):
                try:
                    opts.update(**{key: val})
                except Exception:
                    pass

            id_map: dict = {}
            # 捕获层全量入库，静态由 UI 滤
            addon = _CaptureAddon(self._queue, show_static=True, id_map=id_map)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._master = DumpMaster(opts, loop=loop, with_termlog=False, with_dumper=False)
            self._master.addons.add(addon)

            async def _async_run():
                await self._master.run()

            def _run():
                try:
                    loop.run_until_complete(_async_run())
                except Exception as exc:
                    if self.on_error and not self._stop.is_set():
                        self.on_error(f'mitmproxy 运行失败：{exc}')
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass

            runner = threading.Thread(target=_run, name='ie-mitm-run', daemon=True)
            runner.start()

            deadline = time.time() + 12.0
            bound = False
            while time.time() < deadline and not self._stop.is_set():
                try:
                    with socket.create_connection(('127.0.0.1', self.port), timeout=0.4):
                        bound = True
                        break
                except OSError:
                    time.sleep(0.15)

            if not bound:
                if self.on_error:
                    self.on_error(
                        f'本机代理未能绑定 127.0.0.1:{self.port}（端口占用或 mitmproxy 启动失败）'
                    )
                try:
                    if self._master is not None:
                        self._master.shutdown()
                except Exception:
                    pass
                self._cleanup_proxy()
                self._ready.set()
                return

            if self.apply_system_proxy:
                try:
                    apply_local_proxy(self.port)
                    self._proxy_applied = True
                except Exception as exc:
                    if self.on_error:
                        self.on_error(f'设置系统代理失败：{exc}')
                    try:
                        if self._master is not None:
                            self._master.shutdown()
                    except Exception:
                        pass
                    self._ready.set()
                    return

            self._mark_ready()
            while runner.is_alive() and not self._stop.is_set():
                runner.join(timeout=0.4)
            if self._stop.is_set():
                try:
                    if self._master is not None:
                        self._master.shutdown()
                except Exception:
                    pass
                runner.join(timeout=3.0)
        except Exception as exc:
            if self.on_error and not self._stop.is_set():
                self.on_error(str(exc))
            self._ready.set()
        finally:
            self.ready = False
            self._cleanup_proxy()

    def _cleanup_proxy(self):
        if self._proxy_applied:
            try:
                restore_proxy_from_snapshot()
            except Exception:
                pass
            self._proxy_applied = False

