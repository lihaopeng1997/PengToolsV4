"""Anthropic Messages 协议兼容层。

软件内部（模型对话、Agent、数据中心）统一按 OpenAI Chat Completions 形状组织
请求和解析响应；当模型配置声明 ``api_protocol == 'anthropic'``（例如内网
Claude Code 网关）时，只在 HTTP 边界做双向转换：

- ``to_anthropic_request``：OpenAI 请求体 -> ``POST /v1/messages`` 请求体
- ``from_anthropic_response``：非流式响应 -> OpenAI ``chat.completion`` 形状
- ``AnthropicStreamTranslator``：Anthropic SSE 字节流 -> OpenAI SSE 字节流

本模块不读配置、不发网络请求、不接触 token，便于单测。
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

PROTOCOL_OPENAI = 'openai'
PROTOCOL_ANTHROPIC = 'anthropic'
API_PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)
ANTHROPIC_VERSION = '2023-06-01'
DEFAULT_MAX_TOKENS = 4096

_STRICT_JSON_HINT = '只输出一个 JSON 对象，不要输出任何其它文字或 Markdown 代码块。'

_FINISH_REASONS = {
    'end_turn': 'stop',
    'stop_sequence': 'stop',
    'max_tokens': 'length',
    'tool_use': 'tool_calls',
    'pause_turn': 'stop',
    'refusal': 'content_filter',
}

# 粘贴完整地址时剥掉的后缀（Claude Code 的 ANTHROPIC_BASE_URL 通常不带 /v1）
_ENDPOINT_SUFFIXES = ('/v1/messages', '/messages', '/v1/chat/completions', '/chat/completions')


def normalize_protocol(value: Any) -> str:
    text = str(value or '').strip().lower()
    return PROTOCOL_ANTHROPIC if text in ('anthropic', 'claude', 'messages') else PROTOCOL_OPENAI


def protocol_for_config(cfg: Mapping[str, Any] | None) -> str:
    if not isinstance(cfg, Mapping):
        return PROTOCOL_OPENAI
    if str(cfg.get('provider') or 'intranet').strip().lower() == 'deepseek':
        return PROTOCOL_OPENAI
    return normalize_protocol(cfg.get('api_protocol'))


def api_root(base_url: str) -> str:
    """把 ``http://h:p``、``http://h:p/v1`` 或完整 messages 地址统一成 ``.../v1``。"""
    text = str(base_url or '').strip().rstrip('/')
    lower = text.lower()
    for suffix in _ENDPOINT_SUFFIXES:
        if lower.endswith(suffix):
            text = text[: len(text) - len(suffix)]
            break
    text = text.rstrip('/')
    path = urlparse(text).path or ''
    return text if path.rstrip('/').lower().endswith('/v1') else text + '/v1'


def messages_url(base_url: str) -> str:
    return api_root(base_url) + '/messages'


def models_url(base_url: str) -> str:
    return api_root(base_url) + '/models'


def anthropic_headers(token: str) -> dict[str, str]:
    """Claude Code 网关两种鉴权写法都兼容：x-api-key 与 Bearer。"""
    headers = {'anthropic-version': ANTHROPIC_VERSION}
    if token:
        headers['x-api-key'] = token
        headers['Authorization'] = f'Bearer {token}'
    return headers


# ── 请求：OpenAI -> Anthropic ────────────────────────────────────────────


def _text_of(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, Mapping) and part.get('type') == 'text':
                parts.append(str(part.get('text') or ''))
            elif isinstance(part, str):
                parts.append(part)
        return ''.join(parts)
    return str(content)


def _image_block(part: Mapping[str, Any]) -> dict[str, Any] | None:
    image = part.get('image_url')
    url = image.get('url') if isinstance(image, Mapping) else image
    url = str(url or '')
    if url.startswith('data:') and ';base64,' in url:
        header, data = url.split(';base64,', 1)
        media_type = header[5:] or 'image/png'
        return {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': data}}
    if url:
        return {'type': 'image', 'source': {'type': 'url', 'url': url}}
    return None


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if content else []
    blocks: list[dict[str, Any]] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                if part:
                    blocks.append({'type': 'text', 'text': part})
            elif isinstance(part, Mapping):
                kind = part.get('type')
                if kind == 'text' and part.get('text'):
                    blocks.append({'type': 'text', 'text': str(part.get('text'))})
                elif kind == 'image_url':
                    block = _image_block(part)
                    if block:
                        blocks.append(block)
        return blocks
    text = str(content)
    return [{'type': 'text', 'text': text}] if text else []


def _tool_input(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, Mapping):
        return dict(arguments)
    try:
        value = json.loads(arguments or '{}')
    except (TypeError, ValueError):
        return {'_raw_arguments': str(arguments)}
    return value if isinstance(value, dict) else {'value': value}


def _append(messages: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    """Anthropic 要求 user/assistant 交替：同角色相邻消息合并。"""
    if not blocks:
        return
    if messages and messages[-1]['role'] == role:
        messages[-1]['content'].extend(blocks)
    else:
        messages.append({'role': role, 'content': list(blocks)})


def _convert_tools(tools: Any) -> list[dict[str, Any]]:
    converted = []
    for tool in tools or []:
        if not isinstance(tool, Mapping):
            continue
        fn = tool.get('function') if isinstance(tool.get('function'), Mapping) else tool
        name = str(fn.get('name') or '').strip()
        if not name:
            continue
        schema = fn.get('parameters') or fn.get('input_schema')
        if not isinstance(schema, Mapping):
            schema = {'type': 'object', 'properties': {}}
        item = {'name': name, 'input_schema': dict(schema)}
        if fn.get('description'):
            item['description'] = str(fn.get('description'))
        converted.append(item)
    return converted


def _convert_tool_choice(choice: Any) -> dict[str, Any] | None:
    if isinstance(choice, str):
        return {'auto': {'type': 'auto'}, 'required': {'type': 'any'}, 'any': {'type': 'any'}}.get(choice)
    if isinstance(choice, Mapping):
        fn = choice.get('function')
        name = fn.get('name') if isinstance(fn, Mapping) else choice.get('name')
        if name:
            return {'type': 'tool', 'name': str(name)}
    return None


def to_anthropic_request(body: Mapping[str, Any], *, default_max_tokens: int = DEFAULT_MAX_TOKENS) -> dict[str, Any]:
    system_parts: list[str] = []
    messages: list[dict[str, Any]] = []
    for message in body.get('messages') or []:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get('role') or '')
        content = message.get('content')
        if role in ('system', 'developer'):
            text = _text_of(content).strip()
            if text:
                system_parts.append(text)
        elif role == 'assistant':
            blocks = [b for b in _content_blocks(content) if b.get('type') == 'text']
            for call in message.get('tool_calls') or []:
                if not isinstance(call, Mapping):
                    continue
                fn = call.get('function') if isinstance(call.get('function'), Mapping) else {}
                blocks.append({
                    'type': 'tool_use',
                    'id': str(call.get('id') or ''),
                    'name': str(fn.get('name') or ''),
                    'input': _tool_input(fn.get('arguments')),
                })
            _append(messages, 'assistant', blocks)
        elif role == 'tool':
            _append(messages, 'user', [{
                'type': 'tool_result',
                'tool_use_id': str(message.get('tool_call_id') or ''),
                'content': _text_of(content) or '(empty)',
            }])
        else:
            _append(messages, 'user', _content_blocks(content))
    if not messages or messages[0]['role'] != 'user':
        messages.insert(0, {'role': 'user', 'content': [{'type': 'text', 'text': '（继续）'}]})

    response_format = body.get('response_format')
    if isinstance(response_format, Mapping) and response_format.get('type') in ('json_object', 'json_schema'):
        system_parts.append(_STRICT_JSON_HINT)

    try:
        max_tokens = int(body.get('max_tokens') or body.get('max_completion_tokens') or 0)
    except (TypeError, ValueError):
        max_tokens = 0
    request: dict[str, Any] = {
        'model': str(body.get('model') or '').strip(),
        'max_tokens': max_tokens if max_tokens > 0 else int(default_max_tokens),
        'messages': messages,
    }
    if system_parts:
        request['system'] = '\n\n'.join(system_parts)
    if body.get('stream'):
        request['stream'] = True
    for key in ('temperature', 'top_p'):
        if body.get(key) is not None:
            request[key] = body[key]
    stop = body.get('stop')
    if stop:
        request['stop_sequences'] = [stop] if isinstance(stop, str) else [str(s) for s in stop]
    tools = _convert_tools(body.get('tools'))
    if tools and body.get('tool_choice') != 'none':
        request['tools'] = tools
        choice = _convert_tool_choice(body.get('tool_choice'))
        if choice:
            request['tool_choice'] = choice
    return request


# ── 响应：Anthropic -> OpenAI ────────────────────────────────────────────


def _usage(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, Mapping):
        return None
    prompt = int(usage.get('input_tokens') or 0)
    completion = int(usage.get('output_tokens') or 0)
    return {'prompt_tokens': prompt, 'completion_tokens': completion, 'total_tokens': prompt + completion}


def _error_message(data: Mapping[str, Any]) -> str:
    error = data.get('error')
    if isinstance(error, Mapping):
        return str(error.get('message') or error.get('type') or '模型网关返回错误')
    return str(error or '模型网关返回错误')


def from_anthropic_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    if 'choices' in data:  # 网关已经是 OpenAI 形状
        return dict(data)
    if data.get('type') == 'error' or data.get('error'):
        return {'error': {'message': _error_message(data)}}
    texts: list[str] = []
    reasoning: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in data.get('content') or []:
        if not isinstance(block, Mapping):
            continue
        kind = block.get('type')
        if kind == 'text':
            texts.append(str(block.get('text') or ''))
        elif kind == 'thinking':
            reasoning.append(str(block.get('thinking') or ''))
        elif kind == 'tool_use':
            calls.append({
                'id': str(block.get('id') or ''),
                'type': 'function',
                'function': {
                    'name': str(block.get('name') or ''),
                    'arguments': json.dumps(block.get('input') or {}, ensure_ascii=False),
                },
            })
    message: dict[str, Any] = {'role': 'assistant', 'content': ''.join(texts)}
    if reasoning:
        message['reasoning_content'] = ''.join(reasoning)
    if calls:
        message['tool_calls'] = calls
    result: dict[str, Any] = {
        'id': str(data.get('id') or ''),
        'object': 'chat.completion',
        'model': str(data.get('model') or ''),
        'choices': [{
            'index': 0,
            'message': message,
            'finish_reason': _FINISH_REASONS.get(str(data.get('stop_reason') or ''), 'stop'),
        }],
    }
    usage = _usage(data.get('usage'))
    if usage:
        result['usage'] = usage
    return result


class AnthropicStreamTranslator:
    """把 Anthropic SSE 增量地翻译成 OpenAI ``chat.completion.chunk`` SSE。

    ``feed`` 可以接收任意切分的字节块；返回值是可直接交给 OpenAI SSE 解析器的
    字节。网关若忽略 ``stream`` 返回整段 JSON，``finish`` 时一次性转换。
    """

    def __init__(self) -> None:
        self._buffer = ''
        self._decoder_tail = b''
        self._seen_event = False
        self._raw_body: list[str] = []
        self._id = ''
        self._model = ''
        self._usage: dict[str, int] = {}
        self._tool_index: dict[int, int] = {}
        self._block_kind: dict[int, str] = {}
        self._done = False

    # -- public -------------------------------------------------------------

    def feed(self, chunk: bytes | str) -> bytes:
        text = self._decode(chunk)
        if not text:
            return b''
        self._buffer += text.replace('\r\n', '\n')
        out: list[str] = []
        while '\n\n' in self._buffer:
            event, self._buffer = self._buffer.split('\n\n', 1)
            out.extend(self._handle_event(event))
        return ''.join(out).encode('utf-8')

    def finish(self) -> bytes:
        out: list[str] = []
        tail = (self._decode(b'', final=True) + self._buffer).strip()
        self._buffer = ''
        if tail:
            if self._seen_event or tail.startswith(('event:', 'data:')):
                out.extend(self._handle_event(tail))
            else:
                self._raw_body.append(tail)
        if not self._seen_event and self._raw_body:
            out.extend(self._whole_body(''.join(self._raw_body)))
        if not self._done and (self._seen_event or self._raw_body):
            out.extend(self._emit_done())
        return ''.join(out).encode('utf-8')

    # -- internals ----------------------------------------------------------

    def _decode(self, chunk: bytes | str, final: bool = False) -> str:
        if isinstance(chunk, str):
            return chunk
        data = self._decoder_tail + bytes(chunk or b'')
        if final:
            self._decoder_tail = b''
            return data.decode('utf-8', errors='replace')
        # 保留被切断的 UTF-8 多字节尾巴到下一块
        for cut in range(0, min(4, len(data)) + 1):
            try:
                text = data[: len(data) - cut].decode('utf-8')
            except UnicodeDecodeError:
                continue
            self._decoder_tail = data[len(data) - cut:]
            return text
        self._decoder_tail = b''
        return data.decode('utf-8', errors='replace')

    def _chunk(self, delta: dict[str, Any], finish_reason: str | None = None) -> str:
        payload = {
            'id': self._id,
            'object': 'chat.completion.chunk',
            'model': self._model,
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason}],
        }
        return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'

    def _emit_done(self) -> list[str]:
        self._done = True
        out = []
        if self._usage:
            usage = dict(self._usage)
            usage['total_tokens'] = usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0)
            payload = {'id': self._id, 'object': 'chat.completion.chunk', 'model': self._model,
                       'choices': [], 'usage': usage}
            out.append('data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n')
        out.append('data: [DONE]\n\n')
        return out

    def _handle_event(self, event: str) -> list[str]:
        data_lines = []
        has_sse_field = False
        for line in event.split('\n'):
            stripped = line.strip()
            if stripped.startswith('data:'):
                has_sse_field = True
                data_lines.append(stripped[5:].strip())
            elif stripped.startswith(('event:', 'id:', 'retry:')) or stripped.startswith(':'):
                has_sse_field = True
        if not has_sse_field:
            if event.strip():
                self._raw_body.append(event)
            return []
        self._seen_event = True
        if not data_lines:
            return []
        raw = '\n'.join(data_lines)
        if raw == '[DONE]':
            return [] if self._done else self._emit_done()
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(data, Mapping):
            return []
        if 'choices' in data:  # 网关已经是 OpenAI 流，原样透传
            return ['data: ' + raw + '\n\n']
        return self._handle_data(data)

    def _handle_data(self, data: Mapping[str, Any]) -> list[str]:
        kind = data.get('type')
        if kind == 'message_start':
            message = data.get('message') if isinstance(data.get('message'), Mapping) else {}
            self._id = str(message.get('id') or '')
            self._model = str(message.get('model') or '')
            usage = message.get('usage')
            if isinstance(usage, Mapping):
                self._usage['prompt_tokens'] = int(usage.get('input_tokens') or 0)
                self._usage['completion_tokens'] = int(usage.get('output_tokens') or 0)
            return [self._chunk({'role': 'assistant', 'content': ''})]
        if kind == 'content_block_start':
            index = int(data.get('index') or 0)
            block = data.get('content_block') if isinstance(data.get('content_block'), Mapping) else {}
            block_kind = str(block.get('type') or '')
            self._block_kind[index] = block_kind
            if block_kind == 'tool_use':
                position = len(self._tool_index)
                self._tool_index[index] = position
                return [self._chunk({'tool_calls': [{
                    'index': position,
                    'id': str(block.get('id') or ''),
                    'type': 'function',
                    'function': {'name': str(block.get('name') or ''), 'arguments': ''},
                }]})]
            if block_kind == 'text' and block.get('text'):
                return [self._chunk({'content': str(block.get('text'))})]
            return []
        if kind == 'content_block_delta':
            index = int(data.get('index') or 0)
            delta = data.get('delta') if isinstance(data.get('delta'), Mapping) else {}
            delta_kind = delta.get('type')
            if delta_kind == 'text_delta' and delta.get('text'):
                return [self._chunk({'content': str(delta.get('text'))})]
            if delta_kind == 'thinking_delta' and delta.get('thinking'):
                return [self._chunk({'reasoning_content': str(delta.get('thinking'))})]
            if delta_kind == 'input_json_delta' and index in self._tool_index:
                partial = str(delta.get('partial_json') or '')
                if partial:
                    return [self._chunk({'tool_calls': [{
                        'index': self._tool_index[index],
                        'function': {'arguments': partial},
                    }]})]
            return []
        if kind == 'message_delta':
            delta = data.get('delta') if isinstance(data.get('delta'), Mapping) else {}
            usage = data.get('usage')
            if isinstance(usage, Mapping) and usage.get('output_tokens') is not None:
                self._usage['completion_tokens'] = int(usage.get('output_tokens') or 0)
            reason = delta.get('stop_reason')
            if reason:
                return [self._chunk({}, _FINISH_REASONS.get(str(reason), 'stop'))]
            return []
        if kind == 'message_stop':
            return [] if self._done else self._emit_done()
        if kind == 'error':
            payload = {'error': {'message': _error_message(data)}}
            return ['data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n']
        return []  # ping / content_block_stop 等

    def _whole_body(self, raw: str) -> list[str]:
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        converted = from_anthropic_response(data)
        if 'error' in converted and 'choices' not in converted:
            return ['data: ' + json.dumps(converted, ensure_ascii=False) + '\n\n']
        choices = converted.get('choices') or [{}]
        message = choices[0].get('message') or {}
        self._id = str(converted.get('id') or '')
        self._model = str(converted.get('model') or '')
        usage = converted.get('usage')
        if isinstance(usage, Mapping):
            self._usage = {'prompt_tokens': int(usage.get('prompt_tokens') or 0),
                           'completion_tokens': int(usage.get('completion_tokens') or 0)}
        out = [self._chunk({'role': 'assistant', 'content': ''})]
        if message.get('reasoning_content'):
            out.append(self._chunk({'reasoning_content': message['reasoning_content']}))
        if message.get('content'):
            out.append(self._chunk({'content': message['content']}))
        for position, call in enumerate(message.get('tool_calls') or []):
            out.append(self._chunk({'tool_calls': [dict(call, index=position)]}))
        out.append(self._chunk({}, choices[0].get('finish_reason') or 'stop'))
        return out


def translate_stream_text(raw: str) -> str:
    """整段 Anthropic SSE（或 JSON）文本一次性转换成 OpenAI SSE 文本。"""
    translator = AnthropicStreamTranslator()
    return (translator.feed(raw.encode('utf-8')) + translator.finish()).decode('utf-8')


__all__ = [
    'ANTHROPIC_VERSION',
    'API_PROTOCOLS',
    'AnthropicStreamTranslator',
    'PROTOCOL_ANTHROPIC',
    'PROTOCOL_OPENAI',
    'anthropic_headers',
    'api_root',
    'from_anthropic_response',
    'messages_url',
    'models_url',
    'normalize_protocol',
    'protocol_for_config',
    'to_anthropic_request',
    'translate_stream_text',
]
