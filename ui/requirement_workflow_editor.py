"""Settings editor. Saving configuration never rewrites requirement records."""
from copy import deepcopy
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QComboBox, QPushButton, QDialogButtonBox, QMessageBox, QHeaderView,
    QListWidget, QListWidgetItem)
from tools.requirement_workflow import (DEFAULT_ROWS, GROUPS, load_workflow, load_nodes,
    save_nodes, save_statuses, configured_statuses, nodes_from_rows)


class WorkflowEvents(QObject):
    changed = pyqtSignal()


workflow_events = WorkflowEvents()


class WorkflowEditor(QDialog):
    status_mode = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('任务状态配置' if self.status_mode else '任务进度节点配置')
        self.resize(780, 580)
        layout = QVBoxLayout(self)
        note = QLabel(
            '这里维护任务状态列表，供需求详情下拉框和节点状态选择共同使用。\n'
            '业务分类用于首页统计和上线识别；历史需求的状态不会被批量修改。'
            if self.status_mode else
            '每行一个进度节点，按行顺序展示；点击节点直接切换到该行的默认状态。\n'
            '状态选项来自“任务状态配置”。关联状态用于判断当前进度，同一状态只能关联一个节点。\n'
            '新增、删除或排序节点不会改变任务状态列表。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 2 if self.status_mode else 3)
        self.table.setHorizontalHeaderLabels(['状态名称', '业务分类'] if self.status_mode else
                                             ['进度节点名称', '点击后的默认状态', '关联状态'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setMinimumSectionSize(44)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        for label, action in [('新增状态' if self.status_mode else '新增节点', lambda: self.add_row()), ('删除所选', self.remove_row),
                              ('上移', lambda: self.move(-1)), ('下移', lambda: self.move(1)),
                              ('恢复默认', self.reset_defaults)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存配置')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.fill(load_workflow() if self.status_mode else load_nodes())

    def fill(self, rows):
        self.table.setRowCount(0)
        for row in rows:
            self.add_row(row)

    def add_row(self, data=None):
        data = data or {'name': '', 'group': 'analysis', 'statuses': [], 'default_status': ''}
        row = self.table.rowCount()
        self.table.insertRow(row)
        name_item = QTableWidgetItem(data['name'])
        name_item.setData(Qt.ItemDataRole.UserRole, data.get('original_name', data['name']))
        self.table.setItem(row, 0, name_item)
        combo = QComboBox()
        combo.setStyleSheet('QComboBox { min-height: 24px; padding-top: 3px; padding-bottom: 3px; }')
        if self.status_mode:
            for key, label in GROUPS.items():
                combo.addItem(label, key)
            combo.setCurrentIndex(combo.findData(data['group']))
        else:
            for status in configured_statuses():
                combo.addItem(status, status)
            combo.setCurrentIndex(combo.findData(data['default_status']))
            button = QPushButton()
            button.setProperty('statuses', list(data['statuses']))
            self._update_members_button(button)
            button.clicked.connect(lambda _=False, b=button, c=combo: self._choose_members(b, c))
            combo.currentTextChanged.connect(lambda text, b=button: self._include_default(b, text))
            self.table.setCellWidget(row, 2, button)
        self.table.setCellWidget(row, 1, combo)

    def _update_members_button(self, button):
        members = button.property('statuses') or []
        button.setText(f'选择关联状态（{len(members)}）…')
        button.setToolTip('、'.join(members) or '尚未选择')

    def _include_default(self, button, text):
        members = list(button.property('statuses') or [])
        if text and text not in members:
            members.append(text)
        button.setProperty('statuses', members)
        self._update_members_button(button)

    def _choose_members(self, button, combo):
        dialog = QDialog(self)
        dialog.setWindowTitle('选择该节点关联的任务状态')
        dialog.resize(360, 420)
        layout = QVBoxLayout(dialog)
        choices = QListWidget()
        members = button.property('statuses') or []
        for status in configured_statuses():
            item = QListWidgetItem(status, choices)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if status in members else Qt.CheckState.Unchecked)
        layout.addWidget(choices)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确定')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = [choices.item(i).text() for i in range(choices.count())
                        if choices.item(i).checkState() == Qt.CheckState.Checked]
            button.setProperty('statuses', selected)
            if combo.currentText() not in selected:
                combo.setCurrentIndex(combo.findText(selected[0]) if selected else -1)
            self._update_members_button(button)

    def rows(self):
        result = []
        for i in range(self.table.rowCount()):
            row = {'name': self.table.item(i, 0).text()}
            if self.status_mode:
                row.update(group=self.table.cellWidget(i, 1).currentData(),
                           original_name=self.table.item(i, 0).data(Qt.ItemDataRole.UserRole))
            else:
                row.update(default_status=self.table.cellWidget(i, 1).currentText(),
                           statuses=list(self.table.cellWidget(i, 2).property('statuses') or []))
            result.append(row)
        return result

    def remove_row(self):
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())

    def move(self, delta):
        current = self.table.currentRow()
        target = current + delta
        if current < 0 or not 0 <= target < self.table.rowCount():
            return
        rows = self.rows()
        rows[current], rows[target] = rows[target], rows[current]
        self.fill(rows)
        self.table.setCurrentCell(target, 0)

    def reset_defaults(self):
        if QMessageBox.question(self, '恢复默认配置', '替换当前编辑内容为默认状态？点击保存后生效，已有需求不会被改写。') == QMessageBox.StandardButton.Yes:
            if self.status_mode:
                self.fill(deepcopy(DEFAULT_ROWS))
            else:
                available = set(configured_statuses())
                nodes = nodes_from_rows(DEFAULT_ROWS)
                for node in nodes:
                    node['statuses'] = [s for s in node['statuses'] if s in available]
                    if node['statuses'] and node['default_status'] not in node['statuses']:
                        node['default_status'] = node['statuses'][0]
                self.fill([node for node in nodes if node['statuses']])

    def save(self):
        try:
            rows = self.rows()
            if self.status_mode:
                renames = {row['original_name']: row['name'].strip() for row in rows if row['original_name']}
                save_statuses(rows, renames)
            else:
                save_nodes(rows)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, '配置未保存', str(error))
            return
        workflow_events.changed.emit()
        self.accept()


def open_workflow_editor(parent):
    return WorkflowEditor(parent).exec()


class StatusEditor(WorkflowEditor):
    status_mode = True


def open_status_editor(parent):
    return StatusEditor(parent).exec()
