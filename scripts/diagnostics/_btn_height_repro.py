# -*- coding: utf-8 -*-
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

app = QApplication.instance() or QApplication([])

from tests.test_release_ui import ReleaseUiTests

loader = unittest.TestLoader()
suite = loader.loadTestsFromTestCase(ReleaseUiTests)
cases = []


def collect(s):
    for x in s:
        if isinstance(x, unittest.TestSuite):
            collect(x)
        else:
            cases.append(x)


collect(suite)
idx = next(
    i
    for i, c in enumerate(cases)
    if c._testMethodName
    == 'test_requirement_file_library_keeps_selected_file_actions_and_prioritizes_tree_space'
)
unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, 'w')).run(
    unittest.TestSuite(cases[:idx])
)

from ui.field_metrics import size_compact_button
from ui.icons import apply_icon

EXTRA_QSS = """
QPushButton#primary-btn[compactAction="true"],
QPushButton[compactAction="true"] {
    padding: 3px 10px;
    min-height: 28px;
    max-height: 28px;
}
"""


def trial(name, use_icon=False, margins=(8, 6, 8, 6), spacing=6, reapply=False, extra_qss=False):
    if extra_qss:
        app.setStyleSheet((app.styleSheet() or '') + EXTRA_QSS)
    host = QWidget()
    row = QHBoxLayout(host)
    row.setSpacing(spacing)
    row.setContentsMargins(0, 0, 0, 0)
    buttons = []
    for text in ('打开目录', '更新', '提交'):
        button = QPushButton(text)
        if text == '提交':
            button.setObjectName('primary-btn')
        size_compact_button(button)
        if use_icon and text != '更新':
            apply_icon(button, 'folder-open' if text == '打开目录' else 'export', 16)
        if reapply:
            size_compact_button(button)
        row.addWidget(button)
        buttons.append(button)
    row.addStretch(1)
    card = QFrame()
    card.setObjectName('detail-action-card')
    outer = QVBoxLayout(card)
    outer.setContentsMargins(*margins)
    outer.setSpacing(spacing)
    scroll = QScrollArea()
    scroll.setWidgetResizable(False)
    scroll.setWidget(host)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    outer.addWidget(scroll)
    card.resize(900, 120)
    card.show()
    app.processEvents()
    print(name, [(b.text(), b.height()) for b in buttons], 'hostH', host.height())
    card.hide()
    card.deleteLater()
    app.processEvents()


trial('noicon_newmargins', False, (8, 6, 8, 6), 6)
trial('noicon_oldmargins', False, (6, 4, 6, 4), 4)
trial('icon_new', True, (8, 6, 8, 6), 6)
trial('icon_reapply', True, (8, 6, 8, 6), 6, True)
trial('icon_reapply_qss', True, (8, 6, 8, 6), 6, True, True)
