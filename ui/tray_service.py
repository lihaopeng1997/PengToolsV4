# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


class TrayService:
    def __init__(self, main_window):
        self._main_window = main_window
        self._language = 'zh'
        app_icon = QApplication.windowIcon()
        self._tray = QSystemTrayIcon(app_icon if not app_icon.isNull() else self._create_icon(), QApplication.instance())
        self._tray.setToolTip('PengTools 工作台')
        self.set_language('zh')
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _create_icon(self):
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(0, 212, 255))
        painter.setPen(QPen(QColor(0, 180, 220), 2))
        painter.drawEllipse(4, 4, 56, 56)
        painter.setPen(QColor(10, 14, 26))
        painter.setFont(QFont('Microsoft YaHei', 24, QFont.Weight.Bold))
        painter.drawText(16, 43, 'P')
        painter.end()
        return QIcon(pixmap)

    def _create_menu(self):
        menu = QMenu()
        zh = self._language == 'zh'
        menu.addAction(QAction('打开 PengTools' if zh else 'Open PengTools', menu, triggered=self.show_window))
        menu.addAction(QAction('展开悬浮工具栏' if zh else 'Expand floating toolbar', menu, triggered=self._main_window.toggle_quick_panel))
        menu.addSeparator()
        menu.addAction(QAction('退出' if zh else 'Quit', menu, triggered=self.quit_app))
        return menu

    def set_language(self, language):
        self._language = language
        self._tray.setToolTip('PengTools 工作台' if language == 'zh' else 'PengTools Workspace')
        self._tray.setContextMenu(self._create_menu())

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            self.show_window()

    def show_window(self):
        self._main_window.showNormal()
        self._main_window.raise_()
        self._main_window.activateWindow()

    def quit_app(self):
        self._main_window.exit_application()

    def hide(self):
        self._tray.hide()

    def show_notification(self, title, message):
        self._tray.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.Information, 2500
        )
