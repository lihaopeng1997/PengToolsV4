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


def dump(tag, buttons):
    print(tag, [(b.text(), b.height(), b.minimumHeight(), b.maximumHeight()) for b in buttons])


buttons = []
for text in ('打开目录', '更新', '提交'):
    button = QPushButton(text)
    if text == '提交':
        button.setObjectName('primary-btn')
    size_compact_button(button)
    buttons.append(button)
dump('after size_compact', buttons)

host = QWidget()
row = QHBoxLayout(host)
row.setSpacing(6)
row.setContentsMargins(0, 0, 0, 0)
for button in buttons:
    row.addWidget(button)
row.addStretch(1)
dump('after add to host layout', buttons)
print('host hint', host.sizeHint())

card = QFrame()
card.setObjectName('detail-action-card')
outer = QVBoxLayout(card)
outer.setContentsMargins(8, 6, 8, 6)
scroll = QScrollArea()
scroll.setWidgetResizable(False)
scroll.setWidget(host)
scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
outer.addWidget(scroll)
dump('after scroll parent', buttons)
print('host size/hint', host.size(), host.sizeHint())

# force adjust
host.adjustSize()
dump('after host.adjustSize', buttons)
print('host size/hint', host.size(), host.sizeHint())

# reassert fixed
for button in buttons:
    size_compact_button(button)
host.adjustSize()
dump('after re-size_compact + adjustSize', buttons)
print('host size/hint', host.size(), host.sizeHint())

# ensure host height follows buttons
host.setFixedHeight(28)
dump('after host fixed 28', buttons)
print('host size', host.size())
