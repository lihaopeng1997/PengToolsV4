# -*- coding: utf-8 -*-
"""网关加解密工作台：国密解密 + JSON 结果呈现（XML 已迁至格式工具）。"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QMenu, QPlainTextEdit, QPushButton, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from tools.gateway_crypto import decrypt_gateway_payload
from ui.confirm_dialog import show_warning
from ui.design_system import apply_button, apply_surface
from ui.field_metrics import size_combo
from ui.json_viewer import JsonViewer


def _looks_like_xml(text: str) -> bool:
    s = (text or '').strip()
    if not s:
        return False
    sample = s[:800]
    if sample.startswith('<?xml') or sample.startswith('<'):
        return True
    if sample.startswith('"') and '<' in sample[:120]:
        return True
    if '\\n' in sample and '<' in sample:
        return True
    return False


class GatewayDecodePanel(QWidget):
    """仅负责加解密；JSON 查看器保留；XML 通过信号跳转格式工具。"""

    open_format_xml = pyqtSignal(str)
    open_interface_debug = pyqtSignal()

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._setup_ui()
        self.set_language(language)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.offline_pill = QLabel()
        self.offline_pill.setObjectName('offline-pill')
        try:
            from ui.page_chrome import make_page_header
            header, self.page_title, self.page_subtitle = make_page_header(
                '加解密',
                '本地处理，内容不保存',
                'shield-key',
                trailing=self.offline_pill,
            )
            layout.addWidget(header)
        except Exception:
            head = QHBoxLayout()
            self.page_title = QLabel(); self.page_title.setObjectName('page-title')
            self.page_subtitle = QLabel(); self.page_subtitle.setObjectName('page-subtitle')
            titles = QVBoxLayout(); titles.addWidget(self.page_title); titles.addWidget(self.page_subtitle)
            head.addLayout(titles, 1); head.addWidget(self.offline_pill)
            layout.addLayout(head)

        self.config_toggle = QPushButton('解密参数 ▸')
        self.config_toggle.setCheckable(True)
        self.config_toggle.setProperty('compactAction', True)
        self.config_toggle.setToolTip('新车险系统固定兼容模式 · 密钥仅本机使用')
        self.config_toggle.toggled.connect(self._toggle_config)
        layout.addWidget(self.config_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.config_group = QGroupBox()
        self.config_group.setObjectName('gateway-config-group')
        self.config_group.setTitle('')
        self.config_group.hide()
        config = QFormLayout(self.config_group)
        config.setContentsMargins(14, 12, 14, 12)
        config.setHorizontalSpacing(14)
        config.setVerticalSpacing(10)
        self.system_value = QLabel('新车险系统（固定兼容模式）')
        self.system_value.hide()
        self.system_label = QLabel()
        self.system_label.hide()
        self.environment = QComboBox()
        size_combo(self.environment, 'sm')
        self.environment.addItems(['集成环境', '用户环境', '生产环境'])
        self.environment_label = QLabel()
        config.addRow(self.environment_label, self.environment)
        self.key_cipher = QPlainTextEdit()
        self.key_cipher.setObjectName('gateway-key-edit')
        self.key_cipher.setMaximumHeight(88)
        self.key_cipher.setPlaceholderText('SM2 encrypted SM4 key (hex)')
        self.key_label = QLabel()
        config.addRow(self.key_label, self.key_cipher)
        layout.addWidget(self.config_group)

        work_zone = QFrame()
        apply_surface(work_zone, 'card')
        work_zone.setObjectName('gateway-work-zone')
        work_layout = QVBoxLayout(work_zone)
        work_layout.setContentsMargins(12, 10, 12, 12)
        work_layout.setSpacing(8)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter = self.splitter
        splitter.setObjectName('gateway-splitter')
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        self.cipher_label = QLabel()
        self.cipher_label.setObjectName('zone-title')
        left_layout.addWidget(self.cipher_label)
        self.payload_cipher = QPlainTextEdit()
        self.payload_cipher.setObjectName('gateway-cipher-edit')
        self.payload_cipher.setPlaceholderText('Base64 ciphertext')
        left_layout.addWidget(self.payload_cipher)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        plain_head = QHBoxLayout()
        plain_head.setSpacing(8)
        self.plain_label = QLabel()
        self.plain_label.setObjectName('zone-title')
        plain_head.addWidget(self.plain_label, 1)
        # 低干扰入口：仅当结果像 XML 时显示
        self.to_format_xml_btn = QPushButton()
        apply_button(self.to_format_xml_btn, 'ghost', compact=True, icon='xml', icon_size=16)
        self.to_format_xml_btn.clicked.connect(self._send_plain_to_format_xml)
        self.to_format_xml_btn.hide()
        plain_head.addWidget(self.to_format_xml_btn)
        right_layout.addLayout(plain_head)
        self.json_viewer = JsonViewer(self.language)
        self.plain_text = self.json_viewer.text_edit
        right_layout.addWidget(self.json_viewer)
        splitter.addWidget(right)
        splitter.setSizes([480, 560])
        work_layout.addWidget(splitter, 1)
        layout.addWidget(work_zone, 1)

        action_bar = QFrame()
        apply_surface(action_bar, 'zone')
        action_bar.setObjectName('gateway-action-zone')
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(12, 10, 12, 10)
        actions.setSpacing(8)
        self.note = QLabel()
        self.note.setObjectName('field-hint')
        self.note.hide()
        self.to_iface_btn = QPushButton()
        apply_button(self.to_iface_btn, 'ghost', compact=True, icon='api-debug', icon_size=16)
        self.to_iface_btn.clicked.connect(self.open_interface_debug.emit)
        actions.addWidget(self.to_iface_btn)
        actions.addStretch(1)
        self.clear_btn = QPushButton()
        apply_button(self.clear_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.clear_btn.clicked.connect(self._clear)
        actions.addWidget(self.clear_btn)
        self.copy_btn = QPushButton()
        apply_button(self.copy_btn, 'secondary', compact=True, icon='copy', icon_size=16)
        self.copy_btn.clicked.connect(self._copy)
        actions.addWidget(self.copy_btn)
        self.request_btn = QPushButton()
        apply_button(self.request_btn, 'secondary', compact=True, icon='shield-key', icon_size=16)
        self.request_btn.clicked.connect(lambda: self._decrypt('request'))
        actions.addWidget(self.request_btn)
        self.response_btn = QPushButton()
        apply_button(self.response_btn, 'primary', compact=True, icon='shield-key', icon_size=16)
        self.response_btn.clicked.connect(lambda: self._decrypt('response'))
        actions.addWidget(self.response_btn)
        # 更多菜单：窄屏收纳次要操作
        self.more_btn = QToolButton()
        self.more_btn.setObjectName('responsive-more-btn')
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_menu = QMenu(self.more_btn)
        self.more_btn.setMenu(self._more_menu)
        self.more_btn.hide()
        actions.addWidget(self.more_btn)
        self._secondary_btns = [self.to_iface_btn, self.clear_btn, self.copy_btn, self.request_btn]
        layout.addWidget(action_bar)

        # 兼容旧引用（已移除 work_tabs / xml_workspace）
        self.work_tabs = None
        self.xml_workspace = None
        self.to_xml_btn = self.to_format_xml_btn
        self._layout_mode = 'standard'

    def apply_layout_mode(self, mode, low_height=False):
        self._layout_mode = mode
        from ui.responsive import apply_splitter_orientation, set_subtitle_visible
        set_subtitle_visible(getattr(self, 'page_subtitle', None), low_height)
        apply_splitter_orientation(self.splitter, mode, min_editor=180)
        self._more_menu.clear()
        zh = self.language == 'zh'
        self.more_btn.setText('更多' if zh else 'More')
        if mode in ('compact', 'narrow'):
            # 主操作：响应解密始终可见；Narrow 再收 request/copy/clear/iface
            keep = {self.response_btn}
            if mode == 'compact':
                keep.update({self.request_btn, self.copy_btn})
            for btn in self._secondary_btns:
                if btn in keep:
                    btn.show()
                else:
                    btn.hide()
                    act = QAction(btn.text() or btn.toolTip() or 'action', self)
                    act.triggered.connect(btn.click)
                    self._more_menu.addAction(act)
            self.more_btn.setVisible(bool(self._more_menu.actions()))
            self.response_btn.show()
        else:
            for btn in self._secondary_btns:
                btn.show()
            self.response_btn.show()
            self.more_btn.hide()

    def _toggle_config(self, checked):
        self.config_group.setVisible(bool(checked))
        zh = self.language == 'zh'
        self.config_toggle.setText(
            ('解密参数 ▾' if checked else '解密参数 ▸') if zh else
            ('Decrypt params ▾' if checked else 'Decrypt params ▸')
        )

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('加解密' if zh else 'Crypto')
        self.page_subtitle.setText(
            '本地处理，内容不保存' if zh else 'Processed locally · nothing is saved'
        )
        self.offline_pill.setText('● 本地' if zh else '● Local')
        self.offline_pill.setObjectName('dashboard-local-status')
        self.config_toggle.setText(
            ('解密参数 ▾' if self.config_toggle.isChecked() else '解密参数 ▸') if zh else
            ('Decrypt params ▾' if self.config_toggle.isChecked() else 'Decrypt params ▸')
        )
        self.config_toggle.setToolTip(
            '新车险系统固定兼容模式 · 密钥仅本机使用' if zh else
            'Auto-insurance fixed compatibility · keys stay local'
        )
        self.system_label.setText('系统' if zh else 'System')
        self.system_value.setText(
            '新车险系统（固定兼容模式）' if zh else 'New Auto Insurance (fixed compatibility mode)'
        )
        self.environment_label.setText('环境' if zh else 'Environment')
        env_names = ['集成环境', '用户环境', '生产环境'] if zh else ['Integration', 'User / UAT', 'Production']
        for index, name in enumerate(env_names):
            self.environment.setItemText(index, name)
        self.key_label.setText('SM4 Key 密文' if zh else 'Encrypted SM4 key')
        self.cipher_label.setText('输入' if zh else 'Input')
        self.cipher_label.setToolTip(
            '网关正文密文（Base64）' if zh else 'Gateway payload ciphertext (Base64)'
        )
        self.plain_label.setText('结果' if zh else 'Result')
        self.to_format_xml_btn.setText('在格式工具中打开 XML' if zh else 'Open XML in Format tools')
        self.to_format_xml_btn.setToolTip(
            '跳转到格式工具 · XML Tab' if zh else 'Jump to Format tools · XML tab'
        )
        self.payload_cipher.setPlaceholderText(
            '粘贴 Base64 密文' if zh else 'Paste Base64 ciphertext'
        )
        self.key_cipher.setToolTip(
            '兼容 gatewayDecode.html：SM2 解 Key，再以 Key 作为 SM4-CBC 的 Key 与 IV'
            if zh else
            'Compatible with gatewayDecode.html (SM2 key → SM4-CBC)'
        )
        self.to_iface_btn.setText('进入接口排查' if zh else 'API Debug')
        self.to_iface_btn.setToolTip(
            '跳转到多浏览器接口排查中心' if zh else 'Open multi-browser API debug center'
        )
        self.clear_btn.setText('清空' if zh else 'Clear')
        self.copy_btn.setText('复制明文' if zh else 'Copy plaintext')
        self.request_btn.setText('请求解密' if zh else 'Decrypt request')
        self.response_btn.setText('响应解密' if zh else 'Decrypt response')
        self.json_viewer.set_language(language)

    def set_cipher_text(self, text: str):
        """从接口排查等模块带入密文/正文，不自动解密。"""
        self.payload_cipher.setPlainText(text or '')

    def _decrypt(self, direction):
        try:
            plain = decrypt_gateway_payload(
                direction,
                self.environment.currentIndex() + 1,
                self.key_cipher.toPlainText(),
                self.payload_cipher.toPlainText(),
            )
            self.json_viewer.set_text(plain, auto_format=True)
            if _looks_like_xml(plain):
                self._offer_xml_view(plain)
            else:
                self.to_format_xml_btn.hide()
        except ValueError as exc:
            self.json_viewer.clear()
            self.to_format_xml_btn.hide()
            show_warning(self, '网关解密' if self.language == 'zh' else 'Gateway decrypt', str(exc))

    def _offer_xml_view(self, plain: str):
        zh = self.language == 'zh'
        self.to_format_xml_btn.show()
        if zh:
            self.json_viewer.json_status.setText(
                '已解密 · 内容像 XML · 可「在格式工具中打开 XML」'
            )
        else:
            self.json_viewer.json_status.setText(
                'Decrypted · looks like XML · open in Format tools'
            )

    def _send_plain_to_format_xml(self):
        text = self.json_viewer.plain_text()
        if not text.strip():
            show_warning(
                self,
                '格式工具' if self.language == 'zh' else 'Format tools',
                '当前没有可送入的明文。' if self.language == 'zh' else 'No plaintext to send.',
            )
            return
        self.open_format_xml.emit(text)

    def _copy(self):
        text = self.json_viewer.plain_text()
        if text:
            QApplication.clipboard().setText(text)

    def _clear(self):
        self.key_cipher.clear()
        self.payload_cipher.clear()
        self.json_viewer.clear()
        self.to_format_xml_btn.hide()
