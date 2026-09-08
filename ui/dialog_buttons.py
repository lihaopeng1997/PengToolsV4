# -*- coding: utf-8 -*-
"""统一本地化 QDialogButtonBox 标准按钮文案、尺寸与视觉角色。"""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QPushButton, QSizePolicy

DIALOG_BUTTON_H = 30
DIALOG_BUTTON_MIN_W = 60


def clamp_dialog_geometry(
    dialog: QDialog,
    target_width: int,
    target_height: int | None = None,
    *,
    min_width: int | None = None,
    min_height: int | None = None,
    margin: int = 48,
) -> tuple[int, int]:
    """根据屏幕可用几何区域 (availableGeometry) 自适应夹取弹窗尺寸，防止在小屏或多屏环境下被裁切。"""
    screen = None
    if dialog.parent() is not None and hasattr(dialog.parent(), "window"):
        parent_win = dialog.parent().window()
        if hasattr(parent_win, "screen"):
            screen = parent_win.screen()
    if screen is None and hasattr(dialog, "screen"):
        screen = dialog.screen()
    if screen is None:
        screen = QApplication.primaryScreen()

    if screen is not None:
        avail = screen.availableGeometry()
        max_w = max(320, avail.width() - margin)
        max_h = max(200, avail.height() - margin)
    else:
        max_w, max_h = 1920, 1080

    w = min(target_width, max_w)
    if min_width:
        w = max(min(min_width, max_w), w)

    if target_height is not None:
        h = min(target_height, max_h)
        if min_height:
            h = max(min(min_height, max_h), h)
    else:
        h = min(max(dialog.sizeHint().height(), min_height or 0), max_h)

    dialog.setMaximumSize(max_w, max_h)
    if min_width or min_height:
        dialog.setMinimumSize(min(min_width or 0, max_w), min(min_height or 0, max_h))
    dialog.resize(w, h)
    return w, h


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

_BUTTON_ROLES = {
    QDialogButtonBox.StandardButton.Ok: 'primary',
    QDialogButtonBox.StandardButton.Save: 'primary',
    QDialogButtonBox.StandardButton.Open: 'primary',
    QDialogButtonBox.StandardButton.Yes: 'primary',
    QDialogButtonBox.StandardButton.Apply: 'primary',
    QDialogButtonBox.StandardButton.Cancel: 'secondary',
    QDialogButtonBox.StandardButton.Close: 'secondary',
    QDialogButtonBox.StandardButton.No: 'secondary',
    QDialogButtonBox.StandardButton.Reset: 'secondary',
    QDialogButtonBox.StandardButton.Ignore: 'secondary',
    QDialogButtonBox.StandardButton.Retry: 'secondary',
    QDialogButtonBox.StandardButton.Discard: 'danger',
    QDialogButtonBox.StandardButton.Abort: 'danger',
    QDialogButtonBox.StandardButton.Help: 'ghost',
}


def size_dialog_button(
    button: QPushButton,
    role: str = 'secondary',
    *,
    height: int = DIALOG_BUTTON_H,
    min_w: int = DIALOG_BUTTON_MIN_W,
) -> None:
    """设置弹窗操作按钮的标准紧凑尺寸与角色主题。"""
    if button is None:
        return
    from ui.design_system import apply_button
    apply_button(button, role, compact=True)
    button.setFixedHeight(height)
    button.setMinimumWidth(min_w)
    button.setMaximumWidth(16777215)
    button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)


def localize_button_box(box: QDialogButtonBox, language: str = 'zh', **overrides) -> QDialogButtonBox:
    """把标准按钮改成中/英文，并统一应用弹窗紧凑尺寸与视觉角色。overrides 可覆盖个别按钮文字。"""
    if box is None:
        return box
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
            role = _BUTTON_ROLES.get(standard, 'secondary')
            size_dialog_button(button, role)

    for button in box.buttons():
        standard = box.standardButton(button)
        role = _BUTTON_ROLES.get(standard, 'secondary')
        size_dialog_button(button, role)

    return box
