"""Real widget checks: category navigation keeps editors, values and actions alive."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class SettingsNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_time_step_controls_and_model_selection_survive_theme_size(self):
        from PyQt6.QtCore import Qt, QTime
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QDateTimeEdit, QStyle, QStyleOptionSpinBox, QAbstractSpinBox
        from config import DEFAULT_SETTINGS
        from panels.settings_panel import SettingsPanel
        from ui.theme_manager import ThemeManager
        panel = SettingsPanel(dict(DEFAULT_SETTINGS))
        try:
            panel.resize(856, 528)
            panel.apply_layout_mode('narrow', True)
            panel.show()
            for font in (13, 16):
                ThemeManager.instance().apply(self.app, 'calm', font_size=font)
                panel.section_picker.setCurrentIndex(2)
                self.app.processEvents()
                time = panel.reminder_time
                time.setTime(QTime(17, 30))
                time.setCurrentSection(QDateTimeEdit.Section.MinuteSection)
                option = QStyleOptionSpinBox()
                option.initFrom(time)
                option.frame = True
                option.buttonSymbols = time.buttonSymbols()
                option.stepEnabled = QAbstractSpinBox.StepEnabledFlag.StepUpEnabled | QAbstractSpinBox.StepEnabledFlag.StepDownEnabled
                up = time.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxUp, time)
                down = time.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxDown, time)
                self.assertTrue(time.rect().contains(up))
                self.assertTrue(time.rect().contains(down))
                QTest.mouseClick(time, Qt.MouseButton.LeftButton, pos=up.center())
                self.assertEqual(time.time(), QTime(17, 31))
                QTest.mouseClick(time, Qt.MouseButton.LeftButton, pos=down.center())
                self.assertEqual(panel._current_reminder_time_text(), '17:30')
                time.setEnabled(False)
                QTest.mouseClick(time, Qt.MouseButton.LeftButton, pos=up.center())
                self.assertEqual(time.time(), QTime(17, 30))
                time.setEnabled(True)
                panel.section_picker.setCurrentIndex(5)
                panel._refresh_ai_list({'active_model_id': 'demo-a', 'items': [
                    {'id': 'demo-a', 'name': '示例助手A', 'model': 'demo-one', 'enabled': False},
                    {'id': 'demo-b', 'name': '示例助手B', 'model': 'demo-two', 'enabled': False},
                ]})
                panel.ai_list.setCurrentRow(1)
                self.app.processEvents()
                self.assertEqual(panel.ai_name.text(), '示例助手B')
                self.assertEqual(panel.ai_model.currentText(), 'demo-two')
                self.assertIn('示例助手B', panel.ai_list.currentItem().toolTip())
                self.assertGreaterEqual(panel.ai_list.visualItemRect(panel.ai_list.currentItem()).height(), 36)
                self.assertTrue(panel.save_btn.isVisible())
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_categories_preserve_edits_and_keep_save_visible_at_both_widths(self):
        from config import DEFAULT_SETTINGS
        from panels.settings_panel import SettingsPanel
        with patch.object(SettingsPanel, '_load_ai_local_values'), \
             patch.object(SettingsPanel, '_load_reminder_values'), \
             patch.object(SettingsPanel, '_refresh_oracle_status'):
            panel = SettingsPanel(dict(DEFAULT_SETTINGS))
        try:
            panel.font_size.setValue(16)
            before = panel.values()
            expected = [panel.appearance_group, panel.float_group, panel.reminder_group,
                        panel.security_group, panel.oracle_group, panel.ai_group]
            for mode, size in [('standard', (1144, 740)), ('narrow', (680, 540))]:
                panel.resize(*size)
                panel.apply_layout_mode(mode, False)
                panel.show()
                for index, editor in enumerate(expected):
                    picker = panel.section_picker if mode == 'narrow' else panel.section_nav
                    if mode == 'narrow':
                        picker.setCurrentIndex(index)
                    else:
                        picker.setCurrentRow(index)
                    self.app.processEvents()
                    self.assertTrue(editor.isVisible())
                    self.assertTrue(panel.save_btn.isVisible())
                    self.assertEqual(panel.values(), before)
                    self.assertEqual(panel.sections_stack.currentIndex(), index)
                self.assertLessEqual(panel.width(), size[0])
            panel.set_language('en')
            self.assertEqual(panel.sections_stack.currentIndex(), 5)
            self.assertEqual(panel.section_picker.itemText(0), 'Appearance')
            self.assertEqual(panel.font_size.value(), 16)
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
