"""Bounded remote file picker; directory I/O never runs in the UI thread."""
import posixpath
import stat
import threading
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
                            QLabel, QTableWidget, QTableWidgetItem, QAbstractItemView)


class BrowserSignals(QObject):
    loaded = pyqtSignal(int, object, str)


class RemoteFilePicker(QDialog):
    def __init__(self, server, path='/', parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择服务器日志文件')
        self.resize(760, 480)
        self.server, self.selected_paths = server, []
        self.generation = 0
        self.cancel = threading.Event()
        self.client = None
        self.signals = BrowserSignals(self)
        self.signals.loaded.connect(self.loaded)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.path = QLineEdit(path or '/')
        row.addWidget(self.path, 1)
        up = QPushButton('上级目录')
        up.clicked.connect(lambda: (self.path.setText(posixpath.dirname(self.path.text().rstrip('/')) or '/'), self.browse()))
        row.addWidget(up)
        refresh = QPushButton('浏览')
        refresh.clicked.connect(self.browse)
        row.addWidget(refresh)
        layout.addLayout(row)
        self.path.returnPressed.connect(self.browse)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['文件 / 目录', '大小（字节）', '类型'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 420)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.enter)
        layout.addWidget(self.table, 1)
        self.notice = QLabel('可多选文件；双击目录进入。每个目录最多展示2000项。')
        layout.addWidget(self.notice)
        choose = QPushButton('选择文件')
        choose.clicked.connect(self.choose)
        layout.addWidget(choose)
        self.browse()

    def browse(self):
        self.generation += 1
        generation, path = self.generation, self.path.text().strip()
        if not path.startswith('/'):
            self.notice.setText('请输入远端绝对路径')
            return
        self.notice.setText('正在读取目录…')
        self.table.setRowCount(0)
        self.cancel.set()
        if self.client:
            self.client.close()
        cancellation = self.cancel = threading.Event()
        def work():
            from tools.ops_ssh import open_ssh_client
            client = None
            try:
                client = open_ssh_client(self.server, timeout_sec=8)
                if cancellation.is_set():
                    return
                self.client = client
                sftp = client.open_sftp()
                sftp.get_channel().settimeout(8)
                info = sftp.stat(path)
                directory = path if stat.S_ISDIR(info.st_mode) else posixpath.dirname(path)
                rows = []
                for attr in sftp.listdir_iter(directory):
                    if cancellation.is_set():
                        return
                    if len(rows) >= 2000:
                        break
                    if attr.filename in ('.', '..'):
                        continue
                    rows.append((posixpath.join(directory, attr.filename), attr.st_size, stat.S_ISDIR(attr.st_mode)))
                rows.sort(key=lambda r: (not r[2], r[0].lower()))
                if not cancellation.is_set():
                    self.signals.loaded.emit(generation, (directory, rows), '')
            except Exception as error:
                if not cancellation.is_set():
                    try:
                        self.signals.loaded.emit(generation, None, '读取失败：' + type(error).__name__)
                    except RuntimeError:
                        pass
            finally:
                if client:
                    client.close()
        threading.Thread(target=work, name='log-file-browser', daemon=True).start()

    def loaded(self, generation, result, error):
        if generation != self.generation or self.cancel.is_set():
            return
        if error:
            self.notice.setText(error)
            return
        directory, rows = result
        self.path.setText(directory)
        self.table.setRowCount(len(rows))
        for i, (path, size, is_dir) in enumerate(rows):
            for col, text in enumerate((posixpath.basename(path), str(size), '目录' if is_dir else '文件')):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, (path, is_dir))
                self.table.setItem(i, col, item)
        self.notice.setText(f'{len(rows)}项，可多选文件' + ('；已达展示上限，请输入更具体路径' if len(rows) == 2000 else ''))

    def enter(self, row, _column):
        path, is_dir = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if is_dir:
            self.path.setText(path)
            self.browse()

    def choose(self):
        self.selected_paths = [self.table.item(row.row(), 0).data(Qt.ItemDataRole.UserRole)[0]
                               for row in self.table.selectionModel().selectedRows()
                               if not self.table.item(row.row(), 0).data(Qt.ItemDataRole.UserRole)[1]]
        if self.selected_paths:
            self.accept()
        else:
            self.notice.setText('请选择至少一个具体文件')

    def done(self, result):
        self.cancel.set()
        if self.client:
            self.client.close()
        super().done(result)
