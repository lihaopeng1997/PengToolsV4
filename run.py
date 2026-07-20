# -*- coding: utf-8 -*-
import sys
import os
import ctypes
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QIcon

# Add app directory to path
app_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, app_dir)


def resource_path(*parts):
    base = getattr(sys, '_MEIPASS', app_dir)
    return os.path.join(base, *parts)


def load_stylesheet(app_path):
    """仅读取 QSS 模板；主题色由 ThemeManager 注入。"""
    from ui.theme_manager import ThemeManager
    manager = ThemeManager.instance()
    return manager.load_template(app_path)


def main():
    os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '1'
    os.environ['QT_AUTO_SCREEN_SCALE_FACTOR'] = '1'

    if sys.platform == 'win32':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('PengTools.Hub.Private.4.27')

    app = QApplication(sys.argv)
    app.setApplicationName('PengTools Hub')
    app.setApplicationVersion('4.27')
    app.setOrganizationName('PengTools')
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont('Microsoft YaHei UI', 10))
    app.setWindowIcon(QIcon(resource_path('resources', 'app.ico')))

    from config import load_settings
    from ui.theme_manager import ThemeManager, DEFAULT_THEME_ID

    settings = load_settings()
    theme_id = settings.get('ui_theme', DEFAULT_THEME_ID)
    font_size = settings.get('font_size', 12)
    try:
        ThemeManager.instance().apply(app, theme_id, font_size=font_size)
    except Exception:
        ThemeManager.instance().apply(app, DEFAULT_THEME_ID, font_size=font_size)

    from main_window import MainWindow
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
