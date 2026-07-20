# -*- coding: utf-8 -*-
"""网关加解密工作台：国密解密 + XML 工具并列子模块。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from tools.gateway_crypto import decrypt_gateway_payload
from ui.confirm_dialog import show_warning
from ui.design_system import apply_button, apply_surface
from ui.field_metrics import size_combo
from ui.json_viewer import JsonViewer
from ui.xml_workspace import XmlWorkspace


def _looks_like_xml(text: str) -> bool:
    s = (text or '').strip()
    if not s:
        return False
    # 常见：直接 XML、外层引号包裹、转义后仍带 <tag
    sample = s[:800]
    if sample.startswith('<?xml') or sample.startswith('<'):
        return True
    if sample.startswith('"') and '<' in sample[:120]:
        return True
    if '\\n' in sample and '<' in sample:
        return True
    return False


class GatewayDecodePanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._setup_ui()
        self.set_language(language)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # —— 页头（V2.0 骨架）——
        self.offline_pill = QLabel()
        self.offline_pill.setObjectName('offline-pill')
        try:
            from ui.page_chrome import make_page_header
            header, self.page_title, self.page_subtitle = make_page_header(
                '网关加解密工作台',
                '本地离线处理 · 密钥与明文默认不落盘',
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

        # —— 参数区：默认折叠 ——
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
        # 固定兼容说明移入 tooltip，不占表单行
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

        # —— 工作台 Tab：国密解密 | XML 工具 ——
        self.work_tabs = QTabWidget()
        self.work_tabs.setObjectName('module-tabs')
        self.work_tabs.setDocumentMode(True)

        crypto_page = QWidget()
        crypto_layout = QVBoxLayout(crypto_page)
        crypto_layout.setContentsMargins(0, 8, 0, 0)
        crypto_layout.setSpacing(10)

        work_zone = QFrame()
        apply_surface(work_zone, 'card')
        work_zone.setObjectName('gateway-work-zone')
        work_layout = QVBoxLayout(work_zone)
        work_layout.setContentsMargins(12, 10, 12, 12)
        work_layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
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
        self.to_xml_btn = QPushButton()
        apply_button(self.to_xml_btn, 'ghost', compact=True, icon='xml', icon_size=16)
        self.to_xml_btn.clicked.connect(self._send_plain_to_xml)
        plain_head.addWidget(self.to_xml_btn)
        right_layout.addLayout(plain_head)
        self.json_viewer = JsonViewer(self.language)
        # 兼容原有调用与自动化检查
        self.plain_text = self.json_viewer.text_edit
        right_layout.addWidget(self.json_viewer)
        splitter.addWidget(right)
        splitter.setSizes([480, 560])
        work_layout.addWidget(splitter, 1)
        crypto_layout.addWidget(work_zone, 1)

        action_bar = QFrame()
        apply_surface(action_bar, 'zone')
        action_bar.setObjectName('gateway-action-zone')
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(12, 10, 12, 10)
        actions.setSpacing(8)
        # 常驻说明改为 tooltip，底部只留状态空位
        self.note = QLabel()
        self.note.setObjectName('field-hint')
        self.note.hide()
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
        crypto_layout.addWidget(action_bar)

        self.work_tabs.addTab(crypto_page, '')

        self.xml_workspace = XmlWorkspace(self.language)
        self.work_tabs.addTab(self.xml_workspace, '')
        layout.addWidget(self.work_tabs, 1)
        try:
            from ui.icons import qicon
            # Tab 图标：解密 / XML（文字在 set_language 设置）
            self.work_tabs.setTabIcon(0, qicon('shield-key'))
            self.work_tabs.setTabIcon(1, qicon('xml'))
        except Exception:
            pass

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
        self.config_group.setTitle('' if zh else '')
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
        self.to_xml_btn.setText('送入 XML 工具' if zh else 'Send to XML')
        self.to_xml_btn.setToolTip(
            '将当前明文复制到 XML 工具页并尝试格式化' if zh else 'Copy plaintext into the XML workspace'
        )
        self.note.setText('')
        self.payload_cipher.setPlaceholderText(
            '粘贴 Base64 密文' if zh else 'Paste Base64 ciphertext'
        )
        self.key_cipher.setToolTip(
            '兼容 gatewayDecode.html：SM2 解 Key，再以 Key 作为 SM4-CBC 的 Key 与 IV'
            if zh else
            'Compatible with gatewayDecode.html (SM2 key → SM4-CBC)'
        )
        self.clear_btn.setText('清空' if zh else 'Clear')
        self.copy_btn.setText('复制明文' if zh else 'Copy plaintext')
        self.request_btn.setText('请求解密' if zh else 'Decrypt request')
        self.response_btn.setText('响应解密' if zh else 'Decrypt response')
        self.work_tabs.setTabText(0, '国密解密' if zh else 'SM decrypt')
        self.work_tabs.setTabText(1, 'XML 工具' if zh else 'XML tools')
        self.json_viewer.set_language(language)
        self.xml_workspace.set_language(language)

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
        except ValueError as exc:
            self.json_viewer.clear()
            show_warning(self, '网关解密' if self.language == 'zh' else 'Gateway decrypt', str(exc))

    def _offer_xml_view(self, plain: str):
        """解密结果像 XML 时给出一步懒人入口，不打断主流程。"""
        zh = self.language == 'zh'
        # 轻提示：用户可点「送入 XML」；避免再弹强制对话框打断
        if zh:
            self.json_viewer.json_status.setText(
                '已解密 · 内容像 XML · 可点「送入 XML 工具」继续美化'
            )
        else:
            self.json_viewer.json_status.setText(
                'Decrypted · looks like XML · use “Send to XML” to beautify'
            )

    def _send_plain_to_xml(self):
        text = self.json_viewer.plain_text()
        if not text.strip():
            show_warning(
                self,
                'XML 工具' if self.language == 'zh' else 'XML tools',
                '当前没有可送入的明文。' if self.language == 'zh' else 'No plaintext to send.',
            )
            return
        self.xml_workspace.set_input_text(text, auto_format=True)
        self.work_tabs.setCurrentWidget(self.xml_workspace)

    def _copy(self):
        text = self.json_viewer.plain_text()
        if text:
            QApplication.clipboard().setText(text)

    def _clear(self):
        self.key_cipher.clear()
        self.payload_cipher.clear()
        self.json_viewer.clear()
