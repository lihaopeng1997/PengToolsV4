# -*- coding: utf-8 -*-
"""HTTP/HTTPS 本机数据包抓取（对齐 Fiddler 的「中转站」模型）。

Fiddler 做什么：电脑上的 HTTP/HTTPS 请求先到本地代理，再转发外网；
工具从中看到地址、参数、响应、耗时、状态、Cookie 等。

本实现同样：
1. 127.0.0.1 本地正向代理（系统代理临时指向它）→ 全端走系统代理的程序流量可进；
2. HTTP 明文记录；HTTPS 本机 CA MITM 解密后记录完整 URL/头/体；
3. 每条流量 = 一条 Session 内存记录（method/url/host/path/query/status…）；
4. 仅 loopback；报文只存内存；Private 版不改包、不重放、不 Mock 外发。

系统代理与 CA 工具在 tools.ie_proxy；本文件只做抓取引擎。
"""

from __future__ import annotations

import asyncio
import queue
import socket
import threading
import time
import uuid
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlparse

from tools.browser_debug import STATIC_EXTENSIONS, empty_record


class HttpCaptureError(ValueError):
    pass


def _header_map(headers) -> dict:
    result = {}
    if headers is None:
        return result
    try:
        for k, v in headers.items(multi=True):
            result[str(k)] = str(v)
    except Exception:
        try:
            result = {str(k): str(v) for k, v in dict(headers).items()}
        except Exception:
            result = {}
    return result


def _safe_text(message) -> str:
    if message is None:
        return ''
    try:
        text = message.get_text(strict=False)
        if text is not None:
            return text
    except Exception:
        pass
    try:
        raw = getattr(message, 'content', None) or b''
        if isinstance(raw, bytes):
            return raw.decode('utf-8', errors='replace') if raw else ''
        return str(raw)
    except Exception:
        return ''


def _guess_resource_type(url: str, path: str, request_headers: dict) -> str:
    path_l = (path or '').lower()
    if any(path_l.endswith(ext) for ext in STATIC_EXTENSIONS):
        if path_l.endswith(('.js', '.mjs')):
            return 'Script'
        if path_l.endswith('.css'):
            return 'Stylesheet'
        if path_l.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.bmp')):
            return 'Image'
        if path_l.endswith(('.woff', '.woff2', '.ttf', '.eot', '.otf')):
            return 'Font'
        return 'Other'
    accept = ''
    for k, v in (request_headers or {}).items():
        if str(k).lower() == 'accept':
            accept = str(v).lower()
            break
    if 'text/html' in accept or path_l.endswith(('.html', '.htm')) or path_l in ('', '/'):
        return 'Document'
    if 'application/json' in accept or 'xmlhttprequest' in str(
        request_headers.get('X-Requested-With') or request_headers.get('x-requested-with') or ''
    ).lower():
        return 'XHR'
    # 默认按业务接口保留，避免被 UI 默认筛选误杀
    return 'XHR'


def flow_to_url_record(flow, *, source: str = 'http_capture', record_id: str = '') -> dict:
    """把 mitmproxy HTTPFlow 转成以 URL 为核心的内存记录。"""
    rec = empty_record(record_id or uuid.uuid4().hex)
    rec['source'] = source or 'http_capture'
    try:
        req = flow.request
        url = ''
        try:
            url = req.pretty_url or req.url or ''
        except Exception:
            url = getattr(req, 'url', '') or ''
        parsed = urlparse(url)
        headers = _header_map(getattr(req, 'headers', None))
        query = parsed.query or ''
        # query 结构化（仅内存；敏感值展示层再脱敏）
        query_params = {}
        try:
            for k, v in parse_qsl(query, keep_blank_values=True):
                query_params[str(k)] = str(v)
        except Exception:
            query_params = {}

        rec.update({
            'method': (getattr(req, 'method', None) or 'GET').upper(),
            'url': url,
            'scheme': (parsed.scheme or getattr(req, 'scheme', '') or '').lower(),
            'host': parsed.hostname or getattr(req, 'pretty_host', None) or getattr(req, 'host', '') or '',
            'port': parsed.port or getattr(req, 'port', None),
            'path': parsed.path or '/',
            'query': query,
            'query_params': query_params,
            'fragment': parsed.fragment or '',
            'request_headers': headers,
            'request_body': _safe_text(req),
            'resource_type': _guess_resource_type(url, parsed.path or '/', headers),
        })
        try:
            content = getattr(req, 'content', None) or b''
            rec['request_size'] = len(content) if isinstance(content, (bytes, bytearray)) else len(str(content))
        except Exception:
            rec['request_size'] = len(rec.get('request_body') or '')

        # 时间
        try:
            ts = getattr(flow, 'timestamp_start', None) or getattr(req, 'timestamp_start', None)
            if ts:
                rec['started_at'] = float(ts)
        except Exception:
            pass

        resp = getattr(flow, 'response', None)
        if resp is not None:
            rec['status'] = getattr(resp, 'status_code', None)
            rh = _header_map(getattr(resp, 'headers', None))
            rec['response_headers'] = rh
            rec['mime_type'] = rh.get('Content-Type') or rh.get('content-type') or ''
            rec['response_body'] = _safe_text(resp)
            try:
                content = getattr(resp, 'content', None) or b''
                rec['response_size'] = len(content) if isinstance(content, (bytes, bytearray)) else len(str(content))
            except Exception:
                rec['response_size'] = len(rec.get('response_body') or '')
            try:
                start = getattr(flow, 'timestamp_start', None) or rec.get('started_at') or 0
                end = getattr(flow, 'timestamp_end', None)
                if end is None:
                    end = getattr(resp, 'timestamp_end', None)
                if end and start:
                    rec['duration_ms'] = int(max(0, (float(end) - float(start)) * 1000))
            except Exception:
                pass
        else:
            err = getattr(flow, 'error', None)
            if err is not None:
                msg = str(getattr(err, 'msg', err) or err)
                if (rec.get('scheme') == 'https' or (url or '').lower().startswith('https')) and not msg:
                    msg = 'HTTPS 解密失败：请安装本机抓包证书'
                rec['failure'] = msg or 'capture error'
    except Exception as exc:
        rec['failure'] = str(exc)
    return rec


class _UrlCaptureAddon:
    """mitmproxy 插件：只读抓包，不改写/不重放流量。"""

    def __init__(self, out_queue: queue.Queue, source: str = 'http_capture'):
        self.out_queue = out_queue
        self.source = source
        self._flow_ids: dict[int, str] = {}

    def _id_for(self, flow) -> str:
        key = id(flow)
        rid = self._flow_ids.get(key)
        if not rid:
            rid = uuid.uuid4().hex
            self._flow_ids[key] = rid
        return rid

    def _emit(self, flow):
        try:
            rec = flow_to_url_record(flow, source=self.source, record_id=self._id_for(flow))
            # CONNECT 隧道本身不是业务 URL，跳过空壳
            method = (rec.get('method') or '').upper()
            url = rec.get('url') or ''
            if method == 'CONNECT' and not rec.get('status') and not rec.get('failure'):
                return
            if not url and not rec.get('host'):
                return
            self.out_queue.put(rec)
        except Exception:
            pass

    def request(self, flow):
        # 请求阶段先出一条，列表即时出现 URL
        self._emit(flow)

    def response(self, flow):
        # 响应阶段覆盖同一 id，补齐 status/body
        self._emit(flow)

    def error(self, flow):
        self._emit(flow)


class HttpCaptureWorker:
    """后台 MITM 抓包 worker：127.0.0.1 绑定 + 可选系统代理。"""

    def __init__(
        self,
        port: int = 8899,
        on_record: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_stopped: Optional[Callable[[], None]] = None,
        on_ready: Optional[Callable[[], None]] = None,
        source_label: str = 'http_capture',
        apply_system_proxy: bool = True,
        show_static: bool = True,  # 保留参数兼容；过滤交给 UI
    ):
        self.port = max(1, min(65535, int(port or 8899)))
        self.on_record = on_record
        self.on_error = on_error
        self.on_stopped = on_stopped
        self.on_ready = on_ready
        self.source_label = source_label or 'http_capture'
        self.apply_system_proxy = bool(apply_system_proxy)
        self.show_static = bool(show_static)

        self._stop = threading.Event()
        self._ready = threading.Event()
        self.ready = False
        self._queue: queue.Queue = queue.Queue()
        self.records: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._proxy_applied = False
        self._thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._master = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self.ready = False
        self._thread = threading.Thread(target=self._run, name='http-capture', daemon=True)
        self._thread.start()
        self._poll_thread = threading.Thread(target=self._poll_queue, name='http-capture-poll', daemon=True)
        self._poll_thread.start()

    def wait_ready(self, timeout: float = 12.0) -> bool:
        ok = self._ready.wait(timeout)
        return bool(ok and self.ready and not self._stop.is_set())

    def stop(self):
        self._stop.set()
        self.ready = False
        master = self._master
        loop = self._loop
        try:
            if master is not None:
                if loop is not None and loop.is_running():
                    loop.call_soon_threadsafe(master.shutdown)
                else:
                    master.shutdown()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=4.0)
        self._restore_proxy()
        self.clear_session()
        if self.on_stopped:
            try:
                self.on_stopped()
            except Exception:
                pass

    def clear_session(self):
        with self._lock:
            self.records.clear()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _poll_queue(self):
        while not self._stop.is_set():
            try:
                rec = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            rec = dict(rec)
            rec['source'] = self.source_label
            with self._lock:
                # 同 id 合并更新（request → response）
                prev = self.records.get(rec['id'])
                if prev:
                    merged = dict(prev)
                    merged.update({k: v for k, v in rec.items() if v not in (None, '', {}, []) or k in (
                        'status', 'failure', 'response_body', 'response_headers', 'duration_ms',
                        'mime_type', 'response_size',
                    )})
                    # status/body 以新记录为准
                    for key in (
                        'status', 'response_body', 'response_headers', 'duration_ms',
                        'mime_type', 'response_size', 'failure', 'request_body', 'request_headers',
                        'url', 'path', 'query', 'query_params', 'host', 'scheme', 'method',
                    ):
                        if key in rec:
                            merged[key] = rec[key]
                    rec = merged
                self.records[rec['id']] = rec
            if self.on_record:
                try:
                    self.on_record(dict(rec))
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

    def _emit_error(self, msg: str):
        if self.on_error and not self._stop.is_set():
            try:
                self.on_error(str(msg))
            except Exception:
                pass

    def _port_bound(self) -> bool:
        try:
            with socket.create_connection(('127.0.0.1', self.port), timeout=0.35):
                return True
        except OSError:
            return False

    def _run(self):
        try:
            from mitmproxy import options
            from mitmproxy.tools.dump import DumpMaster
        except ImportError as exc:
            self._emit_error(f'缺少 mitmproxy 依赖：{exc}')
            self._ready.set()
            return

        # 证书 / confdir
        try:
            from tools.ie_proxy import ensure_mitm_ca_exists, mitm_cert_dir
            confdir = mitm_cert_dir()
            try:
                ensure_mitm_ca_exists()
            except Exception:
                pass
        except Exception as exc:
            self._emit_error(f'初始化抓包证书目录失败：{exc}')
            self._ready.set()
            return

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            opts = options.Options(
                listen_host='127.0.0.1',
                listen_port=self.port,
                confdir=confdir,
            )
            # HTTPS 解密 + 兼容上游自签；失败则忽略未知 option
            for key, val in (
                ('ssl_insecure', True),
                ('http2', True),
                ('websocket', False),  # 接口排查以 HTTP(S) 请求为主
                ('connection_strategy', 'lazy'),
            ):
                try:
                    opts.update(**{key: val})
                except Exception:
                    pass

            master = DumpMaster(opts, loop=loop, with_termlog=False, with_dumper=False)
            master.addons.add(_UrlCaptureAddon(self._queue, source=self.source_label))
            self._master = master

            async def _boot_and_run():
                # DumpMaster.run 会 listen + 事件循环
                await master.run()

            # 在独立任务里跑，便于超时检测绑定
            run_task = loop.create_task(_boot_and_run())

            def _watch_bind():
                deadline = time.time() + 10.0
                while time.time() < deadline and not self._stop.is_set():
                    if self._port_bound():
                        return True
                    time.sleep(0.12)
                return False

            watcher = threading.Thread(target=lambda: None, daemon=True)
            # 边跑 loop 边等端口：用 call_soon 调度检查
            bind_ok = {'v': False}

            async def _wait_bind_then_proxy():
                deadline = loop.time() + 10.0
                while loop.time() < deadline and not self._stop.is_set():
                    if await loop.run_in_executor(None, self._port_bound):
                        bind_ok['v'] = True
                        break
                    await asyncio.sleep(0.12)
                if not bind_ok['v']:
                    self._emit_error(
                        f'HTTP 抓包代理未能绑定 127.0.0.1:{self.port}（端口占用或 mitmproxy 启动失败）'
                    )
                    try:
                        master.shutdown()
                    except Exception:
                        pass
                    return
                if self.apply_system_proxy:
                    try:
                        from tools.ie_proxy import apply_local_proxy
                        apply_local_proxy(self.port)
                        self._proxy_applied = True
                    except Exception as exc:
                        self._emit_error(f'设置系统代理失败：{exc}')
                        try:
                            master.shutdown()
                        except Exception:
                            pass
                        return
                self._mark_ready()

            async def _main():
                waiter = asyncio.create_task(_wait_bind_then_proxy())
                try:
                    await run_task
                except Exception as exc:
                    if not self._stop.is_set():
                        self._emit_error(f'mitmproxy 运行失败：{exc}')
                finally:
                    if not waiter.done():
                        waiter.cancel()
                        try:
                            await waiter
                        except Exception:
                            pass
                    if not self._ready.is_set():
                        self._ready.set()

            loop.run_until_complete(_main())
        except Exception as exc:
            self._emit_error(str(exc))
            self._ready.set()
        finally:
            self.ready = False
            self._restore_proxy()
            try:
                if loop.is_running():
                    loop.stop()
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self._master = None

    def _restore_proxy(self):
        if not self._proxy_applied:
            return
        try:
            from tools.ie_proxy import restore_proxy_from_snapshot
            restore_proxy_from_snapshot()
        except Exception:
            pass
        self._proxy_applied = False


# 兼容旧名：面板 / 测试曾用 IeProxyWorker
IeProxyWorker = HttpCaptureWorker


def flow_to_record(flow, **kwargs):
    """兼容旧测试：默认 source=ie_proxy。"""
    kwargs.setdefault('source', 'ie_proxy')
    return flow_to_url_record(flow, **kwargs)
