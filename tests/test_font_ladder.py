# -*- coding: utf-8 -*-
"""字体阶梯：权威只在 theme_manager，角色选择器不得再写第二套字号。"""

import os
import re
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FontLadderTests(unittest.TestCase):
    def test_five_roles_are_defined_once(self):
        from ui import theme_manager
        from ui.theme_manager import FONT_MONO_FAMILY, FONT_ROLES, FONT_UI_FAMILY, font_qss_tokens

        self.assertEqual(
            tuple(FONT_ROLES),
            ('page_title', 'section_title', 'body', 'helper', 'caption'),
        )
        self.assertIn('YaHei', FONT_UI_FAMILY)
        self.assertTrue(FONT_MONO_FAMILY)
        tokens = font_qss_tokens()
        self.assertEqual(tokens['FONT_SIZE_PAGE_TITLE'], f"{FONT_ROLES['page_title']['size']}px")
        self.assertEqual(tokens['FONT_SIZE_SECTION_TITLE'], f"{FONT_ROLES['section_title']['size']}px")
        self.assertEqual(tokens['FONT_SIZE_BODY'], f"{FONT_ROLES['body']['size']}px")
        self.assertEqual(tokens['FONT_SIZE_HELPER'], f"{FONT_ROLES['helper']['size']}px")
        self.assertEqual(tokens['FONT_SIZE_CAPTION'], f"{FONT_ROLES['caption']['size']}px")
        self.assertEqual(len(theme_manager.FONT_ROLES), 5)
        rendered_tokens = theme_manager.ThemeManager.instance().render('calm')
        self.assertNotIn('__FONT_UI__', rendered_tokens)

    def test_mono_font_uses_the_single_code_size(self):
        from ui.design_system import mono_font
        from ui.theme_manager import FONT_MONO_FAMILIES, FONT_MONO_POINT

        font = mono_font()
        self.assertEqual(tuple(font.families()), FONT_MONO_FAMILIES)
        self.assertEqual(font.pointSize(), FONT_MONO_POINT)
        self.assertTrue(font.fixedPitch())

    def test_role_selectors_share_the_token_size(self):
        from ui.theme_manager import FONT_ROLES, ThemeManager

        with open(os.path.join(PROJECT_DIR, 'resources', 'style.qss'), encoding='utf-8') as stream:
            source = stream.read()
        expectations = {
            '#page-title': ('__FONT_SIZE_PAGE_TITLE__', '__FONT_WEIGHT_PAGE_TITLE__', 'page_title'),
            '#sidebar_title': ('__FONT_SIZE_PAGE_TITLE__', '__FONT_WEIGHT_PAGE_TITLE__', 'page_title'),
            '#card-title': ('__FONT_SIZE_PAGE_TITLE__', '__FONT_WEIGHT_PAGE_TITLE__', 'page_title'),
            '#ops-title': ('__FONT_SIZE_PAGE_TITLE__', '__FONT_WEIGHT_PAGE_TITLE__', 'page_title'),
            '#dialog-title': ('__FONT_SIZE_PAGE_TITLE__', '__FONT_WEIGHT_PAGE_TITLE__', 'page_title'),
            '#confirm-title': ('__FONT_SIZE_PAGE_TITLE__', '__FONT_WEIGHT_PAGE_TITLE__', 'page_title'),
            '#empty-state-title': ('__FONT_SIZE_PAGE_TITLE__', '__FONT_WEIGHT_PAGE_TITLE__', 'page_title'),
            '#floating-title': ('__FONT_SIZE_PAGE_TITLE__', '__FONT_WEIGHT_PAGE_TITLE__', 'page_title'),
            '#section-title': ('__FONT_SIZE_SECTION_TITLE__', '__FONT_WEIGHT_SECTION_TITLE__', 'section_title'),
            '#zone-title': ('__FONT_SIZE_SECTION_TITLE__', '__FONT_WEIGHT_SECTION_TITLE__', 'section_title'),
            '#zone-title-inline': ('__FONT_SIZE_SECTION_TITLE__', '__FONT_WEIGHT_SECTION_TITLE__', 'section_title'),
            '#dashboard-section-title': ('__FONT_SIZE_SECTION_TITLE__', '__FONT_WEIGHT_SECTION_TITLE__', 'section_title'),
            '#daily-history-title': ('__FONT_SIZE_SECTION_TITLE__', '__FONT_WEIGHT_SECTION_TITLE__', 'section_title'),
            '#dashboard-task-title': ('__FONT_SIZE_SECTION_TITLE__', '__FONT_WEIGHT_SECTION_TITLE__', 'section_title'),
            '#field-hint': ('__FONT_SIZE_HELPER__', '__FONT_WEIGHT_HELPER__', 'helper'),
            '#small-label': ('__FONT_SIZE_HELPER__', '__FONT_WEIGHT_HELPER__', 'helper'),
            '#page-context': ('__FONT_SIZE_HELPER__', '__FONT_WEIGHT_HELPER__', 'helper'),
        }
        rendered = ThemeManager.instance().render('calm')
        for selector, (size_token, weight_token, role) in expectations.items():
            block = _rule_block(source, selector)
            self.assertIn(size_token, block, selector)
            self.assertIn(weight_token, block, selector)
            self.assertIsNone(
                re.search(r'font-size:\s*\d+(?:\.\d+)?px', block),
                f'{selector} still has a literal font-size',
            )
            self.assertIsNone(
                re.search(r'font-weight:\s*\d+', block),
                f'{selector} still has a literal font-weight',
            )
            self.assertEqual(
                _declared_font_size(rendered, selector),
                f"{FONT_ROLES[role]['size']}px",
                selector,
            )
            self.assertEqual(
                _declared_font_weight(rendered, selector),
                str(FONT_ROLES[role]['weight']),
                selector,
            )
        self.assertEqual(
            _declared_font_size(rendered, '#page-title'),
            _declared_font_size(rendered, '#sidebar_title'),
        )
        self.assertEqual(
            _declared_font_size(rendered, '#page-title'),
            _declared_font_size(rendered, '#card-title'),
        )


def _rule_block(qss: str, selector: str) -> str:
    matches = list(re.finditer(re.escape(selector) + r'\s*\{([^}]*)\}', qss))
    if not matches:
        raise AssertionError(f'missing rule {selector}')
    for match in matches:
        if 'font-size' in match.group(1):
            return match.group(1)
    return matches[-1].group(1)


def _declared_font_size(qss: str, selector: str) -> str:
    found = re.search(r'font-size:\s*([^;]+)', _rule_block(qss, selector))
    if found is None:
        raise AssertionError(f'{selector} has no font-size')
    return found.group(1).strip()


def _declared_font_weight(qss: str, selector: str) -> str:
    found = re.search(r'font-weight:\s*([^;]+)', _rule_block(qss, selector))
    if found is None:
        raise AssertionError(f'{selector} has no font-weight')
    return found.group(1).strip()


if __name__ == '__main__':
    unittest.main()
