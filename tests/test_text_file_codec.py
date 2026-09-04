# -*- coding: utf-8 -*-
"""针对统一文本文件编解码器（tools/text_file_codec.py）及附件、Agent 文件操作的多编码专项测试。"""

import os
import shutil
import tempfile
import unittest

from tools.text_file_codec import decode_text_bytes, is_probably_text
from tools.agent_runtime import _list_dir_impl, _read_file_impl, _search_code_impl, IGNORED_DIR_NAMES


class TextFileCodecTests(unittest.TestCase):
    def test_f1_utf8_text_exact(self):
        """F1. UTF-8 文本精确解码。"""
        raw = "SELECT id, name FROM sys_user WHERE status = 1;".encode('utf-8')
        res = decode_text_bytes(raw)
        self.assertTrue(res['ok'])
        self.assertFalse(res['binary'])
        self.assertEqual(res['encoding'], 'utf-8')
        self.assertEqual(res['text'], "SELECT id, name FROM sys_user WHERE status = 1;")

    def test_f2_utf8_bom_exact(self):
        """F2. UTF-8 BOM (\\xef\\xbb\\xbf) 精确解码并剥离 BOM。"""
        raw = b'\xef\xbb\xbf' + "SELECT * FROM t_claim WHERE codename = '车险';".encode('utf-8')
        res = decode_text_bytes(raw)
        self.assertTrue(res['ok'])
        self.assertEqual(res['encoding'], 'utf-8-sig')
        self.assertFalse(res['text'].startswith('\ufeff'))
        self.assertIn("codename = '车险'", res['text'])

    def test_f3_utf16_bom_exact(self):
        """F3. UTF-16 LE/BE BOM 精确解码。"""
        # UTF-16 LE
        text = "CREATE TABLE claim_info (id INT, note VARCHAR(50));"
        raw_le = b'\xff\xfe' + text.encode('utf-16le')
        res_le = decode_text_bytes(raw_le)
        self.assertTrue(res_le['ok'])
        self.assertEqual(res_le['encoding'], 'utf-16')
        self.assertEqual(res_le['text'], text)

        # UTF-16 BE
        raw_be = b'\xfe\xff' + text.encode('utf-16be')
        res_be = decode_text_bytes(raw_be)
        self.assertTrue(res_be['ok'])
        self.assertEqual(res_be['encoding'], 'utf-16')
        self.assertEqual(res_be['text'], text)

    def test_f4_gb18030_sql_exact_chinese_no_mojibake(self):
        """F4. GB18030 / GBK 中文 SQL 脚本精确还原，杜绝 \\ufffd 乱码。"""
        chinese_sql = (
            "-- 险种查询脚本\n"
            "SELECT riskcode, riskname FROM prpdrisk WHERE riskname LIKE '%机动车辆保险%'\n"
            "AND validstatus = '1';"
        )
        raw_gb = chinese_sql.encode('gb18030')
        res = decode_text_bytes(raw_gb, filename='query_risk.sql')
        self.assertTrue(res['ok'])
        self.assertEqual(res['encoding'], 'gb18030')
        self.assertNotIn('\ufffd', res['text'])
        self.assertEqual(res['text'], chinese_sql)

    def test_f5_binary_rejected_no_replacement_mojibake(self):
        """F5. 含有 NUL 字节或大比例控制字符的二进制文件被严格判定为 binary=True, ok=False。"""
        # 1. 包含 NUL 字节的随机二进制
        bin_data = bytes([0x00, 0x01, 0x02, 0xff, 0xfe, 0x81, 0x30, 0x20, 0x41])
        res = decode_text_bytes(bin_data, filename='test.bin')
        self.assertFalse(res['ok'])
        self.assertTrue(res['binary'])
        self.assertEqual(res['encoding'], 'binary')
        self.assertNotIn('\ufffd', res['text'])

        # 2. 伪装成合法编码范围但全为控制符
        ctrl_data = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08] * 10)
        res_ctrl = decode_text_bytes(ctrl_data, filename='ctrl.dat')
        self.assertFalse(res_ctrl['ok'])
        self.assertTrue(res_ctrl['binary'])

    def test_c1_gb18030_exact_roundtrip(self):
        """C1. GB18030 Chinese SQL decode exact and re-encode exact == original."""
        sql = "-- 保单报表统计\nSELECT * FROM prpduser WHERE username = '张三' AND dept = '理赔部';"
        raw = sql.encode('gb18030')
        res = decode_text_bytes(raw, filename='test_roundtrip.sql')
        self.assertTrue(res['ok'])
        self.assertEqual(res['encoding'], 'gb18030')
        self.assertEqual(res['text'], sql)
        self.assertEqual(res['text'].encode('gb18030', errors='strict'), raw)

    def test_c2_gb18030_roundtrip_mismatch_rejected(self):
        """C2. 验证 GB18030 roundtrip 校验失败分支：若 decode 成功但 re-encode 不等于原字节，必须被阻断。"""
        class FakeBytes(bytes):
            def decode(self, encoding='utf-8', errors='strict'):
                if encoding == 'gb18030':
                    class FakeStr(str):
                        def encode(self, enc='utf-8', errors='strict'):
                            return b'mismatched_bytes'
                    return FakeStr('fake')
                return super().decode(encoding, errors)

        raw = FakeBytes(b'\x81\x30\x81\x30')
        res = decode_text_bytes(raw, filename='mismatch.txt')
        self.assertFalse(res['ok'])
        self.assertTrue(res['binary'])
        self.assertEqual(res['reason'], 'binary')

    def test_c3_max_size_boundary(self):
        """C3. max_size=10, raw len=11 -> controlled too_large, binary=False."""
        raw = b"0123456789A"  # 11 bytes
        res = decode_text_bytes(raw, filename='big.txt', max_size=10)
        self.assertFalse(res['ok'])
        self.assertFalse(res['binary'])
        self.assertTrue(res['too_large'])
        self.assertEqual(res['reason'], 'too_large')
        self.assertIn('超过限制', res['error'])


class AgentFileOperationsCodecTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix='agent_codec_test_')

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_a8_read_file_gb18030_java_and_sql(self):
        """A8. Agent read_file 读取 GB18030 编码的 Java/SQL 文件无乱码。"""
        java_code = (
            "package com.acme;\n"
            "/**\n"
            " * 核心保单计算服务\n"
            " */\n"
            "public class PolicyService {\n"
            "    public String getStatus() { return \"保单生效中\"; }\n"
            "}\n"
        )
        fpath = os.path.join(self.test_dir, 'PolicyService.java')
        with open(fpath, 'wb') as f:
            f.write(java_code.encode('gb18030'))

        res = _read_file_impl('PolicyService.java', self.test_dir)
        self.assertTrue(res['ok'])
        self.assertEqual(res['content'], java_code)
        self.assertNotIn('\ufffd', res['content'])

    def test_a9_search_code_gb18030_no_mojibake(self):
        """A9. Agent search_code 在 GB18030 文件中搜索中文关键字正常命中，且无 \\ufffd。"""
        sql_content = "SELECT * FROM t_policy WHERE remark = '理赔专员审核通过';\n"
        fpath = os.path.join(self.test_dir, 'claim.sql')
        with open(fpath, 'wb') as f:
            f.write(sql_content.encode('gb18030'))

        res = _search_code_impl('理赔专员', self.test_dir)
        self.assertTrue(res['ok'])
        self.assertEqual(len(res['matches']), 1)
        self.assertIn('理赔专员审核通过', res['matches'][0]['text'])
        self.assertNotIn('\ufffd', res['matches'][0]['text'])

    def test_a10_list_dir_and_search_code_skip_ignored_directories(self):
        """A10. list_dir 与 search_code 自动跳过 target, .git, .idea, build, out, node_modules 等构建产物目录。"""
        # 创建 target 与 build 目录并在其中放文件
        target_dir = os.path.join(self.test_dir, 'target')
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, 'App.class'), 'wb') as f:
            f.write(b'\xca\xfe\xba\xbe')
        with open(os.path.join(target_dir, 'target_match.sql'), 'w', encoding='utf-8') as f:
            f.write("target dummy keyword")

        src_dir = os.path.join(self.test_dir, 'src')
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, 'App.java'), 'w', encoding='utf-8') as f:
            f.write("public class App { String s = \"target dummy keyword\"; }")

        # 1. list_dir 不应列出 target 目录
        list_res = _list_dir_impl('', self.test_dir)
        self.assertTrue(list_res['ok'])
        names = [e['name'] for e in list_res['entries']]
        self.assertIn('src', names)
        self.assertNotIn('target', names)

        # 2. search_code 不应在 target 目录下搜索
        search_res = _search_code_impl('target dummy keyword', self.test_dir)
        self.assertTrue(search_res['ok'])
        self.assertEqual(len(search_res['matches']), 1)
        self.assertIn('src', search_res['matches'][0]['file'])
        self.assertNotIn('target', search_res['matches'][0]['file'])

    def test_c4_search_code_300kb_text_within_1mb(self):
        """C4. search_code 300KB 合法 UTF-8 文本（> 256KB 且 <= 1MB）仍可正常搜索，证明显式传入 MAX_SEARCH_TEXT_FILE_SIZE。"""
        large_text = "line with regular text\n" * 12000 + "target_keyword_in_300kb_file\n" + "trailing text\n" * 1000
        raw = large_text.encode('utf-8')
        self.assertGreater(len(raw), 256 * 1024)
        self.assertLess(len(raw), 1024 * 1024)
        fpath = os.path.join(self.test_dir, 'large_search.txt')
        with open(fpath, 'wb') as f:
            f.write(raw)
        res = _search_code_impl('target_keyword_in_300kb_file', self.test_dir)
        self.assertTrue(res['ok'])
        self.assertEqual(len(res['matches']), 1)
        self.assertEqual(res['matches'][0]['file'], 'large_search.txt')
        self.assertIn('target_keyword_in_300kb_file', res['matches'][0]['text'])

    def test_c5_search_code_skips_files_over_1mb(self):
        """C5. > 1MB 的超大文件自动被 search_code 跳过，保障内存安全。"""
        huge_text = "dummy text line\n" * 70000 + "secret_in_huge_file\n"
        raw = huge_text.encode('utf-8')
        self.assertGreater(len(raw), 1024 * 1024)
        fpath = os.path.join(self.test_dir, 'huge_file.txt')
        with open(fpath, 'wb') as f:
            f.write(raw)
        res = _search_code_impl('secret_in_huge_file', self.test_dir)
        self.assertTrue(res['ok'])
        self.assertEqual(len(res['matches']), 0)


if __name__ == '__main__':
    unittest.main()
