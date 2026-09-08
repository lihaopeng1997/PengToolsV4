# -*- coding: utf-8 -*-
"""Prism 图标体系与品牌资产专项测试 (PRISM-UI-P02)。

机械验证：
1. SVG 安全与几何契约：viewBox 0 0 24 24, stroke-width 1.6, currentColor, round cap/join, 无危险标签/URL/栅格。
2. 角色与导航完整性：ICON_FILES (55 角色)、NAV_ICON_BY_INDEX (0..23, 22 叶子 + 2 父级)、NOTICE_ICON_ROLES 全部可解析。
3. 品牌资产与无旧绿色泄漏：Prism 品牌资源存在，无 #1F3D32 / #668C78 等旧绿色残留。
4. 多层 ICO 规范：pengtools-app-v2.ico 与 pengtools-taskbar-hc.ico 必须包含 16/24/32/48/64/128/256 多尺寸层。
5. 运行时渲染与缓存：QApplication 下关键角色在 Calm/Black 主题及不同尺寸下均可正确渲染，clear_icon_cache() 正确清空所有缓存。
"""
from __future__ import annotations

import glob
import os
import re
import struct
import sys
import unittest
import xml.etree.ElementTree as ET

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from ui.icons import (
    BRAND_ROLES,
    ICON_FILES,
    NAV_ICON_BY_INDEX,
    NOTICE_ICON_ROLES,
    apply_icon,
    brand_file,
    brand_pixmap,
    brand_taskbar_icon,
    brand_tray_icon,
    brand_window_icon,
    clear_icon_cache,
    icon_file,
    icon_pixmap,
    known_roles,
    qicon,
)
from ui.theme_manager import ThemeManager

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QIcon, QPixmap
    from PyQt6.QtWidgets import QApplication, QComboBox, QPushButton
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


class PrismSvgContractTests(unittest.TestCase):
    """resources/icons/*.svg 几何规范与安全契约测试。"""

    def setUp(self):
        self.icons_dir = os.path.join(PROJECT_DIR, 'resources', 'icons')
        self.svg_files = sorted(glob.glob(os.path.join(self.icons_dir, '*.svg')))
        self.assertGreaterEqual(len(self.svg_files), 40, "SVG 图标数量异常偏少")

    def test_svg_xml_validity_and_attributes(self):
        """所有普通 SVG 必须符合 24x24、stroke-width 1.6、round cap/join、currentColor 规范。"""
        for path in self.svg_files:
            fname = os.path.basename(path)
            try:
                tree = ET.parse(path)
            except Exception as e:
                self.fail(f"{fname} XML 解析失败: {e}")
            root = tree.getroot()
            self.assertTrue(root.tag.endswith('svg'), f"{fname} 根元素不是 <svg>")
            self.assertEqual(root.attrib.get('viewBox'), '0 0 24 24', f"{fname} viewBox 必须为 0 0 24 24")
            self.assertEqual(root.attrib.get('stroke-width'), '1.6', f"{fname} stroke-width 必须统一为 1.6")
            self.assertEqual(root.attrib.get('stroke'), 'currentColor', f"{fname} stroke 必须为 currentColor")
            self.assertEqual(root.attrib.get('stroke-linecap'), 'round', f"{fname} stroke-linecap 必须为 round")
            self.assertEqual(root.attrib.get('stroke-linejoin'), 'round', f"{fname} stroke-linejoin 必须为 round")

    def test_svg_security_and_no_external_dependencies(self):
        """SVG 安全契约：无 script, foreignObject, 外部网络 URL, javascript, base64 栅格图像, 硬编码语义色。"""
        for path in self.svg_files:
            fname = os.path.basename(path)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()

            tree = ET.parse(path)
            for elem in tree.iter():
                tag = elem.tag.split('}')[-1].lower()
                self.assertNotIn(tag, {'script', 'foreignobject', 'image'}, f"{fname} 包含禁用标签 <{tag}>")
                for attr_name, attr_val in elem.attrib.items():
                    # 允许标准 xmlns
                    if 'xmlns' in attr_name.lower():
                        continue
                    lowered = attr_val.lower()
                    for forbidden in ('http://', 'https://', 'javascript:', 'data:'):
                        self.assertNotIn(forbidden, lowered, f"{fname} 属性 {attr_name} 包含非法引用 {attr_val}")
                    # 普通图标禁止硬编码十六进制颜色
                    self.assertFalse(
                        bool(re.search(r'#[0-9a-fA-F]{3,8}', attr_val)),
                        f"{fname} 包含硬编码十六进制颜色 {attr_val}"
                    )


class PrismRoleAndNavIntegrityTests(unittest.TestCase):
    """图标角色与导航映射完整性。"""

    def test_icon_files_all_resolve(self):
        """ICON_FILES 声明的所有角色在磁盘上均能找到物理文件。"""
        self.assertGreaterEqual(len(ICON_FILES), 50)
        for role in ICON_FILES:
            path = icon_file(role)
            self.assertTrue(path, f"角色 {role} 解析路径为空")
            self.assertTrue(os.path.exists(path), f"角色 {role} 对应文件不存在: {path}")

    def test_nav_icon_by_index_all_resolve(self):
        """NAV_ICON_BY_INDEX 覆盖 0..23 索引，22 个叶子与 2 个父级导航均可解析。"""
        self.assertEqual(len(NAV_ICON_BY_INDEX), 24)
        parent_indices = {14, 15}  # SQL 控制台、模型
        leaf_indices = set(range(24)) - parent_indices
        self.assertEqual(len(leaf_indices), 22)
        self.assertEqual(len(parent_indices), 2)

        for idx in range(24):
            self.assertIn(idx, NAV_ICON_BY_INDEX, f"缺失导航索引 {idx}")
            role = NAV_ICON_BY_INDEX[idx]
            path = icon_file(role)
            self.assertTrue(path, f"导航索引 {idx} 对应角色 {role} 路径为空")
            self.assertTrue(os.path.exists(path), f"导航索引 {idx} 对应图标不存在: {path}")

    def test_notice_icon_roles_resolve(self):
        """NOTICE_ICON_ROLES 状态图标映射均可解析。"""
        for kind, role in NOTICE_ICON_ROLES.items():
            path = icon_file(role)
            self.assertTrue(path, f"状态 {kind} 对应角色 {role} 路径为空")
            self.assertTrue(os.path.exists(path), f"状态 {kind} 对应图标不存在: {path}")

    def test_known_roles_matches_icon_files(self):
        """known_roles() 返回全部 ICON_FILES key。"""
        self.assertEqual(set(known_roles()), set(ICON_FILES.keys()))


class PrismBrandAssetsAndGreenPurgeTests(unittest.TestCase):
    """晴空棱镜品牌资源规范与旧绿色彻底清除测试。"""

    def test_brand_files_exist(self):
        """五大核心品牌资源角色均能找到物理文件。"""
        for role in ('app', 'app_mark', 'app_taskbar', 'tray', 'floating'):
            path = brand_file(role)
            self.assertTrue(path, f"brand_file({role}) 解析为空")
            self.assertTrue(os.path.exists(path), f"brand_file({role}) 文件不存在: {path}")

    def test_brand_assets_no_old_green_residue(self):
        """严格校验 ui/icons.py 与 resources/brand/ 所有文本资源，无任何旧绿色色值与文案残留。"""
        forbidden_patterns = [
            r'#668C78',
            r'#1F3D32',
            r'#1B2A22',
            r'#2F6B52',
            r'#F4F7F5',
            r'#DDEADE',
            r'深墨绿',
            r'静谧.*绿',
            r'绿底',
        ]

        # 1. ui/icons.py
        icons_py = os.path.join(PROJECT_DIR, 'ui', 'icons.py')
        with open(icons_py, 'r', encoding='utf-8') as f:
            icons_text = f.read()
        for p in forbidden_patterns:
            matches = re.findall(p, icons_text, re.IGNORECASE)
            self.assertEqual(matches, [], f"ui/icons.py 中残留旧品牌绿色: {p}")

        # 2. resources/brand/ 下的非二进制文件
        brand_dir = os.path.join(PROJECT_DIR, 'resources', 'brand')
        for fname in os.listdir(brand_dir):
            if fname.endswith(('.ico', '.png')):
                continue
            fpath = os.path.join(brand_dir, fname)
            with open(fpath, 'r', encoding='utf-8') as f:
                b_text = f.read()
            for p in forbidden_patterns:
                matches = re.findall(p, b_text, re.IGNORECASE)
                self.assertEqual(matches, [], f"resources/brand/{fname} 中残留旧品牌绿色: {p}")

    def test_app_mark_contains_prism_identity(self):
        """pengtools-app-mark.svg 包含晴空棱镜紫色渐变与核心元素。"""
        app_mark_path = brand_file('app_mark')
        with open(app_mark_path, 'r', encoding='utf-8') as f:
            svg = f.read()
        self.assertIn('#9e8cf2', svg.lower())
        self.assertIn('#6453d5', svg.lower())
        self.assertIn('#d7fbf2', svg.lower())

    def test_ico_layers_contain_required_resolutions(self):
        """使用 struct 纯二进制解析 ICO 目录，确保 16/24/32/48/64/128/256 完整包含。"""
        required_sizes = {16, 24, 32, 48, 64, 128, 256}

        for fname in ('pengtools-app-v2.ico', 'pengtools-taskbar-hc.ico'):
            ico_path = os.path.join(PROJECT_DIR, 'resources', 'brand', fname)
            with open(ico_path, 'rb') as f:
                data = f.read()

            self.assertGreaterEqual(len(data), 6, f"{fname} 数据过短")
            reserved, ico_type, count = struct.unpack('<HHH', data[:6])
            self.assertEqual(reserved, 0, f"{fname} 保留位必须为 0")
            self.assertEqual(ico_type, 1, f"{fname} 类型必须为 1 (ICO)")
            self.assertGreaterEqual(count, 7, f"{fname} 层数少于 7 层")

            sizes = set()
            for i in range(count):
                offset = 6 + i * 16
                w, h = struct.unpack('<BB', data[offset:offset + 2])
                w = 256 if w == 0 else w
                sizes.add(w)

            missing = required_sizes - sizes
            self.assertFalse(missing, f"{fname} 缺失必需分辨率层: {missing}, 当前存在: {sorted(sizes)}")


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 未就绪')
class PrismRuntimeRenderAndCacheTests(unittest.TestCase):
    """QApplication 环境下的运行时渲染与缓存测试。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([sys.argv[0], '-platform', 'offscreen'])

    def setUp(self):
        clear_icon_cache()

    def tearDown(self):
        clear_icon_cache()

    def test_brand_runtime_icons_not_null(self):
        """窗口/任务栏/托盘/悬浮等品牌运行时接口均返回非空有效图标。"""
        win_icon = brand_window_icon()
        self.assertFalse(win_icon.isNull(), "brand_window_icon() 返回空图标")

        taskbar_icon = brand_taskbar_icon()
        self.assertFalse(taskbar_icon.isNull(), "brand_taskbar_icon() 返回空图标")

        tray_icon = brand_tray_icon()
        self.assertFalse(tray_icon.isNull(), "brand_tray_icon() 返回空图标")

        # 30px visible mark (用于 QuickPanel 52x52 / 44x44 / 30px)
        floating_pix = brand_pixmap('floating', size=30)
        self.assertFalse(floating_pix.isNull(), "brand_pixmap('floating', 30) 返回空 Pixmap")
        self.assertEqual(floating_pix.width(), 30)
        self.assertEqual(floating_pix.height(), 30)

    def test_runtime_render_smoke_calm_and_black(self):
        """关键功能图标在 Calm 与 Black 主题下以 16/18/20/24 渲染均有效。"""
        key_roles = [
            'home', 'requirements', 'database', 'chat', 'workbench',
            'settings', 'search', 'delete', 'success', 'warning', 'error',
        ]
        sizes = (16, 18, 20, 24)

        for theme in ('calm', 'black'):
            ThemeManager.instance().apply(self.app, theme)
            for role in key_roles:
                for sz in sizes:
                    icon = qicon(role, size=sz)
                    self.assertFalse(icon.isNull(), f"主题 {theme} 下角色 {role} (尺寸 {sz}) qicon 为空")
                    pix = icon.pixmap(sz, sz)
                    self.assertFalse(pix.isNull(), f"主题 {theme} 下角色 {role} (尺寸 {sz}) pixmap 为空")

    def test_apply_icon_smoke(self):
        """apply_icon 能够正确为 QPushButton 设置非空图标与尺寸。"""
        btn = QPushButton()
        apply_icon(btn, 'save', size=18)
        self.assertFalse(btn.icon().isNull())
        self.assertEqual(btn.iconSize().width(), 18)
        self.assertEqual(btn.iconSize().height(), 18)

    def test_clear_icon_cache_purges_both_caches(self):
        """clear_icon_cache() 正确清空 icon_pixmap 和 brand_pixmap 的 lru_cache。"""
        # 填充缓存
        _ = icon_pixmap('home', 20)
        _ = brand_pixmap('floating', 28)

        info1 = icon_pixmap.cache_info()
        info2 = brand_pixmap.cache_info()
        self.assertGreater(info1.currsize, 0)
        self.assertGreater(info2.currsize, 0)

        # 执行清理
        clear_icon_cache()

        info1_after = icon_pixmap.cache_info()
        info2_after = brand_pixmap.cache_info()
        self.assertEqual(info1_after.currsize, 0, "icon_pixmap 缓存未被清空")
        self.assertEqual(info2_after.currsize, 0, "brand_pixmap 缓存未被清空")

    def test_dropdown_tint_injection(self):
        """ThemeManager.render() 生成的 QSS 中，__DROPDOWN_ARROW__ 必须注入主题 tint 物理 SVG，不得依赖裸 currentColor。"""
        tm = ThemeManager.instance()
        for theme in ('calm', 'black'):
            qss = tm.render(theme)
            palette = tm.palette(theme)
            expected_tint = palette.get('TEXT_MUTED') or palette.get('PRIMARY_ACTIVE')
            self.assertTrue(expected_tint, f"主题 {theme} 缺少 TEXT_MUTED/PRIMARY_ACTIVE token")

            # 从 QSS 提取 QComboBox 下拉箭头图片路径
            matches = re.findall(r'QComboBox::down-arrow\s*\{[^}]*image:\s*url\(([^)]+)\)', qss)
            self.assertTrue(matches, f"主题 {theme} QSS 未找到 QComboBox::down-arrow image:url")
            raw_url = matches[0].strip('\'"')
            self.assertTrue(os.path.exists(raw_url), f"主题 {theme} dropdown SVG 物理文件不存在: {raw_url}")

            with open(raw_url, 'r', encoding='utf-8') as f:
                svg_content = f.read()

            self.assertNotIn('currentColor', svg_content, f"主题 {theme} dropdown SVG 残留 currentColor")
            self.assertIn(expected_tint, svg_content, f"主题 {theme} dropdown SVG 未包含预期主题色 {expected_tint}")

    def test_qcombobox_render_smoke(self):
        """Calm 与 Black 主题下，应用完整 QSS 渲染 QComboBox 正常展示、无崩溃。"""
        tm = ThemeManager.instance()
        for theme in ('calm', 'black'):
            tm.apply(self.app, theme)
            combo = QComboBox()
            combo.addItems(['Option 1', 'Option 2', 'Option 3'])
            combo.setCurrentIndex(0)
            combo.show()
            self.app.processEvents()
            pix = combo.grab()
            self.assertFalse(pix.isNull(), f"主题 {theme} 下 QComboBox grab 返回空 Pixmap")
            self.assertGreater(pix.width(), 0)
            self.assertGreater(pix.height(), 0)
            combo.close()


if __name__ == '__main__':
    unittest.main()
