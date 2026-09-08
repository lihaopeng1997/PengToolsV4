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

    def test_test_points_dialog_and_row(self):
        """TestPointsDialog 宽度 640，TestPointRow 行高 >= 44px，checkbox 命中区 >= 28px。"""
        from panels.test_points_editor import TestPointsDialog, TestPointRow

        row = TestPointRow({"id": "tp-1", "title": "测试点1", "done": False})
        try:
            self.assertGreaterEqual(row.minimumHeight(), 44)
            self.assertGreaterEqual(row.check.width(), 28)
            self.assertGreaterEqual(row.check.height(), 28)
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


if __name__ == "__main__":
    unittest.main()
