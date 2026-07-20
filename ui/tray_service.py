# -*- coding: utf-8 -*-
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ui.icons import brand_file, brand_pixmap, brand_window_icon


class TrayService:
    def __init__(self, main_window):
        self._main_window = main_window
        self._language = 'zh'
        self._tray = QSystemTrayIcon(self._create_icon(), QApplication.instance())
        self._tray.setToolTip('PengTools 工作台')
        self.set_language('zh')
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _theme_tint(self) -> str:
        try:
            from ui.theme_manager import ThemeManager
            return ThemeManager.instance().token('TEXT_STRONG')
        except Exception:
            return '#1E2A42'

    def _create_icon(self) -> QIcon:
        """品牌托盘 SVG 主题染色；失败则回退 app ICO，不再绘制蓝色圆 P。"""
        tint = self._theme_tint()
        for size in (20, 16, 24, 32):
            pix = brand_pixmap('tray', size=size, tint=tint)
            if not pix.isNull():
                icon = QIcon()
                icon.addPixmap(pix)
                # 多尺寸
                for extra in (16, 20, 24, 32):
                    if extra == size:
                        continue
                    extra_pix = brand_pixmap('tray', size=extra, tint=tint)
                    if not extra_pix.isNull():
                        icon.addPixmap(extra_pix)
                return icon
        # SVG 失败：ICO 回退
        ico_path = brand_file('app')
        if ico_path:
            return QIcon(ico_path)
        return brand_window_icon()

    def refresh_icon(self):
        """主题切换后刷新托盘染色。"""
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
