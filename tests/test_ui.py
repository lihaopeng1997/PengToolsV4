# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    from PyQt6.QtCore import QDate, QPoint, Qt
    from PyQt6.QtGui import QIcon
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
    from panels.sql_panel import SqlToolPanel
    from panels.credit_panel import CreditCodePanel
    from panels.docx_panel import DocxUpdatePanel
    from panels.gateway_panel import GatewayDecodePanel
    from panels.ops_panel import OpsPanel
    from panels.settings_panel import SettingsPanel
    from panels.personal_panel import PersonalPanel
    from panels.requirement_panel import RequirementAttachmentDialog, RequirementPanel
    from main_window import MainWindow
    from ui.tray_service import TrayService
    from config import DEFAULT_SETTINGS
    from ui.quick_panel import QuickPanel
    from ui.aurora_progress import AuroraProgress
    from ui.keep_awake_service import KeepAwakeService
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


class _MainWindowStub:
    def showNormal(self):
        pass

    def raise_(self):
        pass

    def activateWindow(self):
        pass

    def navigate_to(self, _index):
        pass


class _CallRecorder:
    def __init__(self):
        self.called = False
        self.ignored = False
        self.minimized = False
        self.hidden = False

    def unregister(self):
        self.called = True

    def close(self):
        self.called = True

    def hide(self):
        self.called = True
        self.hidden = True

    def accept(self):
        self.called = True

    def ignore(self):
        self.ignored = True

    def showMinimized(self):
        self.minimized = True

    def exit_application(self):
        self.called = True


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 is not installed in this Python runtime')
class UiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_floating_toolbar_restores_position_and_can_hide(self):
        panel = QuickPanel(_MainWindowStub())
        panel.show()
        target = QPoint(320, 240)
        panel.move(target)
        panel._compact_position = QPoint(target)
        # 收起态 toggle 可见；展开态 toggle 隐藏，用 compact 锚点校验
        for _ in range(10):
            panel.toggle_expanded()
            self.assertTrue(panel.expanded)
            QTest.qWait(40)
            self.app.processEvents()
            panel.toggle_expanded()
            QTest.qWait(20)
            self.app.processEvents()
            self.assertEqual(panel.pos(), target)
        self.assertFalse(panel.expanded)
        self.assertEqual(panel.pos(), target)
        panel.close_toolbar()
        self.assertTrue(panel.isHidden())
        panel.show_panel()
        self.assertFalse(panel.isHidden())
        panel.close()

    def test_floating_shortcuts_default_and_rebuild(self):
        panel = QuickPanel(_MainWindowStub())
        self.assertEqual(panel.current_shortcuts(), [10, 2, 9, 5])
        self.assertEqual(len(panel.tool_buttons), 4)
        # 无文字 P / ×
        self.assertEqual(panel.toggle_btn.text(), '')
        panel.apply_shortcuts([10, 5], private_unlocked=False)
        self.assertEqual(panel.current_shortcuts(), [10, 5])
        self.assertEqual(len(panel.tool_buttons), 2)
        panel.apply_shortcuts([10, 2, 9, 5, 1, 4, 6], private_unlocked=True)
        self.assertEqual(len(panel.current_shortcuts()), 6)
        panel.close()

    def test_floating_preview_shows_document_and_vin_keys(self):
        class Credit:
            def __init__(self):
                self._personal_results = [{'name': '张三', 'document': '110101199001011234'}]
                self._unit_results = []

            def _generate_personal(self):
                self._personal_results = [{'name': '李四', 'document': '110101199003033456'}]

            def _generate_unit(self):
                self._unit_results = [{'name': '鑫润科技有限公司', 'document': '91110000MA0012345X'}]

        class Vin:
            def __init__(self):
                self._results = []

            def _generate(self):
                self._results = [{
                    'plate': '粤BD12345',
                    'vin': 'LSV12345678901234',
                    'engine_no': 'TZ180XSA000001',
                }]

        class Win(_MainWindowStub):
            def __init__(self):
                self.credit_panel = Credit()
                self.vin_panel = Vin()

        panel = QuickPanel(Win())
        panel.show()
        panel._open_result_preview(1)
        self.assertTrue(panel.preview.isVisible())
        self.assertFalse(panel.grid_host.isVisible())
        self.assertEqual(panel.preview_table.horizontalHeaderItem(0).text(), '名称')
        self.assertEqual(panel.preview_table.horizontalHeaderItem(1).text(), '证件号码')
        self.assertEqual(panel.preview_table.item(0, 0).text(), '张三')
        self.assertEqual(panel.preview_table.item(0, 1).text(), '110101199001011234')
        panel._copy_preview_item(panel.preview_table.item(0, 0))
        self.assertEqual(QApplication.clipboard().text(), '张三')
        panel._copy_preview_item(panel.preview_table.item(0, 1))
        self.assertEqual(QApplication.clipboard().text(), '110101199001011234')
        panel.preview_gen_personal.click()
        self.assertGreater(panel.preview_table.rowCount(), 0)
        self.assertGreaterEqual(len(panel.preview_table.item(0, 1).text()), 15)
        panel.preview_gen_unit.click()
        self.assertGreater(panel.preview_table.rowCount(), 0)
        self.assertEqual(len(panel.preview_table.item(0, 1).text()), 18)
        self.assertEqual(panel.preview_gen_personal.minimumWidth(), panel.preview_gen_unit.minimumWidth())
        self.assertEqual(panel.preview_gen_personal.objectName(), panel.preview_gen_unit.objectName())
        panel._open_result_preview(4)
        self.assertTrue(panel.preview_gen_vin.isVisible())
        self.assertFalse(panel.preview_gen_personal.isVisible())
        panel.preview_gen_vin.click()
        self.assertEqual(panel.preview_table.horizontalHeaderItem(0).text(), '车牌号')
        self.assertGreater(panel.preview_table.rowCount(), 0)
        self.assertTrue(panel.preview_table.item(0, 0).text())
        self.assertEqual(len(panel.preview_table.item(0, 1).text()), 17)
        self.assertTrue(panel.preview_table.item(0, 2).text())
        panel._copy_preview_item(panel.preview_table.item(0, 1))
        self.assertEqual(QApplication.clipboard().text(), panel.preview_table.item(0, 1).text())
        panel.close()

    def test_floating_learning_search_without_opening_workspace(self):
        entries = [
            {
                'id': 'k1', 'title': '网关解密步骤', 'category': 'learning',
                'content': '先取密钥再解密报文', 'tags': '网关', 'source': '手工',
            },
            {
                'id': 'k2', 'title': 'VIN 规则', 'category': 'auto_business',
                'content': '车架号十七位', 'tags': 'vin', 'source': '手工',
            },
        ]

        class Win(_MainWindowStub):
            def __init__(self):
                self.navigated = []
                self.shown = False

            def showNormal(self):
                self.shown = True

            def navigate_to(self, index):
                self.navigated.append(index)

        win = Win()
        panel = QuickPanel(win)
        panel.show()
        with patch('tools.personal_knowledge.collect_knowledge_entries', return_value=entries), \
                patch('tools.personal_knowledge.rebuild_search_index'):
            panel._activate(8)
        self.assertFalse(win.shown)
        self.assertEqual(win.navigated, [])
        self.assertTrue(panel.learn_search.isVisible())
        self.assertFalse(panel.grid_host.isVisible())
        self.assertFalse(panel.preview.isVisible())
        self.assertEqual(panel.learn_list.count(), 2)
        panel.learn_search_edit.setText('网关')
        panel._run_learning_search()
        self.assertEqual(panel.learn_list.count(), 1)
        self.assertIn('先取密钥再解密报文', panel.learn_content.toPlainText())
        panel._copy_learning_entry()
        self.assertEqual(QApplication.clipboard().text(), '先取密钥再解密报文')
        panel.learn_search_edit.setText('不存在的关键字xyz')
        panel._run_learning_search()
        self.assertEqual(panel.learn_list.count(), 0)
        self.assertIn('没有匹配', panel.learn_hint.text())
        panel.learn_back.click()
        self.assertFalse(panel.learn_search.isVisible())
        self.assertTrue(panel.grid_host.isVisible())
        panel.close()

    def test_floating_learning_search_uses_live_knowledge_tab(self):
        class Tab:
            def all_entries(self):
                return [{
                    'id': 'live', 'title': '现场条目', 'category': 'learning',
                    'content': '未落盘也能搜到', 'tags': '', 'source': '手工',
                }]

        class Panel:
            knowledge_tab = Tab()

        class Win(_MainWindowStub):
            personal_panel = Panel()

            def navigate_to(self, _index):
                raise AssertionError('悬浮搜索不应打开主窗口')

        panel = QuickPanel(Win())
        panel._activate(8)
        self.assertTrue(panel.learn_search.isVisible())
        self.assertEqual(panel.learn_list.count(), 1)
        self.assertIn('未落盘也能搜到', panel.learn_content.toPlainText())
        panel.close()

    def test_brand_icon_resources_exist(self):
        from ui.icons import brand_file, brand_pixmap, brand_window_icon
        self.assertTrue(os.path.exists(brand_file('app')))
        self.assertTrue(os.path.exists(brand_file('floating')))
        self.assertTrue(os.path.exists(brand_file('tray')))
        pix = brand_pixmap('floating', size=28, tint='#4F735F')
        self.assertFalse(pix.isNull())
        self.assertFalse(brand_window_icon().isNull())

    def test_private_tools_are_hidden_until_version_easter_egg_unlocks(self):
        settings = dict(DEFAULT_SETTINGS, private_unlocked=False)
        with patch('main_window.load_settings', return_value=settings):
            window = MainWindow()
        try:
            # 仅自我学习(8)可隐藏；日报(9)、需求(10)必须常显（AGENTS / V2 导航模型）
            self.assertTrue(window.nav_buttons[8].isHidden())
            self.assertFalse(window.nav_buttons[9].isHidden())
            self.assertFalse(window.nav_buttons[10].isHidden())
            self.assertIsNotNone(window.settings_button)
            with patch('main_window.QInputDialog.getText', return_value=('Lihp', True)):
                self.assertTrue(window._unlock_private_tools())
            self.assertFalse(window.nav_buttons[8].isHidden())
            self.assertEqual(window._current_nav_index, 8)
            self.assertIs(window.stack.currentWidget(), window.personal_panel)
        finally:
            window.hotkey_service.unregister()
            window.quick_panel.close_toolbar()
            window.tray_service.hide()
            window.keep_awake_service.stop()
            window.hide()
            window.deleteLater()

    def test_requirement_sql_and_daily_link_targets_exist(self):
        personal = PersonalPanel()
        requirement = RequirementPanel()
        self.assertEqual(personal.stack.count(), 2)
        self.assertTrue(hasattr(requirement, 'send_to_sql'))
        self.assertTrue(hasattr(requirement, 'send_to_docx'))
        self.assertTrue(hasattr(requirement, 'add_to_daily'))
        self.assertEqual(requirement.kind_filter.count(), 3)
        self.assertEqual(requirement.scan_btn.text(), '扫描需求目录')
        self.assertEqual(requirement.checkout_btn.text(), '检出代码')
        self.assertEqual(requirement.update_all_btn.text(), '更新全部')
        # 文件表：名称 / 类型 / 修改时间 / 大小 / 路径
        self.assertEqual(requirement.file_tree.columnCount(), 5)
        self.assertEqual(
            [requirement.file_tree.headerItem().text(i) for i in range(5)],
            ['名称', '类型', '修改时间', '大小', '路径'],
        )

    def test_main_window_close_event_exits_all_auxiliary_services(self):
        fake_window = type('FakeMainWindow', (), {})()
        fake_window._settings = {'close_ask_each_time': False, 'close_default_action': 'exit'}
        fake_window._force_exit = False
        fake_window._shutting_down = False
        fake_window.hotkey_service = _CallRecorder()
        fake_window.quick_panel = _CallRecorder()
        fake_window.tray_service = _CallRecorder()
        fake_window._shutdown = lambda event: MainWindow._shutdown(fake_window, event)
        event = _CallRecorder()
        MainWindow.closeEvent(fake_window, event)
        self.assertTrue(fake_window.hotkey_service.called)
        self.assertTrue(fake_window.quick_panel.called)
        self.assertTrue(fake_window.tray_service.called)
        self.assertTrue(event.called)

    def test_main_window_close_can_hide_from_taskbar_without_stopping_services(self):
        fake_window = type('FakeMainWindow', (), {})()
        fake_window._settings = {'close_ask_each_time': False, 'close_default_action': 'minimize'}
        fake_window._force_exit = False
        fake_window.hotkey_service = _CallRecorder()
        fake_window.quick_panel = _CallRecorder()
        fake_window.tray_service = _CallRecorder()
        hidden = _CallRecorder()
        fake_window.hide = hidden.hide
        event = _CallRecorder()
        MainWindow.closeEvent(fake_window, event)
        self.assertTrue(event.ignored)
        self.assertTrue(hidden.hidden)
        self.assertFalse(fake_window.hotkey_service.called)
        self.assertFalse(fake_window.quick_panel.called)
        self.assertFalse(fake_window.tray_service.called)

    def test_close_prompt_uses_configured_default_button(self):
        fake_window = type('FakeMainWindow', (), {
            'language': 'zh',
            '_settings': {'close_default_action': 'exit'},
        })()
        with patch('main_window.ask_close_action', return_value=('exit', False)) as ask:
            self.assertEqual(MainWindow._ask_close_action(fake_window), ('exit', False))
            ask.assert_called_once_with(fake_window, language='zh', default_action='exit')

    def test_close_event_respects_dont_ask_again(self):
        """退出弹窗勾选不再提示时写回 settings 并下次不询问。"""
        from config import DEFAULT_SETTINGS

        fake_window = type('FakeMainWindow', (), {})()
        fake_window._settings = dict(DEFAULT_SETTINGS, close_ask_each_time=True, close_default_action='minimize')
        fake_window._force_exit = False
        fake_window.language = 'zh'
        fake_window.hotkey_service = _CallRecorder()
        fake_window.quick_panel = _CallRecorder()
        fake_window.tray_service = _CallRecorder()
        fake_window.hide = _CallRecorder().hide
        loaded = []
        fake_window.settings_panel = type('SP', (), {'load_values': lambda self, s: loaded.append(dict(s))})()
        fake_window._ask_close_action = lambda: ('minimize', True)

        with patch('main_window.save_settings', side_effect=lambda s: dict(s)) as save:
            event = _CallRecorder()
            MainWindow.closeEvent(fake_window, event)
            save.assert_called_once()
            self.assertFalse(fake_window._settings['close_ask_each_time'])
            self.assertEqual(fake_window._settings['close_default_action'], 'minimize')
            self.assertTrue(event.ignored)
            self.assertTrue(loaded)

    def test_tray_quit_uses_main_window_close_path(self):
        main_window = _CallRecorder()
        tray = type('FakeTray', (), {'_main_window': main_window})()
        TrayService.quit_app(tray)
        self.assertTrue(main_window.called)

    def test_expanded_drag_anchor_becomes_compact_position(self):
        panel = QuickPanel(_MainWindowStub())
        panel.show()
        panel.move(420, 260)
        panel._compact_position = QPoint(420, 260)
        panel.toggle_expanded()
        panel.move(panel.pos() + QPoint(-35, 25))
        # 展开态拖动后 compact 锚点跟随后缘（向左展开时）
        if panel._expand_right:
            panel._compact_position = QPoint(panel.pos().x(), panel.pos().y())
        else:
            panel._compact_position = QPoint(
                panel.pos().x() + panel.width() - panel.COMPACT_SIZE[0],
                panel.pos().y(),
            )
        expected = QPoint(panel._compact_position)
        panel.toggle_expanded()
        QTest.qWait(40)
        self.assertEqual(panel.pos(), expected)
        panel.close()

    def test_sql_system_selector_belongs_to_configuration_tab(self):
        """system_combo 属于「系统配置」Tab（index=2），非 SQL 整理页。

        设计：Tab0 升级准备 / Tab1 SQL 整理 / Tab2 系统配置。
        SQL 整理页仅有只读 current_system_label 芯片，切换系统必须进系统配置。
        """
        panel = SqlToolPanel()
        self.assertEqual(panel.tabs.count(), 3)
        self.assertEqual(panel.tabs.tabText(2), '系统配置')
        config_tab = panel.tabs.widget(2)
        ancestor = panel.system_combo.parentWidget()
        while ancestor is not None and ancestor is not config_tab:
            ancestor = ancestor.parentWidget()
        self.assertIs(ancestor, config_tab)
        # 配置页标题文案
        self.assertIn('当前配置系统', panel.config_system_label.text())
        # 整理页芯片只展示当前系统摘要，并提示去系统配置切换
        self.assertTrue(panel.current_system_label.toolTip())
        self.assertIn('系统配置', panel.current_system_label.toolTip())
        # 入口可达：从需求「系统配置」按钮映射的 open 逻辑使用 index 2
        panel.tabs.setCurrentIndex(2)
        self.assertIs(panel.tabs.currentWidget(), config_tab)

    def test_document_panel_separates_personal_and_unit_generation(self):
        panel = CreditCodePanel()
        self.assertEqual(panel.category_tabs.count(), 2)
        self.assertEqual(panel.personal_type.count(), 4)
        for index in range(panel.personal_type.count()):
            panel.personal_type.setCurrentIndex(index)
            panel.personal_qty.setValue(10)
            panel._generate_personal()
            self.assertEqual(len(panel._results), 10)
            self.assertNotEqual(panel._results[0]['kind'], 'credit_code')
            self.assertTrue(panel._results[0]['mobile'])
            self.assertIn('@', panel._results[0]['email'])
        panel.category_tabs.setCurrentIndex(1)
        panel.unit_qty.setValue(10)
        panel._generate_unit()
        self.assertEqual(len(panel._results), 10)
        self.assertTrue(all(item['kind'] == 'credit_code' for item in panel._results))
        self.assertTrue(all(item.get('business_scope') for item in panel._results))
        panel._copy_all()
        self.assertIn('统一社会信用代码', QApplication.clipboard().text())
        local, _sep, domain = panel._results[0]['email'].partition('@')
        self.assertTrue(local)
        self.assertTrue(domain.startswith(local))
        panel.category_tabs.setCurrentIndex(0)
        self.assertEqual(len(panel._results), 10)
        self.assertNotEqual(panel._results[0]['kind'], 'credit_code')
        self.assertGreater(panel.table.columnCount(), 4)

    def test_resident_id_custom_region_age_and_gender_controls(self):
        panel = CreditCodePanel()
        panel.personal_type.setCurrentIndex(panel.personal_type.findData('resident_id'))
        panel.personal_mode.setCurrentIndex(1)
        panel.id_province.setCurrentIndex(panel.id_province.findData('44'))
        panel.id_city.setCurrentIndex(panel.id_city.findData('4403'))
        panel.id_district.setCurrentIndex(panel.id_district.findData('440304'))
        panel.id_min_age.setValue(28)
        panel.id_max_age.setValue(28)
        panel.id_gender.setCurrentIndex(panel.id_gender.findData('male'))
        panel.personal_qty.setValue(20)
        panel._generate_personal()
        self.assertFalse(panel.id_custom.isHidden())
        self.assertEqual(len(panel._results), 20)
        self.assertTrue(all(item['document'].startswith('440304') for item in panel._results))
        self.assertTrue(all(int(item['document'][16]) % 2 == 1 for item in panel._results))
        self.assertTrue(all(item.get('ethnicity') for item in panel._results))
        self.assertTrue(all(item.get('issuer', '').endswith('公安局') for item in panel._results))
        self.assertEqual(panel.table.rowCount(), 20)
        self.assertGreaterEqual(panel.table.rowHeight(0), 28)
        self.assertGreaterEqual(panel.table.rowHeight(19), 28)
        panel.show()
        self.app.processEvents()
        panel._fit_fixed_combos()
        longest = '武警身份证件'
        need = panel.personal_type.fontMetrics().horizontalAdvance(longest) + 24
        self.assertGreaterEqual(panel.personal_type.maximumWidth(), need)
        self.assertLessEqual(panel.personal_mode.maximumWidth(), 220)

    def test_unit_tab_does_not_inherit_personal_custom_height(self):
        panel = CreditCodePanel()
        panel.show()
        self.app.processEvents()
        panel.category_tabs.setCurrentIndex(0)
        panel.personal_type.setCurrentIndex(panel.personal_type.findData('resident_id'))
        panel.personal_mode.setCurrentIndex(1)
        self.app.processEvents()
        panel._sync_tab_height()
        personal_h = panel.category_tabs.height()
        panel.category_tabs.setCurrentIndex(1)
        self.app.processEvents()
        panel._sync_tab_height()
        unit_h = panel.category_tabs.height()
        self.assertFalse(panel.id_custom.isHidden())
        self.assertTrue(panel.unit_custom.isHidden())
        self.assertLess(unit_h, personal_h)
        panel.close()

    def test_generated_document_and_vin_rows_are_visible(self):
        from PyQt6.QtWidgets import QMainWindow
        from ui.theme_manager import ThemeManager
        from panels.vin_panel import VinPanel

        tm = ThemeManager.instance()
        tm.load_template()
        tm.apply(self.app, 'calm')
        win = QMainWindow()
        win.setProperty('uiDensity', 'compact')
        credit = CreditCodePanel()
        win.setCentralWidget(credit)
        win.resize(1100, 720)
        win.show()
        self.app.processEvents()
        credit.personal_qty.setValue(10)
        credit._generate_personal()
        self.app.processEvents()
        first = credit.table.visualItemRect(credit.table.item(0, 0))
        fifth = credit.table.visualItemRect(credit.table.item(4, 0))
        self.assertEqual(credit.table.rowCount(), 10)
        self.assertGreater(fifth.y(), first.y())
        self.assertLess(fifth.bottom(), credit.table.viewport().height())
        vin = VinPanel('zh')
        win.setCentralWidget(vin)
        win.show()
        self.app.processEvents()
        vin.qty.setValue(10)
        vin._generate()
        self.app.processEvents()
        first = vin.table.visualItemRect(vin.table.item(0, 0))
        fifth = vin.table.visualItemRect(vin.table.item(4, 0))
        self.assertEqual(vin.table.rowCount(), 10)
        self.assertGreater(fifth.y(), first.y())
        self.assertLess(fifth.bottom(), vin.table.viewport().height())
        win.close()
        tm.apply(self.app, 'calm')

    def test_sql_switch_keeps_unsaved_form_edits(self):
        panel = SqlToolPanel()
        if len(panel._systems) < 2:
            panel._add_system()
        panel.name_box.setText('临时系统名称')
        panel.system_combo.setCurrentIndex(1)
        self.assertEqual(panel._systems[0]['name'], '临时系统名称')

    def test_sql_panel_loads_multiple_files_appends_pastes_and_confirms_mixed_sources(self):
        panel = SqlToolPanel()
        with tempfile.TemporaryDirectory() as folder:
            first = os.path.join(folder, '01.sql')
            second = os.path.join(folder, '02.sql')
            for path in (first, second):
                with open(path, 'w', encoding='utf-8') as stream:
                    stream.write('CREATE TABLE T_MULTI(ID NUMBER);')
            with patch('panels.sql_panel.QFileDialog.getOpenFileNames', return_value=([first, second], '')):
                panel._load_file()
        self.assertTrue(panel._has_file_input)
        self.assertIn('01.sql', panel.input_sql.toPlainText())
        self.assertIn('02.sql', panel.input_sql.toPlainText())

        QApplication.clipboard().setText('INSERT INTO T_MULTI(ID) VALUES (1);')
        with patch('panels.sql_panel.confirm_action', return_value=True) as confirm:
            panel._paste_sql()
        confirm.assert_called_once()
        QApplication.clipboard().setText('UPDATE T_MULTI SET ID=2 WHERE ID=1;')
        panel._paste_sql()
        unique, duplicates = panel._prepared_sql()
        self.assertTrue(panel._has_paste_input)
        self.assertEqual(len(duplicates), 1)
        self.assertIn('INSERT INTO T_MULTI', unique)
        self.assertIn('UPDATE T_MULTI', unique)

    def test_packaged_icon_resource_is_valid(self):
        brand = os.path.join(PROJECT_DIR, 'resources', 'brand', 'pengtools-app-v2.ico')
        legacy = os.path.join(PROJECT_DIR, 'resources', 'app.ico')
        icon = QIcon(brand if os.path.exists(brand) else legacy)
        self.assertFalse(icon.isNull())

    def test_gateway_panel_and_docx_date_are_available(self):
        gateway = GatewayDecodePanel()
        docx = DocxUpdatePanel()
        self.assertEqual(gateway.environment.count(), 3)
        # 加解密不再内嵌 XML Tab；JSON 查看器保留
        self.assertIsNotNone(gateway.json_viewer)
        self.assertIsNone(gateway.xml_workspace)
        self.assertTrue(hasattr(gateway, 'open_format_xml'))
        self.assertTrue(docx.update_date.calendarPopup())
        self.assertEqual(docx.update_date.objectName(), 'docx-date')
        docx.update_date.setDate(QDate(2030, 5, 20))
        docx.today_btn.click()
        self.assertEqual(docx.update_date.date(), QDate.currentDate())

    def test_format_tools_xml_workspace_format_copy_and_errors(self):
        """格式工具 XML Tab：格式化 / 去引号 / 错误提示 / 复制；加解密可跳转。"""
        from panels.format_panel import FormatToolsPanel
        from ui.xml_workspace import XmlWorkspace

        panel = FormatToolsPanel()
        xml = panel.xml_workspace
        self.assertIsInstance(xml, XmlWorkspace)
        raw = '"<root><item id=\\"1\\">hi</item></root>"'
        xml.set_input_text(raw)
        self.assertTrue(xml._format())
        out = xml.output_text()
        self.assertIn('<root>', out)
        self.assertIn('<item', out)
        self.assertIn('hi', out)
        xml._copy_output()
        self.assertEqual(QApplication.clipboard().text(), out)

        with patch('ui.xml_workspace.show_warning') as warn:
            xml.set_input_text('"<broken"')
            self.assertFalse(xml._format())
            warn.assert_called()
        self.assertTrue(xml.status_label.text())

        escaped = r'"<a>\n  <b>1</b>\n</a>"'
        xml.set_input_text(escaped)
        self.assertTrue(xml._normalize_only())
        cleaned = xml.output_text()
        self.assertIn('<a>', cleaned)
        self.assertNotIn('\\n', cleaned)

        # 解密明文通过信号送入格式工具
        gateway = GatewayDecodePanel()
        gateway.json_viewer.set_text('<root><x>1</x></root>', auto_format=False)
        received = []
        gateway.open_format_xml.connect(received.append)
        gateway._send_plain_to_format_xml()
        self.assertEqual(len(received), 1)
        self.assertIn('<root>', received[0])
        panel.open_xml(received[0])
        self.assertEqual(panel.tabs.currentIndex(), 1)
        self.assertIn('<root>', panel.xml_workspace.input_text())

        xml.clear()
        self.assertFalse(xml.input_text().strip())
        self.assertFalse(xml.output_text().strip())
        gateway.close()
        panel.close()

    def test_docx_filename_shows_matched_latest_template(self):
        panel = DocxUpdatePanel()
        panel.docx_path.setText('接报案数据库表结构文档V1.0_整理后接口文档.docx')
        self.assertIsNotNone(panel._template_profile)
        self.assertEqual(panel._template_profile['system'], '接报案')
        # 正常匹配不占行；详情在 tooltip / profile
        self.assertTrue(panel.template_status.property('matched'))
        tip = panel.template_status.toolTip() or ''
        self.assertTrue('接报案' in tip or 'V2.0' in tip or panel._template_profile.get('template'))
        panel.docx_path.setText('未知系统数据库表结构说明文档.docx')
        self.assertIsNone(panel._template_profile)
        self.assertFalse(panel.template_status.property('matched'))

    def test_gateway_json_tree_sorts_object_fields_alphabetically(self):
        gateway = GatewayDecodePanel()
        viewer = gateway.json_viewer
        self.assertTrue(viewer.set_text('{"z":1,"a":{"zebra":1,"apple":2}}'))
        root = viewer.tree.topLevelItem(0)
        self.assertEqual([root.child(index).text(0) for index in range(root.childCount())], ['a', 'z'])
        nested = root.child(0)
        self.assertEqual([nested.child(index).text(0) for index in range(nested.childCount())], ['apple', 'zebra'])
        gateway.close()

    def test_gateway_json_tree_search_and_copy(self):
        gateway = GatewayDecodePanel()
        viewer = gateway.json_viewer
        self.assertTrue(viewer.set_text('{"data":{"users":[{"name":"demo_user"}]}}'))
        self.assertIn('\n', viewer.plain_text())
        # 树节点页签内搜索
        viewer.tabs.setCurrentIndex(1)
        viewer.search_edit.setText('demo_user')
        self.assertEqual(len(viewer._matches), 1)
        self.assertEqual(viewer.path_value.text(), '$.data.users[0].name')
        item = viewer._matches[0]
        viewer._copy_item_value(item)
        self.assertEqual(QApplication.clipboard().text(), 'demo_user')
        # 格式化文本页签内搜索应命中正文并高亮
        viewer.tabs.setCurrentIndex(0)
        viewer.search_edit.setText('demo_user')
        self.assertEqual(viewer._search_mode, 'text')
        self.assertGreaterEqual(len(viewer._matches), 1)

    def test_json_tree_keeps_readable_key_column_and_resizable_headers(self):
        """深层级字段名列可拖宽、有下限，不会被压到不可读。"""
        from PyQt6.QtWidgets import QHeaderView
        from ui.json_viewer import JsonViewer, _KEY_COL_DEFAULT, _KEY_COL_MIN

        viewer = JsonViewer()
        deep = {'level1': {'level2': {'level3': {'very_long_field_name_for_ui': {'leaf': 1}}}}}
        import json
        self.assertTrue(viewer.set_text(json.dumps(deep)))
        header = viewer.tree.header()
        self.assertEqual(header.sectionResizeMode(0), QHeaderView.ResizeMode.Interactive)
        self.assertEqual(header.sectionResizeMode(1), QHeaderView.ResizeMode.Interactive)
        self.assertEqual(header.sectionResizeMode(2), QHeaderView.ResizeMode.Interactive)
        self.assertGreaterEqual(viewer.tree.columnWidth(0), _KEY_COL_DEFAULT)
        # 尝试拖窄字段名列，应被下限拦住
        viewer.tree.setColumnWidth(0, 40)
        viewer._on_tree_section_resized(0, 200, 40)
        self.assertGreaterEqual(viewer.tree.columnWidth(0), _KEY_COL_MIN)
        # 悬停有完整路径提示
        leaf_path = '$.level1.level2.level3.very_long_field_name_for_ui.leaf'
        items = viewer._all_items()
        leaf = next(i for i in items if i.data(0, Qt.ItemDataRole.UserRole) == leaf_path)
        self.assertIn('very_long_field_name_for_ui', leaf.toolTip(0))
        self.assertEqual(viewer.tree.textElideMode(), Qt.TextElideMode.ElideNone)
        viewer.close()

    def test_operations_panel_fuzzy_search_and_builtin_protection(self):
        panel = OpsPanel()
        panel.search_edit.setText('ps -ef')
        QTest.qWait(250)
        self.app.processEvents()
        self.assertGreater(panel.command_list.count(), 0)
        command = panel.command_list.item(0).data(Qt.ItemDataRole.UserRole)
        self.assertEqual(command['command'], 'ps -ef')
        self.assertTrue(command['builtin'])
        self.assertTrue(panel.delete_btn.isHidden())
        self.assertIn('所有进程', panel.description.text())
        self.assertIn('PPID', panel.output_explanation.text())

    def test_learning_workbook_uses_table_and_realtime_row_filter(self):
        owner = PersonalPanel()
        panel = owner.knowledge_tab
        entry = {
            'id': 'table-test', 'title': '测试 Excel · 内网', 'category': 'server',
            'content_type': 'workbook_sheet', 'content': '', 'source': '测试.xlsx',
            'sheet_name': '内网', 'rows': [['编号', '主机'], ['1', 'alpha'], ['2', 'beta']],
            'row_count': 3, 'column_count': 2, 'column_widths': [8, 20],
            'header_rows': [0], 'cell_styles': {}, 'builtin': True,
        }
        panel._seed_entries = [entry]
        panel._custom_entries = []
        panel._refresh()
        self.assertEqual(panel.table_view.rowCount(), 3)
        self.assertEqual(panel.table_view.horizontalHeaderItem(0).text(), 'A')
        panel.search_edit.setText('beta')
        QTest.qWait(250)
        self.app.processEvents()
        self.assertTrue(panel.table_view.isRowHidden(1))
        self.assertFalse(panel.table_view.isRowHidden(2))
        self.assertGreater(panel._suggestion_model.rowCount(), 0)
        suggestion = panel._suggestion_model.stringList()[0]
        self.assertTrue(suggestion.startswith('表格 · ') or suggestion.startswith('内容 · ') or suggestion.startswith('资料 · '))
        self.assertEqual(panel._completer.pathFromIndex(panel._suggestion_model.index(0, 0)), 'beta')
        panel.search_edit.blockSignals(True)
        panel.search_edit.setText(suggestion)
        panel.search_edit.blockSignals(False)
        self.assertEqual(panel._sanitize_search_query(suggestion), 'beta')
        panel._activate_suggestion(suggestion)
        self.assertEqual(panel.search_edit.text(), 'beta')
        self.assertEqual(panel.table_view.currentRow(), 2)
        self.assertTrue(any(entry.get('id') == 'table-test' for entry in panel._filtered))

    def test_learning_excel_copy_hide_restore_and_builtin_override(self):
        owner = PersonalPanel(); panel = owner.knowledge_tab
        entry = {
            'id': 'seed-table', 'title': '内置 Excel', 'category': 'server', 'file_type': 'EXCEL',
            'content_type': 'workbook_sheet', 'content': '', 'source': '内置.xlsx', 'sheet_name': 'Sheet1',
            'rows': [['编号', '值'], ['1', 'alpha'], ['2', 'beta']], 'row_count': 3, 'column_count': 2,
            'column_widths': [8, 20], 'header_rows': [0], 'cell_styles': {}, 'builtin': True,
        }
        panel._seed_entries = [entry]; panel._custom_entries = []; panel._refresh()
        panel.table_view.selectRow(1); panel._copy_current_row()
        self.assertEqual(QApplication.clipboard().text(), '1\talpha')
        panel._hide_selected_rows(); self.assertTrue(panel.table_view.isRowHidden(1))
        panel._copy_visible_table(); self.assertNotIn('alpha', QApplication.clipboard().text())
        panel._restore_hidden_table(); self.assertFalse(panel.table_view.isRowHidden(1))
        updated = dict(entry); updated['rows'] = [['编号', '值'], ['1', 'changed']]
        with patch('panels.personal_panel.save_custom_entries'):
            panel._persist_updated_entry(updated)
        self.assertTrue(panel.all_entries()[0]['builtin_source'])
        self.assertEqual(panel.all_entries()[0]['rows'][1][1], 'changed')

    def test_requirement_attachment_excel_editor_and_type_label(self):
        entry = {
            'name': '需求.xlsx', 'file_type': 'EXCEL', 'content_type': 'workbook_sheet',
            'sheet_name': '需求', 'rows': [['编号', '说明'], ['1', 'alpha'], ['2', 'beta']],
            'row_count': 3, 'column_count': 2, 'column_widths': [8, 20], 'header_rows': [0], 'cell_styles': {},
        }
        dialog = RequirementAttachmentDialog(entry)
        dialog.search.setText('beta')
        self.assertTrue(dialog.table.isRowHidden(1))
        self.assertEqual(dialog.table.currentRow(), 2)
        dialog.table.selectRow(2); dialog._copy_row()
        self.assertEqual(QApplication.clipboard().text(), '2\tbeta')

    def test_operations_copy_feedback_resets_and_switching_resets_immediately(self):
        panel = OpsPanel()
        panel.search_edit.setText('ps -ef')
        panel.copy_btn.click()
        self.assertEqual(panel.copy_btn.text(), '已复制')
        QTest.qWait(1600)
        self.assertEqual(panel.copy_btn.text(), '复制命令')
        panel.copy_btn.click()
        self.assertEqual(panel.copy_btn.text(), '已复制')
        panel.search_edit.setText('uptime')
        QTest.qWait(250)
        self.app.processEvents()
        self.assertEqual(panel.copy_btn.text(), '复制命令')

    def test_settings_panel_values_and_floating_preferences(self):
        settings = dict(
            DEFAULT_SETTINGS, font_size=15, floating_opacity=72,
            copy_feedback_ms=2000, close_ask_each_time=False,
            close_default_action='exit', keep_awake_enabled=True,
            keep_awake_interval_minutes=4,
        )
        page = SettingsPanel(settings)
        self.assertEqual(page.values()['font_size'], 15)
        self.assertEqual(page.values()['floating_opacity'], 72)
        self.assertEqual(page.values()['copy_feedback_ms'], 2000)
        self.assertFalse(page.values()['close_ask_each_time'])
        self.assertEqual(page.values()['close_default_action'], 'exit')
        self.assertTrue(page.values()['keep_awake_enabled'])
        self.assertEqual(page.values()['keep_awake_interval_minutes'], 4)
        self.assertTrue(page.keep_awake_group.isHidden())
        previews = []
        page.floating_opacity_preview.connect(previews.append)
        page.opacity.setValue(61)
        self.assertEqual(previews, [61])
        panel = QuickPanel(_MainWindowStub())
        panel.apply_preferences(72, False)
        self.assertAlmostEqual(panel.windowOpacity(), 0.72, places=2)
        self.assertFalse(bool(panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        panel.show()
        panel.apply_preferences(55, True)
        QTest.qWait(30)
        self.assertAlmostEqual(panel.windowOpacity(), 0.55, places=2)
        panel.apply_preferences(55, False)
        QTest.qWait(30)
        self.assertAlmostEqual(panel.windowOpacity(), 0.55, places=2)
        panel.reset_position()
        self.assertFalse(panel.isHidden())
        panel.close()

    def test_keep_awake_secret_requires_key_and_service_applies_interval(self):
        page = SettingsPanel(DEFAULT_SETTINGS)
        with patch('panels.settings_panel.QInputDialog.getText', return_value=('Lihp', True)):
            page._unlock_keep_awake()
        self.assertFalse(page.keep_awake_group.isHidden())
        page.resize(900, 600)
        page.show()
        self.app.processEvents()
        self.assertTrue(page.keep_awake_note.isVisible())
        self.assertGreaterEqual(page.keep_awake_note.height(), 42)
        self.assertGreater(page.scroll_area.verticalScrollBar().maximum(), 0)
        page.close()

        pulses = []
        service = KeepAwakeService(pulse=lambda: pulses.append(True))
        service.apply_preferences(True, 7)
        self.assertTrue(service.is_active())
        self.assertEqual(service.interval_minutes(), 7)
        self.assertEqual(pulses, [True])
        service.apply_preferences(False, 7)
        self.assertFalse(service.is_active())

    def test_global_stylesheet_keeps_plain_text_background_transparent(self):
        with open(os.path.join(PROJECT_DIR, 'resources', 'style.qss'), encoding='utf-8') as stream:
            stylesheet = stream.read()
        widget_rule = stylesheet.split('QWidget {', 1)[1].split('}', 1)[0]
        self.assertIn('background-color:', widget_rule)
        self.assertIn('QLabel, QCheckBox, QRadioButton { background: transparent; }', stylesheet)

    def test_aurora_progress_uses_light_card_background(self):
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().load_template(PROJECT_DIR)
        ThemeManager.instance().apply(self.app, 'calm', font_size=12)
        progress = AuroraProgress()
        progress.resize(600, 62)
        progress.set_progress(50, 'Processing')
        self.app.processEvents()
        image = progress.grab().toImage()
        # 取样卡片中部（避开外层阴影），应为浅色企业风底
        color = image.pixelColor(image.width() // 2, image.height() // 2)
        self.assertGreater(color.red(), 220)
        self.assertGreater(color.green(), 220)
        self.assertGreater(color.blue(), 220)

    def test_aurora_progress_floating_overlay_does_not_need_layout(self):
        host = QWidget()
        host.resize(900, 600)
        host.show()
        progress = AuroraProgress(host)
        progress.start_busy('正在导出交付文件…')
        self.app.processEvents()
        self.assertFalse(progress.isHidden())
        self.assertGreaterEqual(progress.x(), 24)
        self.assertEqual(progress.parentWidget(), host)
        # 浮层不参与 layout：完成态仅改内部状态，不依赖 addWidget
        progress.finish('导出完成')
        self.assertEqual(progress._value, 100)
        from ui.aurora_progress import SUCCESS_LINGER_MS
        self.assertEqual(SUCCESS_LINGER_MS, 180)
        progress.hide_now()
        self.assertTrue(progress.isHidden())
        host.close()

    def test_requirement_finish_label_from_busy_message(self):
        self.assertEqual(RequirementPanel._finish_label_from_busy('正在提交 SVN，请勿关闭软件……'), '提交完成')
        self.assertEqual(RequirementPanel._finish_label_from_busy('正在扫描本地需求文件夹和 SVN 工作副本……'), '扫描完成')
        self.assertEqual(RequirementPanel._finish_label_from_busy('正在锁定选中的 SVN 文件……'), '锁定完成')

    def test_settings_close_hint_reflects_dont_ask_state(self):
        page = SettingsPanel(dict(DEFAULT_SETTINGS))
        page.close_ask.setChecked(False)
        page.close_default_action.setCurrentIndex(page.close_default_action.findData('exit'))
        page._refresh_close_behavior_hint()
        self.assertIn('关闭时不再提示', page.close_behavior_hint.text())
        self.assertIn('恢复关闭提示', page.close_behavior_hint.text())
        page.close_ask.setChecked(True)
        page._refresh_close_behavior_hint()
        self.assertIn('弹出选择', page.close_behavior_hint.text())
        self.assertIn('关闭时不再提示', page.close_behavior_hint.text())


if __name__ == '__main__':
    unittest.main()
