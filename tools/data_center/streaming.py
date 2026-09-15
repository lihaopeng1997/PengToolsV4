"""Incremental, UI-independent parsing for the data-center model stream.

The data-center Agent receives an OpenAI-compatible Server-Sent Events (SSE)
response.  A response is allowed to be split at any byte boundary: a chunk
can end in the middle of an UTF-8 code point, an SSE line, a JSON document, or
the arguments for a tool call.  This module keeps those boundaries out of the
consumer-facing protocol.

Only the explicit model fields used by the data-center UI are exposed.  In
particular, text that merely looks like JSON (or a ``<think>`` tag) is kept as
text and never becomes a tool call.

This module intentionally has no Qt, database, network, or data-center
``contracts`` dependency.  The small dictionaries returned by
``ModelStreamParser`` are the hand-off format for the adapter layer; the
adapter can translate them to the shared contracts when that layer is wired.
"""

from __future__ import annotations

from codecs import getincrementaldecoder
from dataclasses import dataclass
import json
from typing import Any, Iterable, Iterator, Mapping


class StreamingError(ValueError):
    """Base error for malformed or incomplete stream data."""


class SSEProtocolError(StreamingError):
    """Raised when an SSE event exceeds the configured boundary or is invalid."""


class ToolArgumentsError(StreamingError):
    """Raised when a completed tool call does not contain JSON object arguments."""


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """One dispatched SSE event.

    ``data`` contains the SSE ``data:`` fields joined by a newline, as
    required by the SSE protocol.  Comment heartbeats never produce an
    event.  ``event`` defaults to ``message`` when the wire event omitted an
    event name.
    """

    data: str
    event: str = "message"
    event_id: str | None = None
    retry: int | None = None

    @property
    def id(self) -> str | None:
        """Alias matching the common SSE client spelling."""

        return self.event_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "event": self.event,
            "id": self.event_id,
            "retry": self.retry,
        }


class IncrementalSSEDecoder:
    """Decode arbitrary byte chunks into complete :class:`SSEEvent` values.

    UTF-8 decoding is incremental, so a split multi-byte character is not
    decoded until all its bytes have arrived.  The line reader accepts LF, CR,
    and CRLF boundaries.  ``finish`` flushes the decoder and dispatches a
    final event when a peer closed the response without a trailing blank line.
    """

    def __init__(self, *, max_event_chars: int = 2 * 1024 * 1024) -> None:
        if max_event_chars <= 0:
            raise ValueError("max_event_chars must be positive")
        self.max_event_chars = int(max_event_chars)
        self._decoder = getincrementaldecoder("utf-8")(errors="replace")
        self._line = ""
        self._skip_lf = False
        self._data: list[str] = []
        self._event_name = ""
        self._event_id: str | None = None
        self._retry: int | None = None
        self._last_event_id: str | None = None
        self._finished = False

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def last_event_id(self) -> str | None:
        return self._last_event_id

    def feed(self, chunk: bytes | bytearray | memoryview | str) -> list[SSEEvent]:
        """Consume one arbitrary chunk and return newly completed events."""

        if self._finished:
            raise RuntimeError("cannot feed an SSE decoder after finish")
        if isinstance(chunk, str):
            # This is useful for deterministic unit fixtures.  Production
            # transports should pass bytes so UTF-8 boundaries stay visible.
            raw = chunk.encode("utf-8")
        elif isinstance(chunk, (bytes, bytearray, memoryview)):
            raw = bytes(chunk)
        else:
            raise TypeError("SSE chunks must be bytes-like or str")
        if not raw:
            return []
        text = self._decoder.decode(raw, final=False)
        return self._feed_text(text)

    def _feed_text(self, text: str) -> list[SSEEvent]:
        events: list[SSEEvent] = []
        for character in text:
            if character == "\r":
                events.extend(self._finish_line())
                self._skip_lf = True
                continue
            if character == "\n":
                if self._skip_lf:
                    # The CR already terminated this line.
                    self._skip_lf = False
                    continue
                events.extend(self._finish_line())
                continue
            self._skip_lf = False
            self._line += character
            # A data field can be split over many chunks, so this bound is
            # checked on the line itself as well as on the accumulated event.
            if len(self._line) > self.max_event_chars:
                raise SSEProtocolError("SSE line exceeds the configured limit")
        return events

    def _finish_line(self) -> list[SSEEvent]:
        line = self._line
        self._line = ""
        return self._consume_line(line)

    def _consume_line(self, line: str) -> list[SSEEvent]:
        if line == "":
            return self._dispatch_event()
        if line.startswith(":"):
            # Comment lines are normally heartbeat keep-alives.  They do not
            # dispatch an SSE event and must not reset an accumulated event.
            return []

        if ":" in line:
            field, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""

        if field == "data":
            self._data.append(value)
            if sum(len(item) for item in self._data) + max(0, len(self._data) - 1) > self.max_event_chars:
                raise SSEProtocolError("SSE event exceeds the configured limit")
        elif field == "event":
            self._event_name = value
        elif field == "id":
            # The SSE spec ignores an id containing NUL.  Keeping that rule
            # avoids making a malformed wire value look like a real call id.
            if "\x00" not in value:
                self._event_id = value
                self._last_event_id = value
        elif field == "retry":
            try:
                retry = int(value)
            except (TypeError, ValueError):
                retry = None
            if retry is not None and retry >= 0:
                self._retry = retry
        # Unknown fields are intentionally ignored by SSE.
        return []

    def _dispatch_event(self) -> list[SSEEvent]:
        if not self._data:
            # Event fields without data do not dispatch.  They are cleared at
            # a blank line just like a normal event.
            self._event_name = ""
            self._event_id = None
            self._retry = None
            return []
        event = SSEEvent(
            data="\n".join(self._data),
            event=self._event_name or "message",
            event_id=self._event_id,
            retry=self._retry,
        )
        self._data = []
        self._event_name = ""
        self._event_id = None
        self._retry = None
        return [event]

    def finish(self) -> list[SSEEvent]:
        """Flush the UTF-8 and line buffers and close the decoder."""

        if self._finished:
            return []
        events: list[SSEEvent] = []
        tail = self._decoder.decode(b"", final=True)
        if tail:
            events.extend(self._feed_text(tail))
        # A response is allowed to end after data without an extra blank
        # line.  Treat the unterminated line as a normal line, then dispatch
        # the accumulated data event.
        if self._line:
            events.extend(self._finish_line())
        events.extend(self._dispatch_event())
        self._finished = True
        return events


# Common spellings used by callers and tests.
SSEParser = IncrementalSSEDecoder
SseParser = IncrementalSSEDecoder


def iter_sse_events(chunks: Iterable[bytes | bytearray | memoryview | str]) -> Iterator[SSEEvent]:
    """Yield complete SSE events from an iterable of arbitrary chunks."""

    decoder = IncrementalSSEDecoder()
    for chunk in chunks:
        yield from decoder.feed(chunk)
    yield from decoder.finish()


@dataclass(slots=True)
class _ToolAccumulator:
    index: int
    order: int
    call_id: str = ""
    name: str = ""
    arguments: str = ""


def _text_content(value: Any) -> str:
    """Extract explicit text parts without interpreting their contents."""

    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and item.get("type") in ("text", "output_text"):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _reasoning_content(delta: Mapping[str, Any]) -> str:
    """Read only fields that gateways document as reasoning channels."""

    for key in ("reasoning_content", "reasoning_summary"):
        value = delta.get(key)
        text = _text_content(value)
        if text:
            return text
    # A few OpenAI-compatible gateways nest the same explicit field under a
    # reasoning object.  Arbitrary ``thinking``/``thought`` text is purposely
    # not treated as a reasoning channel.
    nested = delta.get("reasoning")
    if isinstance(nested, Mapping):
        for key in ("content", "summary", "reasoning_content", "reasoning_summary"):
            text = _text_content(nested.get(key))
            if text:
                return text
    elif isinstance(nested, str):
        return nested
    return ""


class ModelStreamParser:
    """Turn OpenAI-compatible SSE payloads into display and call deltas.

    ``feed`` only returns incremental events.  Tool arguments are accumulated
    privately; call :meth:`finalize_tool_calls` after the stream is complete to
    obtain executable-shaped dictionaries.  This makes it impossible for a
    consumer of incremental events to mistake a half JSON argument for a
    complete call.
    """

    def __init__(self, *, max_event_chars: int = 2 * 1024 * 1024) -> None:
        self.sse = IncrementalSSEDecoder(max_event_chars=max_event_chars)
        self._tools: dict[int, _ToolAccumulator] = {}
        self._next_order = 0
        self._usage: dict[str, Any] = {}
        self._finish_reason: str | None = None
        self._model_id = ""
        self._done = False
        self._finished = False

    @property
    def usage(self) -> dict[str, Any]:
        return dict(self._usage)

    @property
    def finish_reason(self) -> str | None:
        return self._finish_reason

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def done(self) -> bool:
        return self._done

    def feed(self, chunk: bytes | bytearray | memoryview | str) -> list[dict[str, Any]]:
        if self._finished:
            raise RuntimeError("cannot feed a model parser after finish")
        if self._done:
            # A well-behaved server closes after [DONE].  Ignore late bytes so
            # they cannot append content to a completed run.
            return []
        result: list[dict[str, Any]] = []
        for event in self.sse.feed(chunk):
            result.extend(self._consume_sse_event(event))
        return result

    def _consume_sse_event(self, event: SSEEvent) -> list[dict[str, Any]]:
        payload = event.data.strip()
        if not payload:
            return []
        if payload == "[DONE]":
            self._done = True
            return [{"kind": "turn_done"}]
        try:
            value = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise SSEProtocolError("SSE data is not valid JSON") from exc
        if not isinstance(value, Mapping):
            raise SSEProtocolError("SSE JSON payload must be an object")
        return self._consume_payload(value)

    def feed_json(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Consume one already-decoded JSON payload (useful for tests)."""

        if self._finished or self._done:
            return []
        if not isinstance(payload, Mapping):
            raise SSEProtocolError("model JSON payload must be an object")
        return self._consume_payload(payload)

    def _consume_payload(self, value: Mapping[str, Any]) -> list[dict[str, Any]]:
        error = value.get("error")
        if error:
            if isinstance(error, Mapping):
                detail = str(error.get("message") or error.get("code") or "model error")
            else:
                detail = str(error)
            raise SSEProtocolError(detail)

        result: list[dict[str, Any]] = []
        model = value.get("model")
        if model:
            self._model_id = str(model)

        usage = value.get("usage")
        if isinstance(usage, Mapping):
            self._usage.update(dict(usage))
            result.append({"kind": "usage", "usage": dict(usage)})

        choices = value.get("choices")
        if not isinstance(choices, list):
            return result
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            reason = choice.get("finish_reason")
            if reason is not None:
                self._finish_reason = str(reason)
            delta = choice.get("delta")
            if not isinstance(delta, Mapping):
                delta = choice.get("message")
            if not isinstance(delta, Mapping):
                continue

            reasoning = _reasoning_content(delta)
            if reasoning:
                result.append({"kind": "reasoning_delta", "text": reasoning})

            text = _text_content(delta.get("content"))
            if text:
                result.append({"kind": "text_delta", "text": text})

            calls = delta.get("tool_calls")
            if isinstance(calls, list):
                for position, call in enumerate(calls):
                    if isinstance(call, Mapping):
                        result.extend(self._consume_tool_delta(call, position))

            # Older OpenAI-compatible gateways use function_call rather than
            # the indexed tool_calls array.  It still follows the same
            # complete-JSON rule and is kept on index zero.
            function_call = delta.get("function_call")
            if isinstance(function_call, Mapping):
                result.extend(self._consume_tool_delta(function_call, 0))
        return result

    def _consume_tool_delta(self, call: Mapping[str, Any], position: int) -> list[dict[str, Any]]:
        raw_index = call.get("index", position)
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            raise ToolArgumentsError("tool call index is invalid")
        state = self._tools.get(index)
        if state is None:
            state = _ToolAccumulator(index=index, order=self._next_order)
            self._next_order += 1
            self._tools[index] = state

        call_id = call.get("id", call.get("call_id"))
        if call_id:
            call_id = str(call_id)
            if state.call_id and state.call_id != call_id:
                raise ToolArgumentsError("tool call id changed for one index")
            state.call_id = call_id

        function = call.get("function")
        if not isinstance(function, Mapping):
            function = call
        name = function.get("name")
        if name:
            name = str(name)
            if state.name and state.name != name:
                raise ToolArgumentsError("tool name changed for one index")
            state.name = name

        fragment = function.get("arguments", call.get("arguments"))
        if fragment is None:
            fragment = ""
        if isinstance(fragment, Mapping) or isinstance(fragment, list):
            # A fully decoded argument object is accepted for compatibility;
            # normal streaming fragments remain strings.
            fragment = json.dumps(fragment, ensure_ascii=False, separators=(",", ":"))
        elif not isinstance(fragment, str):
            raise ToolArgumentsError("tool arguments fragment must be text")
        # Gateways/proxies occasionally replay the last complete tool frame.
        # Once the accumulated buffer is already one valid JSON object, an
        # identical full fragment is a duplicate frame rather than a second
        # argument.  Ignore that exact replay; any different trailing value
        # remains a protocol error at finalization.
        if fragment and state.arguments.strip():
            try:
                existing = json.loads(state.arguments)
            except (TypeError, ValueError):
                existing = None
            if isinstance(existing, Mapping) and fragment.strip() == state.arguments.strip():
                fragment = ""
        state.arguments += fragment
        return [{
            "kind": "tool_call_delta",
            "index": index,
            "call_id": state.call_id or None,
            "name": state.name or None,
            "arguments_delta": fragment,
        }]

    def finish(self) -> list[dict[str, Any]]:
        """Flush pending SSE bytes and mark the parser complete."""

        if self._finished:
            return []
        result: list[dict[str, Any]] = []
        if not self._done:
            for event in self.sse.finish():
                result.extend(self._consume_sse_event(event))
        else:
            # Still close the underlying decoder; late bytes are ignored at
            # the model layer but the UTF-8 state must be released.
            self.sse.finish()
        self._finished = True
        return result

    def finalize_tool_calls(self) -> tuple[dict[str, Any], ...]:
        """Validate and return complete tool calls in arrival order.

        No call is returned until all its accumulated argument text parses as
        one JSON object.  A malformed or incomplete call raises and returns no
        partially executable value.
        """

        calls: list[dict[str, Any]] = []
        by_id: dict[str, str] = {}
        for state in sorted(self._tools.values(), key=lambda item: item.order):
            if not state.call_id:
                raise ToolArgumentsError("tool call is missing an id")
            if not state.name:
                raise ToolArgumentsError("tool call is missing a name")
            raw = state.arguments.strip()
            if not raw:
                arguments: Mapping[str, Any] = {}
            else:
                try:
                    arguments = json.loads(raw)
                except (TypeError, ValueError) as exc:
                    raise ToolArgumentsError(
                        f"tool arguments are incomplete or invalid for {state.call_id}"
                    ) from exc
            if not isinstance(arguments, Mapping):
                raise ToolArgumentsError(f"tool arguments must be a JSON object for {state.call_id}")
            canonical = json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            previous = by_id.get(state.call_id)
            if previous is not None:
                if previous != canonical:
                    raise ToolArgumentsError("same tool call id was used with different arguments")
                continue
            by_id[state.call_id] = canonical
            calls.append({
                "index": state.index,
                "call_id": state.call_id,
                "name": state.name,
                "arguments": dict(arguments),
            })
        return tuple(calls)

    # Friendly aliases for adapter code that calls the operation ``finalize``.
    finalize = finalize_tool_calls


IncrementalModelParser = ModelStreamParser
OpenAIStreamParser = ModelStreamParser


__all__ = [
    "IncrementalModelParser",
    "IncrementalSSEDecoder",
    "ModelStreamParser",
    "OpenAIStreamParser",
    "SSEEvent",
    "SSEParser",
    "SSEProtocolError",
    "SseParser",
    "StreamingError",
    "ToolArgumentsError",
    "iter_sse_events",
]
