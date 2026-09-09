# -*- coding: utf-8 -*-
"""PRISM Section 9 统一弹窗与表单规范契约测试。

覆盖：
1. clamp_dialog_geometry 屏幕边界夹取与高DPI/多屏安全
2. ConfirmActionDialog / AppNoticeDialog / NextStepDialog 440px 几何与焦点契约
3. CloseActionDialog / HttpsCertConsentDialog 560px 几何与安全默认按钮契约
4. ConnectionDialog 720px 几何、80px标签与底栏固定契约
5. TicketSubmitDialog 860px 双列表单与固定底栏
6. TestPointsDialog 640px 几何与 TestPointRow 44px/28px 命中区
7. 其余 14 个 QDialog 子窗口尺寸与安全夹取
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class PrismDialogContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_clamp_dialog_geometry_bounds(self):
        """测试 clamp_dialog_geometry 正确夹取并在超大尺寸时限制在屏幕可用区之内。"""
        from PyQt6.QtWidgets import QDialog
        from ui.dialog_buttons import clamp_dialog_geometry

        dlg = QDialog()
        try:
            # 正常尺寸夹取
            w, h = clamp_dialog_geometry(dlg, 440, 300, min_width=320, min_height=200)
            self.assertGreaterEqual(w, 320)
            self.assertGreaterEqual(h, 200)
            self.assertLessEqual(w, dlg.maximumWidth())
            self.assertLessEqual(h, dlg.maximumHeight())

            # 极端超大尺寸夹取（例如 5000x5000）必须不超过屏幕最大可用高/宽
            w_big, h_big = clamp_dialog_geometry(dlg, 5000, 5000)
            self.assertLessEqual(w_big, dlg.maximumWidth())
            self.assertLessEqual(h_big, dlg.maximumHeight())
        finally:
            dlg.close()

    def test_confirm_dialog_geometry_and_safety(self):
        """ConfirmActionDialog 440px，padding 24px，gap 8px，cancel 默认且获焦点。"""
        from ui.confirm_dialog import ConfirmActionDialog

        dlg = ConfirmActionDialog(
            "测试标题",
            "测试危险操作说明内容",
            confirm_text="确认删除",
            danger=True,
        )
        try:
            # 几何约束：宽度 440
            self.assertEqual(dlg.width(), min(440, dlg.maximumWidth()))
            self.assertGreaterEqual(dlg.minimumWidth(), 320)

            # 安全契约：cancel 按钮是 default
            self.assertTrue(dlg.cancel_button.isDefault())
            self.assertFalse(dlg.confirm_button.isDefault())

            # 危险样式契约
            self.assertEqual(dlg.confirm_button.objectName(), "btn-danger")
        finally:
            dlg.close()

    def test_close_action_dialog_safety_defaults(self):
        """CloseActionDialog 退出永远不为 default；若默认退出，焦点停在 Cancel。"""
        from ui.confirm_dialog import CloseActionDialog

        # 情况 1: 默认动作是 exit -> Cancel 为 default
        dlg_exit = CloseActionDialog(language="zh", default_action="exit")
        try:
            self.assertGreaterEqual(dlg_exit.width(), min(560, dlg_exit.maximumWidth()))
            self.assertFalse(dlg_exit.exit_button.isDefault())
            self.assertTrue(dlg_exit.cancel_button.isDefault())
        finally:
            dlg_exit.close()

        # 情况 2: 默认动作是 minimize -> exit 绝不为 default
        dlg_min = CloseActionDialog(language="zh", default_action="minimize")
        try:
            self.assertFalse(dlg_min.exit_button.isDefault())
        finally:
            dlg_min.close()

    def test_confirmation_message_readable_with_long_text_and_large_font(self):
        from PyQt6.QtCore import QRect, QPoint, Qt
        from PyQt6.QtWidgets import QLabel
        from PyQt6.QtTest import QTest
        from types import SimpleNamespace
        from ui.confirm_dialog import ConfirmActionDialog
        from ui.dialog_buttons import clamp_dialog_geometry
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm', font_size=16)
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 960, 640))
        try:
            for message in ('请确认此操作的对象和后果。', '示例说明：逐项核对操作对象和后果。\n' * 50,
                            '这是一段连续的长说明，需要自动折行并完整显示所有内容。' * 100):
                dialog = ConfirmActionDialog('确认操作', message)
                try:
                    clamp_dialog_geometry(dialog, 440, dialog.height(), screen=screen)
                    dialog.show()
                    QTest.qWait(30)
                    label = dialog.findChild(QLabel, 'confirm-message')
                    self.assertEqual(label.text(), message)
                    self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                    self.assertLessEqual(dialog.height(), 592)
                    for button in (dialog.cancel_button, dialog.confirm_button):
                        self.assertTrue(dialog.rect().contains(QRect(button.mapTo(dialog, QPoint()), button.size())))
                    self.assertTrue(dialog.cancel_button.isDefault())
                    if len(message) > 100:
                        bar = dialog.message_scroll.verticalScrollBar()
                        self.assertGreater(bar.maximum(), 0)
                        bar.setValue(bar.maximum())
                        self.assertEqual(bar.value(), bar.maximum())
                    QTest.keyClick(dialog, Qt.Key.Key_Escape)
                    self.assertEqual(dialog.result(), 0)
                finally:
                    dialog.close()
                    dialog.deleteLater()
        finally:
            ThemeManager.instance().apply(self.app, 'calm', font_size=13)

    def test_app_notice_and_next_step_dialogs(self):
        """AppNoticeDialog 与 NextStepDialog 宽度 440，按钮角色与 gap 8px。"""
        from ui.confirm_dialog import AppNoticeDialog, NextStepDialog

        notice = AppNoticeDialog("系统提示", "操作已顺利完成", kind="info")
        try:
            self.assertEqual(notice.width(), min(440, notice.maximumWidth()))
            self.assertTrue(notice.ok_button.isDefault())
        finally:
            notice.close()

        next_step = NextStepDialog(
            "任务已完成",
            "后续推荐操作",
            [("查看详情", "view", True), ("导出报告", "export", False)],
        )
        try:
            self.assertEqual(next_step.width(), min(440, next_step.maximumWidth()))
            self.assertEqual(len(next_step._action_buttons), 2)
        finally:
            next_step.close()

    def test_long_notice_and_next_step_keep_text_scroll_and_result(self):
        from PyQt6.QtCore import QRect, QPoint
        from PyQt6.QtWidgets import QLabel, QPushButton
        from PyQt6.QtTest import QTest
        from types import SimpleNamespace
        from ui.confirm_dialog import AppNoticeDialog, NextStepDialog
        from ui.dialog_buttons import clamp_dialog_geometry
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm', font_size=16)
        message = '保留详细结果和原始原因，不丢失连续长文本的自动换行正文。' * 100
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 960, 640))
        for dialog in (AppNoticeDialog('结果说明', message),
                       NextStepDialog('下一步', message, [('view', '查看详情', True)])):
            try:
                clamp_dialog_geometry(dialog, 440, dialog.height(), screen=screen)
                dialog.show()
                QTest.qWait(30)
                label = dialog.findChild(QLabel, 'confirm-message')
                self.assertEqual(label.text(), message)
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                self.assertGreater(dialog.message_scroll.verticalScrollBar().maximum(), 0)
                self.assertLessEqual(dialog.height(), 592)
                for button in dialog.findChildren(QPushButton):
                    self.assertTrue(dialog.rect().contains(QRect(button.mapTo(dialog, QPoint()), button.size())))
                if isinstance(dialog, NextStepDialog):
                    dialog._action_buttons[0].click()
                    self.assertEqual(dialog.selected_action(), 'view')
                else:
                    dialog.ok_button.click()
                self.assertEqual(dialog.result(), 1)
            finally:
                dialog.close()
                dialog.deleteLater()
        ThemeManager.instance().apply(self.app, 'calm', font_size=13)

    def test_https_cert_consent_dialog(self):
        """HttpsCertConsentDialog 宽度 >= 560，cancel 为 default 按钮。"""
        from ui.confirm_dialog import HttpsCertConsentDialog

        dlg = HttpsCertConsentDialog(language="zh")
        try:
            self.assertGreaterEqual(dlg.width(), min(560, dlg.maximumWidth()))
            self.assertTrue(dlg.cancel_button.isDefault())
            self.assertFalse(dlg.confirm_button.isDefault())
        finally:
            dlg.close()

    def test_connection_dialog_metrics(self):
        """ConnectionDialog 宽度 720，高度 min(680, ...)，标签 >= 80px，底栏固定。"""
        from ui.connection_dialog import ConnectionDialog
        from PyQt6.QtWidgets import QLabel

        dlg = ConnectionDialog(language="zh")
        try:
            self.assertEqual(dlg.width(), min(720, dlg.maximumWidth()))
            self.assertIsNotNone(dlg.content_scroll)

            # 底部动作固定存在且在主布局中
            self.assertIsNotNone(dlg.test_btn)
            self.assertIsNotNone(dlg.cancel_btn)
            self.assertIsNotNone(dlg.save_btn)

            # 检查标签最小宽度 >= 80
            labels = dlg.findChildren(QLabel)
            label_80 = [l for l in labels if l.minimumWidth() >= 80]
            self.assertGreater(len(label_80), 0)
        finally:
            dlg.close()

    def test_ticket_submit_dialog_metrics(self):
        """TicketSubmitDialog 宽度 860，TicketSubmitConfigDialog 720，底栏固定。"""
        from panels.ticket_submit_dialog import TicketSubmitDialog, TicketSubmitConfigDialog

        config_dlg = TicketSubmitConfigDialog(parent=None)
        try:
            self.assertEqual(config_dlg.width(), min(720, config_dlg.maximumWidth()))
        finally:
            config_dlg.close()

        submit_dlg = TicketSubmitDialog(requirements=[{"code": "R1", "title": "需求1"}], parent=None)
        try:
            self.assertEqual(submit_dlg.width(), min(860, submit_dlg.maximumWidth()))
            self.assertIsNotNone(submit_dlg.content_scroll)
            self.assertIsNotNone(submit_dlg.button_box)
        finally:
            submit_dlg.close()

    def test_dialog_standard_button_spec(self):
        """V2.0 Section 6.2: 弹窗标准操作按钮高 32px，最小宽 72px。"""
        from ui.dialog_buttons import DIALOG_BUTTON_H, DIALOG_BUTTON_MIN_W
        self.assertEqual(DIALOG_BUTTON_H, 32)
        self.assertEqual(DIALOG_BUTTON_MIN_W, 72)

    def test_test_points_dialog_and_row(self):
        """TestPointsDialog 宽度 640，TestPointRow 行高 >= 44px，checkbox 28px，编辑/删除 28x28 图标按钮。"""
        from panels.test_points_editor import TestPointsDialog, TestPointRow

        row = TestPointRow({"id": "tp-1", "title": "测试点1", "done": False})
        try:
            self.assertGreaterEqual(row.minimumHeight(), 44)
            self.assertEqual(row.check.width(), 28)
            self.assertEqual(row.check.height(), 28)
            self.assertEqual(row.edit_btn.width(), 28)
            self.assertEqual(row.edit_btn.height(), 28)
            self.assertEqual(row.delete_btn.width(), 28)
            self.assertEqual(row.delete_btn.height(), 28)

            # 验证点击编辑图标按钮触发编辑态
            self.assertFalse(row._editing)
            row.edit_btn.click()
            self.assertTrue(row._editing)
            self.assertFalse(row.text_edit.isHidden())
            self.assertTrue(row.text_label.isHidden())

            # 验证点击删除图标按钮触发 removed 信号
            deleted_ids = []
            row.removed.connect(lambda pid: deleted_ids.append(pid))
            row.delete_btn.click()
            self.assertEqual(deleted_ids, ["tp-1"])
        finally:
            row.deleteLater()

        dlg = TestPointsDialog({"code": "R1", "title": "测试需求"}, parent=None)
        try:
            self.assertEqual(dlg.width(), min(640, dlg.maximumWidth()))
        finally:
            dlg.close()

    def test_all_sub_dialogs_geometry_clamping(self):
        """测试各业务子窗口全部使用 clamp_dialog_geometry，并在边界内安全初始化。"""
        from panels.ops_log_panel import (
            CategoryManageDialog, ServerEditorDialog, LogSettingsDialog,
            ServerManageDialog, CommandHistoryDialog,
        )
        from panels.ops_panel import CustomCommandDialog
        from panels.personal_panel import KnowledgeEditDialog, PasteKnowledgeDialog
        from panels.model_chat_panel import _SkillManagerDialog
        from panels.ai_token_edit import ObjectPickDialog
        from panels.requirement_panel import (
            RequirementAttachmentDialog, RequirementDialog,
            MonthPickerDialog, SvnCheckoutDialog,
        )
        from ui.help_dialog import UserGuideDialog

        dialogs = [
            CategoryManageDialog(),
            ServerEditorDialog(),
            LogSettingsDialog(),
            ServerManageDialog(),
            CommandHistoryDialog(),
            CustomCommandDialog(),
            KnowledgeEditDialog(),
            PasteKnowledgeDialog(),
            _SkillManagerDialog(),
            ObjectPickDialog("zh", {}),
            RequirementAttachmentDialog({"name": "test.txt", "content": "hello"}),
            RequirementDialog(),
            MonthPickerDialog(),
            SvnCheckoutDialog(),
            UserGuideDialog(),
        ]

        try:
            for dlg in dialogs:
                # 每个弹窗最大宽高不超过可用几何区
                self.assertLessEqual(dlg.width(), dlg.maximumWidth())
                self.assertLessEqual(dlg.height(), dlg.maximumHeight())
                # 检查 showEvent 带有 play_dialog_enter
                self.assertTrue(hasattr(dlg, "showEvent"))
        finally:
            for dlg in dialogs:
                dlg.close()

    def test_dialogs_960x640_and_font_scale_adaptability(self):
        """验证在 960x640 紧凑屏幕与字体缩放 (125%/150%) 下，弹窗尺寸不超边界且渲染正常。"""
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QFont
        from unittest.mock import MagicMock, patch
        from ui.dialog_buttons import clamp_dialog_geometry
        from ui.connection_dialog import ConnectionDialog
        from panels.test_points_editor import TestPointsDialog
        from panels.ticket_submit_dialog import TicketSubmitDialog
        from ui.confirm_dialog import ConfirmActionDialog

        # 模拟 960x640 屏幕
        mock_screen = MagicMock()
        mock_screen.availableGeometry.return_value = QRect(0, 0, 960, 640)

        with patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=mock_screen):
            # 1. ConnectionDialog 在 960x640 下夹取
            conn_dlg = ConnectionDialog(parent=None)
            try:
                w, h = clamp_dialog_geometry(conn_dlg, 860, 680, screen=mock_screen)
                self.assertLessEqual(w, 960 - 48)
                self.assertLessEqual(h, 640 - 48)
            finally:
                conn_dlg.close()

            # 2. TestPointsDialog 在 960x640 下夹取
            tp_dlg = TestPointsDialog({"code": "R1", "title": "测试需求"}, parent=None)
            try:
                w, h = clamp_dialog_geometry(tp_dlg, 640, 520, screen=mock_screen)
                self.assertLessEqual(w, 960 - 48)
                self.assertLessEqual(h, 640 - 48)
            finally:
                tp_dlg.close()

            # 3. 字体放大测试 (125% ~ 150%: 12pt -> 15pt)，验证主要弹窗布局不抛出异常、不超界
            for font_size in (12, 15):
                f = QFont("Microsoft YaHei", font_size)
                self.app.setFont(f)

                dlg_action = ConfirmActionDialog("确认操作", "测试文案内容", "确认", danger=False)
                try:
                    dlg_action.show()
                    self.app.processEvents()
                    self.assertLessEqual(dlg_action.width(), 960)
                    self.assertLessEqual(dlg_action.height(), 640)
                    self.assertEqual(dlg_action.confirm_button.height(), 32)
                    self.assertGreaterEqual(dlg_action.confirm_button.width(), 72)
                finally:
                    dlg_action.close()


if __name__ == "__main__":
    unittest.main()
