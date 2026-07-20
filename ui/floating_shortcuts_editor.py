# -*- coding: utf-8 -*-
"""编辑悬浮快捷入口：勾选、拖动排序、恢复推荐。"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from config import normalize_settings, save_settings
from ui.icons import qicon
from ui.navigation_model import (
    DEFAULT_FLOATING_SHORTCUTS,
    MAX_FLOATING_SHORTCUTS,
    floating_candidates,
    normalize_floating_shortcuts,
)


class FloatingShortcutsEditor(QDialog):
    """编辑快捷入口弹层；完成时写入 settings 并发出 saved 信号。"""

    saved = pyqtSignal(object)

    def __init__(
        self,
        settings: dict,
        *,
        language: str = 'zh',
        private_unlocked: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.language = language
        self._private_unlocked = bool(private_unlocked)
        self._settings = dict(settings or {})
        self._session = list(
            normalize_floating_shortcuts(
                self._settings.get('floating_shortcuts'),
                private_unlocked=self._private_unlocked,
            )
        )
        self.setObjectName('floating-shortcuts-editor')
        self.setWindowTitle('编辑快捷入口' if language == 'zh' else 'Edit shortcuts')
        self.setModal(True)
        self.setMinimumWidth(520)
        self.resize(520, 480)
        self._setup_ui()
        self._reload_list()
        self._apply_language()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        self.hint = QLabel()
        self.hint.setObjectName('field-hint')
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        self.list = QListWidget()
        self.list.setObjectName('floating-shortcut-list')
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setSpacing(4)
        self.list.model().rowsMoved.connect(self._on_rows_moved)
        root.addWidget(self.list, 1)

        self.limit_label = QLabel()
        self.limit_label.setObjectName('small-label')
        root.addWidget(self.limit_label)

        footer = QHBoxLayout()
        self.restore_btn = QPushButton()
        self.restore_btn.setObjectName('ghost-btn')
        self.restore_btn.clicked.connect(self._restore_defaults)
        footer.addWidget(self.restore_btn)
        footer.addStretch()
        self.done_btn = QPushButton()
        self.done_btn.setObjectName('primary-btn')
        self.done_btn.clicked.connect(self._finish)
        footer.addWidget(self.done_btn)
        root.addLayout(footer)

    def _apply_language(self):
        zh = self.language == 'zh'
        self.setWindowTitle('编辑快捷入口' if zh else 'Edit shortcuts')
        self.hint.setText(
            '选择常用功能，最多显示 6 个。拖动可调整展开后的顺序。'
            if zh else
            'Choose up to 6 shortcuts. Drag to reorder the expanded panel.'
        )
        self.restore_btn.setText('恢复推荐' if zh else 'Restore recommended')
        self.done_btn.setText('完成' if zh else 'Done')
        self._refresh_limit_label()

    def _refresh_limit_label(self):
        zh = self.language == 'zh'
        count = len(self._session)
        if count >= MAX_FLOATING_SHORTCUTS:
            self.limit_label.setText(
                f'已选 {count}/{MAX_FLOATING_SHORTCUTS} · 最多 6 项'
                if zh else
                f'{count}/{MAX_FLOATING_SHORTCUTS} selected · max 6'
            )
        else:
            self.limit_label.setText(
                f'已选 {count}/{MAX_FLOATING_SHORTCUTS}' if zh else f'{count}/{MAX_FLOATING_SHORTCUTS} selected'
            )

    def _reload_list(self):
        self.list.clear()
        zh = self.language == 'zh'
        selected = set(self._session)
        # 先按当前会话顺序列出已选项，再列未选项
        ordered_indexes = list(self._session)
        for item in floating_candidates(private_unlocked=self._private_unlocked):
            if item.index not in selected:
                ordered_indexes.append(item.index)

        at_limit = len(self._session) >= MAX_FLOATING_SHORTCUTS
        for index in ordered_indexes:
            meta = next(
                (c for c in floating_candidates(private_unlocked=self._private_unlocked) if c.index == index),
                None,
            )
            if meta is None:
                continue
            row = QWidget()
            row.setObjectName('floating-shortcut-row')
            layout = QHBoxLayout(row)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(10)

            grip = QLabel('⋮⋮')
            grip.setObjectName('drag-handle')
            grip.setFixedWidth(18)
            layout.addWidget(grip)

            icon_label = QLabel()
            icon_label.setFixedSize(24, 24)
            icon = qicon(meta.icon_role, size=20)
            if not icon.isNull():
                icon_label.setPixmap(icon.pixmap(20, 20))
            layout.addWidget(icon_label)

            name = QLabel(meta.name_zh if zh else meta.name_en)
            name.setObjectName('floating-shortcut-name')
            layout.addWidget(name, 1)

            check = QCheckBox()
            check.setChecked(index in selected)
            if not check.isChecked() and at_limit:
                check.setEnabled(False)
                check.setToolTip('最多 6 项' if zh else 'Maximum 6 items')
            if check.isChecked() and len(self._session) <= 1:
                # 至少保留 1 个：不允许关掉最后一个
                check.setEnabled(True)
            check.toggled.connect(lambda checked, value=index: self._on_toggled(value, checked))
            layout.addWidget(check)

            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, index)
            list_item.setSizeHint(QSize(480, 48))
            self.list.addItem(list_item)
            self.list.setItemWidget(list_item, row)

        self._refresh_limit_label()

    def _collect_order_from_list(self) -> list[int]:
        order = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item is None:
                continue
            index = item.data(Qt.ItemDataRole.UserRole)
            if index is None:
                continue
            order.append(int(index))
        return order

    def _on_rows_moved(self, *_args):
        # 拖动后：已勾选项按列表顺序重排，未勾选保持相对位置
        order = self._collect_order_from_list()
        selected = set(self._session)
        new_session = [i for i in order if i in selected]
        # 若拖动导致勾选项丢失（不应发生），回退
        if new_session:
            self._session = normalize_floating_shortcuts(
                new_session, private_unlocked=self._private_unlocked
            )
        self._reload_list()

    def _on_toggled(self, index: int, checked: bool):
        selected = set(self._session)
        if checked:
            if index in selected:
                return
            if len(self._session) >= MAX_FLOATING_SHORTCUTS:
                self._reload_list()
                return
            # 新勾选项追加到末尾（在当前列表顺序中的位置之后处理）
            order = self._collect_order_from_list()
            self._session = [i for i in order if i in selected or i == index]
            self._session = normalize_floating_shortcuts(
                self._session, private_unlocked=self._private_unlocked
            )
        else:
            if index not in selected:
                return
            if len(self._session) <= 1:
                zh = self.language == 'zh'
                QMessageBox.information(
                    self,
                    '快捷入口' if zh else 'Shortcuts',
                    '至少保留 1 个快捷入口。' if zh else 'Keep at least one shortcut.',
                )
                self._reload_list()
                return
            self._session = [i for i in self._session if i != index]
        self._reload_list()

    def _restore_defaults(self):
        self._session = list(DEFAULT_FLOATING_SHORTCUTS)
        self._reload_list()

    def _finish(self):
        shortcuts = normalize_floating_shortcuts(
            self._session, private_unlocked=self._private_unlocked
        )
        payload = dict(self._settings)
        payload['floating_shortcuts'] = shortcuts
        saved = save_settings(normalize_settings(payload))
        self.saved.emit(saved)
        self.accept()


def open_floating_shortcuts_editor(
    parent,
    settings: dict,
    *,
    language: str = 'zh',
    private_unlocked: bool = False,
    on_saved=None,
):
    dialog = FloatingShortcutsEditor(
        settings,
        language=language,
        private_unlocked=private_unlocked,
        parent=parent,
    )
    if on_saved is not None:
        dialog.saved.connect(on_saved)
    dialog.exec()
    return dialog
