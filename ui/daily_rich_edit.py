# -*- coding: utf-8 -*-
"""日报富文本编辑区：插图 / 粘贴图 / 拖入图，离线资源落盘。"""

from __future__ import annotations

import os

from PyQt6.QtCore import QMimeData, QPoint, QRect, QSize, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QTextCursor, QTextImageFormat
from PyQt6.QtWidgets import QTextEdit

from tools.daily_reports import (
    absolute_asset_path,
    save_image_bytes,
    save_image_file,
)


_MIN_IMAGE_WIDTH = 48
_MAX_IMAGE_WIDTH = 2400
_HANDLE = 12


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
        self._preferred_height = 120
        self._hover_hit = None
        self._resize_state = None
        self.document().setDocumentMargin(6)
        self.viewport().setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

    def set_preferred_height(self, height: int):
        self._preferred_height = max(40, int(height or 0))

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(hint.width(), self._preferred_height)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        floor = self.minimumHeight() if self.minimumHeight() > 0 else 40
        return QSize(hint.width(), min(floor, self._preferred_height))

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

    def list_images(self) -> list[dict]:
        """文档中全部插图：start / length / width / height / name / rect。"""
        return list(self._iter_image_hits())

    def apply_image_width(self, hit: dict, width: int) -> bool:
        """按目标宽度缩放，保持宽高比。"""
        if not hit:
            return False
        current = self._hit_by_start(hit.get('start')) or hit
        current_w = max(1, int(current.get('width') or 1))
        current_h = max(1, int(current.get('height') or 1))
        new_w = max(_MIN_IMAGE_WIDTH, min(_MAX_IMAGE_WIDTH, int(width)))
        new_h = max(24, int(round(current_h * (new_w / float(current_w)))))
        return self._set_image_size(current['start'], current['length'], new_w, new_h)

    def apply_image_scale(self, hit: dict, scale: float) -> bool:
        if not hit:
            return False
        current = self._hit_by_start(hit.get('start')) or hit
        return self.apply_image_width(current, int(round(int(current.get('width') or 1) * float(scale))))

    def reset_image_size(self, hit: dict) -> bool:
        if not hit:
            return False
        current = self._hit_by_start(hit.get('start')) or hit
        natural_w, natural_h = self._natural_size(current.get('name') or '')
        return self._set_image_size(current['start'], current['length'], natural_w, natural_h)

    def fit_image_to_viewport(self, hit: dict) -> bool:
        if not hit:
            return False
        current = self._hit_by_start(hit.get('start')) or hit
        target = max(80, self.viewport().width() - 28)
        natural_w, _natural_h = self._natural_size(current.get('name') or '')
        return self.apply_image_width(current, min(target, natural_w))

    def _iter_image_hits(self):
        block = self.document().firstBlock()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    img = fragment.charFormat().toImageFormat()
                    cursor = QTextCursor(self.document())
                    cursor.setPosition(fragment.position())
                    top_left = self.cursorRect(cursor).topLeft()
                    width = int(img.width() or 0)
                    height = int(img.height() or 0)
                    if width <= 0 or height <= 0:
                        width, height = self._natural_size(img.name())
                    yield {
                        'start': fragment.position(),
                        'length': fragment.length(),
                        'width': width,
                        'height': height,
                        'name': img.name(),
                        'rect': QRect(top_left, QSize(max(1, width), max(1, height))),
                    }
                iterator += 1
            block = block.next()

    def _natural_size(self, name: str) -> tuple[int, int]:
        path = name or ''
        if path.startswith('file:'):
            path = QUrl(path).toLocalFile()
        image = QImage(path) if path else QImage()
        if image.isNull():
            return 160, 90
        return max(1, image.width()), max(1, image.height())

    def _set_image_size(self, start: int, length: int, width: int, height: int, *, emit: bool = True) -> bool:
        cursor = QTextCursor(self.document())
        cursor.setPosition(int(start))
        cursor.setPosition(int(start) + max(1, int(length)), QTextCursor.MoveMode.KeepAnchor)
        fmt = cursor.charFormat().toImageFormat()
        if not fmt.isValid():
            return False
        fmt.setWidth(max(_MIN_IMAGE_WIDTH, int(width)))
        fmt.setHeight(max(24, int(height)))
        cursor.setCharFormat(fmt)
        if emit:
            self.assets_changed.emit()
        self.viewport().update()
        return True

    def _hit_by_start(self, start):
        try:
            start = int(start)
        except (TypeError, ValueError):
            return None
        for hit in self._iter_image_hits():
            if hit['start'] == start:
                return hit
        return None

    def _hit_image(self, pos: QPoint):
        pad = _HANDLE
        for hit in self._iter_image_hits():
            if hit['rect'].adjusted(-2, -2, pad, pad).contains(pos):
                return hit
        return None

    def _handle_rect(self, image_rect: QRect) -> QRect:
        return QRect(
            image_rect.right() - _HANDLE + 2,
            image_rect.bottom() - _HANDLE + 2,
            _HANDLE + 2,
            _HANDLE + 2,
        )

    def _on_resize_handle(self, hit: dict, pos: QPoint) -> bool:
        return bool(hit) and self._handle_rect(hit['rect']).contains(pos)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._hit_image(event.pos())
            if hit and self._on_resize_handle(hit, event.pos()):
                self._resize_state = {
                    'start': hit['start'],
                    'length': hit['length'],
                    'width': hit['width'],
                    'height': hit['height'],
                    'left': hit['rect'].left(),
                    'origin': QPoint(event.pos()),
                }
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_state:
            state = self._resize_state
            new_w = max(_MIN_IMAGE_WIDTH, event.pos().x() - int(state['left']))
            new_w = min(_MAX_IMAGE_WIDTH, new_w)
            ratio = float(state['height']) / max(1.0, float(state['width']))
            new_h = max(24, int(round(new_w * ratio)))
            self._set_image_size(state['start'], state['length'], new_w, new_h, emit=False)
            self._hover_hit = self._hit_image(event.pos()) or {
                'rect': QRect(int(state['left']), 0, new_w, new_h),
            }
            self.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor)
            event.accept()
            return
        hit = self._hit_image(event.pos())
        self._hover_hit = hit
        if hit and self._on_resize_handle(hit, event.pos()):
            self.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif hit:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.viewport().unsetCursor()
        self.viewport().update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resize_state and event.button() == Qt.MouseButton.LeftButton:
            self._resize_state = None
            self._hover_hit = self._hit_image(event.pos())
            self.assets_changed.emit()
            self.viewport().update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self._hover_hit = None
        if not self._resize_state:
            self.viewport().unsetCursor()
        self.viewport().update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
            hit = self._hit_image(pos)
            if hit:
                scale = 1.12 if event.angleDelta().y() > 0 else 0.88
                self.apply_image_scale(hit, scale)
                event.accept()
                return
        super().wheelEvent(event)

    def contextMenuEvent(self, event):
        pos = event.pos()
        if not self.viewport().rect().contains(pos):
            pos = self.viewport().mapFrom(self, event.pos())
        hit = self._hit_image(pos)
        menu = self.createStandardContextMenu()
        if hit:
            menu.addSeparator()
            menu.addAction('放大图片', lambda current=hit: self.apply_image_scale(current, 1.25))
            menu.addAction('缩小图片', lambda current=hit: self.apply_image_scale(current, 0.8))
            menu.addAction('适应编辑区宽度', lambda current=hit: self.fit_image_to_viewport(current))
            menu.addAction('原始大小', lambda current=hit: self.reset_image_size(current))
        menu.exec(event.globalPos())

    def paintEvent(self, event):
        super().paintEvent(event)
        hit = self._hover_hit
        if self._resize_state:
            # 拖动中用最新几何
            for item in self._iter_image_hits():
                if item['start'] == self._resize_state['start']:
                    hit = item
                    break
        if not hit:
            return
        rect = hit.get('rect')
        if rect is None or not rect.isValid():
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor('#668C78'))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        handle = self._handle_rect(rect)
        painter.setBrush(QColor('#668C78'))
        painter.drawRoundedRect(handle, 2, 2)
        painter.end()

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
