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


def _resolve_window_icon() -> QIcon:
    """优先高对比任务栏 ICO，其次品牌 ICO，最后旧 app.ico。"""
    candidates = [
        resource_path('resources', 'brand', 'pengtools-taskbar-hc.ico'),
        resource_path('resources', 'brand', 'pengtools-app-v2.ico'),
        resource_path('resources', 'app.ico'),
    ]
    for path in candidates:
        if os.path.exists(path):
            icon = QIcon(path)
            if not icon.isNull():
                return icon
    return QIcon()


def main():
    os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '1'
    os.environ['QT_AUTO_SCREEN_SCALE_FACTOR'] = '1'

    if sys.platform == 'win32':
        # AppUserModelID 区分 Private/标准，避免任务栏合并误判
        try:
            from config import APP_EDITION, APP_VERSION
            aumid = f'PengTools.Hub.{APP_EDITION}.{APP_VERSION}'
        except Exception:
            aumid = 'PengTools.Hub.Private.4.27'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)

    app = QApplication(sys.argv)
    app.setApplicationName('PengTools Hub')
    app.setApplicationVersion('4.27')
    app.setOrganizationName('PengTools')
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont('Microsoft YaHei UI', 10))
    app.setWindowIcon(_resolve_window_icon())

    # 单实例：第二次启动只激活首进程，不建第二套托盘/后台服务
    from ui.single_instance import (
        SingleInstanceGuard,
        local_server_name,
        wire_activate_handler,
    )
    guard = SingleInstanceGuard(server_name=local_server_name(), parent=app)
    if not guard.try_become_primary():
        # 次进程已发送 activate，立即退出
        return 0

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
    window.setWindowIcon(app.windowIcon())
    window._single_instance_guard = guard
    wire_activate_handler(
        guard,
        window,
        message='PengTools 已打开，已为你切换到正在运行的窗口。',
        title='PengTools',
    )
    # 退出时释放本地服务；最小化托盘不释放
    app.aboutToQuit.connect(guard.release)
    window.show()

    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
