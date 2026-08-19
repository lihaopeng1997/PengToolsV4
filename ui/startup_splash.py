# -*- coding: utf-8 -*-
"""启动闪屏：尽早出画面，主窗口就绪后 finish。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen


def _brand_pixmap(size: int = 96) -> QPixmap:
    try:
        from ui.icons import brand_pixmap
        pix = brand_pixmap(size)
        if pix is not None and not pix.isNull():
            return pix
    except Exception:
        pass
    # 退化：纯色块
    pix = QPixmap(size, size)
    pix.fill(QColor('#4058C8'))
    return pix


class StartupSplash(QSplashScreen):
    """轻量闪屏：不依赖业务面板，创建后即可 show。"""

    def __init__(self, app: QApplication | None = None):
        base = QPixmap(420, 240)
        base.fill(QColor('#F5F7FB'))
        super().__init__(base, Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._message = '正在启动…'
        self._title = 'PengToolsHub'
        try:
            from config import APP_NAME, app_version_text
            self._title = f'{APP_NAME}  {app_version_text()}'
        except Exception:
            pass
        self._logo = _brand_pixmap(72)
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.center() - self.rect().center())

    def drawContents(self, painter: QPainter):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor('#F5F7FB'))
        # 品牌图
        logo_x = (self.width() - self._logo.width()) // 2
        painter.drawPixmap(logo_x, 36, self._logo)
        # 标题
        painter.setPen(QColor('#1E2A44'))
        title_font = QFont('Microsoft YaHei UI', 14)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(
            0, 120, self.width(), 32,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            self._title,
        )
        # 状态
        painter.setPen(QColor('#5A6A86'))
        painter.setFont(QFont('Microsoft YaHei UI', 10))
        painter.drawText(
            24, 170, self.width() - 48, 40,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            self._message,
        )

    def show_status(self, text: str):
        self._message = text or '正在启动…'
        self.showMessage('')  # 触发重绘路径
        self.repaint()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
