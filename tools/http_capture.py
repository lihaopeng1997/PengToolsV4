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

    def __init__(self, out_queue: queue.Queue, source: str = 'http_capture',
                 on_running: Optional[Callable[[], None]] = None):
        self.out_queue = out_queue
        self.source = source
        self.on_running = on_running
        self._flow_ids: dict[int, str] = {}

    def running(self):
        """RunningHook：setup_servers 真正绑定成功后 mitmproxy 官方触发的就绪信号。

        就绪判定必须用它，不能用「端口可连」探测——上一轮未释放的旧引擎同样可连。
        """
        if self.on_running:
            try:
                self.on_running()
            except Exception:
                pass

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

    def stop(self, *, join_timeout: float = 1.2, clear_records: bool = False):
        """停止抓包：标记 stopping → 恢复代理 → 主动关 listener → shutdown → 线程收尾。

        mitmproxy 12.2.3 的 Master.shutdown() 只置位 should_exit 结束 run()，
        不会关闭 asyncio.Server 监听 socket；引擎还被 mitmproxy.ctx.master 模块级
        全局引用钉住，旧端口可能长期不释放 → 下次同端口启动绑定失败（10048）。
        因此必须先在 mitmproxy 自己的 event loop 里把 server=False / servers.update([])
        执行完（端口即时释放），再 shutdown，最后才允许 finish_stop。
        """
        self._stop.set()
        self.ready = False
        # 先恢复系统代理，网络立刻可用；引擎在下面主动收尾
        self._restore_proxy()
        self._close_listener()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.2, float(join_timeout or 1.2)))
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=1.0)
        # 保险：主动关停失败（如 loop 已提前退出）时由这里兜底确认端口已释放；
        # 正常路径 listener 已关，应瞬间通过。
        self._wait_port_released(timeout=3.0)
        if clear_records:
            self.clear_session()
        if self.on_stopped:
            try:
                self.on_stopped()
            except Exception:
                pass

    def _close_listener(self, timeout: float = 3.0):
        """在 mitmproxy 自己的 event loop 上主动关闭 listener（线程安全）。"""
        master = self._master
        loop = self._loop
        if master is None or loop is None:
            return

        async def _close():
            # server=False（mitmproxy 12.2.3 配置项 "Start a proxy server. Enabled
            # by default."）→ Proxyserver.configure → Servers.update：所有监听实例
            # 逐一 stop()，asyncio.Server 关闭、端口立即释放。
            try:
                master.options.update(server=False)
            except Exception:
                pass
            try:
                ps = master.addons.get('proxyserver')
                servers = getattr(ps, 'servers', None)
                if servers is not None:
                    # 直接 await update([])：确定性关停全部 listener，await 返回即端口已释放
                    await servers.update([])
            except Exception:
                pass
            try:
                master.shutdown()
            except Exception:
                pass

        try:
            if loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(_close(), loop)
                try:
                    fut.result(timeout=max(0.5, float(timeout)))
                except Exception:
                    pass
            else:
                # loop 已停/未运行：同步尽力而为，收尾交给 join 与端口确认
                try:
                    master.options.update(server=False)
                except Exception:
                    pass
                try:
                    master.shutdown()
                except Exception:
                    pass
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

    def _wait_port_released(self, timeout: float = 3.0) -> None:
        """等待本地代理端口真正关闭（供 stop 后立刻重启使用）。

        仅在本对象已请求 stop（self._stop 已置位）时轮询，避免误等无关连接。
        端口未绑定视为已释放，立即返回。
        """
        if not self._stop.is_set():
            return
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            if not self._port_bound():
                return
            time.sleep(0.08)

    def _apply_proxy(self):
        from tools.ie_proxy import apply_local_proxy
        apply_local_proxy(self.port)
        self._proxy_applied = True

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
        master = None
        try:
            opts = options.Options(
                listen_host='127.0.0.1',
                listen_port=self.port,
                confdir=confdir,
            )
            # HTTPS 解密 + 兼容上游自签；失败则忽略未知 option
            for key, val in (
                ('ssl_insecure', True),
                # mitmproxy 12.2.3 pins h2 4.3.0, which is affected by a
                # duplicate-Host request-smuggling advisory.  The desktop
                # capture use case does not require HTTP/2, so keep it off
                # until mitmproxy permits h2 >= 4.4.1.
                ('http2', False),
                ('websocket', False),  # 接口排查以 HTTP(S) 请求为主
                ('connection_strategy', 'lazy'),
            ):
                try:
                    opts.update(**{key: val})
                except Exception:
                    pass

            master = DumpMaster(opts, loop=loop, with_termlog=False, with_dumper=False)
            started = asyncio.Event()  # RunningHook：listener 真正绑定成功的权威信号
            master.addons.add(_UrlCaptureAddon(
                self._queue, source=self.source_label,
                on_running=lambda: started.set()))
            self._master = master

            async def _boot_and_run():
                # DumpMaster.run 会 listen + 事件循环。
                # 注意：mitmproxy errorcheck 对启动期错误（典型为端口被 10048 占用）
                # 走 sys.exit(1)，asyncio Task 对 SystemExit 会直接击穿事件循环，
                # 必须在此就地转成普通异常，否则任何 await 侧的检测都不会执行。
                try:
                    await master.run()
                except SystemExit:
                    detail = ''
                    try:
                        ec_addon = master.addons.get('errorcheck')
                        records = list(getattr(ec_addon.logger, 'has_errored', []) or [])
                        if records:
                            detail = '：' + ' | '.join(
                                str(r.getMessage()) for r in records[:3]
                            )
                    except Exception:
                        pass
                    raise RuntimeError(
                        f'mitmproxy 启动失败（典型原因：127.0.0.1:{self.port} 端口被占用）{detail}'
                    ) from None

            run_task = loop.create_task(_boot_and_run())
            emitted = {'v': False}

            async def _fail_bind(message: str):
                emitted['v'] = True
                self._emit_error(message)
                try:
                    master.shutdown()
                except Exception:
                    pass

            async def _watch_startup():
                # 就绪以 RunningHook（setup_servers 绑定成功）为准，10s 内必须出结果：
                # 就绪、明确报错或请求 shutdown，绝不 silent 等到 wait_ready 超时。
                started_wait = asyncio.ensure_future(started.wait())
                try:
                    await asyncio.wait(
                        [started_wait, run_task],
                        timeout=10.0, return_when=asyncio.FIRST_COMPLETED,
                    )
                except Exception:
                    pass
                finally:
                    started_wait.cancel()
                if run_task.done():
                    # mitmproxy 12 绑定失败（10048）时 errorcheck 记录错误后优雅退出，
                    # run() 不带异常；非用户停止的提前退出一律按端口占用明确报错。
                    exc = None if run_task.cancelled() else run_task.exception()
                    if not self._stop.is_set():
                        detail = f'：{exc}' if exc else ''
                        await _fail_bind(
                            f'HTTP 抓包代理绑定 127.0.0.1:{self.port} 失败'
                            f'（端口被上一轮抓包或其它程序占用）{detail}'
                        )
                    else:
                        try:
                            master.shutdown()
                        except Exception:
                            pass
                    return
                if not started.is_set():
                    # 超时仍未就绪（setup 悬挂等）：确保引擎退出，线程不得悬挂
                    if not self._stop.is_set():
                        await _fail_bind(
                            f'HTTP 抓包代理未能绑定 127.0.0.1:{self.port}'
                            '（端口占用或 mitmproxy 启动失败）'
                        )
                    else:
                        try:
                            master.shutdown()
                        except Exception:
                            pass
                    return
                if self._stop.is_set():
                    # stop() 在 RunningHook 前抢进（master 尚未创建、shutdown 被
                    # run() 开头的 clear 吞掉）：此时必须主动结束引擎。
                    try:
                        master.shutdown()
                    except Exception:
                        pass
                    return
                if self.apply_system_proxy:
                    try:
                        await loop.run_in_executor(None, self._apply_proxy)
                    except Exception as exc:
                        self._emit_error(f'设置系统代理失败：{exc}')
                        try:
                            master.shutdown()
                        except Exception:
                            pass
                        return
                if not run_task.done():
                    self._mark_ready()

            async def _main():
                waiter = asyncio.create_task(_watch_startup())
                bind_error = None
                try:
                    await run_task
                except Exception as exc:
                    bind_error = exc
                    if not self._stop.is_set() and not emitted['v']:
                        self._emit_error(f'mitmproxy 运行失败：{exc}')
                finally:
                    # 引擎提前退出且从未就绪（典型：端口被占用，mitmproxy errorcheck
                    # 记录 "Error logged during startup" 后优雅退出、无异常抛出）。
                    # 兜底必须放在这里，否则 wait_ready 只能傻等超时（silent failure）。
                    if (bind_error is None and not emitted['v']
                            and not self._ready.is_set() and not self._stop.is_set()):
                        self._emit_error(
                            f'HTTP 抓包代理绑定 127.0.0.1:{self.port} 失败'
                            '（端口被上一轮抓包或其它程序占用）'
                        )
                    if not waiter.done():
                        waiter.cancel()
                    try:
                        await waiter
                    except BaseException:
                        # watcher 被取消时 CancelledError 在此浮出（BaseException），
                        # 必须就地吞掉，否则会击穿 _main 导致 _ready 永不 set。
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
            # 回收 loop 上遗留任务（mitmproxy connection watchdog 等），否则
            # close() 时会刷 "Task was destroyed but it is pending" 噪音。
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                if loop.is_running():
                    loop.stop()
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            # Master.__init__ 把引擎挂到模块级全局 mitmproxy.ctx.master 且从不清理；
            # 不解除则旧引擎（含已关停的 server 对象）永远无法回收（每轮确定性泄漏）。
            try:
                from mitmproxy import ctx as _mp_ctx
                if master is not None and getattr(_mp_ctx, 'master', None) is master:
                    _mp_ctx.master = None
            except Exception:
                pass
            self._loop = None
            self._master = None

    def _restore_proxy(self):
        if not self._proxy_applied:
            # 仍做一次安全检查（应对异常半开状态）
            try:
                from tools.ie_proxy import ensure_system_proxy_safe
                ensure_system_proxy_safe(reason='capture_stop_idle')
            except Exception:
                pass
            return
        try:
            from tools.ie_proxy import restore_proxy_from_snapshot, ensure_system_proxy_safe, mark_capture_proxy_inactive
            restore_proxy_from_snapshot()
            mark_capture_proxy_inactive()
            ensure_system_proxy_safe(reason='capture_stop')
        except Exception:
            try:
                from tools.ie_proxy import ensure_system_proxy_safe
                ensure_system_proxy_safe(reason='capture_stop_fallback')
            except Exception:
                pass
        self._proxy_applied = False


# 兼容旧名：面板 / 测试曾用 IeProxyWorker
IeProxyWorker = HttpCaptureWorker


def flow_to_record(flow, **kwargs):
    """兼容旧测试：默认 source=ie_proxy。"""
    kwargs.setdefault('source', 'ie_proxy')
    return flow_to_url_record(flow, **kwargs)
