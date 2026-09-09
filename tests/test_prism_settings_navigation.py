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
