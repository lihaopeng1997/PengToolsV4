# -*- coding: utf-8 -*-
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ui.icons import brand_tray_icon, brand_window_icon


class TrayService:
    def __init__(self, main_window):
        self._main_window = main_window
        self._language = 'zh'
        self._tray = QSystemTrayIcon(self._create_icon(), QApplication.instance())
        self._tray.setToolTip('PengTools 工作台')
        self.set_language('zh')
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _create_icon(self) -> QIcon:
        """托盘使用高对比图标，不随夜间主题变黑；主题导航图标不受影响。"""
        icon = brand_tray_icon()
        if icon is not None and not icon.isNull():
            return icon
        return brand_window_icon()

    def refresh_icon(self):
        """主题切换后仍保持高对比托盘图标（任务栏可读性优先）。"""
        self._tray.setIcon(self._create_icon())

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

    # 兼容旧调用名
    def show_message(self, title, message):
        self.show_notification(title, message)
