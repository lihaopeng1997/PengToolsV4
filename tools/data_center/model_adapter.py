"""Streaming model adapter for the data-center Agent.

The existing :mod:`tools.intranet_llm` client remains the compatibility path
for the older chat features.  This module adds an Agent-specific transport and
keeps its response incremental all the way to ``on_delta``.  Tests can inject
an iterable or a small ``stream`` transport, so protocol behaviour is
verifiable without opening a port or contacting a model.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import math
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol

from .contracts import AgentTurn, AgentTurnRequest, CancellationToken, ModelDelta, ToolCall
from .model_config import (
    AgentModelCapability,
    DEFAULT_AGENT_DEADLINE_SECONDS,
    DEFAULT_MAX_RAW_BUFFER_BYTES,
    effective_capability,
    normalize_capability,
)
from .streaming import (
    ModelStreamParser,
    DEFAULT_MAX_REASONING_BYTES,
    DEFAULT_MAX_TEXT_BYTES,
    DEFAULT_MAX_TOOL_ARGUMENT_BYTES,
    SSEProtocolError,
    StreamingLimitError,
    StreamingError,
    ToolArgumentsError,
)


class ModelAdapterError(RuntimeError):
    """Base error raised by the Agent model boundary."""


class ModelProtocolError(ModelAdapterError):
    """The gateway returned a response that cannot safely be interpreted."""


class ModelTransportError(ModelAdapterError):
    """A network/transport failure, retaining only a safe status code."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(str(message))
        self.status_code = status_code
        self.retryable = retryable


class ModelCancelled(ModelAdapterError):
    """The host cancellation token stopped this model turn."""

    def __init__(self, message: str = "model turn cancelled", *, partial_turn: AgentTurn | None = None) -> None:
        super().__init__(message)
        self.partial_turn = partial_turn or AgentTurn(finish_reason="cancelled")


class ModelDeadlineExceeded(ModelTransportError):
    """The one total budget for a model turn elapsed."""

    def __init__(self, message: str = "model turn deadline exceeded") -> None:
        super().__init__(message, retryable=False)


class ModelLimitExceeded(ModelProtocolError):
    """A model stream exceeded a bounded display, argument, or raw buffer."""


class _DeadlineReached(Exception):
    """Internal signal used by the deadline-aware iterator pump."""


_PUMP_DONE = object()


def _finite_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _positive_limit(value: Any, *, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if parsed <= 0:
        return default
    return min(parsed, maximum)


def _config_value(config: Mapping[str, Any], names: tuple[str, ...], default: Any) -> Any:
    for name in names:
        if name in config and config[name] is not None:
            return config[name]
    return default


class _DeadlineCancellation:
    """Expose host cancellation plus a monotonic deadline to transports."""

    __slots__ = ("_token", "deadline")

    def __init__(self, token: Any, deadline: float) -> None:
        self._token = token
        self.deadline = deadline

    def is_cancelled(self) -> bool:
        return _is_cancelled(self._token) or time.monotonic() >= self.deadline

    def cancel(self, reason: str = "cancelled") -> None:
        cancel = getattr(self._token, "cancel", None)
        if callable(cancel):
            cancel(reason)

    @property
    def cancelled(self) -> bool:
        return self.is_cancelled()

    @property
    def reason(self) -> str:
        if _is_cancelled(self._token):
            return str(getattr(self._token, "reason", ""))
        return "model deadline exceeded"

    def wait(self, timeout: float | None = None) -> bool:
        remaining = max(0.0, self.deadline - time.monotonic())
        if timeout is not None:
            remaining = min(remaining, max(0.0, float(timeout)))
        waiter = getattr(self._token, "wait", None)
        if callable(waiter):
            return bool(waiter(remaining)) or self.is_cancelled()
        if remaining:
            time.sleep(remaining)
        return self.is_cancelled()


class _IteratorPump:
    """Pull exactly one response chunk on demand in a deadline-aware thread.

    A response's ``read`` may block.  Pulling in a daemon helper lets the
    adapter stop waiting at the total deadline while preserving true
    incremental delivery: the next read is not started until the previous
    chunk has been consumed by the parser and callback.
    """

    def __init__(self, source: Any) -> None:
        self.source = source
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        self._request = threading.Event()
        self._ack = threading.Event()
        self._stop = threading.Event()
        self._iterator: Iterator[Any] | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="pengtools-agent-model-reader",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _put(self, value: tuple[str, Any]) -> bool:
        while not self._stop.is_set():
            try:
                self._queue.put(value, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _run(self) -> None:
        iterator: Iterator[Any] | None = None
        try:
            iterator = iter(_response_chunks(self.source))
            self._iterator = iterator
            while not self._stop.is_set():
                self._request.wait()
                self._request.clear()
                if self._stop.is_set():
                    return
                try:
                    value = next(iterator)
                except StopIteration:
                    self._put(("done", _PUMP_DONE))
                    return
                except Exception as exc:
                    self._put(("error", exc))
                    return
                if not self._put(("item", value)):
                    return
                while not self._stop.is_set() and not self._ack.wait(0.05):
                    pass
                self._ack.clear()
        except Exception as exc:
            self._put(("error", exc))
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def next(self, *, deadline: float, cancellation: Any) -> Any:
        self._request.set()
        while True:
            if _is_cancelled(cancellation):
                raise ModelCancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _DeadlineReached()
            try:
                kind, value = self._queue.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue
            self._ack.set()
            if kind == "done":
                return _PUMP_DONE
            if kind == "error":
                raise value
            return value

    def close(self) -> None:
        self._stop.set()
        self._request.set()
        self._ack.set()
        for candidate in (self._iterator, self.source):
            close = getattr(candidate, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(0.2)


class StreamingTransport(Protocol):
    """Minimal injectable transport used by :class:`ModelAdapter`."""

    def stream(self, body: Mapping[str, Any], *, cancellation: CancellationToken) -> Any:
        """Return bytes chunks, a response object, or a decoded JSON object."""


@dataclass(slots=True)
class _AttemptError(Exception):
    error: Exception
    started: bool


def _status_code(error: BaseException) -> int | None:
    for name in ("status_code", "code", "status"):
        value = getattr(error, name, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _retryable(error: BaseException) -> bool:
    explicit = getattr(error, "retryable", None)
    if explicit is not None:
        return bool(explicit)
    status = _status_code(error)
    if status == 429 or (status is not None and 500 <= status <= 599):
        return True
    return isinstance(error, (TimeoutError, ConnectionError, OSError))


def _is_cancelled(token: Any) -> bool:
    checker = getattr(token, "is_cancelled", None)
    return bool(checker()) if callable(checker) else bool(getattr(token, "cancelled", False))


def _call_transport_method(
    method: Callable[..., Any],
    body: Mapping[str, Any],
    cancellation: Any,
    *,
    deadline: float | None = None,
) -> Any:
    """Call common fake/HTTP transport signatures without masking its errors."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        # Most Python test doubles accept this spelling.  A fallback to a
        # positional token keeps simple two-argument callables usable.
        try:
            kwargs = {"cancellation": cancellation}
            if deadline is not None:
                kwargs["deadline"] = deadline
            return method(body, **kwargs)
        except TypeError:
            return method(body, cancellation)

    parameters = signature.parameters
    accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    kwargs: dict[str, Any] = {}
    cancellation_parameter = parameters.get("cancellation")
    deadline_parameter = parameters.get("deadline")
    if (
        accepts_kwargs
        or cancellation_parameter is None
        or cancellation_parameter.kind is not inspect.Parameter.POSITIONAL_ONLY
    ) and ("cancellation" in parameters or accepts_kwargs):
        kwargs["cancellation"] = cancellation
    if (
        deadline is not None
        and (accepts_kwargs or deadline_parameter is None or deadline_parameter.kind is not inspect.Parameter.POSITIONAL_ONLY)
        and ("deadline" in parameters or accepts_kwargs)
    ):
        kwargs["deadline"] = deadline
    if kwargs:
        return method(body, **kwargs)
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    positional_args: list[Any] = [body]
    if any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters.values()) or len(positional) >= 2:
        positional_args.append(cancellation)
    if deadline is not None and (
        any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters.values())
        or len(positional) >= 3
    ):
        positional_args.append(deadline)
    return method(*positional_args)


def _response_chunks(response: Any) -> Iterator[Any]:
    """Normalize response objects used by urllib and in-memory transports."""

    if isinstance(response, (bytes, bytearray, memoryview, str, Mapping)):
        yield response
        return
    iterator_method = getattr(response, "iter_bytes", None)
    if callable(iterator_method):
        source = iterator_method()
        try:
            yield from source
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return
    iterator_method = getattr(response, "iter_content", None)
    if callable(iterator_method):
        try:
            source = iterator_method(chunk_size=64 * 1024)
        except TypeError:
            source = iterator_method()
        try:
            yield from source
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return
    # ``HTTPResponse.read(size)`` is allowed to wait for the requested size,
    # which makes a token stream appear frozen behind a large buffer.  read1
    # returns whatever is immediately available on buffered HTTP responses.
    reader = getattr(response, "read1", None) or getattr(response, "read", None)
    if callable(reader):
        try:
            while True:
                chunk = reader(8 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return
    try:
        yield from response
    except TypeError as exc:
        raise ModelTransportError("model transport did not return a stream") from exc
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _decode_json_bytes(raw: bytes | str) -> Mapping[str, Any]:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ModelProtocolError("model response is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ModelProtocolError("model JSON response must be an object")
    return value


def _mapping_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(config) if isinstance(config, Mapping) else {}


def _configured_capability(config: Mapping[str, Any]) -> AgentModelCapability:
    """Read the host-selected capability without trusting model output."""

    return normalize_capability(
        config.get("agent_capability", config.get("capability", config.get("agent_mode")))
    )


def _request_capability(config: Mapping[str, Any]) -> AgentModelCapability:
    """Return the safe request mode for this adapter instance."""

    return effective_capability(_configured_capability(config))


def _strict_response_format(config: Mapping[str, Any]) -> dict[str, Any]:
    """Build the only structured-output request accepted by strict-json mode."""

    selected = config.get("agent_response_format", config.get("response_format"))
    if isinstance(selected, Mapping):
        kind = str(selected.get("type") or "").strip().lower()
        if kind in {"json_object", "json_schema"}:
            result = {"type": kind}
            if kind == "json_schema" and isinstance(selected.get("json_schema"), Mapping):
                result["json_schema"] = dict(selected["json_schema"])
            return result
    kind = str(selected or "json_object").strip().lower()
    return {"type": kind} if kind in {"json_object", "json_schema"} else {"type": "json_object"}


def _response_reader(response: Any) -> Callable[[int], Any] | None:
    """Prefer non-buffering read1 on urllib responses and their file object."""

    candidates = [
        response,
        getattr(response, "fp", None),
        getattr(response, "raw", None),
    ]
    for candidate in candidates:
        reader = getattr(candidate, "read1", None)
        if callable(reader):
            return reader
    for candidate in candidates:
        reader = getattr(candidate, "read", None)
        if callable(reader):
            return reader
    return None


class ModelAdapter:
    """OpenAI-compatible incremental adapter for one Agent model turn.

    ``transport`` is intentionally injectable.  It may expose ``stream`` or
    ``request`` and may return an iterable of bytes, a response object, or a
    decoded mapping.  The default transport is created lazily and reuses the
    existing private-network/TLS/token helpers from ``intranet_llm`` without
    changing that module's legacy behaviour.
    """

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        transport: Any = None,
        model_id: str | None = None,
        model: str | None = None,
        retry_count: int = 2,
        deadline_seconds: float | None = None,
        reasoning_fields: Iterable[str] | None = None,
        max_reasoning_bytes: int | None = None,
        max_text_bytes: int | None = None,
        max_tool_argument_bytes: int | None = None,
        max_raw_buffer_bytes: int | None = None,
    ) -> None:
        self.config = _mapping_config(config)
        if model_id or model:
            self.config["model"] = str(model_id or model)
        self.transport = transport if transport is not None else UrlLibStreamingTransport(self.config)
        self.retry_count = max(0, min(2, int(retry_count)))
        configured_deadline = (
            deadline_seconds
            if deadline_seconds is not None
            else _config_value(
                self.config,
                ("deadline_seconds", "agent_deadline_seconds", "timeout_seconds"),
                DEFAULT_AGENT_DEADLINE_SECONDS,
            )
        )
        self.deadline_seconds = _finite_float(
            configured_deadline,
            default=DEFAULT_AGENT_DEADLINE_SECONDS,
            minimum=0.01,
            maximum=60.0,
        )
        self.max_reasoning_bytes = _positive_limit(
            max_reasoning_bytes
            if max_reasoning_bytes is not None
            else _config_value(
                self.config,
                (
                    "agent_max_reasoning_bytes",
                    "max_reasoning_bytes",
                    "reasoning_max_bytes",
                    "max_reasoning_buffer_bytes",
                ),
                DEFAULT_MAX_REASONING_BYTES,
            ),
            default=DEFAULT_MAX_REASONING_BYTES,
            maximum=4 * 1024 * 1024,
        )
        self.max_text_bytes = _positive_limit(
            max_text_bytes
            if max_text_bytes is not None
            else _config_value(
                self.config,
                ("agent_max_text_bytes", "max_text_bytes", "text_max_bytes", "max_text_buffer_bytes"),
                DEFAULT_MAX_TEXT_BYTES,
            ),
            default=DEFAULT_MAX_TEXT_BYTES,
            maximum=4 * 1024 * 1024,
        )
        self.max_tool_argument_bytes = _positive_limit(
            max_tool_argument_bytes
            if max_tool_argument_bytes is not None
            else _config_value(
                self.config,
                (
                    "agent_max_tool_argument_bytes",
                    "max_tool_argument_bytes",
                    "tool_argument_max_bytes",
                    "max_tool_args_bytes",
                ),
                DEFAULT_MAX_TOOL_ARGUMENT_BYTES,
            ),
            default=DEFAULT_MAX_TOOL_ARGUMENT_BYTES,
            maximum=4 * 1024 * 1024,
        )
        self.max_raw_buffer_bytes = _positive_limit(
            max_raw_buffer_bytes
            if max_raw_buffer_bytes is not None
            else _config_value(
                self.config,
                (
                    "agent_max_raw_buffer_bytes",
                    "max_raw_buffer_bytes",
                    "raw_buffer_max_bytes",
                    "max_raw_bytes",
                ),
                DEFAULT_MAX_RAW_BUFFER_BYTES,
            ),
            default=DEFAULT_MAX_RAW_BUFFER_BYTES,
            maximum=8 * 1024 * 1024,
        )
        configured_reasoning_fields = (
            reasoning_fields
            if reasoning_fields is not None
            else _config_value(
                self.config,
                ("agent_reasoning_fields", "reasoning_fields"),
                (),
            )
        )
        if isinstance(configured_reasoning_fields, str):
            configured_reasoning_fields = (configured_reasoning_fields,)
        elif not isinstance(configured_reasoning_fields, (list, tuple)):
            configured_reasoning_fields = ()
        fields: list[str] = []
        for value in configured_reasoning_fields:
            field_name = str(value or "").strip()
            if field_name and field_name not in fields:
                fields.append(field_name)
        self.reasoning_fields = tuple(fields)

    @property
    def capability(self) -> AgentModelCapability:
        """The configured capability declaration, without probing the model."""

        return _configured_capability(self.config)

    @property
    def effective_capability(self) -> AgentModelCapability:
        """The request mode enforced by this adapter instance."""

        return _request_capability(self.config)

    def _request_body(self, request: AgentTurnRequest) -> dict[str, Any]:
        configured_model = str(self.config.get("model") or "").strip()
        if not configured_model:
            # ``RunContext.model_config_id`` is an identifier, never a model
            # name fallback.  Sending it to a gateway would select an
            # unintended model when a configuration is incomplete.
            raise ModelAdapterError("model is not configured")
        messages = [dict(message) for message in request.messages]
        system_prompt = str(request.system_prompt or "")
        if system_prompt:
            already_system = bool(messages) and messages[0].get("role") == "system"
            if not already_system:
                messages.insert(0, {"role": "system", "content": system_prompt})
        body: dict[str, Any] = {
            "model": configured_model,
            "messages": messages,
            "stream": True,
        }
        capability = self.effective_capability
        if capability is AgentModelCapability.NATIVE_TOOLS and request.tools:
            body["tools"] = [dict(tool) for tool in request.tools]
        elif capability is AgentModelCapability.STRICT_JSON:
            # Strict JSON execution is intentionally not enabled yet.  The
            # request remains structured so a later parser can be introduced,
            # while _build_turn below still refuses every tool call in this
            # mode.  This is a safe no-execute downgrade, never a text parser
            # that extracts JSON from arbitrary prose.
            body["response_format"] = _strict_response_format(self.config)
        output_tokens = request.max_output_tokens
        if output_tokens is None:
            output_tokens = self.config.get("max_tokens")
        if output_tokens is not None:
            try:
                value = int(output_tokens)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                body["max_tokens"] = value

        # Gateways differ in their optional parameter names.  Only pass a
        # parameter when the selected config explicitly supplied it.
        for key in ("temperature", "top_p", "seed", "reasoning_effort", "enable_reasoning", "reasoning"):
            if key in self.config and self.config[key] is not None:
                body[key] = self.config[key]
        if capability is AgentModelCapability.NATIVE_TOOLS:
            response_format = self.config.get("response_format")
            if isinstance(response_format, Mapping):
                body["response_format"] = dict(response_format)
        stream_options = self.config.get("stream_options")
        if isinstance(stream_options, Mapping):
            body["stream_options"] = dict(stream_options)
        elif self.config.get("include_usage") is True:
            body["stream_options"] = {"include_usage": True}
        return body

    def _transport_source(self, body: Mapping[str, Any], cancellation: Any, *, deadline: float) -> Any:
        transport = self.transport
        method = getattr(transport, "stream", None)
        if not callable(method):
            method = getattr(transport, "request", None)
        if not callable(method) and callable(transport):
            method = transport
        if not callable(method):
            raise ModelTransportError("model transport has no stream method")
        return _call_transport_method(method, body, cancellation, deadline=deadline)

    def _source_with_deadline(
        self,
        body: Mapping[str, Any],
        cancellation: Any,
        *,
        deadline: float,
    ) -> Any:
        """Call a possibly blocking transport without exceeding the deadline."""

        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        stopped = threading.Event()

        def invoke() -> None:
            try:
                value = self._transport_source(body, cancellation, deadline=deadline)
            except Exception as exc:
                result = ("error", exc)
            else:
                result = ("value", value)
            if stopped.is_set() and result[0] == "value":
                close = getattr(result[1], "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
                return
            try:
                result_queue.put(result, timeout=0.05)
            except queue.Full:
                if result[0] == "value":
                    close = getattr(result[1], "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            pass

        thread = threading.Thread(
            target=invoke,
            name="pengtools-agent-model-open",
            daemon=True,
        )
        thread.start()
        try:
            while True:
                if _is_cancelled(cancellation):
                    raise ModelCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _DeadlineReached()
                try:
                    kind, value = result_queue.get(timeout=min(0.05, remaining))
                except queue.Empty:
                    continue
                if kind == "error":
                    raise value
                return value
        finally:
            stopped.set()

    def _emit(self, on_delta: Callable[[ModelDelta], None], event: Mapping[str, Any]) -> ModelDelta:
        kind = str(event.get("kind") or "")
        if kind in ("reasoning_delta", "text_delta"):
            delta = ModelDelta(kind=kind, text=str(event.get("text") or ""))
        elif kind == "tool_call_delta":
            delta = ModelDelta(
                kind=kind,
                call_id=event.get("call_id"),
                name=event.get("name"),
                arguments_delta=str(event.get("arguments_delta") or ""),
            )
        elif kind == "usage":
            usage = event.get("usage")
            delta = ModelDelta(kind=kind, usage=dict(usage) if isinstance(usage, Mapping) else {})
        elif kind == "turn_done":
            delta = ModelDelta(kind=kind)
        else:
            # Preserve a future/diagnostic event kind while keeping all data
            # in the contract's explicitly bounded fields.
            delta = ModelDelta(kind=kind or "stream_event", text=str(event.get("text") or ""))
        on_delta(delta)
        return delta

    @staticmethod
    def _partial_turn(text_parts: list[str], parser: ModelStreamParser) -> AgentTurn:
        try:
            raw_calls = parser.finalize_tool_calls()
            calls = tuple(
                ToolCall(call_id=str(item["call_id"]), name=str(item["name"]), arguments=item["arguments"])
                for item in raw_calls
            )
        except Exception:
            calls = ()
        return AgentTurn(
            text="".join(text_parts),
            tool_calls=calls,
            finish_reason="cancelled",
            usage=parser.usage,
            model_id=parser.model_id,
        )

    def _consume_mapping(
        self,
        parser: ModelStreamParser,
        value: Mapping[str, Any],
        on_delta: Callable[[ModelDelta], None],
        text_parts: list[str],
        cancellation: Any,
        *,
        deadline: float,
    ) -> tuple[bool, bool]:
        """Consume one decoded model payload; return (saw_event, done)."""

        saw_event = False
        for event in parser.feed_json(value):
            if _is_cancelled(cancellation):
                raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
            if time.monotonic() >= deadline:
                raise ModelDeadlineExceeded()
            delta = self._emit(on_delta, event)
            saw_event = True
            if delta.kind == "text_delta":
                text_parts.append(delta.text)
            if delta.kind == "turn_done":
                return saw_event, True
        return saw_event, False

    def _run_once(
        self,
        request: AgentTurnRequest,
        cancellation: Any,
        on_delta: Callable[[ModelDelta], None],
        *,
        deadline: float,
    ) -> AgentTurn:
        if _is_cancelled(cancellation):
            raise ModelCancelled()
        if time.monotonic() >= deadline:
            raise ModelDeadlineExceeded()
        parser = ModelStreamParser(
            reasoning_fields=self.reasoning_fields,
            max_event_bytes=self.max_raw_buffer_bytes,
            max_reasoning_bytes=self.max_reasoning_bytes,
            max_text_bytes=self.max_text_bytes,
            max_tool_argument_bytes=self.max_tool_argument_bytes,
        )
        text_parts: list[str] = []
        body = self._request_body(request)
        transport_cancellation = _DeadlineCancellation(cancellation, deadline)
        try:
            source = self._source_with_deadline(
                body,
                transport_cancellation,
                deadline=deadline,
            )
        except ModelCancelled:
            raise
        except _DeadlineReached as exc:
            raise ModelDeadlineExceeded() from exc
        except Exception as exc:
            raise _AttemptError(self._as_transport_error(exc), started=False) from exc

        # A decoded mapping is an explicit non-streaming fallback.  A complete
        # JSON byte body is handled by the same branch after the iterable is
        # drained; no typewriter-style fake deltas are generated.
        if isinstance(source, Mapping):
            on_delta(ModelDelta(kind="non_streaming", usage={"streaming": False}))
            self._consume_mapping(
                parser,
                source,
                on_delta,
                text_parts,
                cancellation,
                deadline=deadline,
            )
            if _is_cancelled(cancellation):
                raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
            if time.monotonic() >= deadline:
                raise ModelDeadlineExceeded()
            if not parser.done:
                on_delta(ModelDelta(kind="turn_done"))
            return self._build_turn(parser, text_parts, request)

        raw = bytearray()
        saw_event = False
        done = False
        started = False
        pump = _IteratorPump(source)
        pump.start()
        try:
            while True:
                try:
                    chunk = pump.next(deadline=deadline, cancellation=cancellation)
                except _DeadlineReached as exc:
                    raise ModelDeadlineExceeded() from exc
                except ModelCancelled:
                    raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
                except Exception as exc:
                    raise _AttemptError(self._as_transport_error(exc), started=started) from exc
                if chunk is _PUMP_DONE:
                    break
                started = True
                if _is_cancelled(cancellation):
                    raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
                if time.monotonic() >= deadline:
                    raise ModelDeadlineExceeded()
                if isinstance(chunk, Mapping):
                    if raw or saw_event:
                        raise ModelProtocolError("mixed JSON and stream response")
                    on_delta(ModelDelta(kind="non_streaming", usage={"streaming": False}))
                    self._consume_mapping(
                        parser,
                        chunk,
                        on_delta,
                        text_parts,
                        cancellation,
                        deadline=deadline,
                    )
                    saw_event = True
                    done = parser.done
                    break
                if isinstance(chunk, str):
                    encoded = chunk.encode("utf-8")
                elif isinstance(chunk, (bytes, bytearray, memoryview)):
                    encoded = bytes(chunk)
                else:
                    raise ModelProtocolError("model stream yielded a non-byte chunk")
                if len(encoded) > self.max_raw_buffer_bytes:
                    raise ModelLimitExceeded("model raw chunk exceeds the configured byte limit")
                # Keep a bounded plain-JSON fallback buffer only until the
                # first complete SSE event proves that this is a live stream.
                # Long-running token streams must not accumulate their entire
                # response merely to support the non-streaming path.
                for event in parser.feed(encoded):
                    if _is_cancelled(cancellation):
                        raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
                    if time.monotonic() >= deadline:
                        raise ModelDeadlineExceeded()
                    delta = self._emit(on_delta, event)
                    saw_event = True
                    if delta.kind == "text_delta":
                        text_parts.append(delta.text)
                    if delta.kind == "turn_done":
                        done = True
                        break
                if saw_event:
                    raw.clear()
                else:
                    if len(raw) + len(encoded) > self.max_raw_buffer_bytes:
                        raise ModelLimitExceeded("model raw buffer exceeds the configured byte limit")
                    raw.extend(encoded)
                if done:
                    break

            if not done:
                for event in parser.finish():
                    if _is_cancelled(cancellation):
                        raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
                    if time.monotonic() >= deadline:
                        raise ModelDeadlineExceeded()
                    delta = self._emit(on_delta, event)
                    saw_event = True
                    if delta.kind == "text_delta":
                        text_parts.append(delta.text)
                    if delta.kind == "turn_done":
                        done = True

            # A chunked plain JSON response is not an SSE stream.  Parse it
            # only after the full body is available and mark the fallback.
            if not saw_event and raw:
                if len(raw) > self.max_raw_buffer_bytes:
                    raise ModelLimitExceeded("model raw buffer exceeds the configured byte limit")
                value = _decode_json_bytes(bytes(raw))
                on_delta(ModelDelta(kind="non_streaming", usage={"streaming": False}))
                self._consume_mapping(
                    parser,
                    value,
                    on_delta,
                    text_parts,
                    cancellation,
                    deadline=deadline,
                )
                saw_event = True

            if _is_cancelled(cancellation):
                raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
            if time.monotonic() >= deadline:
                raise ModelDeadlineExceeded()
            if not saw_event:
                raise ModelProtocolError("model returned no data")
            if not done:
                on_delta(ModelDelta(kind="turn_done"))
            return self._build_turn(parser, text_parts, request)
        finally:
            pump.close()

    def _build_turn(self, parser: ModelStreamParser, text_parts: list[str], request: AgentTurnRequest) -> AgentTurn:
        try:
            raw_calls = parser.finalize_tool_calls()
            calls = tuple(
                ToolCall(call_id=str(item["call_id"]), name=str(item["name"]), arguments=item["arguments"])
                for item in raw_calls
            )
        except (ToolArgumentsError, StreamingError, ValueError) as exc:
            raise ModelProtocolError(str(exc)) from exc
        if calls and self.effective_capability is not AgentModelCapability.NATIVE_TOOLS:
            # A gateway must not be able to turn an omitted/strict request into
            # an executable call.  In strict-json mode the complete envelope
            # parser is deliberately not implemented yet, so fail closed.
            raise ModelProtocolError("tool calls require native_tools capability")
        configured_model = str(self.config.get("model") or "").strip()
        model_id = parser.model_id or configured_model or str(request.context.model_config_id or "")
        return AgentTurn(
            text="".join(text_parts),
            tool_calls=calls,
            finish_reason=parser.finish_reason or ("tool_calls" if calls else "stop"),
            usage=parser.usage,
            model_id=model_id,
        )

    @staticmethod
    def _as_transport_error(error: Exception) -> ModelAdapterError:
        if isinstance(error, ModelAdapterError):
            return error
        if isinstance(error, urllib.error.HTTPError):
            return ModelTransportError(f"model gateway HTTP {error.code}", status_code=error.code)
        return ModelTransportError(
            "model transport failed",
            status_code=_status_code(error),
            retryable=_retryable(error),
        )

    def complete_turn(
        self,
        request: AgentTurnRequest,
        *,
        cancellation: CancellationToken,
        on_delta: Callable[[ModelDelta], None],
    ) -> AgentTurn:
        """Run one model turn, delivering real deltas as chunks arrive."""

        if request is None:
            raise TypeError("request is required")
        callback = on_delta or (lambda _delta: None)
        deadline = time.monotonic() + self.deadline_seconds
        attempts = 0
        while True:
            if _is_cancelled(cancellation):
                raise ModelCancelled()
            if time.monotonic() >= deadline:
                raise ModelDeadlineExceeded()
            try:
                return self._run_once(
                    request,
                    cancellation,
                    callback,
                    deadline=deadline,
                )
            except ModelCancelled:
                raise
            except ModelDeadlineExceeded:
                raise
            except _AttemptError as wrapped:
                error = wrapped.error
                if (
                    not wrapped.started
                    and attempts < self.retry_count
                    and _retryable(error)
                    and time.monotonic() < deadline
                ):
                    attempts += 1
                    continue
                if isinstance(error, ModelAdapterError):
                    raise error
                raise ModelTransportError("model transport failed", status_code=_status_code(error)) from error
            except (SSEProtocolError, ToolArgumentsError, StreamingLimitError) as exc:
                if isinstance(exc, StreamingLimitError):
                    raise ModelLimitExceeded(str(exc)) from exc
                raise ModelProtocolError(str(exc)) from exc

    # Original requirement wording used this name before the shared contract
    # settled on ``complete_turn``.  Keep the alias for early callers.
    complete_agent_turn = complete_turn


class UrlLibStreamingTransport:
    """Default streaming transport using the existing intranet policy."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = _mapping_config(config)

    def stream(
        self,
        body: Mapping[str, Any],
        *,
        cancellation: Any,
        deadline: float | None = None,
    ) -> Iterator[bytes]:
        # Import lazily so protocol-only tests never read model configuration or
        # instantiate network helpers.
        from tools import intranet_llm

        if _is_cancelled(cancellation):
            return
        cfg = self.config
        if not cfg:
            cfg = intranet_llm.load_ai_local()
        if not cfg.get("enabled"):
            raise ModelTransportError("intranet model is disabled")
        base_url = cfg.get("base_url") or ""
        url = intranet_llm._join(base_url, "chat/completions")
        intranet_llm.resolve_private_host(intranet_llm._hostname(url))
        headers = intranet_llm.build_headers(cfg)
        headers["Accept"] = "text/event-stream, application/json"
        payload = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        timeout = _finite_float(
            _config_value(cfg, ("read_timeout_seconds", "timeout_seconds"), 60.0),
            default=60.0,
            minimum=0.01,
            maximum=60.0,
        )
        local_deadline = deadline if deadline is not None else time.monotonic() + timeout
        response = None
        watcher_stop = threading.Event()
        watcher: threading.Thread | None = None
        open_stop = threading.Event()
        open_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def close_response() -> None:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        def cancel_watcher() -> None:
            # urllib's read call can be blocked while the server is silent.
            # Polling the host-owned token lets cancellation close the socket
            # without exposing a transport object to the UI thread.
            while not watcher_stop.wait(0.05):
                if _is_cancelled(cancellation) or time.monotonic() >= local_deadline:
                    open_stop.set()
                    close_response()
                    return

        def open_response() -> None:
            if open_stop.is_set() or _is_cancelled(cancellation) or time.monotonic() >= local_deadline:
                return
            try:
                value = opener.open(
                    request,
                    timeout=max(0.01, min(timeout, local_deadline - time.monotonic())),
                )
            except Exception as exc:
                result = ("error", exc)
            else:
                result = ("value", value)
            if open_stop.is_set() or _is_cancelled(cancellation) or time.monotonic() >= local_deadline:
                if result[0] == "value":
                    close = getattr(result[1], "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            pass
                return
            try:
                open_queue.put(result, timeout=0.05)
            except queue.Full:
                if result[0] == "value":
                    close = getattr(result[1], "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            pass

        try:
            opener = intranet_llm._direct_opener(bool(cfg.get("ssl_verify", True)))
            opener_thread = threading.Thread(
                target=open_response,
                name="pengtools-agent-model-open-url",
                daemon=True,
            )
            opener_thread.start()
            while True:
                if _is_cancelled(cancellation):
                    open_stop.set()
                    return
                remaining = local_deadline - time.monotonic()
                if remaining <= 0:
                    open_stop.set()
                    raise ModelDeadlineExceeded()
                try:
                    kind, value = open_queue.get(timeout=min(0.05, remaining))
                except queue.Empty:
                    continue
                if kind == "error":
                    raise value
                response = value
                break
            watcher = threading.Thread(
                target=cancel_watcher,
                name="pengtools-agent-model-cancel",
                daemon=True,
            )
            watcher.start()
            reader = _response_reader(response)
            if reader is None:
                raise ModelTransportError("model response has no readable stream")
            while True:
                if _is_cancelled(cancellation):
                    return
                if time.monotonic() >= local_deadline:
                    raise ModelDeadlineExceeded()
                try:
                    # read1/read from the underlying response is deliberately
                    # small so the first SSE event can reach the adapter
                    # before the server closes or fills a large buffer.
                    chunk = reader(8 * 1024)
                except (socket.timeout, TimeoutError):
                    if _is_cancelled(cancellation):
                        return
                    if time.monotonic() >= local_deadline:
                        raise ModelDeadlineExceeded()
                    continue
                except OSError as exc:
                    if _is_cancelled(cancellation):
                        return
                    if time.monotonic() >= local_deadline:
                        raise ModelDeadlineExceeded()
                    raise ModelTransportError("model stream read failed", retryable=True) from exc
                if not chunk:
                    return
                yield chunk
        except urllib.error.HTTPError as exc:
            raise ModelTransportError(f"model gateway HTTP {exc.code}", status_code=exc.code) from exc
        except urllib.error.URLError as exc:
            raise ModelTransportError("cannot connect to intranet model", retryable=True) from exc
        except ModelTransportError:
            raise
        except TimeoutError as exc:
            raise ModelTransportError("model gateway timed out", retryable=True) from exc
        except OSError as exc:
            if _is_cancelled(cancellation):
                return
            raise ModelTransportError("model transport failed", retryable=True) from exc
        finally:
            watcher_stop.set()
            close_response()
            if watcher is not None and watcher.is_alive():
                watcher.join(0.2)


__all__ = [
    "ModelAdapter",
    "ModelAdapterError",
    "ModelCancelled",
    "ModelDeadlineExceeded",
    "ModelLimitExceeded",
    "ModelProtocolError",
    "ModelTransportError",
    "StreamingTransport",
    "UrlLibStreamingTransport",
]
