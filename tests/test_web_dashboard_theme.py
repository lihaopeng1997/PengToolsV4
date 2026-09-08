# -*- coding: utf-8 -*-
"""Web Dashboard 语义主题与 Token 归一化静态/逻辑回归测试。"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui import web_shell


class DashboardThemeStaticAuditTest(unittest.TestCase):
    """验证 DashboardApp.vue 主题权威归于 ThemeManager，无遗留硬编码与分支覆盖。"""

    @classmethod
    def setUpClass(cls):
        cls.vue_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'DashboardApp.vue')
        with open(cls.vue_path, 'r', encoding='utf-8') as f:
            cls.vue_source = f.read()

    def test_required_semantic_tokens_present(self):
        required_tokens = [
            '--primary-grad-start',
            '--primary-grad-end',
            '--aurora-start',
            '--aurora-mid',
            '--aurora-end',
            '--app-bg',
            '--surface',
            '--surface-soft',
            '--elevated-surface',
            '--warning',
            '--success',
            '--accent-indigo',
            '--accent-cyan',
            '--accent-rose',
            '--accent-emerald',
        ]
        for token in required_tokens:
            self.assertIn(token, self.vue_source, f'DashboardApp.vue 缺失必须语义 token: {token}')

    def test_legacy_palette_literals_absent(self):
        legacy_literals = [
            '#6366F1',
            '#818CF8',
            '#0EA5E9',
            '#38BDF8',
            '#EC4899',
            '#F472B6',
            '#10B981',
            '#34D399',
        ]
        for literal in legacy_literals:
            self.assertNotIn(
                literal.lower(),
                self.vue_source.lower(),
                f'DashboardApp.vue 仍包含旧硬编码调色板: {literal}',
            )

    def test_no_duplicated_theme_color_overrides(self):
        # 不得再有第二套针对 clear/warm 的独立调色板覆盖
        self.assertNotIn('html[data-theme="clear"]', self.vue_source)
        self.assertNotIn('html[data-theme="warm"]', self.vue_source)

    def test_no_hardcoded_hex_colors_in_styles(self):
        style_match = re.search(r'<style[^>]*>(.*?)</style>', self.vue_source, re.DOTALL)
        self.assertTrue(style_match, 'DashboardApp.vue 必须包含 <style> 标签')
        style_content = style_match.group(1)
        hex_colors = re.findall(r'#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b', style_content)
        self.assertEqual(hex_colors, [], f'DashboardApp.vue <style> 仍包含硬编码 hex 颜色: {hex_colors}')

    def test_aurora_and_primary_gradients_consume_semantic_tokens(self):
        self.assertIn('var(--aurora-start)', self.vue_source)
        self.assertIn('var(--aurora-mid)', self.vue_source)
        self.assertIn('var(--aurora-end)', self.vue_source)
        self.assertIn('var(--primary-grad-start)', self.vue_source)
        self.assertIn('var(--primary-grad-end)', self.vue_source)

    def test_no_self_referential_css_variables(self):
        # 禁止出现 --foo: var(--foo) 自引用
        self_refs = re.findall(r'--([a-z0-9-]+)\s*:\s*var\(--\1\)', self.vue_source)
        self.assertEqual(self_refs, [], f'DashboardApp.vue 存在自引用 CSS 变量: {self_refs}')

    def test_shadow_tokens_have_geometry(self):
        # 阴影 token 是颜色值，不能直接作为裸 box-shadow: var(--shadow-...)
        bare_shadows = re.findall(r'box-shadow\s*:\s*var\(--shadow-[a-z0-9]+\)', self.vue_source)
        self.assertEqual(bare_shadows, [], f'DashboardApp.vue 存在缺少几何偏移的裸 shadow token: {bare_shadows}')
        # 必须存在几何偏移 + 语义 shadow token
        self.assertIn('0 2px 8px var(--shadow-l1)', self.vue_source)
        self.assertIn('0 4px 14px var(--shadow-l2)', self.vue_source)
        self.assertIn('0 6px 16px var(--shadow-l2)', self.vue_source)


class RgbaCssNormalizationTest(unittest.TestCase):
    """验证 Qt 0-255 alpha 到 CSS 0-1 alpha 的归一化逻辑与 Bridge 契约。"""

    @classmethod
    def setUpClass(cls):
        cls.bridge_path = os.path.join(ROOT, 'frontend', 'src', 'shared', 'bridge.ts')
        with open(cls.bridge_path, 'r', encoding='utf-8') as f:
            cls.bridge_source = f.read()

    def _run_node_normalization(self, input_val):
        js_code = """
        function normalizeCssTokenValue(value) {
          if (typeof value !== 'string') return value;
          return value.replace(
            /rgba\\(\\s*(\\d+)(\\s*,\\s*)(\\d+)(\\s*,\\s*)(\\d+)(\\s*,\\s*)([\\d.]+)\\s*\\)/gi,
            (_match, r, sep1, g, sep2, b, sep3, aStr) => {
              const a = Number(aStr);
              if (Number.isFinite(a) && a > 1 && a <= 255) {
                const normalizedA = parseFloat((a / 255).toFixed(4));
                return `rgba(${r}${sep1}${g}${sep2}${b}${sep3}${normalizedA})`;
              }
              return _match;
            }
          );
        }
        console.log(normalizeCssTokenValue(process.argv[1]));
        """
        res = subprocess.run(
            ['node', '-e', js_code, input_val],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()

    def test_alpha_236_normalized(self):
        out = self._run_node_normalization('rgba(255, 254, 251, 236)')
        self.assertEqual(out, 'rgba(255, 254, 251, 0.9255)')

    def test_alpha_45_normalized(self):
        out = self._run_node_normalization('rgba(43, 48, 74, 45)')
        self.assertEqual(out, 'rgba(43, 48, 74, 0.1765)')

    def test_css_alpha_less_than_one_unchanged(self):
        out = self._run_node_normalization('rgba(16, 185, 129, 0.45)')
        self.assertEqual(out, 'rgba(16, 185, 129, 0.45)')

    def test_hex_and_px_unchanged(self):
        self.assertEqual(self._run_node_normalization('#5B5FC7'), '#5B5FC7')
        self.assertEqual(self._run_node_normalization('32px'), '32px')

    def test_bridge_source_wiring(self):
        self.assertIn('export function normalizeCssTokenValue(', self.bridge_source)
        self.assertIn('normalizeCssTokenValue(value)', self.bridge_source)
        self.assertIn("document.documentElement.setAttribute('data-theme', themeId)", self.bridge_source)
        self.assertIn("document.documentElement.classList.toggle('dark', isDark)", self.bridge_source)
        self.assertIn('rootStyle.setProperty(varName,', self.bridge_source)

    def test_bridge_public_api_unchanged(self):
        bridge = web_shell.HomeBridge()
        for method in ['navigate', 'openPalette', 'navModel', 'homeUsername', 'dashboardSummary', 'pageReady', 'set_theme_payload']:
            self.assertTrue(hasattr(bridge, method), f'HomeBridge 缺少公有方法/槽: {method}')
        for sig in ['navigateRequested', 'paletteRequested', 'activeChanged', 'pageReadyReceived', 'themeChanged']:
            self.assertTrue(hasattr(bridge, sig), f'HomeBridge 缺少信号: {sig}')

    def test_all_dashboard_icons_defined_in_sprite(self):
        """Regression: DashboardApp.vue 中使用到的所有 #i-* 图标必须在 IconSprite.vue 中存在。"""
        vue_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'DashboardApp.vue')
        sprite_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'IconSprite.vue')
        with open(vue_path, 'r', encoding='utf-8') as f:
            vue_source = f.read()
        with open(sprite_path, 'r', encoding='utf-8') as f:
            sprite_source = f.read()
        used = set(re.findall(r'#i-([a-zA-Z0-9_-]+)', vue_source))
        defined = set(re.findall(r'id=["\']i-([a-zA-Z0-9_-]+)["\']', sprite_source))
        missing = used - defined
        self.assertFalse(missing, f'IconSprite.vue 缺少图标定义: {missing}')


if __name__ == '__main__':
    unittest.main()
