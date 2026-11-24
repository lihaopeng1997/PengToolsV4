# -*- coding: utf-8 -*-
"""内置使用说明：离线 HTML（QTextBrowser），不引入浏览器内核。"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QTextDocument
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
)

from ui.design_system import apply_button
from ui.dialog_buttons import clamp_dialog_geometry
from ui.field_metrics import size_line
from ui.motion import play_dialog_enter


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
        clamp_dialog_geometry(self, 960, 720, min_width=640, min_height=480)
        self.setModal(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel('使用说明' if zh else 'User Guide')
        title.setObjectName('confirm-title')
        head.addWidget(title)
        head.addSpacing(8)
        self.search_edit = QLineEdit(self)
        size_line(self.search_edit, 'search')
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText(
            '搜关键词：抓包、代理、JVM、SSH、快捷键…' if zh else 'Search guide: proxy, JVM, SSH, shortcuts…'
        )
        self.search_edit.returnPressed.connect(lambda: self._find_in_guide(False))
        head.addWidget(self.search_edit, 1)
        self.find_prev_btn = QPushButton('上一个' if zh else 'Prev')
        apply_button(self.find_prev_btn, 'ghost', compact=True)
        self.find_prev_btn.clicked.connect(lambda: self._find_in_guide(True))
        head.addWidget(self.find_prev_btn)
        self.find_next_btn = QPushButton('下一个' if zh else 'Next')
        apply_button(self.find_next_btn, 'secondary', compact=True)
        self.find_next_btn.clicked.connect(lambda: self._find_in_guide(False))
        head.addWidget(self.find_next_btn)
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

    def showEvent(self, event):
        super().showEvent(event)
        play_dialog_enter(self)

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

    def _find_in_guide(self, backward: bool = False):
        keyword = self.search_edit.text().strip()
        if not keyword:
            return
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if not self.browser.find(keyword, flags):
            # 到达文末/文首时循环回到另一端重搜一次
            cursor = self.browser.textCursor()
            cursor.movePosition(cursor.MoveOperation.End if backward else cursor.MoveOperation.Start)
            self.browser.setTextCursor(cursor)
            self.browser.find(keyword, flags)

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
