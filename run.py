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
    # V2 Web 壳：QWebEngine 必须在 QApplication 创建前初始化（不可用时自动跳过）
    try:
        from ui import web_shell as _web_shell
        from ui.web_diagnostics import log_web_event
        _sandbox_before = os.environ.get('QTWEBENGINE_DISABLE_SANDBOX')
        log_web_event('startup_env', app_frozen=getattr(sys, 'frozen', False),
                      executable=sys.executable,
                      meipass_present=getattr(sys, '_MEIPASS', '') != '',
                      webengine_available=_web_shell.WEB_SHELL_AVAILABLE,
                      sandbox_env_present=_sandbox_before is not None,
                      sandbox_env_value=(_sandbox_before or 'empty')[:12],
                      chromium_flags_has_no_sandbox=(
                          '--no-sandbox' in (os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS') or '')),
                      app_forces_sandbox_disabled=False)
        if _web_shell.WEB_SHELL_AVAILABLE:
            # onedir 发布模式下不由应用主动关闭 Chromium sandbox；
            # 如宿主环境自行设置 QTWEBENGINE_DISABLE_SANDBOX，仅在 startup_env 中记录诊断，不在应用内覆盖。
            from PyQt6.QtWebEngineQuick import QtWebEngineQuick
            log_web_event('webengine_initialize_start')
            QtWebEngineQuick.initialize()
            log_web_event('webengine_initialize_success')
    except Exception as exc:
        try:
            from ui.web_diagnostics import log_web_event as _log
            _log('webengine_initialize_failure', error_type=type(exc).__name__, error=str(exc))
        except Exception:
            pass
        try:
            _web_shell.mark_webengine_runtime_failed()
            _log('webengine_runtime_unavailable')
        except Exception:
            pass
        # WebEngine 初始化失败不阻断启动：main_window 经 runtime_web_shell_available() 走经典 QWidget UI
    # Qt6 默认启用并统一处理高 DPI 缩放；不要再注入 Qt5 时代环境变量，
    # 以避免多屏切换和系统缩放策略发生冲突。
    if sys.platform == 'win32':
        # AppUserModelID 区分 Private/标准，避免任务栏合并误判
        try:
            from config import APP_EDITION, APP_VERSION
            aumid = f'PengTools.Hub.{APP_EDITION}.{APP_VERSION}'
        except Exception:
            aumid = 'PengTools.Hub.Private.4.27'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)

    from config import APP_NAME, APP_VERSION
    app = QApplication(sys.argv)
    try:
        from PyQt6.QtWidgets import QStyleFactory
        fusion = QStyleFactory.create('Fusion')
        if fusion is not None:
            app.setStyle(fusion)
    except Exception:
        pass
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(str(APP_VERSION))
    app.setOrganizationName(APP_NAME)
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
        # SECONDARY：activate 已送达既有实例；或守卫无法证明唯一性（fail closed）。
        # 两种情况都不得继续创建 Splash / MainWindow / 托盘服务。
        if guard.last_error:
            print(f'[single-instance] {guard.last_error}', file=sys.stderr, flush=True)
        return 0

    from config import load_settings
    from ui.theme_manager import ThemeManager, DEFAULT_THEME_ID
    from ui.startup_splash import StartupSplash

    # 启动闪屏：慢启动延迟展示统一品牌反馈，快启动静默跳过
    splash = StartupSplash(app)
    splash.show_status('正在加载主题…')

    settings = load_settings()
    theme_id = settings.get('ui_theme', DEFAULT_THEME_ID)
    font_size = settings.get('font_size', 12)
    try:
        ThemeManager.instance().apply(app, theme_id, font_size=font_size)
    except Exception:
        ThemeManager.instance().apply(app, DEFAULT_THEME_ID, font_size=font_size)

    # 尽早清理：上次抓包强杀/崩溃残留的系统代理，避免其它接口全挂
    splash.show_status('正在检查系统代理…')
    try:
        from tools.ie_proxy import ensure_system_proxy_safe
        ensure_system_proxy_safe(reason='app_startup')
    except Exception:
        pass

    splash.show_status('正在打开主窗口…')
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
    splash.finish(window)
    app.processEvents()

    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
