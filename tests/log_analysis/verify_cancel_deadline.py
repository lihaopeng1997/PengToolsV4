"""Cancel vs deadline on a closed model stream (offscreen, fake HTTP response)."""
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from tools.agent_core.contracts import CancellationToken
from tools.agent_core.model_adapter import (
    ModelDeadlineExceeded,
    ModelTransportError,
    UrlLibStreamingTransport,
)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))

# ---------------------------------------------------------------- bug 1: cancel
CFG = {'enabled': True, 'base_url': 'http://127.0.0.1:18000/v1',
       'model': 'demo'}  # provider defaults to the local gateway


class SingleChunkResponse:
    """Ends cleanly after one event; used for the non-cancel baselines."""

    def __init__(self):
        self.count = 0

    def read(self, size=-1):
        self.count += 1
        if self.count == 1:
            return b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        return b''

    def close(self):
        pass


class ClosedHandleResponse:
    """Mimics http.client: our own close() makes the bound reader blow up."""

    def __init__(self):
        self.fp = object()
        self.count = 0
        self.closed = False

    def read(self, size=-1):
        self.count += 1
        if self.count == 1:
            return b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        time.sleep(0.25)
        return self.fp.read(size)   # AttributeError after close()

    def close(self):
        self.closed = True
        self.fp = None


def stream_with(cancel_after, response_cls=ClosedHandleResponse):
    original = urllib.request.OpenerDirector.open
    urllib.request.OpenerDirector.open = lambda self, req, timeout=None: response_cls()
    token = CancellationToken()
    if cancel_after is not None:
        threading.Timer(cancel_after, token.cancel).start()
    error = None
    chunks = 0
    try:
        for _ in UrlLibStreamingTransport(dict(CFG)).stream(
                {'model': 'demo', 'messages': []}, cancellation=token):
            chunks += 1
    except Exception as exc:  # noqa: BLE001
        error = exc
    finally:
        urllib.request.OpenerDirector.open = original
    return token, chunks, error


print('--- bug 1: cancel paths ---')
token, chunks, error = stream_with(None, SingleChunkResponse)
check('刚启动就取消前的基线可读', chunks >= 1 and error is None, f'{chunks} {error}')

token_before = CancellationToken()
token_before.cancel()
transport = UrlLibStreamingTransport(dict(CFG))
now_error = None
try:
    for _ in transport.stream({'model': 'demo', 'messages': []}, cancellation=token_before):
        pass
except Exception as exc:  # noqa: BLE001
    now_error = exc
check('启动前已取消不报错', now_error is None, str(now_error))

token_mid, chunks_mid, error_mid = stream_with(0.1)
check('读取中取消不报错', error_mid is None, f'{type(error_mid).__name__}: {error_mid}')
check('读取中取消已生效', token_mid.is_cancelled())

token_late, _chunks_late, error_late = stream_with(None, SingleChunkResponse)
token_late.cancel()
check('结束后再取消不报错', error_late is None, str(error_late))

# A real handle loss (no cancel) must still surface as a transport error.
token_none, _c, error_real = stream_with(None)
check('未取消的句柄丢失仍报错', isinstance(error_real, ModelTransportError),
      type(error_real).__name__)

# A blocked read that outlives the deadline must raise, never return half a stream.
DEADLINE_CFG = dict(CFG, read_timeout_seconds=0.3)


class SlowResponse(ClosedHandleResponse):
    def read(self, size=-1):
        self.count += 1
        if self.count == 1:
            return b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        time.sleep(0.7)
        return self.fp.read(size)


original_open = urllib.request.OpenerDirector.open
urllib.request.OpenerDirector.open = lambda self, req, timeout=None: SlowResponse()
token_timeout = CancellationToken()
timeout_error = None
timeout_chunks = 0
try:
    for _ in UrlLibStreamingTransport(dict(DEADLINE_CFG)).stream(
            {'model': 'demo', 'messages': []}, cancellation=token_timeout):
        timeout_chunks += 1
except Exception as exc:  # noqa: BLE001
    timeout_error = exc
finally:
    urllib.request.OpenerDirector.open = original_open
check('读取阻塞超时必须抛 ModelDeadlineExceeded',
      isinstance(timeout_error, ModelDeadlineExceeded), type(timeout_error).__name__)
check('超时不是静默返回半截', timeout_chunks <= 1, str(timeout_chunks))
check('超时未被当成取消', not token_timeout.is_cancelled())


print()
try:
    _isolation.assert_untouched()
    check('未触碰真实数据目录与用户配置', True)
except AssertionError as exc:
    check('未触碰真实数据目录与用户配置', False, str(exc))

print(f'RESULT: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('FAILED:', FAIL)
sys.exit(1 if FAIL else 0)
