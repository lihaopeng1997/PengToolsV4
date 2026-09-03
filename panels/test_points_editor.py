# -*- coding: utf-8 -*-
"""测试任务点清单：首页弹窗、需求详情 Tab、编辑需求弹窗共用。"""

from __future__ import annotations

import uuid

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from tools.requirements import (
    extract_test_points_from_text,
    normalize_test_points,
    save_requirement_test_points,
    test_points_progress,
)
from ui.confirm_dialog import confirm_action, show_error
from ui.design_system import apply_button
from ui.field_metrics import size_line


class TestPointRow(QFrame):
    toggled = pyqtSignal(str, bool)
    edited = pyqtSignal(str, str)
    removed = pyqtSignal(str)

    def __init__(self, point, parent=None):
        super().__init__(parent)
        self.setObjectName('test-point-row')
        self._point_id = point['id']
        self._editing = False
        self.setProperty('done', bool(point.get('done')))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        self.check = QCheckBox()
        self.check.setChecked(bool(point.get('done')))
        self.check.toggled.connect(self._on_toggled)
        layout.addWidget(self.check, 0)
        self.text_label = QLabel(point.get('text') or '')
        self.text_label.setObjectName('test-point-text')
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.text_label, 1)
        self.text_edit = QLineEdit(point.get('text') or '')
        size_line(self.text_edit, 'std')
        self.text_edit.hide()
        self.text_edit.editingFinished.connect(self._commit_edit)
        self.text_edit.returnPressed.connect(self._commit_edit)
        layout.addWidget(self.text_edit, 1)
        self.delete_btn = QPushButton('删除')
        apply_button(self.delete_btn, 'ghost', compact=True)
        self.delete_btn.clicked.connect(self._on_delete)
        layout.addWidget(self.delete_btn, 0)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._editing:
            self._begin_edit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _on_toggled(self, checked):
        self.setProperty('done', bool(checked))
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.toggled.emit(self._point_id, bool(checked))

    def _begin_edit(self):
        self._editing = True
        self.text_label.hide()
        self.text_edit.setText(self.text_label.text())
        self.text_edit.show()
        self.text_edit.setFocus()
        self.text_edit.selectAll()

    def _commit_edit(self):
        if not self._editing:
            return
        self._editing = False
        text = self.text_edit.text().strip()
        self.text_edit.hide()
        self.text_label.show()
        if not text:
            self.text_edit.setText(self.text_label.text())
            return
        if text != self.text_label.text():
            self.text_label.setText(text)
            self.edited.emit(self._point_id, text)

    def _on_delete(self):
        self.removed.emit(self._point_id)


class TestPointsEditor(QWidget):
    """persist_callback 有值时，勾选/增删改立刻回写；否则只改内存，由父级保存。"""

    changed = pyqtSignal(list)

    def __init__(
        self,
        points=None,
        *,
        description='',
        persist_callback=None,
        compact=False,
        auto_seed=True,
        show_header=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName('test-points-editor')
        self._persist_callback = persist_callback
        self._description = str(description or '')
        self._auto_seed = bool(auto_seed)
        self._pending_seed = False
        self._points = []
        self._rebuilding = False
        self._compact = bool(compact)
        self._show_header = bool(compact if show_header is None else show_header)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.hint = QLabel()
        self.hint.setObjectName('test-points-hint')
        self.hint.setWordWrap(True)
        self.hint.hide()
        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(0, 0, 0, 0)
        hint_row.setSpacing(8)
        hint_row.addWidget(self.hint, 1)
        self.commit_seed_btn = QPushButton('写入清单')
        apply_button(self.commit_seed_btn, 'secondary', compact=True)
        self.commit_seed_btn.clicked.connect(self._commit_seed)
        self.commit_seed_btn.hide()
        hint_row.addWidget(self.commit_seed_btn, 0)
        root.addLayout(hint_row)

        self.header = QWidget()
        header_l = QHBoxLayout(self.header)
        header_l.setContentsMargins(0, 0, 0, 0)
        self.header_title = QLabel('测试任务点')
        self.header_title.setObjectName('zone-title')
        self.header_progress = QLabel('0 / 0')
        self.header_progress.setObjectName('field-hint')
        header_l.addWidget(self.header_title)
        header_l.addStretch(1)
        header_l.addWidget(self.header_progress)
        self.header.setVisible(self._show_header)
        root.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(4)
        self.empty = QLabel('暂无测试点')
        self.empty.setObjectName('test-points-empty')
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        if compact:
            self.scroll.setMinimumHeight(48)
            self.scroll.setMaximumHeight(140)
            self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            self.setMaximumHeight(220)
        else:
            self.scroll.setMinimumHeight(160)
            self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.scroll, 1)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.setSpacing(8)
        self.add_edit = QLineEdit()
        size_line(self.add_edit, 'std')
        self.add_edit.setPlaceholderText('新增测试点，回车添加')
        self.add_edit.returnPressed.connect(self._add_point)
        add_row.addWidget(self.add_edit, 1)
        self.add_btn = QPushButton('添加')
        apply_button(self.add_btn, 'secondary', compact=True)
        self.add_btn.clicked.connect(self._add_point)
        add_row.addWidget(self.add_btn, 0)
        self.extract_btn = QPushButton('从需求说明提取')
        apply_button(self.extract_btn, 'ghost', compact=True)
        self.extract_btn.clicked.connect(self._extract_from_description)
        add_row.addWidget(self.extract_btn, 0)
        root.addLayout(add_row)

        self.set_source(points=points, description=description)

    def points(self):
        return [dict(item) for item in self._points]

    def set_source(self, points=None, description='', *, pending_seed=None):
        self._description = str(description or '')
        normalized = normalize_test_points(points)
        extracted = extract_test_points_from_text(self._description)
        if pending_seed is None:
            pending_seed = bool(self._auto_seed) and (not normalized) and bool(extracted)
        if pending_seed and extracted:
            self._points = extracted
            self._pending_seed = True
        else:
            self._points = normalized
            self._pending_seed = False
        self.extract_btn.setEnabled(bool(extracted))
        self.extract_btn.setToolTip(
            '从当前需求说明中识别列表行并追加' if extracted else '需求说明里没有可识别的列表行'
        )
        self._rebuild()

    def set_description(self, text):
        self._description = str(text or '')
        extracted = extract_test_points_from_text(self._description)
        self.extract_btn.setEnabled(bool(extracted))
        self.extract_btn.setToolTip(
            '从当前需求说明中识别列表行并追加' if extracted else '需求说明里没有可识别的列表行'
        )

    def set_persist_callback(self, callback):
        self._persist_callback = callback

    def pending_seed(self):
        return bool(self._pending_seed)

    def _rebuild(self):
        self._rebuilding = True
        try:
            for index in range(self.list_layout.count() - 1, -1, -1):
                item = self.list_layout.takeAt(index)
                widget = item.widget()
                if widget is None or widget is self.empty:
                    continue
                widget.deleteLater()
            if self.empty.parent() is not self.list_host:
                self.list_layout.addWidget(self.empty)
            elif self.list_layout.indexOf(self.empty) < 0:
                self.list_layout.addWidget(self.empty)
            self.empty.setVisible(not self._points)
            self.empty.setText('暂无测试点')
            for point in self._points:
                row = TestPointRow(point)
                row.toggled.connect(self._on_toggled)
                row.edited.connect(self._on_edited)
                row.removed.connect(self._on_removed)
                self.list_layout.addWidget(row)
            self.list_layout.addStretch(1)
            self._sync_hint()
        finally:
            self._rebuilding = False

    def _sync_hint(self):
        done, total = test_points_progress(self._points)
        if hasattr(self, 'header_progress'):
            self.header_progress.setText(f'{done} / {total}')
        extracted = extract_test_points_from_text(self._description)
        can_extract = bool(extracted)
        self.extract_btn.setEnabled(can_extract)
        self.extract_btn.setToolTip('' if can_extract else '需求说明中没有可提取的测试点')
        if self._pending_seed and self._points:
            self.hint.setText(f'已从需求说明识别 {total} 条，尚未写入台账。勾选或点「写入清单」后保存；原说明不会删除。')
            self.hint.show()
            self.commit_seed_btn.show()
            self.commit_seed_btn.setEnabled(True)
        else:
            self.hint.hide()
            self.commit_seed_btn.hide()

    def _find(self, point_id):
        return next((item for item in self._points if item.get('id') == point_id), None)

    def _on_toggled(self, point_id, done):
        if self._rebuilding:
            return
        item = self._find(point_id)
        if item is None:
            return
        item['done'] = bool(done)
        self._commit_change()

    def _on_edited(self, point_id, text):
        item = self._find(point_id)
        if item is None:
            return
        cleaned = str(text or '').strip()
        if not cleaned:
            return
        item['text'] = cleaned
        self._commit_change()

    def _on_removed(self, point_id):
        item = self._find(point_id)
        if item is None:
            return
        if not confirm_action(
            self, '删除测试点',
            f'从清单中移除「{item.get("text") or "未命名"}」？',
            confirm_text='确认删除',
            danger=True,
        ):
            return
        self._points = [entry for entry in self._points if entry.get('id') != point_id]
        self._rebuild()
        self._commit_change()

    def _add_point(self):
        text = self.add_edit.text().strip()
        if not text:
            self.add_edit.setFocus()
            return
        self._points.append({'id': uuid.uuid4().hex, 'text': text, 'done': False})
        self.add_edit.clear()
        self._rebuild()
        self._commit_change()
        self.add_edit.setFocus()

    def _extract_from_description(self):
        incoming = extract_test_points_from_text(self._description)
        if not incoming:
            return
        existing = {str(item.get('text') or '').casefold() for item in self._points}
        added = 0
        for point in incoming:
            key = str(point.get('text') or '').casefold()
            if not key or key in existing:
                continue
            self._points.append(point)
            existing.add(key)
            added += 1
        if added:
            self._rebuild()
            self._commit_change()

    def _commit_seed(self):
        if not self._points:
            return
        self._commit_change()
        self._sync_hint()

    def _commit_change(self):
        self._pending_seed = False
        payload = self.points()
        self.changed.emit(payload)
        if self._persist_callback is not None:
            self._persist_callback(payload)
        self._sync_hint()


class TestPointsDialog(QDialog):
    """首页待升级事项打开的测试点清单。关闭后由调用方刷新进度。"""

    def __init__(self, requirement, parent=None, persist=True):
        super().__init__(parent)
        self.setObjectName('test-points-dialog')
        self.setWindowTitle('测试任务点')
        self.setModal(True)
        self.resize(520, 560)
        self._requirement = dict(requirement or {})
        self._persist = bool(persist)
        self._saved = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        title = QLabel(self._requirement.get('title') or '未命名需求')
        title.setObjectName('section-title')
        title.setWordWrap(True)
        root.addWidget(title)
        self.subtitle = QLabel()
        self.subtitle.setObjectName('field-hint')
        root.addWidget(self.subtitle)

        persist_cb = self._persist_points if self._persist else None
        self.editor = TestPointsEditor(
            self._requirement.get('test_points'),
            description=self._requirement.get('description') or '',
            persist_callback=persist_cb,
            compact=False,
            auto_seed=True,
            parent=self,
        )
        self.editor.changed.connect(self._on_changed)
        root.addWidget(self.editor, 1)
        self._refresh_subtitle()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton('关闭')
        apply_button(close_btn, 'secondary', compact=True)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    def saved(self):
        return self._saved

    def points(self):
        return self.editor.points()

    def _refresh_subtitle(self):
        done, total = test_points_progress(self.editor.points())
        pending = ' · 未写入台账' if self.editor.pending_seed() else ''
        if total:
            self.subtitle.setText(f'测试进度 {done}/{total}{pending}')
        else:
            self.subtitle.setText('测试进度 0/0')

    def _on_changed(self, _points):
        self._refresh_subtitle()

    def _persist_points(self, points):
        req_id = str(self._requirement.get('id') or '')
        if not req_id:
            return
        try:
            updated = save_requirement_test_points(req_id, points)
        except OSError as exc:
            show_error(self, '保存失败', f'无法写入测试点：\n{exc}')
            return
        if updated is not None:
            self._requirement['test_points'] = list(updated.get('test_points') or [])
            self._saved = True
        self._refresh_subtitle()
