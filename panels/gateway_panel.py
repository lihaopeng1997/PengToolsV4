# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from tools.gateway_crypto import decrypt_gateway_payload
from ui.field_metrics import size_combo
from ui.json_viewer import JsonViewer


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

        self.config_group = QGroupBox()
        config = QFormLayout(self.config_group)
        self.system_value = QLabel('新车险系统（固定兼容模式）')
        self.system_value.setObjectName('path-note')
        self.system_label = QLabel()
        config.addRow(self.system_label, self.system_value)
        self.environment = QComboBox()
        size_combo(self.environment, 'sm')
        self.environment.addItems(['集成环境', '用户环境', '生产环境'])
        self.environment_label = QLabel()
        config.addRow(self.environment_label, self.environment)
        self.key_cipher = QPlainTextEdit()
        self.key_cipher.setMaximumHeight(92)
        self.key_cipher.setPlaceholderText('SM2 encrypted SM4 key (hex)')
        self.key_label = QLabel()
        config.addRow(self.key_label, self.key_cipher)
        layout.addWidget(self.config_group)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.cipher_label = QLabel()
        self.cipher_label.setObjectName('section-title')
        left_layout.addWidget(self.cipher_label)
        self.payload_cipher = QPlainTextEdit()
        self.payload_cipher.setPlaceholderText('Base64 ciphertext')
        left_layout.addWidget(self.payload_cipher)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.plain_label = QLabel()
        self.plain_label.setObjectName('section-title')
        right_layout.addWidget(self.plain_label)
        self.json_viewer = JsonViewer(self.language)
        # 保留 plain_text 别名，兼容原有调用与自动化检查。
        self.plain_text = self.json_viewer.text_edit
        right_layout.addWidget(self.json_viewer)
        splitter.addWidget(right)
        splitter.setSizes([520, 520])
        layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        self.note = QLabel()
        self.note.setObjectName('field-hint')
        self.note.setWordWrap(True)
        actions.addWidget(self.note, 1)
        self.clear_btn = QPushButton()
        self.clear_btn.clicked.connect(self._clear)
        actions.addWidget(self.clear_btn)
        self.copy_btn = QPushButton()
        self.copy_btn.clicked.connect(self._copy)
        actions.addWidget(self.copy_btn)
        self.request_btn = QPushButton()
        self.request_btn.clicked.connect(lambda: self._decrypt('request'))
        actions.addWidget(self.request_btn)
        self.response_btn = QPushButton()
        self.response_btn.setObjectName('primary-btn')
        self.response_btn.clicked.connect(lambda: self._decrypt('response'))
        actions.addWidget(self.response_btn)
        layout.addLayout(actions)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.config_group.setTitle('网关国密参数' if zh else 'Gateway cryptographic parameters')
        self.system_label.setText('系统' if zh else 'System')
        self.system_value.setText('新车险系统（固定兼容模式）' if zh else 'New Auto Insurance (fixed compatibility mode)')
        self.environment_label.setText('环境' if zh else 'Environment')
        env_names = ['集成环境', '用户环境', '生产环境'] if zh else ['Integration', 'User / UAT', 'Production']
        for index, name in enumerate(env_names):
            self.environment.setItemText(index, name)
        self.key_label.setText('SM4 Key 密文' if zh else 'Encrypted SM4 key')
        self.cipher_label.setText('网关正文密文（Base64）' if zh else 'Gateway payload ciphertext (Base64)')
        self.plain_label.setText('解密明文' if zh else 'Decrypted plaintext')
        self.note.setText(
            '兼容 gatewayDecode.html：SM2 解 Key，再以 Key 同时作为 SM4-CBC 的 Key 与 IV。所有数据仅在本机处理。'
            if zh else 'Compatible with gatewayDecode.html: SM2 decrypts the key, then that key is used as both SM4-CBC key and IV. Local processing only.'
        )
        self.clear_btn.setText('清空' if zh else 'Clear')
        self.copy_btn.setText('复制明文' if zh else 'Copy plaintext')
        self.request_btn.setText('请求解密' if zh else 'Decrypt request')
        self.response_btn.setText('响应解密' if zh else 'Decrypt response')
        self.json_viewer.set_language(language)

    def _decrypt(self, direction):
        try:
            plain = decrypt_gateway_payload(
                direction,
                self.environment.currentIndex() + 1,
                self.key_cipher.toPlainText(),
                self.payload_cipher.toPlainText(),
            )
            self.json_viewer.set_text(plain, auto_format=True)
        except ValueError as exc:
            self.json_viewer.clear()
            QMessageBox.warning(self, 'PengTools', str(exc))

    def _copy(self):
        text = self.json_viewer.plain_text()
        if text:
            QApplication.clipboard().setText(text)

    def _clear(self):
        self.key_cipher.clear()
        self.payload_cipher.clear()
        self.json_viewer.clear()
