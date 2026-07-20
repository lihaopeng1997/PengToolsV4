# -*- coding: utf-8 -*-
"""XML 美化逻辑定向测试。"""
import os
import sys
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from tools.xml_formatter import format_xml_text, normalize_xml_input


class XmlFormatterTests(unittest.TestCase):
    def test_pretty_print_basic_indent(self):
        raw = '<root><a>1</a><b>2</b></root>'
        out = format_xml_text(raw)
        self.assertIn('<root>', out)
        self.assertIn('  <a>1</a>', out)
        self.assertIn('  <b>2</b>', out)
        self.assertIn('</root>', out)

    def test_strip_outer_double_quotes(self):
        raw = '"<root><a>1</a></root>"'
        out = format_xml_text(raw)
        self.assertIn('<root>', out)
        self.assertIn('<a>1</a>', out)
        self.assertFalse(out.strip().startswith('"'))

    def test_unescape_json_style_escapes(self):
        # 日志/JSON 字符串形态：含 \" \n \t
        raw = r'"<root>\n\t<a attr=\"x\">1</a>\n</root>"'
        out = format_xml_text(raw)
        self.assertIn('<root>', out)
        self.assertIn('attr="x"', out)
        self.assertIn('<a', out)
        self.assertIn('>1</a>', out)

    def test_unescape_without_outer_quotes(self):
        raw = r'<root>\n  <item>ok</item>\n</root>'
        out = format_xml_text(raw)
        self.assertIn('<item>ok</item>', out)
        self.assertIn('  <item>', out)

    def test_preserve_xml_declaration(self):
        raw = '<?xml version="1.0" encoding="UTF-8"?><root><a>1</a></root>'
        out = format_xml_text(raw)
        self.assertTrue(out.lstrip().startswith('<?xml'))
        self.assertIn('encoding="UTF-8"', out.splitlines()[0])
        self.assertIn('<a>1</a>', out)

    def test_no_declaration_when_input_has_none(self):
        out = format_xml_text('<root><a>1</a></root>')
        self.assertFalse(out.lstrip().lower().startswith('<?xml'))

    def test_keeps_chinese_text(self):
        raw = '<报文><姓名>李浩鹏</姓名><说明>中文内容</说明></报文>'
        out = format_xml_text(raw)
        self.assertIn('李浩鹏', out)
        self.assertIn('中文内容', out)
        self.assertNotIn('\\u', out)

    def test_invalid_xml_reports_line_and_column(self):
        bad = '<root>\n  <a>1</b>\n</root>'
        with self.assertRaises(ValueError) as ctx:
            format_xml_text(bad)
        message = str(ctx.exception)
        self.assertIn('第', message)
        self.assertIn('行', message)
        self.assertIn('列', message)

    def test_empty_raises(self):
        with self.assertRaisesRegex(ValueError, '为空'):
            format_xml_text('   ')
        with self.assertRaisesRegex(ValueError, '为空'):
            normalize_xml_input('')

    def test_normalize_strips_quotes_only(self):
        self.assertEqual(
            normalize_xml_input('  "<x/>"  '),
            '<x/>',
        )


if __name__ == '__main__':
    unittest.main()
