# -*- coding: utf-8 -*-
"""启动更新公告弹窗（Prism Calm 风格，支持查看历史版本与「不再提醒」偏好）。"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tools.release_notes import (
    build_announcement_settings_patch,
    latest_release,
    load_release_notes,
)
from ui.design_system import apply_button
from ui.dialog_buttons import clamp_dialog_geometry


_TAG_STYLE = {
    '新功能': ('#EEE9FF', '#503DBE', '#D8CEFA'),
    '体验优化': ('#E6F6FD', '#0369A1', '#BAE6FD'),
    '稳定性修复': ('#E5F4F1', '#1E6F66', '#B5DFD6'),
}


class ReleaseNotesDialog(QDialog):
    """软件更新公告弹窗：展示新版本亮点与历史版本，支持按版本或永久关闭启动提醒。"""

    def __init__(
        self,
        *,
        data: dict[str, Any] | None = None,
        dismissed_version: str = '',
        muted_all: bool = False,
        from_startup: bool = False,
        language: str = 'zh',
        on_open_guide=None,
        parent=None,
    ):
        super().__init__(parent)
        self.language = language
        self._zh = language == 'zh'
        self._data = data if isinstance(data, dict) else load_release_notes()
        self._releases: list[dict[str, Any]] = list(self._data.get('releases') or [])
        self._latest = latest_release(self._data) or {}
        self._latest_version = str(self._latest.get('version') or '').strip()
        self._previous_dismissed = str(dismissed_version or '').strip()
        self._on_open_guide = on_open_guide
        self._from_startup = bool(from_startup)
        self._selected_index = 0
        self._version_buttons: list[QPushButton] = []

        self.setObjectName('confirm-dialog')
        self.setWindowTitle('更新公告' if self._zh else "What's New")
        self.setModal(True)
        clamp_dialog_geometry(self, 596, 520, min_width=500, min_height=420)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        # ── 顶部品牌头区 ──
        hero = QHBoxLayout()
        hero.setSpacing(12)
        icon_lbl = QLabel(self)
        icon_lbl.setFixedSize(46, 46)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(
            'background-color: #F3F0FF; border: 1px solid #DFD9F0; border-radius: 14px;'
        )
        try:
            from ui.icons import brand_pixmap
            from ui.theme_manager import ThemeManager
            pix = brand_pixmap('app_mark', 32, ThemeManager.instance().token('PRIMARY') or '#6C58D9')
            if not pix.isNull():
                icon_lbl.setPixmap(pix)
        except Exception:
            icon_lbl.setText('P')
        hero.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        head_col = QVBoxLayout()
        head_col.setSpacing(4)
        eyebrow_row = QHBoxLayout()
        eyebrow_row.setSpacing(8)
        eyebrow_lbl = QLabel('更新公告' if self._zh else "What's New", self)
        eyebrow_lbl.setStyleSheet(
            'color: #6C58D9; font-size: 11px; font-weight: 700; background: transparent;'
        )
        eyebrow_row.addWidget(eyebrow_lbl)

        self.version_badge = QLabel(self)
        self.version_badge.setStyleSheet(
            'background-color: #EEE9FF; color: #503DBE; border: 1px solid #D8CEFA;'
            ' border-radius: 8px; padding: 1px 8px; font-size: 11px; font-weight: 700;'
        )
        eyebrow_row.addWidget(self.version_badge)
        eyebrow_row.addStretch(1)
        head_col.addLayout(eyebrow_row)

        self.headline_label = QLabel(self)
        self.headline_label.setWordWrap(True)
        self.headline_label.setStyleSheet(
            'color: #262438; font-size: 16px; font-weight: 700; background: transparent;'
        )
        head_col.addWidget(self.headline_label)

        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            'color: #615D73; font-size: 12px; background: transparent;'
        )
        head_col.addWidget(self.summary_label)
        hero.addLayout(head_col, 1)
        root.addLayout(hero)

        # ── 多版本切换条（存在多个版本时展示） ──
        if len(self._releases) > 1:
            ver_bar = QHBoxLayout()
            ver_bar.setContentsMargins(0, 0, 0, 0)
            ver_bar.setSpacing(6)
            ver_hint = QLabel('版本记录' if self._zh else 'Releases', self)
            ver_hint.setStyleSheet('color: #847F99; font-size: 11px; font-weight: 600; background: transparent;')
            ver_bar.addWidget(ver_hint)
            for idx, rel in enumerate(self._releases):
                v = str(rel.get('version') or f'#{idx + 1}')
                d = str(rel.get('date') or '')
                btn = QPushButton(f'v{v}' + (f' · {d}' if idx == 0 and d else ''), self)
                btn.setCheckable(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFixedHeight(26)
                btn.clicked.connect(lambda _checked=False, i=idx: self._select_release(i))
                ver_bar.addWidget(btn)
                self._version_buttons.append(btn)
            ver_bar.addStretch(1)
            root.addLayout(ver_bar)

        # ── 更新亮点滚动区 ──
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        self._content_host = QWidget()
        self._content_host.setStyleSheet('background: transparent;')
        self._content_layout = QVBoxLayout(self._content_host)
        self._content_layout.setContentsMargins(0, 2, 4, 2)
        self._content_layout.setSpacing(8)
        scroll.setWidget(self._content_host)
        root.addWidget(scroll, 1)

        # ── 底部分割线 + 「不再提醒」勾选与按钮 ──
        divider = QFrame(self)
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        divider.setStyleSheet('background-color: #E6E2F0; border: none;')
        root.addWidget(divider)

        footer = QHBoxLayout()
        footer.setSpacing(10)

        self.dismiss_version_chk = QCheckBox(
            '本次更新不再提醒' if self._zh else "Don't show again for this version",
            self,
        )
        self.dismiss_version_chk.setToolTip(
            '勾选后当前版本启动不再弹出，后续发布新版本时会自动提醒'
            if self._zh else
            'Hide for this version; automatically show again on future updates'
        )
        already_dismissed = bool(self._latest_version and self._previous_dismissed == self._latest_version)
        self.dismiss_version_chk.setChecked(already_dismissed or bool(muted_all))
        footer.addWidget(self.dismiss_version_chk)

        self.mute_all_chk = QCheckBox(
            '永久关闭弹窗' if self._zh else 'Disable startup alerts',
            self,
        )
        self.mute_all_chk.setToolTip(
            '关闭所有未来版本的启动自动弹窗；仍可随时在顶部「帮助 → 更新公告」中查看'
            if self._zh else
            'Disable automatic startup popups for all future releases (still available under Help menu)'
        )
        self.mute_all_chk.setChecked(bool(muted_all))
        self.mute_all_chk.toggled.connect(self._on_mute_all_toggled)
        footer.addWidget(self.mute_all_chk)

        footer.addStretch(1)

        if callable(self._on_open_guide):
            guide_btn = QPushButton('使用说明' if self._zh else 'User Guide', self)
            apply_button(guide_btn, 'secondary', compact=True)
            guide_btn.clicked.connect(self._open_guide)
            footer.addWidget(guide_btn)

        self.ok_btn = QPushButton('我知道了' if self._zh else 'Got it', self)
        apply_button(self.ok_btn, 'primary', compact=True)
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        footer.addWidget(self.ok_btn)
        root.addLayout(footer)

        self._select_release(0)

    def _on_mute_all_toggled(self, checked: bool) -> None:
        if checked:
            self.dismiss_version_chk.setChecked(True)
        self.dismiss_version_chk.setEnabled(not checked)

    def _style_version_buttons(self) -> None:
        for idx, btn in enumerate(self._version_buttons):
            active = idx == self._selected_index
            btn.setChecked(active)
            if active:
                btn.setStyleSheet(
                    'QPushButton { background-color: #EEE9FF; color: #503DBE;'
                    ' border: 1px solid #C8BCF6; border-radius: 8px;'
                    ' padding: 2px 10px; font-size: 11px; font-weight: 700; }'
                )
            else:
                btn.setStyleSheet(
                    'QPushButton { background-color: #F7F6FC; color: #615D73;'
                    ' border: 1px solid #E6E2F0; border-radius: 8px;'
                    ' padding: 2px 10px; font-size: 11px; font-weight: 500; }'
                    'QPushButton:hover { background-color: #EFEBF9; color: #262438; }'
                )

    def _select_release(self, index: int) -> None:
        if not self._releases:
            self.headline_label.setText('暂无更新公告' if self._zh else 'No release notes')
            self.version_badge.setText('')
            self.version_badge.hide()
            self.summary_label.setText('')
            return
        self._selected_index = max(0, min(index, len(self._releases) - 1))
        self._style_version_buttons()
        rel = self._releases[self._selected_index]
        ver = str(rel.get('version') or '').strip()
        date_str = str(rel.get('date') or '').strip()
        title = str(rel.get('title') or (f'版本 v{ver}' if ver else '更新公告'))
        summary = str(rel.get('summary') or '')

        self.headline_label.setText(title)
        badge_text = f'v{ver}' + (f' · {date_str}' if date_str else '')
        self.version_badge.setText(badge_text)
        self.version_badge.setVisible(bool(badge_text))
        self.summary_label.setText(summary)
        self.summary_label.setVisible(bool(summary))

        # Rebuild highlight cards
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        highlights = [h for h in (rel.get('highlights') or []) if isinstance(h, dict)]
        for item in highlights:
            tag = str(item.get('tag') or '更新').strip()
            h_title = str(item.get('title') or '').strip()
            h_desc = str(item.get('desc') or '').strip()
            card = QFrame(self._content_host)
            card.setStyleSheet(
                'QFrame { background-color: #FDFDFF; border: 1px solid #E6E2F0; border-radius: 12px; }'
            )
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 10, 14, 10)
            cl.setSpacing(4)

            top = QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            top.setSpacing(8)
            bg, fg, bd = _TAG_STYLE.get(tag, ('#F2EFFB', '#503DBE', '#DDD6F7'))
            tag_lbl = QLabel(tag, card)
            tag_lbl.setStyleSheet(
                f'background-color: {bg}; color: {fg}; border: 1px solid {bd};'
                ' border-radius: 6px; padding: 1px 7px; font-size: 11px; font-weight: 700;'
            )
            tag_lbl.setFixedHeight(20)
            top.addWidget(tag_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

            t_lbl = QLabel(h_title, card)
            t_lbl.setWordWrap(True)
            t_lbl.setStyleSheet(
                'color: #262438; font-size: 13px; font-weight: 700; background: transparent; border: none;'
            )
            top.addWidget(t_lbl, 1)
            cl.addLayout(top)

            if h_desc:
                d_lbl = QLabel(h_desc, card)
                d_lbl.setWordWrap(True)
                d_lbl.setStyleSheet(
                    'color: #615D73; font-size: 12px; background: transparent; border: none;'
                )
                cl.addWidget(d_lbl)
            self._content_layout.addWidget(card)

        self._content_layout.addStretch(1)

    def _open_guide(self) -> None:
        self.accept()
        if callable(self._on_open_guide):
            self._on_open_guide()

    def settings_patch(self) -> dict[str, Any]:
        """返回用户当前的「不再提醒」偏好设置补丁。"""
        return build_announcement_settings_patch(
            self._latest_version,
            dismiss_this_version=self.dismiss_version_chk.isChecked(),
            mute_all=self.mute_all_chk.isChecked(),
            previous_dismissed=self._previous_dismissed,
        )


def show_release_notes_dialog(
    *,
    settings: dict[str, Any] | None = None,
    from_startup: bool = False,
    language: str = 'zh',
    on_open_guide=None,
    parent=None,
) -> dict[str, Any] | None:
    """打开更新公告弹窗，并在关闭时返回需要合并保存的偏好补丁。"""
    cfg = settings if isinstance(settings, dict) else {}
    data = load_release_notes()
    if not (data.get('releases') or []):
        return None
    dlg = ReleaseNotesDialog(
        data=data,
        dismissed_version=str(cfg.get('announcement_dismissed_version') or ''),
        muted_all=bool(cfg.get('announcement_muted_all', False)),
        from_startup=from_startup,
        language=language,
        on_open_guide=on_open_guide,
        parent=parent,
    )
    dlg.exec()
    return dlg.settings_patch()
