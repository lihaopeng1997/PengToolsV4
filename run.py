# -*- coding: utf-8 -*-
import sys
import os
import datetime
import ctypes
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon

# Add app directory to path
app_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, app_dir)


def resource_path(*parts):
    base = getattr(sys, '_MEIPASS', app_dir)
    return os.path.join(base, *parts)

def load_stylesheet(app_path):
    qss_path = os.path.join(app_path, 'resources', 'style.qss')
    if not os.path.exists(qss_path):
        qss_path = os.path.join(os.path.dirname(sys.executable), 'resources', 'style.qss')
    if os.path.exists(qss_path):
        with open(qss_path, 'r', encoding='utf-8') as f:
            resource_dir = os.path.dirname(qss_path)
            arrow_path = os.path.join(resource_dir, 'chevron_down.svg').replace('\\', '/')
            check_path = os.path.join(resource_dir, 'check_white.svg').replace('\\', '/')
            return (
                f.read()
                .replace('__DROPDOWN_ARROW__', arrow_path)
                .replace('__CHECKMARK__', check_path)
            )
    return ''


def main():
    # High DPI scaling (auto in PyQt6.5+)
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

    # Load stylesheet
    qss = load_stylesheet(app_dir)
    if qss:
        app.setProperty('base_stylesheet', qss)
        app.setStyleSheet(qss)

    from main_window import MainWindow
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
