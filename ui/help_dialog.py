# -*- coding: utf-8 -*-
"""内置使用说明：离线 HTML（QTextBrowser），不引入浏览器内核。"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout,
)

from ui.design_system import apply_button


def help_html_path() -> str:
    """打包态 _MEIPASS/resources/help；开发态仓库 resources/help。"""
    candidates = []
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        candidates.append(os.path.join(meipass, 'resources', 'help', 'user_guide.html'))
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(here, 'resources', 'help', 'user_guide.html'))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return candidates[-1] if candidates else ''


class UserGuideDialog(QDialog):
    """全屏友好的使用说明窗口。"""

    def __init__(self, language: str = 'zh', parent=None):
        super().__init__(parent)
        self.language = language
        zh = language == 'zh'
        self.setObjectName('user-guide-dialog')
        self.setWindowTitle('PengToolsHub 使用说明' if zh else 'PengToolsHub User Guide')
        self.setMinimumSize(880, 620)
        self.resize(960, 720)
        self.setModal(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel('使用说明' if zh else 'User Guide')
        title.setObjectName('confirm-title')
        head.addWidget(title, 1)
        self.open_external_btn = QPushButton('用系统浏览器打开' if zh else 'Open in browser')
        apply_button(self.open_external_btn, 'secondary', compact=True, icon='external-open', icon_size=16)
        self.open_external_btn.clicked.connect(self._open_external)
        head.addWidget(self.open_external_btn)
        root.addLayout(head)

        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(True)
        self.browser.setObjectName('user-guide-browser')
        root.addWidget(self.browser, 1)

        foot = QHBoxLayout()
        tip = QLabel(
            '说明内置在软件中，无需联网。' if zh else
            'Built-in guide · offline · no network.'
        )
        tip.setObjectName('field-hint')
        foot.addWidget(tip, 1)
        self.close_btn = QPushButton('关闭' if zh else 'Close')
        apply_button(self.close_btn, 'primary', compact=False)
        self.close_btn.clicked.connect(self.accept)
        foot.addWidget(self.close_btn)
        root.addLayout(foot)

        self._load_html()

    def _load_html(self):
        path = help_html_path()
        if path and os.path.isfile(path):
            # setSource 支持锚点与相对资源；本地 file://
            self.browser.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
            return
        zh = self.language == 'zh'
        self.browser.setHtml(
            f'<p>{"未找到使用说明文件。" if zh else "User guide file not found."}</p>'
            f'<p><code>{path or "(empty)"}</code></p>'
        )

    def _open_external(self):
        path = help_html_path()
        if path and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))


def show_user_guide(parent=None, language: str = 'zh'):
    dlg = UserGuideDialog(language=language, parent=parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
