# -*- coding: utf-8 -*-
"""让菜单与下拉弹层真正圆角。

QMenu / 下拉列表是独立的顶层窗口，QSS 的 border-radius 只画在窗口里面，四角仍露出
方形底色。这里在它们首次 polish（创建原生窗口之前）时打开透明背景、去掉系统方形阴影，
圆角就由 QSS 完整画出来。
"""

from __future__ import annotations

import atexit

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QApplication, QFrame, QMenu, QWidget

_POPUP_CLASS_NAMES = {'QComboBoxPrivateContainer'}


class RoundedPopupFilter(QObject):
    def eventFilter(self, watched, event):  # noqa: N802 — Qt API
        if event.type() == QEvent.Type.Polish and isinstance(watched, QWidget) and watched.isWindow():
            if isinstance(watched, QMenu) or watched.metaObject().className() in _POPUP_CLASS_NAMES:
                if not watched.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
                    watched.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                    watched.setWindowFlags(
                        watched.windowFlags()
                        | Qt.WindowType.FramelessWindowHint
                        | Qt.WindowType.NoDropShadowWindowHint
                    )
                if isinstance(watched, QFrame):
                    # 下拉容器自带的方框去掉，圆角边框由列表视图的 QSS 画
                    watched.setFrameShape(QFrame.Shape.NoFrame)
                    watched.setLineWidth(0)
        return False


def install_rounded_popups(app: QApplication | None = None) -> None:
    """为整个应用安装一次（重复调用无副作用）。"""
    app = app or QApplication.instance()
    if app is None or getattr(app, '_rounded_popup_filter', None) is not None:
        return
    popup_filter = RoundedPopupFilter(app)
    app.installEventFilter(popup_filter)
    app._rounded_popup_filter = popup_filter

    # 退出时先摘掉过滤器：解释器收尾阶段 Qt 仍会派发事件，不能再回调 Python
    def _remove():
        try:
            app.removeEventFilter(popup_filter)
        except RuntimeError:
            pass

    app.aboutToQuit.connect(_remove)
    atexit.register(_remove)
