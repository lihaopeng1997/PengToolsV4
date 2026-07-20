# -*- coding: utf-8 -*-
from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget


class QuickPanel(QWidget):
    """桌面悬浮工具栏；圆形按钮是展开与收起状态唯一且不变的屏幕锚点。"""

    COMPACT_SIZE = (60, 60)
    EXPANDED_SIZE = (330, 512)
    BUTTON_SIZE = 44
    BUTTON_MARGIN = 8
    EXPANDED_LEFT_OFFSET = 270

    def __init__(self, main_window, language='zh'):
        super().__init__(None)
        self._main_window = main_window
        self.language = language
        self.expanded = False
        self._drag_offset = None
        self._dragging = False
        self._compact_position = QPoint()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._setup_ui()
        self.set_language(language)
        self._place_initially()

    def _setup_ui(self):
        # 外框和开关按钮使用固定几何位置，不参与动态布局，避免 Windows
        # 在下一帧重新布局后造成屏幕锚点漂移。
        self.shell = QFrame(self)
        self.shell.setObjectName('floating-toolbar')

        self.toggle_btn = QPushButton('P', self)
        self.toggle_btn.setObjectName('floating-toggle')
        self.toggle_btn.clicked.connect(self.toggle_expanded)

        self.tools = QWidget(self)
        tool_layout = QVBoxLayout(self.tools)
        tool_layout.setContentsMargins(4, 0, 4, 4)
        tool_layout.setSpacing(7)
        self.title = QLabel()
        self.title.setObjectName('floating-title')
        tool_layout.addWidget(self.title)
        self.tool_buttons = []
        for index in range(1, 8):
            button = QPushButton()
            button.setObjectName('floating-item')
            button.clicked.connect(lambda checked=False, value=index: self._activate(value))
            tool_layout.addWidget(button)
            self.tool_buttons.append(button)
        self.home_btn = QPushButton()
        self.home_btn.setObjectName('floating-home')
        self.home_btn.clicked.connect(lambda: self._activate(0))
        tool_layout.addWidget(self.home_btn)

        for widget in (self.toggle_btn, self.title, self.shell):
            widget.installEventFilter(self)
        for widget in (self, self.toggle_btn, self.shell, self.title, self.tools):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda position, source=widget: self._show_context_menu(source.mapToGlobal(position))
            )
        self._apply_compact_geometry()

    def set_language(self, language):
        self.language = language
        if language == 'zh':
            self.title.setText('PengTools 快捷工具')
            names = ['证件类型生成', '升级准备', '接口文档更新', '车辆 VIN 生成', '网关国密解密', 'Linux 运维助手', '设置']
            self.home_btn.setText('返回工作台')
            self.toggle_btn.setToolTip('展开/收起快捷工具栏')
        else:
            self.title.setText('PengTools Quick Tools')
            names = ['Documents', 'SQL Processing', 'Interface Docs', 'Vehicle VIN', 'Gateway Decode', 'Linux Operations', 'Settings']
            self.home_btn.setText('Open workspace')
            self.toggle_btn.setToolTip('Expand/collapse toolbar')
        for button, name in zip(self.tool_buttons, names):
            button.setText(name)

    def _place_initially(self):
        screen = QApplication.primaryScreen().availableGeometry()
        width, height = self.COMPACT_SIZE
        x = screen.right() - width - 18
        y = screen.center().y() - height // 2
        self._compact_position = QPoint(x, y)
        self.setGeometry(x, y, width, height)
        self._apply_compact_geometry()

    def reset_position(self):
        if self.expanded:
            self.toggle_expanded()
        self._place_initially()
        self.show()
        self.raise_()

    def apply_preferences(self, opacity=96, always_on_top=True):
        was_visible = self.isVisible()
        position = QPoint(self.pos())
        desired_on_top = bool(always_on_top)
        current_on_top = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if current_on_top != desired_on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, desired_on_top)
            self.move(position)
            if was_visible:
                self.show()
                self.raise_()
        self.set_opacity(opacity)

    def set_opacity(self, opacity):
        opacity_value = max(0.45, min(1.0, float(opacity) / 100.0))
        self.setWindowOpacity(opacity_value)
        if self.isVisible():
            QTimer.singleShot(0, lambda value=opacity_value: self.setWindowOpacity(value))

    def _apply_compact_geometry(self):
        width, height = self.COMPACT_SIZE
        self.shell.setGeometry(0, 0, width, height)
        self.toggle_btn.setGeometry(
            self.BUTTON_MARGIN, self.BUTTON_MARGIN,
            self.BUTTON_SIZE, self.BUTTON_SIZE,
        )
        self.tools.hide()
        self.shell.setProperty('compact', True)
        self.shell.style().unpolish(self.shell)
        self.shell.style().polish(self.shell)

    def _apply_expanded_geometry(self):
        width, height = self.EXPANDED_SIZE
        self.shell.setGeometry(8, 8, width - 16, height - 16)
        self.toggle_btn.setGeometry(
            self.EXPANDED_LEFT_OFFSET + self.BUTTON_MARGIN,
            self.BUTTON_MARGIN,
            self.BUTTON_SIZE,
            self.BUTTON_SIZE,
        )
        self.tools.setGeometry(18, 64, width - 36, height - 82)
        self.tools.show()
        self.tools.raise_()
        self.toggle_btn.raise_()
        self.shell.setProperty('compact', False)
        self.shell.style().unpolish(self.shell)
        self.shell.style().polish(self.shell)

    def _button_global_top_left(self):
        return self.toggle_btn.mapToGlobal(QPoint(0, 0))

    def toggle_expanded(self):
        if not self.expanded:
            # compact_position 始终表示收起窗口左上角；圆钮屏幕位置为它 + (8, 8)。
            self._compact_position = QPoint(self.pos())
            self.expanded = True
            width, height = self.EXPANDED_SIZE
            self.setGeometry(
                self._compact_position.x() - self.EXPANDED_LEFT_OFFSET,
                self._compact_position.y(), width, height,
            )
            self._apply_expanded_geometry()
            self.toggle_btn.setText('×')
        else:
            self.expanded = False
            width, height = self.COMPACT_SIZE
            self.setGeometry(
                self._compact_position.x(), self._compact_position.y(), width, height,
            )
            self._apply_compact_geometry()
            self.toggle_btn.setText('P')
        self.show()
        self.raise_()

    def show_panel(self):
        if not self.expanded:
            self.toggle_expanded()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def hide_panel(self):
        if self.expanded:
            self.toggle_expanded()

    def _activate(self, index):
        self._main_window.showNormal()
        self._main_window.raise_()
        self._main_window.activateWindow()
        self._main_window.navigate_to(index)
        self.hide_panel()

    def _show_context_menu(self, global_position):
        menu = QMenu()
        close_action = QAction('关闭悬浮栏' if self.language == 'zh' else 'Close floating toolbar', menu)
        close_action.triggered.connect(self.close_toolbar)
        menu.addAction(close_action)
        menu.exec(global_position)

    def close_toolbar(self):
        if self.expanded:
            self.expanded = False
            width, height = self.COMPACT_SIZE
            self.setGeometry(
                self._compact_position.x(), self._compact_position.y(), width, height,
            )
            self._apply_compact_geometry()
            self.toggle_btn.setText('P')
        self.hide()

    def eventFilter(self, watched, event):
        if watched in (self.toggle_btn, self.title, self.shell):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self._dragging = False
            elif event.type() == QEvent.Type.MouseMove and self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
                target = event.globalPosition().toPoint() - self._drag_offset
                if (target - self.pos()).manhattanLength() > 3:
                    self._dragging = True
                    screen = QApplication.screenAt(event.globalPosition().toPoint()) or QApplication.primaryScreen()
                    area = screen.availableGeometry()
                    target.setX(max(area.left(), min(target.x(), area.right() - self.width() + 1)))
                    target.setY(max(area.top(), min(target.y(), area.bottom() - self.height() + 1)))
                    self.move(target)
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease and self._drag_offset is not None:
                self._drag_offset = None
                if self._dragging:
                    button_position = self._button_global_top_left()
                    self._compact_position = button_position - QPoint(self.BUTTON_MARGIN, self.BUTTON_MARGIN)
                    self._dragging = False
                    return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide_panel()
        else:
            super().keyPressEvent(event)
