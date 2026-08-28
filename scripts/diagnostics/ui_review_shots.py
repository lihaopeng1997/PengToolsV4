# -*- coding: utf-8 -*-
"""离屏截图：主窗口各导航页面实际 UI 效果（仅评估用，不改动任何业务代码）。"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

app_dir = os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(app_dir)
sys.path.insert(0, root)

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtGui import QFont, QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# Headless 容器无中文字体，回退到 Noto Sans SC（系统已有）
_CJK_FONT = r'C:\Windows\Fonts\NotoSansSC-VF.ttf'
if os.path.exists(_CJK_FONT):
    QCoreApplication.setAttribute(__import__('PyQt6.QtCore', fromlist=['Qt']).Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)

OUT_DIR = os.path.join(app_dir, 'shots')
os.makedirs(OUT_DIR, exist_ok=True)

# 页面标签 → 导航 index
PAGES = [
    ('01_工作台', 0),
    ('02_证件类型', 1),
    ('03_升级准备', 2),
    ('04_接口文档更新', 3),
    ('05_VIN', 4),
    ('06_加解密', 5),
    ('07_命令库', 6),
    ('08_设置', 7),
    ('09_日报', 9),
    ('10_需求管理', 10),
    ('11_格式工具', 11),
    ('12_接口排查', 12),
    ('13_日志排查', 13),
    ('14_模型工作台', 14),
]


def main():
    app = QApplication(sys.argv)
    # Headless 容器回退字体：Noto Sans SC（系统已有）
    if os.path.exists(_CJK_FONT):
        fid = QFontDatabase.addApplicationFont(_CJK_FONT)
        if fid >= 0:
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                app.setFont(QFont(fams[0], 10))
    os.environ['PENGTOOLS_SYNC_BOOT'] = '1'
    from main_window import MainWindow  # noqa: E402

    window = MainWindow()
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    for label, nav in PAGES:
        try:
            window._show_panel(nav)
            app.processEvents()
            # 处理懒加载中的后台预热
            for _ in range(8):
                app.processEvents()
            pix = window.grab()
            path = os.path.join(OUT_DIR, f'{label}.png')
            pix.save(path)
            print(f'OK {label} -> {path}')
        except Exception as exc:
            print(f'FAIL {label}: {exc}')

    window.close()
    app.quit()
    return 0


if __name__ == '__main__':
    sys.exit(main())
