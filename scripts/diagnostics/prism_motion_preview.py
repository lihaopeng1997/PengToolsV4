"""Capture actual native Qt animation frames without launching business workflows."""
from io import BytesIO
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    from PIL import Image
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QFontDatabase
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QLabel, QWidget
    from ui.theme_manager import ThemeManager
    from ui.motion import set_motion_enabled_for_test
    from ui.aurora_progress import AuroraProgress
    from ui.thinking_indicator import ThinkingIndicator
    from panels.dashboard_panel import PrismOrbWidget

    app = QApplication([])
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    ThemeManager.instance().apply(app, 'calm')
    set_motion_enabled_for_test(True)
    host = QWidget()
    host.resize(640, 320)
    title = QLabel('晴空棱镜 · 原生动效', host)
    title.setGeometry(24, 24, 260, 32)
    orb = PrismOrbWidget(host)
    orb.move(52, 120)
    caption = QLabel('首页棱镜：轻浮动与微旋转', host)
    caption.setGeometry(24, 260, 270, 28)
    thinking = ThinkingIndicator(host, '正在等待回复…')
    thinking.setGeometry(300, 170, 300, 28)
    progress = AuroraProgress(host, delay_show_ms=0)
    host.show()
    thinking.start()
    progress.start_busy('示例 · 正在加载', immediate=True)
    app.processEvents()

    def png(widget):
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        widget.grab().save(buffer, 'PNG')
        return bytes(buffer.data())

    frames = []
    distinct = {name: set() for name in ('orb', 'loading', 'thinking')}
    for _ in range(60):
        QTest.qWait(80)
        frames.append(Image.open(BytesIO(png(host))).convert('RGB'))
        for name, widget in (('orb', orb), ('loading', progress), ('thinking', thinking)):
            distinct[name].add(png(widget))
    assert all(len(values) > 1 for values in distinct.values()), 'An animation did not advance'
    host.hide()
    QTest.qWait(100)
    assert orb._anim.state() == orb._anim.State.Paused
    assert not progress._anim_timer.isActive()
    assert not thinking._timer.isActive()
    set_motion_enabled_for_test(False)
    host.show()
    app.processEvents()
    widgets = {'orb': orb, 'loading': progress, 'thinking': thinking}
    still = {name: png(widget) for name, widget in widgets.items()}
    QTest.qWait(180)
    assert all(png(widget) == still[name] for name, widget in widgets.items()), 'Reduced motion changed frames'
    output = ROOT / 'docs/ui/prism-implementation-2026-09/native-motion.gif'
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, save_all=True, append_images=frames[1:], duration=80, loop=0)
    print(json.dumps({'image': str(output), 'frames': len(frames),
                      'distinct_frames': {name: len(values) for name, values in distinct.items()},
                      'hidden_animations_paused': True, 'reduced_motion_static': True}))
    progress.hide_now()
    thinking.stop()
    host.close()
    set_motion_enabled_for_test(None)


if __name__ == '__main__':
    main()
