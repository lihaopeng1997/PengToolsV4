# -*- coding: utf-8 -*-
"""统一本地化 QDialogButtonBox 标准按钮文案。"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialogButtonBox


_ZH_LABELS = {
    QDialogButtonBox.StandardButton.Ok: '确定',
    QDialogButtonBox.StandardButton.Cancel: '取消',
    QDialogButtonBox.StandardButton.Close: '关闭',
    QDialogButtonBox.StandardButton.Save: '保存',
    QDialogButtonBox.StandardButton.Open: '打开',
    QDialogButtonBox.StandardButton.Yes: '是',
    QDialogButtonBox.StandardButton.No: '否',
    QDialogButtonBox.StandardButton.Apply: '应用',
    QDialogButtonBox.StandardButton.Reset: '重置',
    QDialogButtonBox.StandardButton.Discard: '放弃',
    QDialogButtonBox.StandardButton.Help: '帮助',
    QDialogButtonBox.StandardButton.Retry: '重试',
    QDialogButtonBox.StandardButton.Ignore: '忽略',
    QDialogButtonBox.StandardButton.Abort: '中止',
}

_EN_LABELS = {
    QDialogButtonBox.StandardButton.Ok: 'OK',
    QDialogButtonBox.StandardButton.Cancel: 'Cancel',
    QDialogButtonBox.StandardButton.Close: 'Close',
    QDialogButtonBox.StandardButton.Save: 'Save',
    QDialogButtonBox.StandardButton.Open: 'Open',
    QDialogButtonBox.StandardButton.Yes: 'Yes',
    QDialogButtonBox.StandardButton.No: 'No',
    QDialogButtonBox.StandardButton.Apply: 'Apply',
    QDialogButtonBox.StandardButton.Reset: 'Reset',
    QDialogButtonBox.StandardButton.Discard: 'Discard',
    QDialogButtonBox.StandardButton.Help: 'Help',
    QDialogButtonBox.StandardButton.Retry: 'Retry',
    QDialogButtonBox.StandardButton.Ignore: 'Ignore',
    QDialogButtonBox.StandardButton.Abort: 'Abort',
}


def localize_button_box(box: QDialogButtonBox, language: str = 'zh', **overrides) -> QDialogButtonBox:
    """把标准按钮改成中/英文；overrides 可覆盖个别按钮（如 Save→保存需求）。"""
    labels = dict(_ZH_LABELS if language == 'zh' else _EN_LABELS)
    for key, value in overrides.items():
        if isinstance(key, str):
            key = getattr(QDialogButtonBox.StandardButton, key, None)
        if key is not None and value:
            labels[key] = value
    for standard, text in labels.items():
        button = box.button(standard)
        if button is not None:
            button.setText(text)
    return box
