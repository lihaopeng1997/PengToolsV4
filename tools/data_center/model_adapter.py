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
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol

from .contracts import AgentTurn, AgentTurnRequest, CancellationToken, ModelDelta, ToolCall
from .streaming import (
    ModelStreamParser,
    SSEProtocolError,
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


def _call_transport_method(method: Callable[..., Any], body: Mapping[str, Any], cancellation: Any) -> Any:
    """Call common fake/HTTP transport signatures without masking its errors."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        # Most Python test doubles accept this spelling.  A fallback to a
        # positional token keeps simple two-argument callables usable.
        try:
            return method(body, cancellation=cancellation)
        except TypeError:
            return method(body, cancellation)

    parameters = signature.parameters
    if "cancellation" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    ):
        return method(body, cancellation=cancellation)
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters.values()) or len(positional) >= 2:
        return method(body, cancellation)
    return method(body)


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
    ) -> None:
        self.config = _mapping_config(config)
        if model_id or model:
            self.config["model"] = str(model_id or model)
        self.transport = transport if transport is not None else UrlLibStreamingTransport(self.config)
        self.retry_count = max(0, min(2, int(retry_count)))

    def _request_body(self, request: AgentTurnRequest) -> dict[str, Any]:
        configured_model = str(self.config.get("model") or "").strip()
        messages = [dict(message) for message in request.messages]
        system_prompt = str(request.system_prompt or "")
        if system_prompt:
            already_system = bool(messages) and messages[0].get("role") == "system"
            if not already_system:
                messages.insert(0, {"role": "system", "content": system_prompt})
        body: dict[str, Any] = {
            "model": configured_model or str(request.context.model_config_id or ""),
            "messages": messages,
            "stream": True,
        }
        if request.tools:
            body["tools"] = [dict(tool) for tool in request.tools]
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
        for key in ("temperature", "top_p", "seed", "response_format", "reasoning_effort", "enable_reasoning", "reasoning"):
            if key in self.config and self.config[key] is not None:
                body[key] = self.config[key]
        stream_options = self.config.get("stream_options")
        if isinstance(stream_options, Mapping):
            body["stream_options"] = dict(stream_options)
        elif self.config.get("include_usage") is True:
            body["stream_options"] = {"include_usage": True}
        return body

    def _transport_source(self, body: Mapping[str, Any], cancellation: Any) -> Any:
        transport = self.transport
        method = getattr(transport, "stream", None)
        if not callable(method):
            method = getattr(transport, "request", None)
        if not callable(method) and callable(transport):
            method = transport
        if not callable(method):
            raise ModelTransportError("model transport has no stream method")
        return _call_transport_method(method, body, cancellation)

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
    ) -> tuple[bool, bool]:
        """Consume one decoded model payload; return (saw_event, done)."""

        saw_event = False
        for event in parser.feed_json(value):
            if _is_cancelled(cancellation):
                raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
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
    ) -> AgentTurn:
        if _is_cancelled(cancellation):
            raise ModelCancelled()
        parser = ModelStreamParser()
        text_parts: list[str] = []
        body = self._request_body(request)
        try:
            source = self._transport_source(body, cancellation)
        except Exception as exc:
            raise _AttemptError(self._as_transport_error(exc), started=False) from exc

        # A decoded mapping is an explicit non-streaming fallback.  A complete
        # JSON byte body is handled by the same branch after the iterable is
        # drained; no typewriter-style fake deltas are generated.
        if isinstance(source, Mapping):
            on_delta(ModelDelta(kind="non_streaming", usage={"streaming": False}))
            self._consume_mapping(parser, source, on_delta, text_parts, cancellation)
            if _is_cancelled(cancellation):
                raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
            if not parser.done:
                on_delta(ModelDelta(kind="turn_done"))
            return self._build_turn(parser, text_parts, request)

        raw = bytearray()
        saw_event = False
        done = False
        iterator: Iterator[Any] | None = None
        started = False
        try:
            try:
                iterator = iter(_response_chunks(source))
            except Exception as exc:
                raise _AttemptError(self._as_transport_error(exc), started=False) from exc
            while True:
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                except Exception as exc:
                    raise _AttemptError(self._as_transport_error(exc), started=started) from exc
                started = True
                if _is_cancelled(cancellation):
                    raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
                if isinstance(chunk, Mapping):
                    if raw or saw_event:
                        raise ModelProtocolError("mixed JSON and stream response")
                    on_delta(ModelDelta(kind="non_streaming", usage={"streaming": False}))
                    self._consume_mapping(parser, chunk, on_delta, text_parts, cancellation)
                    saw_event = True
                    done = parser.done
                    break
                if isinstance(chunk, str):
                    encoded = chunk.encode("utf-8")
                elif isinstance(chunk, (bytes, bytearray, memoryview)):
                    encoded = bytes(chunk)
                else:
                    raise ModelProtocolError("model stream yielded a non-byte chunk")
                # Keep a bounded plain-JSON fallback buffer only until the
                # first complete SSE event proves that this is a live stream.
                # Long-running token streams must not accumulate their entire
                # response merely to support the non-streaming path.
                if not saw_event:
                    raw.extend(encoded)
                for event in parser.feed(encoded):
                    if _is_cancelled(cancellation):
                        raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
                    delta = self._emit(on_delta, event)
                    saw_event = True
                    if delta.kind == "text_delta":
                        text_parts.append(delta.text)
                    if delta.kind == "turn_done":
                        done = True
                        break
                if saw_event:
                    raw.clear()
                if done:
                    break

            if not done:
                for event in parser.finish():
                    if _is_cancelled(cancellation):
                        raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
                    delta = self._emit(on_delta, event)
                    saw_event = True
                    if delta.kind == "text_delta":
                        text_parts.append(delta.text)
                    if delta.kind == "turn_done":
                        done = True

            # A chunked plain JSON response is not an SSE stream.  Parse it
            # only after the full body is available and mark the fallback.
            if not saw_event and raw:
                value = _decode_json_bytes(bytes(raw))
                on_delta(ModelDelta(kind="non_streaming", usage={"streaming": False}))
                self._consume_mapping(parser, value, on_delta, text_parts, cancellation)
                saw_event = True

            if _is_cancelled(cancellation):
                raise ModelCancelled(partial_turn=self._partial_turn(text_parts, parser))
            if not saw_event:
                raise ModelProtocolError("model returned no data")
            if not done:
                on_delta(ModelDelta(kind="turn_done"))
            return self._build_turn(parser, text_parts, request)
        finally:
            if iterator is not None:
                close = getattr(iterator, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass

    def _build_turn(self, parser: ModelStreamParser, text_parts: list[str], request: AgentTurnRequest) -> AgentTurn:
        try:
            raw_calls = parser.finalize_tool_calls()
            calls = tuple(
                ToolCall(call_id=str(item["call_id"]), name=str(item["name"]), arguments=item["arguments"])
                for item in raw_calls
            )
        except (ToolArgumentsError, StreamingError, ValueError) as exc:
            raise ModelProtocolError(str(exc)) from exc
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
        attempts = 0
        while True:
            try:
                return self._run_once(request, cancellation, callback)
            except ModelCancelled:
                raise
            except _AttemptError as wrapped:
                error = wrapped.error
                if (
                    not wrapped.started
                    and attempts < self.retry_count
                    and _retryable(error)
                ):
                    attempts += 1
                    continue
                if isinstance(error, ModelAdapterError):
                    raise error
                raise ModelTransportError("model transport failed", status_code=_status_code(error)) from error
            except (SSEProtocolError, ToolArgumentsError) as exc:
                raise ModelProtocolError(str(exc)) from exc

    # Original requirement wording used this name before the shared contract
    # settled on ``complete_turn``.  Keep the alias for early callers.
    complete_agent_turn = complete_turn


class UrlLibStreamingTransport:
    """Default streaming transport using the existing intranet policy."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = _mapping_config(config)

    def stream(self, body: Mapping[str, Any], *, cancellation: Any) -> Iterator[bytes]:
        # Import lazily so protocol-only tests never read model configuration or
        # instantiate network helpers.
        from tools import intranet_llm

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
        timeout = int(cfg.get("timeout_seconds") or 120)
        try:
            opener = intranet_llm._direct_opener(bool(cfg.get("ssl_verify", True)))
            with opener.open(request, timeout=timeout) as response:
                while True:
                    if _is_cancelled(cancellation):
                        return
                    chunk = response.read(64 * 1024)
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
            raise ModelTransportError("model transport failed", retryable=True) from exc


__all__ = [
    "ModelAdapter",
    "ModelAdapterError",
    "ModelCancelled",
    "ModelProtocolError",
    "ModelTransportError",
    "StreamingTransport",
    "UrlLibStreamingTransport",
]
