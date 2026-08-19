# -*- coding: utf-8 -*-
"""Chromium CDP 调试：发现浏览器、启动调试实例、监听 Network 事件。

仅允许 127.0.0.1 loopback；报文只存内存，不落盘。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import uuid
from typing import Callable, Optional
from urllib.parse import urlparse
from urllib.request import urlopen

from config import local_data_dir

STATIC_EXTENSIONS = (
    '.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
    '.woff', '.woff2', '.ttf', '.map', '.webp', '.mp4', '.mp3',
)

# 候选浏览器：名称、常见相对路径、是否 Chromium 内核
KNOWN_BROWSERS = [
    ('Google Chrome', [
        r'Google\Chrome\Application\chrome.exe',
        r'Google\Chrome Beta\Application\chrome.exe',
    ], True),
    ('Microsoft Edge', [
        r'Microsoft\Edge\Application\msedge.exe',
    ], True),
    ('360 安全浏览器', [
        r'360se6\Application\360se.exe',
        r'360Chrome\Chrome\Application\360chrome.exe',
        r'360ChromeX\Chrome\Application\360ChromeX.exe',
    ], True),
    ('QQ 浏览器', [
        r'Tencent\QQBrowser\QQBrowser.exe',
    ], True),
    ('搜狗浏览器', [
        r'SogouExplorer\SogouExplorer.exe',
    ], True),
    ('Brave', [
        r'BraveSoftware\Brave-Browser\Application\brave.exe',
    ], True),
    ('Opera', [
        r'Opera\launcher.exe',
        r'Opera software\Opera Stable\opera.exe',
    ], True),
    ('Mozilla Firefox', [
        r'Mozilla Firefox\firefox.exe',
    ], False),
]


class BrowserDebugError(ValueError):
    pass


def is_loopback_host(host: str) -> bool:
    h = (host or '').strip().lower()
    return h in ('127.0.0.1', 'localhost', '::1', '')


def profile_dir() -> str:
    path = os.path.join(local_data_dir(), 'browser_debug_profile')
    os.makedirs(path, exist_ok=True)
    return path


def _program_roots():
    roots = []
    for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        val = os.environ.get(key)
        if val and os.path.isdir(val):
            roots.append(val)
    return roots


def _registry_app_paths():
    """从 HKLM/HKCU App Paths 收集已知浏览器 exe。"""
    found = []
    try:
        import winreg
    except ImportError:
        return found
    names = (
        'chrome.exe', 'msedge.exe', '360se.exe', '360chrome.exe',
        'QQBrowser.exe', 'SogouExplorer.exe', 'brave.exe', 'opera.exe',
        'firefox.exe',
    )
    for root_name in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            base = winreg.OpenKey(root_name, r'Software\Microsoft\Windows\CurrentVersion\App Paths')
        except OSError:
            continue
        for name in names:
            try:
                key = winreg.OpenKey(base, name)
                path, _ = winreg.QueryValueEx(key, None)
                winreg.CloseKey(key)
                if path and os.path.isfile(path):
                    found.append(os.path.normpath(path))
            except OSError:
                continue
        try:
            winreg.CloseKey(base)
        except OSError:
            pass
    return found


def discover_browsers() -> list[dict]:
    """返回 [{name, path, is_chromium, is_firefox}, ...] 去重。"""
    candidates = []
    seen = set()

    def add(name, path, is_chromium):
        if not path:
            return
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen or not os.path.isfile(path):
            return
        seen.add(norm)
        lower = path.lower()
        is_ff = 'firefox' in lower
        candidates.append({
            'name': name,
            'path': path,
            'is_chromium': bool(is_chromium) and not is_ff,
            'is_firefox': is_ff,
        })

    for path in _registry_app_paths():
        base = os.path.basename(path).lower()
        name = base
        is_chromium = True
        if 'chrome' in base and '360' not in path.lower():
            name = 'Google Chrome'
        elif 'msedge' in base:
            name = 'Microsoft Edge'
        elif '360' in path.lower():
            name = '360 浏览器'
        elif 'qqbrowser' in base:
            name = 'QQ 浏览器'
        elif 'sogou' in base:
            name = '搜狗浏览器'
        elif 'brave' in base:
            name = 'Brave'
        elif 'opera' in base:
            name = 'Opera'
        elif 'firefox' in base:
            name = 'Mozilla Firefox'
            is_chromium = False
        add(name, path, is_chromium)

    roots = _program_roots()
    for display, rels, is_chromium in KNOWN_BROWSERS:
        for root in roots:
            for rel in rels:
                add(display, os.path.join(root, rel), is_chromium)

    candidates.sort(key=lambda c: (0 if c['is_chromium'] else 1, c['name'].lower()))
    return candidates


def build_launch_args(exe_path: str, debug_port: int = 9222) -> list[str]:
    port = max(1, min(65535, int(debug_port or 9222)))
    return [
        exe_path,
        f'--remote-debugging-address=127.0.0.1',
        f'--remote-debugging-port={port}',
        f'--user-data-dir={profile_dir()}',
        '--no-first-run',
        '--no-default-browser-check',
    ]


def launch_debug_browser(exe_path: str, debug_port: int = 9222) -> subprocess.Popen:
    if not exe_path:
        raise BrowserDebugError('浏览器可执行文件不存在')
    lower = exe_path.lower()
    if 'firefox' in lower:
        raise BrowserDebugError('Firefox 暂不支持实时监听；请使用 Chromium 内核浏览器。')
    if not os.path.isfile(exe_path):
        raise BrowserDebugError('浏览器可执行文件不存在')
    args = build_launch_args(exe_path, debug_port)
    try:
        return subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except OSError as exc:
        raise BrowserDebugError(f'启动浏览器失败：{exc}') from exc


def wait_debug_port(port: int = 9222, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(0.25)
    return False


def port_open(port: int, host: str = '127.0.0.1') -> bool:
    if not is_loopback_host(host):
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=0.6):
            return True
    except OSError:
        return False


def _loopback_opener():
    """绕过 HTTP_PROXY/HTTPS_PROXY，确保 127.0.0.1 CDP 探测不被内网代理劫持。"""
    try:
        import urllib.request
        # ProxyHandler({}) 表示不使用任何代理
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    except Exception:
        return None


def fetch_cdp_targets(port: int = 9222, host: str = '127.0.0.1') -> list[dict]:
    if not is_loopback_host(host):
        raise BrowserDebugError('CDP 仅允许连接 127.0.0.1 / localhost')
    url = f'http://{host}:{int(port)}/json/list'
    try:
        opener = _loopback_opener()
        if opener is not None:
            with opener.open(url, timeout=2.0) as resp:
                data = json.loads(resp.read().decode('utf-8', errors='replace'))
        else:
            with urlopen(url, timeout=2.0) as resp:
                data = json.loads(resp.read().decode('utf-8', errors='replace'))
    except Exception as exc:
        raise BrowserDebugError(
            f'无法连接调试端口 {host}:{port}。请用 --remote-debugging-port={port} 启动浏览器。\n{exc}'
        ) from exc
    if not isinstance(data, list):
        return []
    return data


def pick_default_page_target(targets: list[dict]) -> Optional[dict]:
    pages = [t for t in targets if (t.get('type') or '') == 'page']
    if not pages:
        return None
    for t in pages:
        if t.get('url') and not str(t.get('url')).startswith('chrome://'):
            return t
    return pages[0]


def is_static_url(url: str) -> bool:
    path = urlparse(url or '').path.lower()
    return any(path.endswith(ext) for ext in STATIC_EXTENSIONS)


def mask_sensitive_value(key: str, value: str, reveal: bool = False) -> str:
    if reveal:
        return value or ''
    k = (key or '').lower()
    if k in ('authorization', 'cookie', 'set-cookie', 'proxy-authorization'):
        if not value:
            return ''
        return '••••••••'
    # query token 类
    if any(tok in k for tok in ('token', 'secret', 'password', 'passwd', 'apikey', 'api_key')):
        return '••••••••' if value else ''
    return value or ''


def mask_url_query(url: str, reveal: bool = False) -> str:
    if reveal or not url:
        return url or ''
    parsed = urlparse(url)
    if not parsed.query:
        return url
    from urllib.parse import parse_qsl, quote, urlunparse
    sensitive = ('token', 'access_token', 'refresh_token', 'password', 'secret', 'key', 'auth')
    parts = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        if any(s in k.lower() for s in sensitive):
            parts.append(f'{quote(k, safe="")}={quote("********", safe="*")}')
        else:
            parts.append(f'{quote(str(k), safe="")}={quote(str(v), safe="")}')
    return urlunparse((
        parsed.scheme, parsed.netloc, parsed.path, parsed.params,
        '&'.join(parts), parsed.fragment,
    ))


def empty_record(request_id: str = '') -> dict:
    return {
        'id': request_id or uuid.uuid4().hex,
        'started_at': time.time(),
        'method': '',
        'url': '',
        'path': '',
        'query': '',
        'request_headers': {},
        'request_body': '',
        'status': None,
        'mime_type': '',
        'response_headers': {},
        'response_body': '',
        'duration_ms': None,
        'failure': '',
        'resource_type': '',
        'source': 'cdp',
    }


def merge_cdp_event(records: dict, event_method: str, params: dict) -> Optional[str]:
    """合并 CDP Network 事件到 records[requestId]，返回受影响 id。"""
    if not isinstance(params, dict):
        return None
    rid = params.get('requestId') or params.get('loaderId')
    if not rid:
        return None
    rec = records.get(rid)
    if event_method == 'Network.requestWillBeSent':
        req = params.get('request') or {}
        if rec is None:
            rec = empty_record(rid)
            records[rid] = rec
        rec['method'] = (req.get('method') or 'GET').upper()
        rec['url'] = req.get('url') or rec.get('url') or ''
        parsed = urlparse(rec['url'])
        rec['path'] = parsed.path or '/'
        rec['query'] = parsed.query or ''
        headers = req.get('headers') or {}
        if isinstance(headers, dict):
            rec['request_headers'] = {str(k): str(v) for k, v in headers.items()}
        post = req.get('postData')
        if post:
            rec['request_body'] = post if isinstance(post, str) else str(post)
        rec['resource_type'] = params.get('type') or rec.get('resource_type') or ''
        rec['started_at'] = time.time()
        return rid
    if event_method == 'Network.responseReceived':
        if rec is None:
            rec = empty_record(rid)
            records[rid] = rec
        resp = params.get('response') or {}
        rec['status'] = resp.get('status')
        rec['mime_type'] = resp.get('mimeType') or ''
        rec['url'] = resp.get('url') or rec.get('url') or ''
        headers = resp.get('headers') or {}
        if isinstance(headers, dict):
            rec['response_headers'] = {str(k): str(v) for k, v in headers.items()}
        rec['resource_type'] = params.get('type') or rec.get('resource_type') or ''
        return rid
    if event_method == 'Network.loadingFinished':
        if rec is None:
            return None
        if rec.get('started_at'):
            rec['duration_ms'] = int((time.time() - rec['started_at']) * 1000)
        return rid
    if event_method == 'Network.loadingFailed':
        if rec is None:
            rec = empty_record(rid)
            records[rid] = rec
        rec['failure'] = params.get('errorText') or 'loadingFailed'
        if rec.get('started_at'):
            rec['duration_ms'] = int((time.time() - rec['started_at']) * 1000)
        return rid
    return None


def should_keep_record(rec: dict, show_static: bool = False) -> bool:
    """默认保留 XHR/Fetch、Document、未知类型、WebSocket/EventSource；静态可隐藏。

    不得因默认过滤导致业务接口全部消失。
    """
    rtype = (rec.get('resource_type') or '').strip().lower()
    url = rec.get('url') or ''
    static_types = {
        'stylesheet', 'script', 'image', 'font', 'media', 'texttrack',
        'manifest', 'ping', 'cspviolationreport',
    }
    # 业务与未知类型一律保留
    keep_types = {
        '', 'xhr', 'fetch', 'document', 'xhr/fetch', 'other',
        'websocket', 'eventsource', 'signedexchange', 'preflight',
    }
    if rtype in keep_types:
        if not show_static and is_static_url(url) and rtype in ('', 'other'):
            # URL 明显是静态且类型未知时才隐藏
            return False
        return True
    if rtype in static_types:
        return bool(show_static)
    # 未识别类型：保留（避免误杀）
    if not show_static and is_static_url(url):
        return False
    return True


class CdpNetworkSession:
    """在后台线程连接 page WebSocket，转发 Network 事件到回调。"""

    def __init__(
        self,
        ws_url: str,
        on_event: Optional[Callable[[str, dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_closed: Optional[Callable[[], None]] = None,
        on_ready: Optional[Callable[[], None]] = None,
    ):
        self.ws_url = ws_url
        self.on_event = on_event
        self.on_error = on_error
        self.on_closed = on_closed
        self.on_ready = on_ready
        self._ws = None
        self._thread = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._msg_id = 0
        self._lock = threading.Lock()
        self.records: dict[str, dict] = {}
        self.ready = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self.ready = False
        self._thread = threading.Thread(target=self._run, name='cdp-network', daemon=True)
        self._thread.start()

    def wait_ready(self, timeout: float = 8.0) -> bool:
        """等待 Network.enable 成功并回调注册完成。"""
        ok = self._ready.wait(timeout)
        return bool(ok and self.ready and not self._stop.is_set())

    def stop(self):
        self._stop.set()
        self.ready = False
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        self.clear_session()

    def clear_session(self):
        with self._lock:
            self.records.clear()

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _send(self, method: str, params: Optional[dict] = None, msg_id: Optional[int] = None):
        if self._ws is None:
            return None
        mid = self._next_id() if msg_id is None else msg_id
        payload = {'id': mid, 'method': method}
        if params:
            payload['params'] = params
        self._ws.send(json.dumps(payload))
        return mid

    def get_response_body(self, request_id: str, timeout: float = 3.0) -> tuple[str, bool]:
        """同步请求响应体；返回 (body, base64Encoded)。失败返回 ('', False)。"""
        if self._ws is None or self._stop.is_set():
            return '', False
        result_holder = {}
        event = threading.Event()
        msg_id = self._next_id()
        rec = self.records.get(request_id)
        if rec and rec.get('response_body'):
            return rec['response_body'], False
        try:
            payload = json.dumps({
                'id': msg_id,
                'method': 'Network.getResponseBody',
                'params': {'requestId': request_id},
            })
            with self._lock:
                if not hasattr(self, '_pending'):
                    self._pending = {}
                self._pending[msg_id] = (result_holder, event)
            self._ws.send(payload)
            if event.wait(timeout):
                data = result_holder.get('data') or {}
                if data.get('error'):
                    return '', False
                body = (data.get('result') or {}).get('body') or ''
                b64 = bool((data.get('result') or {}).get('base64Encoded'))
                if b64 and body:
                    import base64
                    try:
                        body = base64.b64decode(body).decode('utf-8', errors='replace')
                        b64 = False
                    except Exception:
                        pass
                return body, b64
        except Exception:
            pass
        finally:
            with self._lock:
                if hasattr(self, '_pending'):
                    self._pending.pop(msg_id, None)
        return '', False

    def _mark_ready(self):
        self.ready = True
        self._ready.set()
        if self.on_ready:
            try:
                self.on_ready()
            except Exception:
                pass

    def _run(self):
        try:
            import websocket
        except ImportError as exc:
            if self.on_error:
                self.on_error(f'缺少 websocket-client 依赖：{exc}')
            if self.on_closed:
                self.on_closed()
            self._ready.set()
            return

        self._pending = {}
        enable_id = {'id': None}

        def on_message(ws, message):
            try:
                data = json.loads(message)
            except Exception:
                return
            mid = data.get('id')
            if mid is not None and mid in self._pending:
                holder, ev = self._pending.pop(mid)
                holder['data'] = data
                ev.set()
                if enable_id['id'] is not None and mid == enable_id['id']:
                    if data.get('error'):
                        err = data.get('error')
                        msg = err.get('message') if isinstance(err, dict) else str(err)
                        if self.on_error:
                            self.on_error(f'Network.enable 失败：{msg}')
                        self._ready.set()
                    else:
                        self._mark_ready()
                return
            # 无 pending 但可能是 enable 响应
            if mid is not None and enable_id['id'] is not None and mid == enable_id['id']:
                if data.get('error'):
                    err = data.get('error')
                    msg = err.get('message') if isinstance(err, dict) else str(err)
                    if self.on_error:
                        self.on_error(f'Network.enable 失败：{msg}')
                    self._ready.set()
                else:
                    self._mark_ready()
                return
            method = data.get('method') or ''
            params = data.get('params') or {}
            if method.startswith('Network.') or method.startswith('Network'):
                with self._lock:
                    rid = merge_cdp_event(self.records, method, params)
                    if method == 'Network.loadingFinished' and rid:
                        threading.Thread(
                            target=self._fetch_body_async,
                            args=(rid,),
                            daemon=True,
                        ).start()
                    # WebSocket 帧：标注类型，不丢弃
                    if method in (
                        'Network.webSocketCreated',
                        'Network.webSocketFrameSent',
                        'Network.webSocketFrameReceived',
                        'Network.webSocketClosed',
                        'Network.webSocketHandshakeResponseReceived',
                    ):
                        rid = params.get('requestId')
                        if rid:
                            rec = self.records.get(rid)
                            if rec is None:
                                rec = empty_record(rid)
                                rec['url'] = params.get('url') or rec.get('url') or ''
                                self.records[rid] = rec
                            rec['resource_type'] = 'WebSocket'
                            if method == 'Network.webSocketClosed':
                                rec['failure'] = rec.get('failure') or ''
                            if 'response' in params and isinstance(params.get('response'), dict):
                                rec['status'] = params['response'].get('status')
                if self.on_event:
                    try:
                        self.on_event(method, params)
                    except Exception:
                        pass

        def on_error(ws, error):
            if self.on_error and not self._stop.is_set():
                try:
                    self.on_error(str(error))
                except Exception:
                    pass
            if not self.ready:
                self._ready.set()

        def on_close(ws, *args):
            self.ready = False
            if self.on_closed:
                try:
                    self.on_closed()
                except Exception:
                    pass
            self._ready.set()

        def on_open(ws):
            try:
                mid = self._next_id()
                enable_id['id'] = mid
                with self._lock:
                    self._pending[mid] = ({}, threading.Event())
                self._send('Network.enable', msg_id=mid)
                # 兜底：若浏览器不回 enable 响应，短延迟后仍标记就绪（通道已开）
                def _fallback_ready():
                    time.sleep(1.2)
                    if not self.ready and not self._stop.is_set() and self._ws is not None:
                        self._mark_ready()
                threading.Thread(target=_fallback_ready, daemon=True).start()
            except Exception as exc:
                if self.on_error:
                    self.on_error(str(exc))
                self._ready.set()

        try:
            # http_proxy_host=None 强制绕过环境代理
            self._ws = websocket.WebSocketApp(
                self.ws_url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open,
            )
            self._ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
                http_proxy_host=None,
                http_proxy_port=None,
                proxy_type=None,
            )
        except TypeError:
            # 旧版 websocket-client 无 proxy 参数
            try:
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                if self.on_error and not self._stop.is_set():
                    self.on_error(str(exc))
                if self.on_closed:
                    self.on_closed()
                self._ready.set()
        except Exception as exc:
            if self.on_error and not self._stop.is_set():
                self.on_error(str(exc))
            if self.on_closed:
                self.on_closed()
            self._ready.set()

    def _fetch_body_async(self, request_id: str):
        body, _ = self.get_response_body(request_id)
        if body:
            with self._lock:
                rec = self.records.get(request_id)
                if rec is not None:
                    rec['response_body'] = body
            if self.on_event:
                try:
                    self.on_event('Network.responseBody', {'requestId': request_id})
                except Exception:
                    pass
        else:
            # getResponseBody 失败时保留摘要，不删除记录
            if self.on_event:
                try:
                    self.on_event('Network.responseBody', {
                        'requestId': request_id,
                        'failed': True,
                    })
                except Exception:
                    pass


def connect_page_session(
    port: int,
    target: Optional[dict] = None,
    host: str = '127.0.0.1',
    on_event=None,
    on_error=None,
    on_closed=None,
    on_ready=None,
    wait_ready: bool = True,
    ready_timeout: float = 8.0,
) -> CdpNetworkSession:
    if not is_loopback_host(host):
        raise BrowserDebugError('CDP 仅允许连接 127.0.0.1 / localhost')
    if not port_open(port, host):
        raise BrowserDebugError(f'调试端口 {host}:{port} 不可连接')
    targets = fetch_cdp_targets(port, host)
    page = target or pick_default_page_target(targets)
    if not page:
        raise BrowserDebugError('未找到可监听的 page target，请先打开业务页面')
    ws_url = page.get('webSocketDebuggerUrl') or ''
    if not ws_url:
        raise BrowserDebugError('目标页缺少 webSocketDebuggerUrl')
    # 强制 ws host 为 loopback
    parsed = urlparse(ws_url)
    if parsed.hostname and not is_loopback_host(parsed.hostname):
        raise BrowserDebugError('调试 WebSocket 地址非本机 loopback，已拒绝连接')
    # 规范化 ws URL 使用 127.0.0.1，避免 localhost 被代理解析
    if parsed.hostname in ('localhost', '::1'):
        from urllib.parse import urlunparse
        netloc = f'127.0.0.1:{parsed.port}' if parsed.port else '127.0.0.1'
        ws_url = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    session = CdpNetworkSession(
        ws_url,
        on_event=on_event,
        on_error=on_error,
        on_closed=on_closed,
        on_ready=on_ready,
    )
    session.start()
    if wait_ready and not session.wait_ready(ready_timeout):
        session.stop()
        raise BrowserDebugError('CDP 通道已连接，但 Network.enable / 事件回调未在时限内就绪')
    return session

