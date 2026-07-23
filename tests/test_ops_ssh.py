# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from tools.ops_commands import COMMANDS, build_command
from tools.ops_ssh import (
    DEFAULT_CATEGORY_NAME, UNCATEGORIZED_ID, build_remote_grep_command,
    decrypt_secret, delete_category, encrypt_secret, ensure_category,
    format_connection_ok, load_categories, load_server_store, load_servers,
    normalize_server_store, save_server_store, save_servers, split_extra_keywords,
    test_connection, OpsSshError,
)
from ui.navigation_model import GROUP_LABELS, get_nav_item


class OpsSshTests(unittest.TestCase):
    def test_nav_ops_group(self):
        self.assertIn('ops', GROUP_LABELS)
        self.assertEqual(get_nav_item(13).name_zh, '日志排查')
        self.assertEqual(get_nav_item(6).name_zh, '命令库')

    def test_build_remote_grep_and(self):
        cmd = build_remote_grep_command(
            '/var/log/app.log', 'ERROR', ['order-1', 'timeout'], context_lines=20,
        )
        self.assertIn('grep -a -n -i -C 20 -- ERROR', cmd)
        self.assertIn("grep -a -i -- order-1", cmd)
        self.assertIn('timeout', cmd)

    def test_split_extra_keywords(self):
        self.assertEqual(split_extra_keywords('a,b，c\nd'), ['a', 'b', 'c', 'd'])

    def test_export_filename_ip_service(self):
        from tools.ops_ssh import local_export_filename, parse_keywords
        name = local_export_filename(
            {'host': '192.168.1.10', 'name': '集成1'},
            'ERROR',
            service_name='core',
        )
        self.assertEqual(name, '192.168.1.10-core.log')
        primary, extras = parse_keywords('ERROR, orderId')
        self.assertEqual(primary, 'ERROR')
        self.assertEqual(extras, ['orderId'])

    def test_terminal_normalize_keeps_backspace(self):
        from tools.ops_ssh_shell import normalize_terminal_text
        text = normalize_terminal_text('ab\x08 \x08c')
        self.assertIn('\x08', text)
        self.assertEqual(text, 'ab\x08 \x08c')

    def test_password_roundtrip_not_plain(self):
        token = encrypt_secret('p@ss-测试')
        self.assertFalse(token.startswith('p@ss'))
        self.assertFalse(token.startswith('b64:'), '安测：禁止新写 b64 弱编码')
        self.assertTrue(token.startswith('dpapi:') or token.startswith('enc:'))
        self.assertEqual(decrypt_secret(token), 'p@ss-测试')

    def test_save_servers_encrypts_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            import tools.ops_ssh as mod
            old = mod.OPS_SERVERS_FILE
            try:
                mod.OPS_SERVERS_FILE = os.path.join(tmp, 'ops_servers.json')
                save_servers([{
                    'name': 'n1', 'host': '10.0.0.1', 'port': 22,
                    'username': 'app', 'password': 'secret-xyz',
                    'default_log_path': '/a.log',
                }])
                loaded = load_servers()
                self.assertEqual(len(loaded), 1)
                self.assertNotIn('password', loaded[0])
                self.assertTrue(loaded[0]['password_token'])
                self.assertNotIn('secret-xyz', loaded[0]['password_token'])
                self.assertEqual(decrypt_secret(loaded[0]['password_token']), 'secret-xyz')
                self.assertEqual(loaded[0].get('category_id'), UNCATEGORIZED_ID)
            finally:
                mod.OPS_SERVERS_FILE = old

    def test_user_defined_categories_and_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            import tools.ops_ssh as mod
            old = mod.OPS_SERVERS_FILE
            try:
                mod.OPS_SERVERS_FILE = os.path.join(tmp, 'ops_servers.json')
                # v1 旧数据：只有 group 文本
                path = mod.OPS_SERVERS_FILE
                with open(path, 'w', encoding='utf-8') as f:
                    import json
                    json.dump({
                        'version': 1,
                        'servers': [
                            {
                                'id': 's1', 'name': '集成1', 'host': '10.1.1.1',
                                'port': 22, 'username': 'app', 'group': '集成服务器',
                            },
                            {
                                'id': 's2', 'name': '模拟1', 'host': '10.2.2.2',
                                'port': 22, 'username': 'app', 'group': '模拟服务器',
                            },
                        ],
                    }, f)
                store = load_server_store()
                names = {c['name'] for c in store['categories']}
                self.assertIn('集成服务器', names)
                self.assertIn('模拟服务器', names)
                self.assertIn(DEFAULT_CATEGORY_NAME, names)
                servers = store['servers']
                self.assertEqual(len(servers), 2)
                self.assertNotEqual(servers[0]['category_id'], servers[1]['category_id'])
                # 新建分类 + 归类
                cats = list(store['categories'])
                cats, cid = ensure_category(cats, '灾备服务器')
                servers[0]['category_id'] = cid
                servers[0]['group'] = '灾备服务器'
                save_server_store(servers=servers, categories=cats)
                again = load_server_store()
                self.assertTrue(any(c['name'] == '灾备服务器' for c in again['categories']))
                # 删除分类后机器回未分类
                cats2, servers2 = delete_category(
                    again['categories'], again['servers'],
                    next(c['id'] for c in again['categories'] if c['name'] == '灾备服务器'),
                )
                save_server_store(servers=servers2, categories=cats2)
                final = load_servers()
                s1 = next(s for s in final if s['id'] == 's1')
                self.assertEqual(s1['category_id'], UNCATEGORIZED_ID)
            finally:
                mod.OPS_SERVERS_FILE = old

    def test_command_library_and_workflow(self):
        cmd = next(c for c in COMMANDS if c.get('workflow') == 'log_and_keywords')
        text = build_command(cmd, {
            'keyword': 'ERR', 'also_1': 'abc', 'also_2': 'def', 'also_3': '',
            'context': '5', 'log_path': '/var/log/a.log',
        })
        self.assertIn('ERR', text)
        self.assertIn('abc', text)
        self.assertIn('def', text)

    def test_connection_requires_host_and_formats_ok(self):
        with self.assertRaises(OpsSshError):
            test_connection({'host': '', 'username': 'app'})
        with self.assertRaises(OpsSshError):
            test_connection({'host': '10.0.0.1', 'username': 'app'})  # no password
        text = format_connection_ok({
            'name': '集成1', 'host': '10.1.1.1', 'port': 22,
            'username': 'app', 'elapsed_ms': 120, 'hostname': 'int-host',
            'uname': 'Linux 5.10 x86_64',
        }, 'zh')
        self.assertIn('连通成功', text)
        self.assertIn('10.1.1.1', text)
        self.assertIn('120', text)




    def test_multi_service_export_jobs(self):
        from tools.ops_ssh import build_export_jobs, normalize_services, primary_log_path, _normalize_server
        raw = {
            'id': 's1', 'name': '机A', 'host': '10.0.0.1', 'username': 'u',
            'default_log_path': '/old.log',
            'services': [
                {'name': '网关', 'log_path': '/gw/app.log', 'enabled': True},
                {'name': '核心', 'log_path': '/core/app.log', 'enabled': True},
                {'name': '关闭', 'log_path': '/x.log', 'enabled': False},
            ],
        }
        server = _normalize_server(raw)
        self.assertEqual(len(server['services']), 3)
        self.assertEqual(server['default_log_path'], '/gw/app.log')
        jobs = build_export_jobs([server])
        self.assertEqual(len(jobs), 2)
        names = {j['service_name'] for j in jobs}
        self.assertEqual(names, {'网关', '核心'})
        # 迁移：仅 default
        migrated = normalize_services(None, default_log_path='/only.log')
        self.assertEqual(len(migrated), 1)
        self.assertEqual(migrated[0]['log_path'], '/only.log')
        # 覆盖路径
        jobs2 = build_export_jobs([server], selected_keys={'s1'}, override_path='/override.log')
        self.assertEqual(len(jobs2), 1)
        self.assertEqual(jobs2[0]['log_path'], '/override.log')

if __name__ == '__main__':
    unittest.main()
