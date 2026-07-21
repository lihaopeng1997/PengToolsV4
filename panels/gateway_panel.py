# -*- coding: utf-8 -*-
"""网关加解密工作台：国密解密 + JSON 结果呈现（XML 已迁至格式工具）。

解密参数区始终可见；从接口排查送入报文时只填充密文，不覆盖已录入 Key。
密钥仅内存，不落盘、不写日志。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton, QSplitter, QToolButton,
    QVBoxLayout, QWidget,
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
        self._key_visible = True
        self._setup_ui()
        self.set_language(language)
        self._refresh_param_visibility()

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

        # 解密参数区：始终显示，不得默认折叠
        self.config_group = QGroupBox()
        self.config_group.setObjectName('gateway-config-group')
        self.config_group.setTitle('解密参数')
        config = QFormLayout(self.config_group)
        config.setContentsMargins(14, 16, 14, 12)
        config.setHorizontalSpacing(14)
        config.setVerticalSpacing(10)

        self.system_label = QLabel()
        self.system_value = QLabel('新车险系统（固定兼容模式）')
        self.system_value.setObjectName('field-hint')
        config.addRow(self.system_label, self.system_value)

        self.algo_label = QLabel()
        self.algo_value = QLabel('SM2 + SM4')
        config.addRow(self.algo_label, self.algo_value)

        self.mode_label = QLabel()
        self.mode_value = QLabel('CBC')
        config.addRow(self.mode_label, self.mode_value)

        self.padding_label = QLabel()
        self.padding_value = QLabel('PKCS#7 / 分组填充')
        config.addRow(self.padding_label, self.padding_value)

        self.encoding_label = QLabel()
        self.encoding_value = QLabel('Key=Hex · 正文=Base64 · 明文=UTF-8')
        config.addRow(self.encoding_label, self.encoding_value)

        self.environment = QComboBox()
        size_combo(self.environment, 'sm')
        self.environment.addItems(['集成环境', '用户环境', '生产环境'])
        self.environment_label = QLabel()
        config.addRow(self.environment_label, self.environment)

        # Key（密钥）— 清晰中文标签 + 可录入区域 + 显示/隐藏
        key_wrap = QWidget()
        key_layout = QVBoxLayout(key_wrap)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(4)
        key_head = QHBoxLayout()
        key_head.setContentsMargins(0, 0, 0, 0)
        self.key_label = QLabel()
        self.key_label.setObjectName('zone-title')
        key_head.addWidget(self.key_label, 1)
        self.key_reveal_cb = QCheckBox()
        self.key_reveal_cb.setChecked(True)
        self.key_reveal_cb.toggled.connect(self._toggle_key_visibility)
        key_head.addWidget(self.key_reveal_cb)
        key_layout.addLayout(key_head)
        self.key_cipher = QPlainTextEdit()
        self.key_cipher.setObjectName('gateway-key-edit')
        self.key_cipher.setMinimumHeight(72)
        self.key_cipher.setMaximumHeight(110)
        self.key_cipher.setPlaceholderText('粘贴 SM2 加密后的 SM4 Key（十六进制 Hex）')
        key_layout.addWidget(self.key_cipher)
        self.key_hint = QLabel()
        self.key_hint.setObjectName('field-hint')
        self.key_hint.setWordWrap(True)
        key_layout.addWidget(self.key_hint)
        config.addRow(key_wrap)

        # IV/Nonce：本协议 Key 同时作 IV，说明原因
        self.iv_label = QLabel()
        self.iv_value = QLabel()
        self.iv_value.setObjectName('field-hint')
        self.iv_value.setWordWrap(True)
        config.addRow(self.iv_label, self.iv_value)

        self.param_note = QLabel()
        self.param_note.setObjectName('field-hint')
        self.param_note.setWordWrap(True)
        config.addRow(self.param_note)

        # 兼容旧代码：config_toggle 仍存在但不隐藏参数区
        self.config_toggle = QPushButton('解密参数（始终显示）')
        self.config_toggle.setCheckable(True)
        self.config_toggle.setChecked(True)
        self.config_toggle.setProperty('compactAction', True)
        self.config_toggle.hide()  # 不再作为折叠入口

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
        self.payload_cipher.setPlaceholderText('粘贴 Base64 密文（网关正文）')
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
        self.more_btn = QToolButton()
        self.more_btn.setObjectName('responsive-more-btn')
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_menu = QMenu(self.more_btn)
        self.more_btn.setMenu(self._more_menu)
        self.more_btn.hide()
        actions.addWidget(self.more_btn)
        self._secondary_btns = [self.to_iface_btn, self.clear_btn, self.copy_btn, self.request_btn]
        layout.addWidget(action_bar)

        self.work_tabs = None
        self.xml_workspace = None
        self.to_xml_btn = self.to_format_xml_btn
        self._layout_mode = 'standard'

    def _toggle_key_visibility(self, checked: bool):
        self._key_visible = bool(checked)
        # QPlainTextEdit 无 password 模式；用占位样式提示。完整隐藏用只读遮罩不合适，
        # 这里仅切换 echo 风格的提示文案；实际 Key 仍在控件中，默认可见可编辑。
        zh = self.language == 'zh'
        if checked:
            self.key_cipher.setStyleSheet('')
            self.key_reveal_cb.setText('显示 Key' if zh else 'Show Key')
        else:
            # 视觉弱化（仍可编辑，避免误清空）
            self.key_cipher.setStyleSheet('color: transparent;')
            self.key_reveal_cb.setText('显示 Key' if zh else 'Show Key')

    def _refresh_param_visibility(self):
        """根据算法动态说明参数；当前固定 SM2+SM4-CBC。"""
        zh = self.language == 'zh'
        # 本协议无独立 IV 输入：SM4-CBC 的 IV = 解密后的 SM4 Key
        self.iv_label.setText('IV/Nonce（初始向量）' if zh else 'IV/Nonce')
        self.iv_value.setText(
            '当前算法不需要单独录入 IV：兼容 gatewayDecode.html，SM4-CBC 使用解出的 SM4 Key 同时作为 Key 与 IV。'
            if zh else
            'No separate IV field: SM4-CBC uses the decrypted SM4 key as both key and IV.'
        )
        self.param_note.setText(
            '算法固定为国密 SM2 解 Key + SM4-CBC 解正文；Padding 与 Mode 由协议固定，无需手工切换。密钥仅本机内存使用，不落盘。'
            if zh else
            'Fixed SM2+SM4-CBC protocol. Keys stay in memory only.'
        )
        # 始终显示参数组
        self.config_group.show()

    def apply_layout_mode(self, mode, low_height=False):
        self._layout_mode = mode
        from ui.responsive import apply_splitter_orientation, set_subtitle_visible
        set_subtitle_visible(getattr(self, 'page_subtitle', None), low_height)
        apply_splitter_orientation(self.splitter, mode, min_editor=180)
        # 窄屏：参数区在报文上方（已在 VBox 中）；宽屏保持并列报文区
        self.config_group.show()
        self.key_cipher.setMinimumHeight(64 if mode in ('compact', 'narrow') else 72)
        self._more_menu.clear()
        zh = self.language == 'zh'
        self.more_btn.setText('更多' if zh else 'More')
        if mode in ('compact', 'narrow'):
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
        # 兼容旧调用：始终显示，忽略折叠
        self.config_group.show()
        self.config_toggle.setChecked(True)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('加解密' if zh else 'Crypto')
        self.page_subtitle.setText(
            '本地处理，内容不保存' if zh else 'Processed locally · nothing is saved'
        )
        self.offline_pill.setText('● 本地' if zh else '● Local')
        self.offline_pill.setObjectName('dashboard-local-status')
        self.config_group.setTitle('解密参数' if zh else 'Decrypt parameters')
        self.system_label.setText('系统' if zh else 'System')
        self.system_value.setText(
            '新车险系统（固定兼容模式）' if zh else 'New Auto Insurance (fixed compatibility mode)'
        )
        self.algo_label.setText('算法' if zh else 'Algorithm')
        self.algo_value.setText('SM2 + SM4')
        self.mode_label.setText('模式' if zh else 'Mode')
        self.mode_value.setText('CBC')
        self.padding_label.setText('Padding（填充）' if zh else 'Padding')
        self.padding_value.setText('PKCS#7 / 分组填充' if zh else 'PKCS#7 block padding')
        self.encoding_label.setText('编码' if zh else 'Encoding')
        self.encoding_value.setText(
            'Key=Hex · 正文=Base64 · 明文=UTF-8' if zh else
            'Key=Hex · Payload=Base64 · Plain=UTF-8'
        )
        self.environment_label.setText('环境' if zh else 'Environment')
        env_names = ['集成环境', '用户环境', '生产环境'] if zh else ['Integration', 'User / UAT', 'Production']
        for index, name in enumerate(env_names):
            self.environment.setItemText(index, name)
        self.key_label.setText('Key（密钥）' if zh else 'Key')
        self.key_hint.setText(
            '请在此录入 SM2 加密后的 SM4 Key 密文（十六进制）。示例格式：a1b2c3…（请勿把真实密钥写入示例或日志）'
            if zh else
            'Paste SM2-encrypted SM4 key ciphertext (hex). Do not log real keys.'
        )
        self.key_reveal_cb.setText('显示 Key' if zh else 'Show Key')
        self.cipher_label.setText('输入报文' if zh else 'Ciphertext')
        self.cipher_label.setToolTip(
            '网关正文密文（Base64）' if zh else 'Gateway payload ciphertext (Base64)'
        )
        self.plain_label.setText('解密结果' if zh else 'Result')
        self.to_format_xml_btn.setText('在格式工具中打开 XML' if zh else 'Open XML in Format tools')
        self.to_format_xml_btn.setToolTip(
            '跳转到格式工具 · XML Tab' if zh else 'Jump to Format tools · XML tab'
        )
        self.payload_cipher.setPlaceholderText(
            '粘贴 Base64 密文（网关正文）' if zh else 'Paste Base64 ciphertext'
        )
        self.key_cipher.setPlaceholderText(
            '粘贴 SM2 加密后的 SM4 Key（十六进制 Hex）' if zh else
            'Paste SM2-encrypted SM4 key (hex)'
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
        self._refresh_param_visibility()
        self._toggle_key_visibility(self.key_reveal_cb.isChecked())

    def set_cipher_text(self, text: str):
        """从接口排查等模块带入密文/正文，不自动解密，不覆盖 Key/IV/环境。"""
        self.payload_cipher.setPlainText(text or '')
        # 若 Key 为空，给出轻提示（不弹阻塞框）
        if not (self.key_cipher.toPlainText() or '').strip():
            zh = self.language == 'zh'
            try:
                self.json_viewer.json_status.setText(
                    '已填入报文 · 请在上方录入 Key（密钥）后解密' if zh else
                    'Payload filled · enter Key above, then decrypt'
                )
            except Exception:
                pass

    def _decrypt(self, direction):
        # 解密失败不得清空 Key / 环境 / 报文
        key_before = self.key_cipher.toPlainText()
        payload_before = self.payload_cipher.toPlainText()
        env_before = self.environment.currentIndex()
        try:
            plain = decrypt_gateway_payload(
                direction,
                self.environment.currentIndex() + 1,
                key_before,
                payload_before,
            )
            self.json_viewer.set_text(plain, auto_format=True)
            if _looks_like_xml(plain):
                self._offer_xml_view(plain)
            else:
                self.to_format_xml_btn.hide()
        except Exception as exc:
            # 仅清空结果区，保留用户已录入的 Key / IV / 环境 / 报文
            self.json_viewer.clear()
            self.to_format_xml_btn.hide()
            if self.key_cipher.toPlainText() != key_before:
                self.key_cipher.setPlainText(key_before)
            if self.payload_cipher.toPlainText() != payload_before:
                self.payload_cipher.setPlainText(payload_before)
            if self.environment.currentIndex() != env_before:
                self.environment.setCurrentIndex(env_before)
            msg = str(exc) if str(exc) else '解密失败，请检查 Key 与正文是否配套'
            show_warning(self, '网关解密' if self.language == 'zh' else 'Gateway decrypt', msg)

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
        # 清空全部用户输入（用户主动）
        self.key_cipher.clear()
        self.payload_cipher.clear()
        self.json_viewer.clear()
        self.to_format_xml_btn.hide()
