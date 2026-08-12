# -*- coding: utf-8 -*-
"""日报富文本编辑区：插图 / 粘贴图 / 拖入图，离线资源落盘。"""

from __future__ import annotations

import os

from PyQt6.QtCore import QMimeData, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QImageReader, QTextCursor, QTextDocument, QTextImageFormat
from PyQt6.QtWidgets import QTextEdit

from tools.daily_reports import (
    absolute_asset_path,
    relative_asset_path,
    save_image_bytes,
    save_image_file,
)


class DailyRichEdit(QTextEdit):
    """支持本地图片的日报段落编辑器。"""

    assets_changed = pyqtSignal()
    image_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setAcceptDrops(True)
        self._date_key = ''
        self._max_image_width = 640
        self.document().setDocumentMargin(6)

    def set_report_date(self, date_key: str):
        self._date_key = str(date_key or '')[:10]

    def set_plain_or_html(self, plain: str = '', html_text: str = ''):
        """优先 HTML；无则纯文本。加载时把相对路径图转成绝对 file URL。"""
        html_val = (html_text or '').strip()
        if html_val:
            fixed = self._html_with_absolute_images(html_val)
            self.setHtml(fixed)
        else:
            self.setPlainText(plain or '')

    def export_content(self) -> tuple[str, str, list[str]]:
        """返回 (plain, html, asset_rels)。"""
        plain = self.toPlainText().strip()
        html_val = self.toHtml()
        # 将绝对路径归一为 daily_assets 相对路径，便于换机/备份
        html_val = self._html_with_relative_images(html_val)
        from tools.daily_reports import collect_asset_refs_from_html
        assets = collect_asset_refs_from_html(html_val)
        return plain, html_val, assets

    def insert_image_from_path(self, path: str) -> bool:
        if not self._date_key:
            self.image_error.emit('请先选择日报日期')
            return False
        try:
            rel = save_image_file(self._date_key, path)
        except ValueError as exc:
            self.image_error.emit(str(exc))
            return False
        except OSError as exc:
            self.image_error.emit(f'保存图片失败：{exc}')
            return False
        return self._insert_rel_image(rel)

    def insert_image_from_qimage(self, image: QImage, *, ext: str = '.png') -> bool:
        if image is None or image.isNull():
            return False
        if not self._date_key:
            self.image_error.emit('请先选择日报日期')
            return False
        from PyQt6.QtCore import QBuffer, QIODevice
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        fmt = 'PNG' if ext.lower() in ('.png', 'png') else 'JPEG'
        if not image.save(buffer, fmt):
            self.image_error.emit('无法编码剪贴板图片')
            return False
        data = bytes(buffer.data())
        try:
            rel = save_image_bytes(self._date_key, data, ext=ext if ext.startswith('.') else f'.{ext}')
        except ValueError as exc:
            self.image_error.emit(str(exc))
            return False
        except OSError as exc:
            self.image_error.emit(f'保存图片失败：{exc}')
            return False
        return self._insert_rel_image(rel)

    def _insert_rel_image(self, rel: str) -> bool:
        abs_path = absolute_asset_path(rel)
        if not os.path.isfile(abs_path):
            self.image_error.emit('图片文件丢失')
            return False
        image = QImage(abs_path)
        if image.isNull():
            self.image_error.emit('无法读取图片')
            return False
        width = image.width()
        height = image.height()
        if width > self._max_image_width > 0:
            ratio = self._max_image_width / float(width)
            width = self._max_image_width
            height = max(1, int(height * ratio))
        fmt = QTextImageFormat()
        # Qt 用 file URL 或本地路径；保存时再归一相对路径
        fmt.setName(QUrl.fromLocalFile(abs_path).toString())
        fmt.setWidth(width)
        fmt.setHeight(height)
        cursor = self.textCursor()
        cursor.insertImage(fmt)
        cursor.insertBlock()
        self.setTextCursor(cursor)
        self.assets_changed.emit()
        return True

    def _html_with_absolute_images(self, html_text: str) -> str:
        import re

        def repl(match):
            src = match.group(1)
            if src.startswith('file:') or os.path.isabs(src):
                return match.group(0)
            if 'daily_assets/' in src.replace('\\', '/'):
                abs_path = absolute_asset_path(src)
                if os.path.isfile(abs_path):
                    url = QUrl.fromLocalFile(abs_path).toString()
                    return f'src="{url}"'
            return match.group(0)

        return re.sub(r'src=["\']([^"\']+)["\']', repl, html_text, flags=re.I)

    def _html_with_relative_images(self, html_text: str) -> str:
        import re
        from config import local_data_dir
        data_root = os.path.normpath(local_data_dir())

        def repl(match):
            src = match.group(1)
            local = src
            if src.startswith('file:'):
                local = QUrl(src).toLocalFile()
            local_norm = local.replace('\\', '/')
            # 已是相对
            idx = local_norm.lower().find('daily_assets/')
            if idx >= 0:
                return f'src="{local_norm[idx:]}"'
            # 绝对路径落在 data 下
            try:
                norm = os.path.normpath(local)
                if norm.lower().startswith(data_root.lower()):
                    rel = os.path.relpath(norm, data_root).replace('\\', '/')
                    if rel.startswith('daily_assets/'):
                        return f'src="{rel}"'
            except Exception:
                pass
            return match.group(0)

        return re.sub(r'src=["\']([^"\']+)["\']', repl, html_text, flags=re.I)

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        if source is None:
            return super().canInsertFromMimeData(source)
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData):
        if source is None:
            return
        # 剪贴板位图
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                if self.insert_image_from_qimage(image):
                    return
        # 文件 URL
        if source.hasUrls():
            inserted = False
            for url in source.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                ext = os.path.splitext(path)[1].lower()
                if ext in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'} and os.path.isfile(path):
                    if self.insert_image_from_path(path):
                        inserted = True
            if inserted:
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        if event.mimeData() and (event.mimeData().hasUrls() or event.mimeData().hasImage()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime and mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                ext = os.path.splitext(path)[1].lower()
                if ext in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'} and os.path.isfile(path):
                    self.insert_image_from_path(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
