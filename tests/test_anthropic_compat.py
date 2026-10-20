"""Anthropic Messages 兼容协议 + Claude Code 配置导入。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tools import anthropic_compat as ac
from tools.claude_code_import import (
    candidate_paths,
    find_profiles,
    profile_from_env,
    profile_from_file,
    strip_jsonc,
    to_model_item,
)

FAKE_TOKEN = 'sk-ccr-TEST-ONLY-NOT-REAL'
BASE = 'http://10.128.1.20:13458'


def _sse(event: str, payload: dict) -> str:
    return f'event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'


def _anthropic_tool_stream() -> str:
    return ''.join([
        _sse('message_start', {'type': 'message_start', 'message': {
            'id': 'msg_1', 'model': 'qwen3.8', 'usage': {'input_tokens': 12, 'output_tokens': 0}}}),
        _sse('ping', {'type': 'ping'}),
        _sse('content_block_start', {'type': 'content_block_start', 'index': 0,
                                     'content_block': {'type': 'text', 'text': ''}}),
        _sse('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                                     'delta': {'type': 'text_delta', 'text': '先读取'}}),
        _sse('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                                     'delta': {'type': 'text_delta', 'text': '说明文件。'}}),
        _sse('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
        _sse('content_block_start', {'type': 'content_block_start', 'index': 1, 'content_block': {
            'type': 'tool_use', 'id': 'toolu_1', 'name': 'read_file', 'input': {}}}),
        _sse('content_block_delta', {'type': 'content_block_delta', 'index': 1,
                                     'delta': {'type': 'input_json_delta', 'partial_json': '{"path": "REA'}}),
        _sse('content_block_delta', {'type': 'content_block_delta', 'index': 1,
                                     'delta': {'type': 'input_json_delta', 'partial_json': 'DME.md"}'}}),
        _sse('content_block_stop', {'type': 'content_block_stop', 'index': 1}),
        _sse('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'},
                               'usage': {'output_tokens': 30}}),
        _sse('message_stop', {'type': 'message_stop'}),
    ])


def _openai_chunks(sse_text: str) -> list:
    out = []
    for line in sse_text.splitlines():
        if line.startswith('data:'):
            payload = line[5:].strip()
            out.append(payload if payload == '[DONE]' else json.loads(payload))
    return out


class RequestTranslationTests(unittest.TestCase):
    def test_system_tools_and_tool_exchange_are_converted(self):
        body = {
            'model': ' qwen3.8 ',
            'stream': True,
            'messages': [
                {'role': 'system', 'content': '你是 Agent。'},
                {'role': 'user', 'content': '读一下 README'},
                {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'call_1', 'type': 'function',
                    'function': {'name': 'read_file', 'arguments': '{"path": "README.md"}'}}]},
                {'role': 'tool', 'tool_call_id': 'call_1', 'content': '# Demo'},
                {'role': 'user', 'content': '总结一下'},
            ],
            'tools': [{'type': 'function', 'function': {
                'name': 'read_file', 'description': '读文件',
                'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}}],
            'stream_options': {'include_usage': True},
        }
        req = ac.to_anthropic_request(body, default_max_tokens=2048)
        self.assertEqual(req['model'], 'qwen3.8')
        self.assertEqual(req['system'], '你是 Agent。')
        self.assertEqual(req['max_tokens'], 2048)
        self.assertTrue(req['stream'])
        self.assertNotIn('stream_options', req)
        self.assertEqual([m['role'] for m in req['messages']], ['user', 'assistant', 'user'])
        tool_use = req['messages'][1]['content'][0]
        self.assertEqual(tool_use, {'type': 'tool_use', 'id': 'call_1', 'name': 'read_file',
                                    'input': {'path': 'README.md'}})
        # 工具结果与随后的用户文本合并进同一条 user 消息，结果在前
        merged = req['messages'][2]['content']
        self.assertEqual(merged[0], {'type': 'tool_result', 'tool_use_id': 'call_1', 'content': '# Demo'})
        self.assertEqual(merged[1], {'type': 'text', 'text': '总结一下'})
        self.assertEqual(req['tools'][0]['name'], 'read_file')
        self.assertEqual(req['tools'][0]['input_schema']['properties']['path']['type'], 'string')

    def test_strict_json_hint_images_and_leading_assistant(self):
        req = ac.to_anthropic_request({
            'model': 'm',
            'max_tokens': 99,
            'response_format': {'type': 'json_object'},
            'messages': [
                {'role': 'assistant', 'content': '上次的回答'},
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': '看图'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,QUJD'}},
                ]},
            ],
            'tool_choice': 'none',
            'tools': [{'type': 'function', 'function': {'name': 'x'}}],
        })
        self.assertEqual(req['max_tokens'], 99)
        self.assertIn('JSON', req['system'])
        self.assertEqual(req['messages'][0]['role'], 'user')
        image = req['messages'][2]['content'][1]
        self.assertEqual(image['source'], {'type': 'base64', 'media_type': 'image/png', 'data': 'QUJD'})
        self.assertNotIn('tools', req)

    def test_urls_and_headers(self):
        for base in (BASE, BASE + '/', BASE + '/v1', BASE + '/v1/messages'):
            self.assertEqual(ac.messages_url(base), BASE + '/v1/messages')
        self.assertEqual(ac.models_url(BASE), BASE + '/v1/models')
        headers = ac.anthropic_headers(FAKE_TOKEN)
        self.assertEqual(headers['x-api-key'], FAKE_TOKEN)
        self.assertEqual(headers['Authorization'], f'Bearer {FAKE_TOKEN}')
        self.assertEqual(headers['anthropic-version'], ac.ANTHROPIC_VERSION)
        self.assertEqual(ac.protocol_for_config({'provider': 'deepseek', 'api_protocol': 'anthropic'}), 'openai')
        self.assertEqual(ac.protocol_for_config({'api_protocol': 'anthropic'}), 'anthropic')
        self.assertEqual(ac.protocol_for_config({}), 'openai')


class ResponseTranslationTests(unittest.TestCase):
    def test_stream_split_at_every_byte_keeps_text_and_tool_call(self):
        raw = _anthropic_tool_stream().encode('utf-8')
        translator = ac.AnthropicStreamTranslator()
        out = b''.join(translator.feed(raw[i:i + 1]) for i in range(len(raw))) + translator.finish()
        chunks = _openai_chunks(out.decode('utf-8'))
        self.assertEqual(chunks[-1], '[DONE]')
        text = ''.join(c['choices'][0]['delta'].get('content') or '' for c in chunks[:-1] if c.get('choices'))
        self.assertEqual(text, '先读取说明文件。')
        calls = [c['choices'][0]['delta']['tool_calls'][0] for c in chunks[:-1]
                 if c.get('choices') and 'tool_calls' in c['choices'][0]['delta']]
        self.assertEqual(calls[0]['id'], 'toolu_1')
        self.assertEqual(calls[0]['function']['name'], 'read_file')
        self.assertEqual(json.loads(''.join(c['function']['arguments'] for c in calls)), {'path': 'README.md'})
        finishes = [c['choices'][0]['finish_reason'] for c in chunks[:-1] if c.get('choices')]
        self.assertIn('tool_calls', finishes)
        usage = [c['usage'] for c in chunks[:-1] if c.get('usage')]
        self.assertEqual(usage[-1], {'prompt_tokens': 12, 'completion_tokens': 30, 'total_tokens': 42})

    def test_whole_json_body_and_error_and_openai_passthrough(self):
        body = {'id': 'msg_2', 'type': 'message', 'model': 'm', 'stop_reason': 'end_turn',
                'content': [{'type': 'text', 'text': '你好'}], 'usage': {'input_tokens': 1, 'output_tokens': 2}}
        converted = ac.from_anthropic_response(body)
        self.assertEqual(converted['choices'][0]['message']['content'], '你好')
        self.assertEqual(converted['choices'][0]['finish_reason'], 'stop')
        chunks = _openai_chunks(ac.translate_stream_text(json.dumps(body)))
        self.assertEqual(chunks[-1], '[DONE]')
        self.assertIn('你好', json.dumps(chunks, ensure_ascii=False))
        error = ac.from_anthropic_response({'type': 'error', 'error': {'type': 'auth', 'message': 'bad key'}})
        self.assertEqual(error, {'error': {'message': 'bad key'}})
        openai_line = 'data: {"choices": [{"delta": {"content": "x"}}]}\n\n'
        self.assertIn('"content": "x"', ac.translate_stream_text(openai_line))


class _FakeResponse:
    def __init__(self, raw: bytes):
        self._chunks = [raw[i:i + 7] for i in range(0, len(raw), 7)]
        self.status = 200
        self.closed = False

    def read1(self, size):
        return self._chunks.pop(0) if self._chunks else b''

    def read(self, size=-1):
        if size is None or size < 0:
            data = b''.join(self._chunks)
            self._chunks = []
            return data
        return self.read1(size)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _FakeOpener:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return _FakeResponse(self.raw)


def _anthropic_cfg(**extra):
    cfg = {'id': 'cfg-a', 'provider': 'intranet', 'api_protocol': 'anthropic', 'enabled': True,
           'base_url': BASE, 'model': 'qwen3.8', 'token': 'encrypted', 'timeout_seconds': 30}
    cfg.update(extra)
    return cfg


class TransportIntegrationTests(unittest.TestCase):
    def test_agent_transport_posts_messages_and_returns_tool_call(self):
        from tools.data_center.contracts import AgentTurnRequest, CancellationToken, RunContext
        from tools.data_center.host_model_adapter import AgentModelHostAdapter
        from tools.data_center.model_adapter import UrlLibStreamingTransport

        opener = _FakeOpener(_anthropic_tool_stream().encode('utf-8'))
        cfg = _anthropic_cfg(agent_capability='native_tools')
        request = AgentTurnRequest(
            context=RunContext(run_id='r', tab_id='t', model_config_id='cfg-a',
                               connection_id='c', database='d'),
            system_prompt='只读。',
            messages=({'role': 'user', 'content': '读 README'},),
            tools=({'type': 'function', 'function': {'name': 'read_file'}},),
            turn_index=0,
            max_output_tokens=256,
        )
        with patch('tools.intranet_llm._direct_opener', return_value=opener), \
                patch('tools.intranet_llm.decrypt_token', return_value=FAKE_TOKEN), \
                patch('tools.intranet_llm.resolve_private_host'):
            adapter = AgentModelHostAdapter('cfg-a', config=cfg, transport=UrlLibStreamingTransport(cfg))
            turn = adapter.complete_turn(request, cancellation=CancellationToken(), on_delta=lambda _d: None)
        sent = opener.requests[0]
        self.assertEqual(sent.full_url, BASE + '/v1/messages')
        self.assertEqual(sent.get_header('X-api-key'), FAKE_TOKEN)
        body = json.loads(sent.data.decode('utf-8'))
        self.assertEqual(body['system'], '只读。')
        self.assertEqual(body['max_tokens'], 256)
        self.assertEqual(body['tools'][0]['name'], 'read_file')
        self.assertEqual([(c.name, dict(c.arguments)) for c in turn.tool_calls], [('read_file', {'path': 'README.md'})])
        self.assertEqual(turn.text, '先读取说明文件。')

    def test_chat_completions_uses_messages_endpoint(self):
        from tools import intranet_llm
        stream = ''.join([
            _sse('message_start', {'type': 'message_start', 'message': {'id': 'm', 'model': 'qwen3.8'}}),
            _sse('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                                         'delta': {'type': 'text_delta', 'text': '内网回复'}}),
            _sse('message_stop', {'type': 'message_stop'}),
        ]).encode('utf-8')
        opener = _FakeOpener(stream)
        with patch('tools.intranet_llm._direct_opener', return_value=opener), \
                patch('tools.intranet_llm.decrypt_token', return_value=FAKE_TOKEN), \
                patch('tools.intranet_llm.resolve_private_host'):
            text = intranet_llm.chat_completions(
                [{'role': 'system', 'content': 'S'}, {'role': 'user', 'content': '你好'}],
                cfg=_anthropic_cfg(),
            )
        self.assertEqual(text, '内网回复')
        sent = opener.requests[0]
        self.assertEqual(sent.full_url, BASE + '/v1/messages')
        body = json.loads(sent.data.decode('utf-8'))
        self.assertEqual(body['system'], 'S')
        self.assertEqual(body['messages'], [{'role': 'user', 'content': [{'type': 'text', 'text': '你好'}]}])

    def test_chat_completions_surfaces_gateway_error(self):
        from tools import intranet_llm
        error = _sse('error', {'type': 'error', 'error': {'type': 'overloaded', 'message': '网关繁忙'}})
        opener = _FakeOpener(error.encode('utf-8'))
        with patch('tools.intranet_llm._direct_opener', return_value=opener), \
                patch('tools.intranet_llm.decrypt_token', return_value=FAKE_TOKEN), \
                patch('tools.intranet_llm.resolve_private_host'):
            with self.assertRaises(intranet_llm.IntranetLlmError) as ctx:
                intranet_llm._request(_anthropic_cfg(), 'POST', 'chat/completions',
                                      {'model': 'm', 'messages': [{'role': 'user', 'content': 'x'}]})
        self.assertIn('网关繁忙', str(ctx.exception))

    def test_openai_profiles_are_unchanged(self):
        from tools import intranet_llm
        cfg = intranet_llm.normalize_ai_local({'base_url': 'http://10.1.1.1:8000/v1', 'enabled': True})
        self.assertEqual(cfg['api_protocol'], 'openai')
        self.assertEqual(intranet_llm.endpoint_url(cfg, 'chat/completions'),
                         'http://10.1.1.1:8000/v1/chat/completions')
        with patch('tools.intranet_llm.decrypt_token', return_value=FAKE_TOKEN):
            headers = intranet_llm.build_headers(cfg)
        self.assertEqual(headers['Authorization'], f'Bearer {FAKE_TOKEN}')
        self.assertNotIn('x-api-key', headers)
        deepseek = intranet_llm.normalize_ai_local({'provider': 'deepseek', 'api_protocol': 'anthropic'})
        self.assertEqual(deepseek['api_protocol'], 'openai')


VSCODE_SETTINGS = """{
  "claudeCode.environmentVariables": [
      {"name": "ANTHROPIC_AUTH_TOKEN", "value": "%s"},
      {"name": "ANTHROPIC_BASE_URL", "value": "%s"},
      {"name": "ANTHROPIC_MODEL", "value": " qwen3.8"},
      {"name": "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "value": "10000"},
  ],
  // 行注释里有 "引号" 和 // 斜杠
  "claude.code.permissions.bypassAll": true, /* 块注释 */
  "workbench.colorTheme": "Light Modern",
  "editor.codeActionsOnSave": {

  }
}""" % (FAKE_TOKEN, BASE)


class ClaudeCodeImportTests(unittest.TestCase):
    def test_strip_jsonc_keeps_urls_inside_strings(self):
        data = json.loads(strip_jsonc('{"u": "http://a//b", // c\n "x": [1, 2,],}'))
        self.assertEqual(data, {'u': 'http://a//b', 'x': [1, 2]})

    def test_vscode_settings_profile_and_model_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'settings.json')
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write(VSCODE_SETTINGS)
            profile = profile_from_file(path)
        self.assertEqual(profile.base_url, BASE)
        self.assertEqual(profile.model, 'qwen3.8')
        self.assertEqual(profile.token, FAKE_TOKEN)
        self.assertEqual(profile.max_output_tokens, 10000)
        self.assertNotIn(FAKE_TOKEN, repr(profile))
        item = to_model_item(profile, encrypted_token='ENC')
        self.assertEqual(item['api_protocol'], 'anthropic')
        self.assertEqual(item['token'], 'ENC')
        self.assertEqual(item['agent_capability'], 'native_tools')
        self.assertTrue(item['enabled'])

    def test_cli_env_and_discovery_order(self):
        profile = profile_from_env({'ANTHROPIC_BASE_URL': BASE + '/', 'ANTHROPIC_API_KEY': 'k',
                                    'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen'})
        self.assertEqual((profile.base_url, profile.token, profile.model), (BASE, 'k', 'qwen'))
        self.assertIsNone(profile_from_env({'ANTHROPIC_MODEL': 'x'}))
        with tempfile.TemporaryDirectory() as tmp:
            env = {'APPDATA': os.path.join(tmp, 'AppData'), 'USERPROFILE': tmp}
            paths = candidate_paths(env)
            self.assertEqual(paths[0], os.path.join(tmp, 'AppData', 'Code', 'User', 'settings.json'))
            cli = os.path.join(tmp, '.claude', 'settings.json')
            self.assertEqual(paths[-1], cli)
            os.makedirs(os.path.dirname(cli))
            with open(cli, 'w', encoding='utf-8') as stream:
                json.dump({'env': {'ANTHROPIC_BASE_URL': BASE, 'ANTHROPIC_AUTH_TOKEN': 'cli'}}, stream)
            found = find_profiles(paths)
        self.assertEqual([p.token for p in found], ['cli'])


class SettingsImportButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_import_creates_encrypted_default_profile_without_touching_real_data(self):
        from panels.settings_panel import SettingsPanel
        from tools import claude_code_import, intranet_llm

        profile = profile_from_env({'ANTHROPIC_BASE_URL': BASE, 'ANTHROPIC_AUTH_TOKEN': FAKE_TOKEN,
                                    'ANTHROPIC_MODEL': 'qwen3.8'}, source_path='C:/x/User/settings.json')
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, 'ai_local.json')
            with patch.object(intranet_llm, 'AI_LOCAL_FILE', store), \
                    patch.object(claude_code_import, 'find_profiles', return_value=[profile]), \
                    patch('tools.intranet_llm.encrypt_token', side_effect=lambda t: 'ENC:' + t[::-1]), \
                    patch('tools.intranet_llm.decrypt_token', side_effect=lambda t: t[4:][::-1] if t else ''):
                panel = SettingsPanel('zh')
                try:
                    panel._import_claude_code_config()
                    panel._import_claude_code_config()  # 再导一次：更新同一条，不重复
                    with open(store, encoding='utf-8') as stream:
                        raw = stream.read()
                    catalog = intranet_llm.load_model_catalog()
                    status = panel.ai_status.text()
                    protocol = panel.ai_protocol.currentData()
                finally:
                    panel.deleteLater()
        self.assertNotIn(FAKE_TOKEN, raw)
        items = catalog['items']
        imported = [row for row in items if row.get('api_protocol') == 'anthropic']
        self.assertEqual(len(imported), 1)
        self.assertEqual(catalog['active_model_id'], imported[0]['id'])
        self.assertEqual(imported[0]['base_url'], BASE)
        self.assertEqual(protocol, 'anthropic')
        self.assertIn('已加密保存在本机', status)
        self.assertNotIn(FAKE_TOKEN, status)


if __name__ == '__main__':
    unittest.main()
