# -*- coding: utf-8 -*-
"""界面一致性：紧凑按钮高度、ghost 角色、显隐切换文案。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class SectionToggleTests(unittest.TestCase):
    def test_toggle_labels_zh_en(self):
        from ui.section_toggle import toggle_labels

        hide, show = toggle_labels('session_list', 'zh')
        self.assertEqual(hide, '隐藏列表')
        self.assertEqual(show, '显示列表')
        hide_en, show_en = toggle_labels('session_list', 'en')
        self.assertEqual(hide_en, 'Hide list')
        self.assertEqual(show_en, 'Show list')
        collapse, expand = toggle_labels('log', 'zh')
        self.assertIn('收起', collapse)
        self.assertIn('展开', expand)

    def test_apply_visibility_toggle_uses_btn_ghost_and_compact(self):
        from PyQt6.QtWidgets import QApplication, QPushButton
        from ui.section_toggle import apply_visibility_toggle

        app = QApplication.instance() or QApplication([])
        btn = QPushButton()
        apply_visibility_toggle(btn, content_visible=True, language='zh', kind='list')
        self.assertEqual(btn.objectName(), 'btn-ghost')
        self.assertTrue(btn.property('compactAction'))
        self.assertEqual(btn.text(), '隐藏')
        self.assertGreaterEqual(btn.minimumWidth(), 72)
        apply_visibility_toggle(btn, content_visible=False, language='zh', kind='list')
        self.assertEqual(btn.text(), '显示')
        btn.deleteLater()


class CompactButtonMetricsTests(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])
        self._prev_qss = self.app.styleSheet()
        self.app.setStyleSheet('')

    def tearDown(self):
        self.app.setStyleSheet(self._prev_qss)

    def test_size_compact_button_height_is_28(self):
        from PyQt6.QtWidgets import QPushButton
        from ui.field_metrics import BTN_COMPACT_H, size_compact_button

        btn = QPushButton('操作')
        size_compact_button(btn)
        self.assertEqual(BTN_COMPACT_H, 28)
        self.assertEqual(btn.height(), 28)
        self.assertTrue(btn.property('compactAction'))
        btn.deleteLater()

    def test_design_system_ghost_maps_to_btn_ghost(self):
        from PyQt6.QtWidgets import QPushButton
        from ui.design_system import BUTTON_ROLES, apply_button

        self.assertEqual(BUTTON_ROLES['ghost'], 'btn-ghost')
        btn = QPushButton('次要')
        apply_button(btn, 'ghost', compact=True)
        self.assertEqual(btn.objectName(), 'btn-ghost')
        self.assertEqual(btn.height(), 28)
        btn.deleteLater()

    def test_apply_button_always_uses_compact_28(self):
        from PyQt6.QtWidgets import QPushButton, QSizePolicy
        from ui.design_system import apply_button

        btn = QPushButton('生成')
        apply_button(btn, 'primary')
        self.assertEqual(btn.height(), 28)
        self.assertTrue(btn.property('compactAction'))
        self.assertEqual(btn.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Maximum)
        btn.deleteLater()

    def test_size_combo_hugs_content(self):
        from PyQt6.QtWidgets import QComboBox, QSizePolicy
        from ui.field_metrics import COMBO_PICK_W, FIELD_H, size_combo

        combo = QComboBox()
        combo.addItems(['GET', 'POST', 'OPTIONS'])
        size_combo(combo, 'sm')
        self.assertEqual(FIELD_H, 28)
        self.assertEqual(combo.height(), 28)
        self.assertEqual(combo.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Fixed)
        self.assertEqual(
            combo.sizeAdjustPolicy(),
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self.assertEqual(combo.minimumWidth(), COMBO_PICK_W)
        self.assertEqual(combo.maximumWidth(), COMBO_PICK_W)
        combo.deleteLater()


class DialogButtonStandardizationTests(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_localize_button_box_sets_roles_and_sizes(self):
        from PyQt6.QtWidgets import QDialogButtonBox
        from ui.dialog_buttons import DIALOG_BUTTON_H, DIALOG_BUTTON_MIN_W, localize_button_box

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Discard
            | QDialogButtonBox.StandardButton.Help
        )
        localize_button_box(box, 'zh')

        ok_btn = box.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = box.button(QDialogButtonBox.StandardButton.Cancel)
        discard_btn = box.button(QDialogButtonBox.StandardButton.Discard)
        help_btn = box.button(QDialogButtonBox.StandardButton.Help)

        self.assertEqual(ok_btn.text(), '确定')
        self.assertEqual(ok_btn.objectName(), 'primary-btn')
        self.assertEqual(ok_btn.height(), DIALOG_BUTTON_H)
        self.assertEqual(ok_btn.minimumWidth(), DIALOG_BUTTON_MIN_W)

        self.assertEqual(cancel_btn.text(), '取消')
        self.assertEqual(cancel_btn.objectName(), 'btn-secondary')
        self.assertEqual(cancel_btn.height(), DIALOG_BUTTON_H)

        self.assertEqual(discard_btn.text(), '放弃')
        self.assertEqual(discard_btn.objectName(), 'btn-danger')
        self.assertEqual(discard_btn.height(), DIALOG_BUTTON_H)

        self.assertEqual(help_btn.text(), '帮助')
        self.assertEqual(help_btn.objectName(), 'btn-ghost')
        self.assertEqual(help_btn.height(), DIALOG_BUTTON_H)
        box.deleteLater()

    def test_localize_button_box_override_preserves_standard_role(self):
        from PyQt6.QtWidgets import QDialogButtonBox
        from ui.dialog_buttons import localize_button_box

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        localize_button_box(box, 'zh', Save='保存需求')

        save_btn = box.button(QDialogButtonBox.StandardButton.Save)
        self.assertEqual(save_btn.text(), '保存需求')
        self.assertEqual(save_btn.objectName(), 'primary-btn')
        box.deleteLater()


class ConfirmDialogConsistencyTests(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_confirm_action_dialog_button_roles(self):
        from ui.confirm_dialog import ConfirmActionDialog
        from ui.dialog_buttons import DIALOG_BUTTON_H, DIALOG_BUTTON_MIN_W

        dlg_danger = ConfirmActionDialog('删除', '确定删除？', '确认删除', danger=True)
        self.assertEqual(dlg_danger.cancel_button.objectName(), 'confirm-cancel')
        self.assertEqual(dlg_danger.confirm_button.objectName(), 'btn-danger')
        self.assertEqual(dlg_danger.cancel_button.height(), DIALOG_BUTTON_H)
        self.assertEqual(dlg_danger.confirm_button.height(), DIALOG_BUTTON_H)
        self.assertEqual(dlg_danger.confirm_button.minimumWidth(), DIALOG_BUTTON_MIN_W)
        dlg_danger.close()

        dlg_normal = ConfirmActionDialog('同步', '确定同步？', '确认同步', danger=False)
        self.assertEqual(dlg_normal.cancel_button.objectName(), 'confirm-cancel')
        self.assertEqual(dlg_normal.confirm_button.objectName(), 'primary-btn')
        dlg_normal.close()

    def test_close_action_dialog_button_roles(self):
        from ui.confirm_dialog import CloseActionDialog
        dlg = CloseActionDialog('zh')
        self.assertEqual(dlg.minimize_button.objectName(), 'confirm-cancel')
        self.assertEqual(dlg.exit_button.objectName(), 'btn-danger')
        self.assertEqual(dlg.cancel_button.objectName(), 'confirm-cancel')
        dlg.close()

    def test_app_notice_dialog_button_role(self):
        from ui.confirm_dialog import AppNoticeDialog
        dlg = AppNoticeDialog('提示', '操作已完成')
        self.assertEqual(dlg.ok_button.objectName(), 'primary-btn')
        dlg.close()

    def test_next_step_dialog_button_roles(self):
        from ui.confirm_dialog import NextStepDialog
        actions = [
            ('view', '查看详情', True),
            ('continue', '继续录入', False),
        ]
        dlg = NextStepDialog('完成', '已创建需求', actions, recommended='view')
        self.assertEqual(len(dlg._action_buttons), 2)
        self.assertEqual(dlg._action_buttons[0].objectName(), 'primary-btn')
        self.assertEqual(dlg._action_buttons[1].objectName(), 'btn-secondary')
        dlg.close()


class CompactStepperTests(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_stepper_structure_and_accessibility(self):
        from ui.field_metrics import CompactStepper, FIELD_H

        stepper = CompactStepper(minimum=1, maximum=10, value=5, suffix='条')
        self.assertEqual(stepper.objectName(), 'compact-stepper')
        self.assertEqual(stepper.layout().spacing(), 0)

        self.assertEqual(stepper.minus_btn.objectName(), 'compact-step-minus')
        self.assertEqual(stepper.minus_btn.accessibleName(), '减少')
        self.assertEqual(stepper.minus_btn.height(), FIELD_H)

        self.assertEqual(stepper.edit.objectName(), 'compact-step-value')
        self.assertEqual(stepper.edit.accessibleName(), '数值')
        self.assertEqual(stepper.edit.height(), FIELD_H)
        self.assertEqual(stepper.edit.text(), '5')

        self.assertEqual(stepper.plus_btn.objectName(), 'compact-step-plus')
        self.assertEqual(stepper.plus_btn.accessibleName(), '增加')
        self.assertEqual(stepper.plus_btn.height(), FIELD_H)

        self.assertFalse(stepper.suffix_label.isHidden())
        self.assertEqual(stepper.suffix_label.text(), '条')
        stepper.deleteLater()

    def test_stepper_value_clamping_and_bounds(self):
        from ui.field_metrics import CompactStepper

        stepper = CompactStepper(minimum=0, maximum=5, value=0)
        self.assertEqual(stepper.value(), 0)
        self.assertFalse(stepper.minus_btn.isEnabled())
        self.assertTrue(stepper.plus_btn.isEnabled())

        stepper.setValue(5)
        self.assertEqual(stepper.value(), 5)
        self.assertTrue(stepper.minus_btn.isEnabled())
        self.assertFalse(stepper.plus_btn.isEnabled())

        stepper.setValue(10)  # 超界自动截断
        self.assertEqual(stepper.value(), 5)

        stepper.setValue(-2)  # 下限自动截断
        self.assertEqual(stepper.value(), 0)

        # 非法文本安全回退
        stepper.edit.setText('invalid')
        stepper._commit_edit()
        self.assertEqual(stepper.value(), 0)
        stepper.deleteLater()


class SegmentedStepperQssTests(unittest.TestCase):
    def test_qss_contains_segmented_stepper_contract(self):
        from ui.theme_manager import ThemeManager

        manager = ThemeManager.instance()
        manager.load_template()
        qss = manager.render('calm')

        self.assertIn('QWidget#compact-stepper', qss)
        self.assertIn('QPushButton#compact-step-minus', qss)
        self.assertIn('QLineEdit#compact-step-value', qss)
        self.assertIn('QPushButton#compact-step-plus', qss)
        self.assertIn('border-top-left-radius: 6px', qss)
        self.assertIn('border-top-right-radius: 6px', qss)


if __name__ == '__main__':
    unittest.main()
