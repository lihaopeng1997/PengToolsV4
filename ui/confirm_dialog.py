# -*- coding: utf-8 -*-
"""统一弹窗体系：确认 / 通知 / 退出选择。

设计原则（Fluent 2 / Material 3 思路）：
- 标题直接表达核心决定，不空泛
- 动作数量少、按钮语义直接
- 危险操作与普通通知层级分明
- 默认焦点落在安全动作上

API：confirm_action / show_* / offer_next_steps；
ask_close_action → (action, dont_ask) 或 None。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)
import random

from ui.design_system import apply_button
from ui.dialog_buttons import size_dialog_button
from ui.icons import make_badge_label, apply_icon


class ConfirmActionDialog(QDialog):
    """危险/确认操作：取消在左、确认在右，默认焦点永远在取消。"""

    def __init__(self, title, message, confirm_text='确认删除', parent=None, danger=True):
        super().__init__(parent)
        self.setObjectName('confirm-dialog')
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        badge = make_badge_label('danger' if danger else 'info', size=40, icon_size=22)
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName('confirm-title')
        title_label.setWordWrap(True)
        title_wrap.addWidget(title_label)
        if danger:
            role_hint = QLabel('此操作需二次确认 · 默认焦点在「取消」')
            role_hint.setObjectName('field-hint')
            title_wrap.addWidget(role_hint)
        header.addLayout(title_wrap, 1)
        root.addLayout(header)

        card = QFrame()
        card.setObjectName('confirm-card')
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        message_label = QLabel(message)
        message_label.setObjectName('confirm-message')
        message_label.setWordWrap(True)
        message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(message_label)
        root.addWidget(card)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch()
        self.cancel_button = QPushButton('取消')
        size_dialog_button(self.cancel_button, 'secondary')
        self.cancel_button.setObjectName('confirm-cancel')
        self.cancel_button.setDefault(True)
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.confirm_button = QPushButton(confirm_text)
        size_dialog_button(
            self.confirm_button,
            'danger' if danger else 'primary',
        )
        if danger:
            self.confirm_button.setObjectName('btn-danger')
            try:
                from ui.icons import apply_icon
                apply_icon(self.confirm_button, 'delete', 16)
            except Exception:
                pass
        else:
            self.confirm_button.setObjectName('primary-btn')
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.setDefault(False)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(self.confirm_button)
        root.addLayout(buttons)
        self.cancel_button.setFocus()


class _CloseOptionCard(QFrame):
    """可键盘聚焦的选择卡片：一点即选，不再二次确认。"""

    clicked = pyqtSignal()

    def __init__(self, icon_role, title_text, tip_text, object_name, parent=None, icon_tint=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 16, 14)
        layout.setSpacing(12)

        badge = QLabel()
        badge.setObjectName('close-option-badge')
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(36, 36)
        from ui.icons import icon_pixmap, status_icon_tint
        # 徽章底为有色，图标用 ON_STATUS / 主题对比色
        tint = icon_tint or status_icon_tint('info')
        pix = icon_pixmap(icon_role, 20, tint)
        if not pix.isNull():
            badge.setPixmap(pix)
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title = QLabel(title_text)
        title.setObjectName('close-option-title')
        tip = QLabel(tip_text)
        tip.setObjectName('close-option-tip')
        tip.setWordWrap(True)
        text_col.addWidget(title)
        text_col.addWidget(tip)
        layout.addLayout(text_col, 1)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class CloseActionDialog(QDialog):
    """关闭主窗口决策：隐藏托盘 / 退出；可勾选「不再提示」。

    幽默版：随机展示一条贴合接口排查场景的搞笑提示语。
    """

    _FUNNY_LINES_ZH = [
        '接口还没排查完，你就要跑了？\n跑得了和尚跑不了报文啊！',
        '别走啊！你的 Bug 还在等你修呢，它说它想你了！',
        '500 个请求里还有 499 个没看，确定要辜负它们吗？',
        '你的报文们在哭泣：「主人，别丢下我们！」',
        '再排查一个接口嘛，就一个！好不好嘛～',
        'Exit？这个单词太伤感情了，要不要改成 Stay？',
        '确认退出？所有未保存的排查记录将化作一道彩虹飞走～',
        '接口排查工具申请添加你为好友，验证消息：别走！',
        '你忍心让你的 200 OK 变成 404 Not Found 吗？',
        'Bug 们正在开派对庆祝你离开，确定要走吗？',
    ]
    _FUNNY_LINES_EN = [
        'Your API bugs are waving goodbye... with middle fingers.',
        '500 requests? You\'ve only checked 1. Quitter!',
        'Exit? How about "Strategic Retreat" instead?',
        'Your packets are crying in the corner right now.',
        'One more API, just one! Pretty please?',
    ]

    def __init__(self, language='zh', default_action='minimize', parent=None):
        super().__init__(parent)
        self._result = None
        zh = language == 'zh'
        self.setObjectName('confirm-dialog')
        try:
            from config import APP_NAME
        except Exception:
            APP_NAME = 'PengToolsHub'
        self.setWindowTitle(f'关闭 {APP_NAME}？' if zh else f'Close {APP_NAME}?')
        self.setModal(True)
        self.setMinimumWidth(360)
        self.setMaximumWidth(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        root.setSpacing(10)

        # —— 决策标题 ——
        title = QLabel(f'关闭 {APP_NAME}？' if zh else f'Close {APP_NAME}?')
        title.setObjectName('confirm-title')
        root.addWidget(title)

        # —— 幽默提示语（随机一条）——
        lines = self._FUNNY_LINES_ZH if zh else self._FUNNY_LINES_EN
        funny = QLabel(random.choice(lines))
        funny.setObjectName('confirm-message')
        funny.setWordWrap(True)
        root.addWidget(funny)

        # —— 两个按钮同一行 ——
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.minimize_button = QPushButton('隐藏' if zh else 'Hide')
        size_dialog_button(self.minimize_button, 'secondary')
        self.minimize_button.setObjectName('confirm-cancel')
        self.minimize_button.clicked.connect(lambda: self._choose('minimize'))
        btn_row.addWidget(self.minimize_button, 1)

        self.exit_button = QPushButton('退出软件' if zh else 'Exit')
        size_dialog_button(self.exit_button, 'danger')
        self.exit_button.setObjectName('btn-danger')
        self.exit_button.clicked.connect(lambda: self._choose('exit'))
        btn_row.addWidget(self.exit_button, 1)
        root.addLayout(btn_row)

        # —— 底部：勾选框 + 取消，同一行 ——
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.dont_ask_check = QCheckBox(
            '关闭时不再提示' if zh else "Don't ask again"
        )
        self.dont_ask_check.setObjectName('close-dont-ask')
        footer.addWidget(self.dont_ask_check, 1)

        self.cancel_button = QPushButton('取消' if zh else 'Cancel')
        size_dialog_button(self.cancel_button, 'ghost')
        self.cancel_button.setObjectName('confirm-cancel')
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        root.addLayout(footer)

        # 默认焦点落在安全控件：隐藏；若默认动作为退出，则落在取消
        if default_action == 'exit':
            self.cancel_button.setAutoDefault(True)
            self.cancel_button.setDefault(True)
            self.cancel_button.setFocus()
        else:
            self.minimize_button.setFocus()

    def _choose(self, action):
        self._result = action
        self.accept()

    def selected_action(self):
        return self._result

    def dont_ask_again(self):
        return bool(self.dont_ask_check.isChecked())


class AppNoticeDialog(QDialog):
    """统一成功 / 提示 / 警告弹窗，替代原生 QMessageBox 的生硬外观。"""

    def __init__(self, title, message, kind='info', parent=None, button_text='知道了'):
        super().__init__(parent)
        self.setObjectName('confirm-dialog')
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        badge = make_badge_label(kind if kind in ('info', 'success', 'warning', 'error') else 'info', size=40, icon_size=22)
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName('confirm-title')
        title_wrap.addWidget(title_label)
        if message:
            message_label = QLabel(message)
            message_label.setObjectName('confirm-message')
            message_label.setWordWrap(True)
            message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            title_wrap.addWidget(message_label)
        header.addLayout(title_wrap, 1)
        root.addLayout(header)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.ok_button = QPushButton(button_text)
        size_dialog_button(self.ok_button, 'primary')
        self.ok_button.setObjectName('primary-btn')
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(self.ok_button)
        root.addLayout(buttons)
        self.ok_button.setFocus()


def confirm_action(parent, title, message, confirm_text='确认删除', danger=True):
    return ConfirmActionDialog(
        title, message, confirm_text, parent, danger=danger
    ).exec() == QDialog.DialogCode.Accepted


def ask_close_action(parent, language='zh', default_action='minimize'):
    """返回 (action, dont_ask_again)；取消返回 None。

    action: 'minimize' | 'exit'
    dont_ask_again: bool — 为 True 时调用方应写入 settings 并关闭询问。
    """
    dialog = CloseActionDialog(language=language, default_action=default_action, parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    action = dialog.selected_action()
    if action not in ('minimize', 'exit'):
        return None
    return action, dialog.dont_ask_again()


def show_info(parent, title, message, kind='info', button_text='知道了'):
    return AppNoticeDialog(title, message, kind=kind, parent=parent, button_text=button_text).exec()


def show_success(parent, title, message, button_text='知道了'):
    return show_info(parent, title, message, kind='success', button_text=button_text)


def show_warning(parent, title, message, button_text='知道了'):
    return show_info(parent, title, message, kind='warning', button_text=button_text)


def show_error(parent, title, message, button_text='知道了'):
    return show_info(parent, title, message, kind='error', button_text=button_text)


class NextStepDialog(QDialog):
    """懒人下一步：单次弹窗提供推荐操作，避免连环确认。"""

    def __init__(self, title, message, actions, parent=None, recommended=None):
        super().__init__(parent)
        self._result = None
        self.setObjectName('confirm-dialog')
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(620)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName('confirm-title')
        root.addWidget(title_label)

        if message:
            message_label = QLabel(message)
            message_label.setObjectName('confirm-message')
            message_label.setWordWrap(True)
            message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            root.addWidget(message_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch()
        later = QPushButton('稍后')
        size_dialog_button(later, 'ghost')
        later.setObjectName('confirm-cancel')
        later.setAutoDefault(False)
        later.clicked.connect(self.reject)
        button_row.addWidget(later)

        self._action_buttons = []
        for action_id, label, is_primary in actions:
            button = QPushButton(label)
            role = 'primary' if (is_primary or action_id == recommended) else 'secondary'
            size_dialog_button(button, role)
            if role == 'primary':
                button.setObjectName('primary-btn')
            else:
                button.setObjectName('btn-secondary')
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, value=action_id: self._choose(value))
            button_row.addWidget(button)
            self._action_buttons.append(button)
            if action_id == recommended or is_primary:
                button.setDefault(True)
                button.setFocus()
        root.addLayout(button_row)
        if not any(action_id == recommended or is_primary for action_id, _label, is_primary in actions):
            later.setDefault(True)
            later.setFocus()

    def _choose(self, action_id):
        self._result = action_id
        self.accept()

    def selected_action(self):
        return self._result


def offer_next_steps(parent, title, message, actions, recommended=None):
    """显示下一步建议，返回 action_id 或 None（稍后/取消）。

    actions: [(action_id, label, is_primary), ...]
    """
    dialog = NextStepDialog(title, message, actions, parent=parent, recommended=recommended)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected_action()


class HttpsCertConsentDialog(QDialog):
    """首次启用 HTTPS 抓包或手动安装根证书前的明确授权弹窗。"""

    def __init__(self, parent=None, language: str = 'zh', for_listen: bool = True):
        super().__init__(parent)
        self.setObjectName('confirm-dialog')
        zh = language == 'zh'
        title = '启用 HTTPS 抓包' if zh else 'Enable HTTPS Capture'
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setMaximumWidth(580)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        badge = make_badge_label('info', size=40, icon_size=22)
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName('confirm-title')
        title_label.setWordWrap(True)
        title_wrap.addWidget(title_label)
        role_hint = QLabel('安装前请阅读用途与安全说明 · 默认焦点在「取消」' if zh else 'Please review the purpose and security notice')
        role_hint.setObjectName('field-hint')
        title_wrap.addWidget(role_hint)
        header.addLayout(title_wrap, 1)
        root.addLayout(header)

        card = QFrame()
        card.setObjectName('confirm-card')
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        desc_text = (
            'HTTPS 内容经过加密。为了查看请求头、请求体和响应内容，PengTools 需要让浏览器通过本机 127.0.0.1 代理，'
            '并信任一个仅用于本机抓包的 mitmproxy CA 证书。\n\n'
            '• 本机安全：证书生成并保存在本机，安装证书动作本身不会联网下载任何软件；\n'
            '• 信任范围：将写入“当前 Windows 用户”的受信任根证书库，仅用于解密通过本机代理的抓包流量；\n'
            '• 自动恢复：停止监听时会自动恢复原系统代理；\n'
            '• 随时移除：可在接口排查页面的「更多」菜单中随时一键移除该证书。\n\n'
            '注：不安装证书时不会启动会影响 HTTPS 浏览的抓包代理。'
            if zh else
            'HTTPS content is encrypted. To inspect request headers, body, and responses, '
            'PengTools routes traffic through a local 127.0.0.1 proxy and requires trusting a local mitmproxy CA certificate.\n\n'
            '• Local security: The certificate is generated and stored locally; no software is downloaded from the internet;\n'
            '• Scope: Added to Current User Trusted Root store, only decrypting traffic through local proxy;\n'
            '• Auto restore: System proxy is restored automatically when listening stops;\n'
            '• Removable anytime: You can remove the certificate anytime from the More menu.\n\n'
            'Note: Without installing the certificate, capture proxy affecting HTTPS traffic will not be started.'
        )
        msg_label = QLabel(desc_text)
        msg_label.setObjectName('confirm-message')
        msg_label.setWordWrap(True)
        msg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(msg_label)
        root.addWidget(card)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch()

        self.cancel_button = QPushButton('取消' if zh else 'Cancel')
        size_dialog_button(self.cancel_button, 'secondary')
        self.cancel_button.setObjectName('confirm-cancel')
        self.cancel_button.setDefault(True)
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)

        confirm_text = ('安装证书并开始' if for_listen else '安装证书') if zh else ('Install Certificate & Start' if for_listen else 'Install Certificate')
        self.confirm_button = QPushButton(confirm_text)
        size_dialog_button(self.confirm_button, 'primary')
        self.confirm_button.setObjectName('primary-btn')
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.setDefault(False)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(self.confirm_button)

        root.addLayout(buttons)
        self.cancel_button.setFocus()


def confirm_https_cert_consent(parent=None, language: str = 'zh', for_listen: bool = True) -> bool:
    dialog = HttpsCertConsentDialog(parent, language=language, for_listen=for_listen)
    return dialog.exec() == QDialog.DialogCode.Accepted
