# -*- coding: utf-8 -*-
"""内置使用说明：离线 HTML（QTextBrowser），不引入浏览器内核。"""

from __future__ import annotations

import os
import random
import sys

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QTextDocument
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
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
        self.browser.setStyleSheet(
            'QTextBrowser#user-guide-browser {'
            ' background-color: #FFFFFF;'
            ' border: 1px solid #DFD9F0;'
            ' border-radius: 10px;'
            '}'
        )
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


class AboutDialog(QDialog):
    """精致有趣味性的「关于 PengToolsHub」弹窗。"""

    _QUIPS_ZH = [
        '“需求不会凭空消失，但写清楚测试点和发版清单，它至少不会在周五晚上六点找你。”',
        '“查日志的最高境界：还没等业务群 @你，你已经用多机截取和 AI 把堆栈拍桌上了。”',
        '“代码可以重构，发际线不行；先喝口水，记得点保存，顺手把今天日报写了。”',
        '“别担心不会用——每个页面右上角都有「引导」，不会就下一步下一步，主打一个零门槛。”',
        '“离线也能起飞：没有花里胡哨的云端弹窗，数据稳稳躺在本机 data/ 里，安全感拉满。”',
        '“数据库别乱删、生产别硬扛、抓包记得停代理——剩下的脏活累活交给我来干。”',
    ]
    _QUIPS_EN = [
        '"Requirements never vanish, but clean checklists keep Friday 6 PM calm."',
        '"Offline first: your data stays right inside local data/, zero cloud surprises."',
        '"Drink some water, hit Save, and let the interactive Guide walk you through any page."',
        '"Never run rm in prod, always restore proxy — leave the repetitive chores to me."',
    ]

    def __init__(
        self,
        *,
        app_name: str,
        version_text: str,
        build_date: str,
        language: str = 'zh',
        on_open_guide=None,
        parent=None,
    ):
        super().__init__(parent)
        self.language = language
        self._on_open_guide = on_open_guide
        zh = language == 'zh'
        self.setObjectName('confirm-dialog')
        self.setWindowTitle(f'关于 {app_name}' if zh else f'About {app_name}')
        self.setModal(True)
        clamp_dialog_geometry(self, 488, 344, min_width=450, min_height=306)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        # 品牌头区
        hero = QHBoxLayout()
        hero.setSpacing(12)
        icon_lbl = QLabel(self)
        icon_lbl.setFixedSize(48, 48)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(
            'background-color: #EEE9FF; border: 1px solid #DFD9F0; border-radius: 14px;'
        )
        try:
            from ui.icons import brand_pixmap
            from ui.theme_manager import ThemeManager
            pix = brand_pixmap('app_mark', 32, ThemeManager.instance().token('PRIMARY') or '#6C58D9')
            if not pix.isNull():
                icon_lbl.setPixmap(pix)
        except Exception:
            icon_lbl.setText('P')
        hero.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        title_col = QVBoxLayout()
        title_col.setSpacing(3)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_lbl = QLabel(app_name, self)
        name_lbl.setStyleSheet('color: #262438; font-size: 17px; font-weight: 700; background: transparent;')
        name_row.addWidget(name_lbl)
        ver_pill = QLabel(f'{version_text} · Prism Calm', self)
        ver_pill.setStyleSheet(
            'background-color: #EEE9FF; color: #503DBE; border-radius: 8px;'
            ' padding: 2px 8px; font-size: 11px; font-weight: 700;'
        )
        name_row.addWidget(ver_pill)
        name_row.addStretch(1)
        title_col.addLayout(name_row)

        sub_lbl = QLabel(
            'Lihp 亲手打磨的内网离线研发与运维工作台'
            if zh else
            'Offline engineering & ops desktop workbench crafted by Lihp',
            self,
        )
        sub_lbl.setStyleSheet('color: #615D73; font-size: 12px; background: transparent;')
        title_col.addWidget(sub_lbl)
        hero.addLayout(title_col, 1)
        root.addLayout(hero)

        # 每日打工金句卡（可点换一句）
        quip_card = QFrame(self)
        quip_card.setStyleSheet(
            'QFrame { background-color: #F7F5FD; border: 1px solid #DFD9F0; border-radius: 12px; }'
        )
        ql = QVBoxLayout(quip_card)
        ql.setContentsMargins(14, 12, 14, 10)
        ql.setSpacing(6)
        self._quips = list(self._QUIPS_ZH if zh else self._QUIPS_EN)
        self._quip_idx = random.randrange(len(self._quips))
        self.quip_label = QLabel(self._quips[self._quip_idx], quip_card)
        self.quip_label.setWordWrap(True)
        self.quip_label.setStyleSheet('color: #262438; font-size: 13px; line-height: 1.6; border: none; background: transparent;')
        ql.addWidget(self.quip_label)
        quip_foot = QHBoxLayout()
        quip_foot.setContentsMargins(0, 0, 0, 0)
        tag = QLabel('☕ 打工续命语录' if zh else '☕ Daily dev note', quip_card)
        tag.setStyleSheet('color: #6C58D9; font-size: 11px; font-weight: 600; border: none; background: transparent;')
        quip_foot.addWidget(tag)
        quip_foot.addStretch(1)
        self.next_quip_btn = QPushButton('换一句' if zh else 'Another', quip_card)
        apply_button(self.next_quip_btn, 'ghost', compact=True)
        self.next_quip_btn.clicked.connect(self._next_quip)
        quip_foot.addWidget(self.next_quip_btn)
        ql.addLayout(quip_foot)
        root.addWidget(quip_card)

        # 核心元信息（紧凑两行）
        meta_card = QFrame(self)
        meta_card.setStyleSheet(
            'QFrame { background-color: #FFFFFF; border: 1px solid #E8E3F6; border-radius: 10px; }'
        )
        ml = QVBoxLayout(meta_card)
        ml.setContentsMargins(12, 9, 12, 9)
        ml.setSpacing(4)
        meta_line_1 = QLabel(
            f'作者：Lihp    ·    构建日期：{build_date}    ·    运行模式：纯离线本机'
            if zh else
            f'Author: Lihp  ·  Build: {build_date}  ·  Mode: Offline local',
            meta_card,
        )
        meta_line_1.setStyleSheet('color: #3D3952; font-size: 12px; border: none; background: transparent;')
        meta_line_2 = QLabel(
            '涵盖：数据中心 · 智能助手 · 需求发版 · 日志与 AI 排查 · 命令库 · 接口排查 · 测试数据'
            if zh else
            'Includes: Data Center · AI Chat · Requirements · Log & AI Inspect · Commands · API Debug · Test Data',
            meta_card,
        )
        meta_line_2.setWordWrap(True)
        meta_line_2.setStyleSheet('color: #615D73; font-size: 11px; border: none; background: transparent;')
        ml.addWidget(meta_line_1)
        ml.addWidget(meta_line_2)
        root.addWidget(meta_card)

        # 底栏按钮
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.guide_btn = QPushButton('查看使用说明' if zh else 'User Guide', self)
        apply_button(self.guide_btn, 'secondary', compact=True)
        self.guide_btn.clicked.connect(self._open_guide)
        foot.addWidget(self.guide_btn)
        foot.addStretch(1)
        self.ok_btn = QPushButton('笑完继续搬砖' if zh else 'Back to work', self)
        apply_button(self.ok_btn, 'primary', compact=True)
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        foot.addWidget(self.ok_btn)
        root.addLayout(foot)
        self.ok_btn.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        play_dialog_enter(self)

    def _next_quip(self):
        self._quip_idx = (self._quip_idx + 1) % len(self._quips)
        self.quip_label.setText(self._quips[self._quip_idx])

    def _open_guide(self):
        self.accept()
        if callable(self._on_open_guide):
            self._on_open_guide()


def show_about_dialog(
    *,
    app_name: str,
    version_text: str,
    build_date: str,
    language: str = 'zh',
    on_open_guide=None,
    parent=None,
):
    dlg = AboutDialog(
        app_name=app_name,
        version_text=version_text,
        build_date=build_date,
        language=language,
        on_open_guide=on_open_guide,
        parent=parent,
    )
    dlg.exec()
    return dlg
