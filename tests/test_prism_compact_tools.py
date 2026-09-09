"""Compact tool geometry and existing VIN filter-to-generator wiring."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class CompactToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_vin_custom_filters_fit_and_reach_existing_generator(self):
        from panels.vin_panel import VinPanel
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm')
        with patch('panels.vin_panel.generate_vehicle_batch', return_value=[]) as generate:
            panel = VinPanel('zh')
            try:
                panel.resize(864, 520)
                panel.apply_layout_mode('narrow', True)
                panel.show()
                panel.mode_combo.setCurrentIndex(1)
                panel.year_combo.setCurrentText('2027')
                self.app.processEvents()
                self.assertTrue(panel.custom.isVisible())
                self.assertEqual((panel.width(), panel.height()), (864, 520))
                generate.reset_mock()
                panel.generate_btn.click()
                generate.assert_called_once()
                self.assertEqual(generate.call_args.args[1], 2027)
                self.assertEqual(generate.call_args.kwargs['energy'], panel.energy_combo.currentData() or '')
                panel.mode_combo.setCurrentIndex(0)
                self.assertEqual(panel.year_combo.currentText(), '2027')
            finally:
                panel.close()
                panel.deleteLater()
                self.app.processEvents()

    def test_credit_result_area_does_not_expand_low_window(self):
        from panels.credit_panel import CreditCodePanel
        panel = CreditCodePanel()
        try:
            panel.resize(864, 520)
            panel.apply_layout_mode('narrow', True)
            panel.show()
            self.app.processEvents()
            self.assertEqual((panel.width(), panel.height()), (864, 520))
            self.assertTrue(panel.table.isVisible())
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
