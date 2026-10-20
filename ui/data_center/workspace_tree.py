"""Native connection catalog tree for the Data Center workspace."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QRegularExpression, QSize, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeView, QWidget

from tools.data_center.workspace_catalog import ConnectionSummary
from ui.theme_manager import ThemeManager


NODE_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1
CONNECTION_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 2
ENGINE_ROLE = int(Qt.ItemDataRole.UserRole) + 3
ENVIRONMENT_ROLE = int(Qt.ItemDataRole.UserRole) + 4
AMBIGUOUS_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 5
TREE_TARGET_ROLE = int(Qt.ItemDataRole.UserRole) + 6
TREE_NODE_ROLE = int(Qt.ItemDataRole.UserRole) + 7
TREE_CURSOR_ROLE = int(Qt.ItemDataRole.UserRole) + 8
TREE_GENERATION_ROLE = int(Qt.ItemDataRole.UserRole) + 9
TREE_LOADING_ROLE = int(Qt.ItemDataRole.UserRole) + 10
TREE_LOADED_ROLE = int(Qt.ItemDataRole.UserRole) + 11
TREE_STATE_ROLE = int(Qt.ItemDataRole.UserRole) + 12
TREE_CLOSING_ROLE = int(Qt.ItemDataRole.UserRole) + 13
TREE_REQUESTED_DATABASE_ROLE = int(Qt.ItemDataRole.UserRole) + 14
CONNECTION_SUBTITLE_ROLE = int(Qt.ItemDataRole.UserRole) + 15
ENVIRONMENT_LABEL_ROLE = int(Qt.ItemDataRole.UserRole) + 16
CONNECTION_OPEN_ROLE = int(Qt.ItemDataRole.UserRole) + 17
SEARCH_TEXT_ROLE = int(Qt.ItemDataRole.UserRole) + 18
SECTION_COUNT_ROLE = int(Qt.ItemDataRole.UserRole) + 19

_TREE_ITEM_LIMIT = 2048
_TREE_PAGE_LIMIT = 50


class _RecursiveFilterProxy(QSortFilterProxyModel):
    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:  # noqa: N802
        source = self.sourceModel()
        index = source.index(source_row, 0, source_parent)
        if not index.isValid():
            return False
        needle = self.filterRegularExpression().pattern().casefold()
        if not needle:
            return True
        expression = self.filterRegularExpression()
        for role in (Qt.ItemDataRole.DisplayRole, SEARCH_TEXT_ROLE):
            if expression.match(str(source.data(index, role) or "")).hasMatch():
                return True
        for child_row in range(source.rowCount(index)):
            if self.filterAcceptsRow(child_row, index):
                return True
        return False


# 引擎徽标：缩写 + 主题色 token（只用于展示）
_ENGINE_BADGES = {
    "oracle": ("Or", "DANGER"),
    "mysql": ("My", "CYAN"),
    "oceanbase": ("OB", "PRIMARY"),
    "dameng": ("达", "WARNING"),
    "redis": ("Re", "DANGER"),
    "mongodb": ("Mo", "SUCCESS"),
}

# 自定义环境名（如「模拟」「联调」）按关键字取色；规范环境键仍以 environment_key 为准
_ENVIRONMENT_HINTS = (
    (("生产", "prod", "prd", "正式"), "production"),
    (("模拟", "预发", "准生产", "uat", "pre", "stag"), "staging"),
    (("测试", "联调", "集成", "sit", "test", "qa"), "test"),
    (("开发", "dev"), "development"),
    (("本地", "local"), "local"),
)


def _environment_visual_key(environment_key: str, raw: str) -> str:
    if environment_key and environment_key != "unknown":
        return environment_key
    lowered = str(raw or "").casefold()
    for keywords, key in _ENVIRONMENT_HINTS:
        if any(word in lowered for word in keywords):
            return key
    return "unknown"


class _CatalogRowDelegate(QStyledItemDelegate):
    """连接行：左侧环境色条 + 引擎徽标 + 名称/主机两行 + 右侧环境标签与已打开圆点。"""

    _PAD = 6

    def sizeHint(self, option, index):  # noqa: N802
        size = super().sizeHint(option, index)
        kind = index.data(NODE_KIND_ROLE)
        metrics = option.fontMetrics
        if kind == "engine":
            return QSize(size.width(), max(size.height(), metrics.height() + 16))
        if kind != "connection":
            return size
        return QSize(size.width(), max(size.height(), metrics.height() * 2 + 14))

    def _paint_engine_section(self, painter, option, index) -> None:
        theme = ThemeManager.instance()
        token = lambda name: QColor(theme.token(name))  # noqa: E731
        rect = QRectF(option.rect).adjusted(1, 0, -2, 0)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if index.row() > 0:
            painter.setPen(token("BORDER"))
            painter.drawLine(int(rect.left()), int(rect.top()) + 1, int(rect.right()), int(rect.top()) + 1)
        dialect = str(index.data(ENGINE_ROLE) or "").lower()
        _text, badge_token = _ENGINE_BADGES.get(dialect, ("", "TEXT_MUTED"))
        color = token(badge_token)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(rect.left() + 2, rect.center().y() - 4, 8, 8), 2, 2)
        font = QFont(option.font)
        font.setBold(True)
        metrics = QFontMetrics(font)
        label = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        left = rect.left() + 16
        painter.setFont(font)
        painter.setPen(token("TEXT_STRONG"))
        text_rect = QRectF(left, rect.top(), max(0.0, rect.width() - 16), rect.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        count = index.data(SECTION_COUNT_ROLE)
        if count:
            count_font = QFont(option.font)
            painter.setFont(count_font)
            painter.setPen(token("TEXT_MUTED"))
            painter.drawText(
                text_rect.adjusted(metrics.horizontalAdvance(label) + 8, 0, 0, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(count),
            )
        painter.restore()

    def paint(self, painter, option, index):  # noqa: N802
        kind = index.data(NODE_KIND_ROLE)
        if kind == "engine":
            self._paint_engine_section(painter, option, index)
            return
        if kind != "connection":
            super().paint(painter, option, index)
            return
        theme = ThemeManager.instance()
        token = lambda name: QColor(theme.token(name))  # noqa: E731
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        rect = QRectF(opt.rect).adjusted(1, 2, -2, -2)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hovered = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        env_key = str(index.data(ENVIRONMENT_ROLE) or "unknown")
        env_color = token(WorkspaceTree.environment_token(env_key))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        if selected:
            painter.setBrush(token("PRIMARY_SOFT"))
            painter.drawRoundedRect(rect, 6, 6)
        elif hovered:
            painter.setBrush(token("SURFACE_SOFT"))
            painter.drawRoundedRect(rect, 6, 6)
        # 环境色条
        painter.setBrush(env_color)
        painter.drawRoundedRect(QRectF(rect.left() + 1, rect.top() + 4, 3, rect.height() - 8), 1.5, 1.5)

        # 引擎徽标
        dialect = str(index.data(ENGINE_ROLE) or "").lower()
        badge_text, badge_token = _ENGINE_BADGES.get(dialect, ((dialect[:2] or "?").title(), "TEXT_MUTED"))
        badge_color = token(badge_token)
        side = min(24.0, rect.height() - 8)
        badge = QRectF(rect.left() + 9, rect.center().y() - side / 2, side, side)
        fill = QColor(badge_color)
        fill.setAlpha(36)
        painter.setBrush(fill)
        painter.drawRoundedRect(badge, 5, 5)
        base_size = opt.font.pointSizeF() if opt.font.pointSizeF() > 0 else 9.0
        badge_font = QFont(opt.font)
        badge_font.setBold(True)
        badge_font.setPointSizeF(max(6.5, base_size * 0.78))
        painter.setFont(badge_font)
        painter.setPen(badge_color)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, badge_text)

        # 右侧：环境标签 + 已打开圆点
        right = rect.right() - self._PAD
        env_label = str(index.data(ENVIRONMENT_LABEL_ROLE) or "")
        tag_font = QFont(opt.font)
        tag_font.setPointSizeF(max(6.5, base_size * 0.8))
        tag_metrics = QFontMetrics(tag_font)
        if env_label and rect.width() > 150:
            label = tag_metrics.elidedText(env_label, Qt.TextElideMode.ElideRight, 64)
            width = tag_metrics.horizontalAdvance(label) + 12
            height = tag_metrics.height() + 4
            tag = QRectF(right - width, rect.center().y() - height / 2, width, height)
            tag_fill = QColor(env_color)
            tag_fill.setAlpha(30)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tag_fill)
            painter.drawRoundedRect(tag, height / 2, height / 2)
            painter.setFont(tag_font)
            painter.setPen(env_color)
            painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)
            right = tag.left() - 6
        if index.data(CONNECTION_OPEN_ROLE):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(token("SUCCESS"))
            painter.drawEllipse(QRectF(right - 7, rect.center().y() - 3.5, 7, 7))
            right -= 13

        # 名称（粗体）+ 主机 · 库名（小字）
        left = badge.right() + 8
        width = max(0.0, right - left)
        name_font = QFont(opt.font)
        name_font.setBold(True)
        name_metrics = QFontMetrics(name_font)
        sub_font = QFont(opt.font)
        sub_font.setPointSizeF(max(6.5, base_size * 0.85))
        sub_metrics = QFontMetrics(sub_font)
        subtitle = str(index.data(CONNECTION_SUBTITLE_ROLE) or "")
        total = name_metrics.height() + (sub_metrics.height() if subtitle else 0)
        top = rect.center().y() - total / 2
        name = name_metrics.elidedText(str(opt.text or ""), Qt.TextElideMode.ElideRight, int(width))
        painter.setFont(name_font)
        painter.setPen(token("TEXT_STRONG" if enabled else "DISABLED_TEXT"))
        painter.drawText(QRectF(left, top, width, name_metrics.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        if subtitle:
            painter.setFont(sub_font)
            painter.setPen(token("TEXT_MUTED" if enabled else "DISABLED_TEXT"))
            painter.drawText(
                QRectF(left, top + name_metrics.height(), width, sub_metrics.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                sub_metrics.elidedText(subtitle, Qt.TextElideMode.ElideMiddle, int(width)),
            )
        painter.restore()


class WorkspaceTree(QTreeView):
    """One tree for project groups and connections (environment/engine shown on each row).

    Only display labels and connection IDs enter the Qt model. Connection
    profiles, passwords, tokens, and vendor fields remain in WorkspaceCatalog.
    """

    connection_selected = pyqtSignal(str)
    connection_activated = pyqtSignal(str)
    connection_ambiguous = pyqtSignal(str)
    directory_connection_expanded = pyqtSignal(str)
    directory_node_expanded = pyqtSignal(str, object, object, int)
    directory_more_requested = pyqtSignal(str, object, object, str, int)
    directory_node_activated = pyqtSignal(str, object, object, int)

    def __init__(self, *, language: str = "zh", parent: QWidget | None = None):
        super().__init__(parent)
        self.language = language
        self.setObjectName("data-center-connection-tree")
        self.setHeaderHidden(True)
        # 连接行是双行卡片，对象目录行是单行，行高不统一
        self.setUniformRowHeights(False)
        self.setMouseTracking(True)
        self.setItemDelegate(_CatalogRowDelegate(self))
        self.setExpandsOnDoubleClick(False)
        self.setIndentation(14)
        self.setAnimated(False)
        self.source_model = QStandardItemModel(self)
        self.proxy_model = _RecursiveFilterProxy(self)
        self.proxy_model.setSourceModel(self.source_model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setModel(self.proxy_model)
        self.selectionModel().currentChanged.connect(self._on_current_changed)
        self.doubleClicked.connect(self._on_double_clicked)
        self.expanded.connect(self._on_expanded)
        self._connection_indices: dict[str, object] = {}
        self._connection_items: dict[str, QStandardItem] = {}
        self._suppress_directory_expansion = False

    def set_language(self, language: str) -> None:
        self.language = "en" if str(language).lower().startswith("en") else "zh"

    def set_filter_text(self, text: str) -> None:
        escaped = str(text or "").strip()
        expression = QRegularExpression.escape(escaped) if escaped else ""
        self.proxy_model.setFilterRegularExpression(expression)
        self._expand_visible_paths()

    def set_connections(
        self,
        summaries: tuple[ConnectionSummary, ...] | list[ConnectionSummary],
        *,
        ambiguous_ids: tuple[str, ...] | list[str] = (),
        record_errors: tuple[str, ...] | list[str] = (),
        open_connection_ids: tuple[str, ...] | set[str] = (),
    ) -> None:
        selected = self.selected_connection_id()
        ambiguous = {str(value) for value in ambiguous_ids}
        preserved: dict[str, tuple[dict[int, object], list[list[QStandardItem]]]] = {}
        expanded_connections: set[str] = set()
        expanded_nodes: set[tuple[str, str]] = set()
        for connection_id, old_item in tuple(self._connection_items.items()):
            proxy_index = self.proxy_model.mapFromSource(old_item.index())
            if proxy_index.isValid() and self.isExpanded(proxy_index):
                expanded_connections.add(connection_id)
            self._collect_expanded_nodes(old_item, connection_id, expanded_nodes)
            if connection_id in ambiguous:
                continue
            roles = {
                role: old_item.data(role)
                for role in (
                    TREE_TARGET_ROLE,
                    TREE_NODE_ROLE,
                    TREE_GENERATION_ROLE,
                    TREE_LOADING_ROLE,
                    TREE_LOADED_ROLE,
                    TREE_CLOSING_ROLE,
                    TREE_REQUESTED_DATABASE_ROLE,
                )
            }
            rows = []
            while old_item.rowCount():
                rows.append(old_item.takeRow(0))
            preserved[connection_id] = (roles, rows)

        self._suppress_directory_expansion = True
        self.source_model.clear()
        self.source_model.setHorizontalHeaderLabels(["Connections"])
        self._connection_indices.clear()
        self._connection_items.clear()
        path_nodes: dict[tuple[str, ...], QStandardItem] = {(): self.source_model.invisibleRootItem()}
        # 同类型数据库排在一起（按引擎清单顺序，未知类型靠后），类型内保持原顺序
        engine_order = {key: rank for rank, key in enumerate(_ENGINE_BADGES)}
        summaries = sorted(summaries, key=lambda item: engine_order.get(item.dialect, len(engine_order)))
        for summary in summaries:
            path: tuple[str, ...] = (f"engine:{summary.dialect}",)
            if path not in path_nodes:
                section = self._folder(summary.engine_label, "engine")
                section.setData(summary.dialect, ENGINE_ROLE)
                section.setData(0, SECTION_COUNT_ROLE)
                path_nodes[()].appendRow(section)
                path_nodes[path] = section
            section = path_nodes[path]
            section.setData(int(section.data(SECTION_COUNT_ROLE) or 0) + 1, SECTION_COUNT_ROLE)
            for segment in summary.group_path or ():
                path += (f"group:{segment}",)
                if path not in path_nodes:
                    node = self._folder(segment, "group")
                    parent_key = path[:-1]
                    path_nodes[parent_key].appendRow(node)
                    path_nodes[path] = node

            favorite_mark = "★ " if summary.favorite else ""
            is_open = summary.connection_id in open_connection_ids
            visual_env = _environment_visual_key(summary.environment_key, summary.environment_raw)
            env_tag = (
                self._environment_label(summary.environment_key)
                if summary.environment_key != "unknown" else str(summary.environment_raw or "")
            )
            subtitle = " · ".join(part for part in (summary.host, summary.database) if part) or summary.engine_label
            connection = QStandardItem(f"{favorite_mark}{summary.name}")
            connection.setEditable(False)
            connection.setData("connection", NODE_KIND_ROLE)
            connection.setData(summary.connection_id, CONNECTION_ID_ROLE)
            connection.setData(summary.dialect, ENGINE_ROLE)
            connection.setData(visual_env, ENVIRONMENT_ROLE)
            connection.setData(subtitle, CONNECTION_SUBTITLE_ROLE)
            connection.setData(env_tag, ENVIRONMENT_LABEL_ROLE)
            connection.setData(is_open, CONNECTION_OPEN_ROLE)
            connection.setData(
                " ".join((summary.engine_label, env_tag, summary.host, summary.database)),
                SEARCH_TEXT_ROLE,
            )
            tooltip = self._connection_tooltip(summary)
            if is_open:
                tooltip += " · " + ("工作区已打开" if self.language == "zh" else "Workspace open")
            connection.setToolTip(tooltip)
            path_nodes[path].appendRow(connection)
            self._connection_indices[summary.connection_id] = connection.index()
            self._connection_items[summary.connection_id] = connection
            prior = preserved.get(summary.connection_id)
            if prior is not None:
                roles, child_rows = prior
                for role, value in roles.items():
                    if value is not None:
                        connection.setData(value, role)
                for row in child_rows:
                    connection.appendRow(row)
            if connection.rowCount() == 0:
                connection.appendRow(self._state_item(
                    "展开以浏览对象目录" if self.language == "zh" else "Expand to browse the object directory",
                    "expand",
                ))

        if ambiguous_ids or record_errors:
            warning_title = "需要处理的连接记录" if self.language == "zh" else "Connection records to review"
            warning = self._folder(warning_title, "warnings")
            self.source_model.invisibleRootItem().appendRow(warning)
            for connection_id in ambiguous_ids:
                message = (
                    f"重复 ID：{connection_id}" if self.language == "zh" else
                    f"Duplicate ID: {connection_id}"
                )
                item = QStandardItem(message)
                item.setEditable(False)
                item.setData("ambiguous", NODE_KIND_ROLE)
                item.setData(str(connection_id), AMBIGUOUS_ID_ROLE)
                item.setToolTip(
                    "多个连接记录使用相同 ID；为避免目标重定向，此项不可打开。"
                    if self.language == "zh" else
                    "Multiple connection records share this ID. Opening is disabled to prevent target redirection."
                )
                item.setForeground(self._environment_color("unknown"))
                warning.appendRow(item)
            for error in record_errors:
                item = QStandardItem("⚠ " + str(error))
                item.setEditable(False)
                item.setData("invalid-record", NODE_KIND_ROLE)
                item.setForeground(self._environment_color("unknown"))
                warning.appendRow(item)

        try:
            self._expand_static_folders()
            for connection_id in expanded_connections:
                item = self._connection_items.get(connection_id)
                if item is not None and item.data(TREE_TARGET_ROLE) is not None and bool(
                    item.data(TREE_LOADED_ROLE)
                ):
                    self.expand(self.proxy_model.mapFromSource(item.index()))
            for connection_id, node_ref in expanded_nodes:
                item = self._find_directory_item_by_ref(connection_id, node_ref)
                if item is not None and bool(item.data(TREE_LOADED_ROLE)):
                    self.expand(self.proxy_model.mapFromSource(item.index()))
            self._expand_visible_paths()
            if selected:
                self.select_connection(selected)
        finally:
            self._suppress_directory_expansion = False

    def selected_connection_id(self) -> str:
        index = self.currentIndex()
        if not index.isValid():
            return ""
        source_index = self.proxy_model.mapToSource(index)
        item = self.source_model.itemFromIndex(source_index)
        return self._connection_id_for_item(item)

    def select_connection(self, connection_id: str) -> bool:
        source_index = self._connection_indices.get(str(connection_id or ""))
        if source_index is None or not source_index.isValid():
            return False
        proxy_index = self.proxy_model.mapFromSource(source_index)
        if not proxy_index.isValid():
            return False
        self.setCurrentIndex(proxy_index)
        self.scrollTo(proxy_index)
        return True

    def connection_source_item(self, connection_id: str) -> QStandardItem | None:
        """Return the current source item for UI wiring and behavior tests."""
        return self._connection_items.get(str(connection_id or ""))

    def set_directory_root(self, connection_id: str, target, root, generation: int) -> bool:
        item = self._connection_items.get(str(connection_id or ""))
        if item is None or item.data(CONNECTION_ID_ROLE) != str(connection_id):
            return False
        old_target = item.data(TREE_TARGET_ROLE)
        if old_target != target:
            self._clear_children(item)
            item.setData(False, TREE_LOADED_ROLE)
        else:
            self._update_generation(item, target, int(generation))
        self._remove_state_rows(item)
        item.setData(target, TREE_TARGET_ROLE)
        item.setData(root, TREE_NODE_ROLE)
        item.setData(int(generation), TREE_GENERATION_ROLE)
        item.setData(False, TREE_LOADING_ROLE)
        item.setData(bool(item.data(TREE_LOADED_ROLE)), TREE_LOADED_ROLE)
        item.setData(False, TREE_CLOSING_ROLE)
        return True

    def set_directory_loading(self, connection_id: str, target, parent_node, generation: int, loading: bool) -> bool:
        item = self._find_directory_parent(connection_id, target, parent_node)
        if item is None or int(item.data(TREE_GENERATION_ROLE) or 0) != int(generation):
            return False
        item.setData(bool(loading), TREE_LOADING_ROLE)
        if loading and not bool(item.data(TREE_LOADED_ROLE)) and item.rowCount() == 0:
            label = "加载中…" if self.language == "zh" else "Loading…"
            item.appendRow(self._state_item(label, "loading"))
        elif not loading:
            self._remove_state_rows(item, {"loading"})
        return True

    def set_directory_page(self, connection_id: str, page, request_cursor: str | None) -> bool:
        target = getattr(page, "target", None)
        parent_ref = str(getattr(page, "parent_ref", "") or "")
        generation = getattr(page, "generation", None)
        parent_item = self._find_directory_parent_by_ref(connection_id, target, parent_ref)
        if parent_item is None or int(parent_item.data(TREE_GENERATION_ROLE) or 0) != generation:
            return False
        parent_node = parent_item.data(TREE_NODE_ROLE)
        if getattr(parent_node, "node_ref", None) != parent_ref:
            return False
        nodes = tuple(getattr(page, "nodes", ()) or ())
        old_descendants = self._count_signed_descendants(parent_item)
        owner_root = self._connection_items.get(str(connection_id or ""))
        current_count = self._count_signed_nodes(owner_root) if owner_root is not None else 0
        if (
            len(nodes) > _TREE_PAGE_LIMIT
            or current_count - old_descendants + len(nodes) > _TREE_ITEM_LIMIT
        ):
            self.set_directory_status(
                connection_id,
                target,
                parent_ref,
                "目录项过多，当前页面已保留；请缩小范围或刷新后重试。"
                if self.language == "zh" else
                "There are too many directory items. The current page was kept; narrow the scope or refresh and try again.",
            )
            parent_item.setData(False, TREE_LOADING_ROLE)
            return False

        self._clear_children(parent_item)
        parent_item.setData(False, TREE_LOADING_ROLE)
        parent_item.setData(True, TREE_LOADED_ROLE)
        for node in nodes:
            child = QStandardItem(self._node_label(node))
            child.setEditable(False)
            child.setData(str(node.kind), NODE_KIND_ROLE)
            child.setData(target, TREE_TARGET_ROLE)
            child.setData(node, TREE_NODE_ROLE)
            child.setData(int(generation), TREE_GENERATION_ROLE)
            child.setData(False, TREE_LOADING_ROLE)
            child.setData(False, TREE_LOADED_ROLE)
            child.setToolTip(self._node_tooltip(node))
            if not bool(getattr(node, "actionable", False)):
                child.setForeground(QColor(ThemeManager.instance().token("TEXT_MUTED")))
            if self._is_expandable_kind(str(node.kind)):
                child.appendRow(QStandardItem(""))
            parent_item.appendRow(child)

        next_cursor = getattr(page, "next_cursor", None)
        if isinstance(next_cursor, str) and next_cursor:
            more = self._state_item(
                "更多…" if self.language == "zh" else "More…",
                "more",
            )
            more.setData(target, TREE_TARGET_ROLE)
            more.setData(parent_node, TREE_NODE_ROLE)
            more.setData(next_cursor, TREE_CURSOR_ROLE)
            more.setData(int(generation), TREE_GENERATION_ROLE)
            more.appendRow(QStandardItem(""))
            parent_item.appendRow(more)

        capability_notice = str(getattr(page, "capability_notice", "") or "")
        if not nodes and not next_cursor:
            empty = self._capability_notice(page) or (self._notice_text(capability_notice) if capability_notice else "") or (
                "没有可显示的目录项。" if self.language == "zh" else "No directory items to display."
            )
            parent_item.appendRow(self._state_item(empty, "empty"))
        return True

    def set_directory_details(self, connection_id: str, details) -> bool:
        target = getattr(details, "target", None)
        node_ref = str(getattr(details, "node_ref", "") or "")
        item = self._find_directory_item(connection_id, target, node_ref)
        if item is None or int(item.data(TREE_GENERATION_ROLE) or 0) != int(details.generation):
            return False
        parent_node = item.data(TREE_NODE_ROLE)
        if getattr(parent_node, "kind", "") != "redis_key":
            return False
        seconds = int(details.ttl_seconds)
        if seconds == -1:
            ttl = "无过期时间" if self.language == "zh" else "No expiration"
        elif seconds == -2:
            ttl = "键已不存在" if self.language == "zh" else "Key no longer exists"
        else:
            ttl = f"TTL {seconds}s"
        detail = (
            f"类型：{details.value_type} · {ttl}" if self.language == "zh" else
            f"Type: {details.value_type} · {ttl}"
        )
        item.setToolTip(self._node_tooltip(parent_node, extra_lines=(detail,)))
        self._remove_state_rows(item, {"detail"})
        item.appendRow(self._state_item(item.toolTip(), "detail"))
        return True

    def set_directory_status(self, connection_id: str, target, parent_ref: str, message: str) -> bool:
        parent_item = (
            self._find_directory_parent_by_ref(connection_id, target, parent_ref)
            if parent_ref else self._connection_items.get(str(connection_id or ""))
        )
        if parent_item is None:
            return False
        self._remove_state_rows(parent_item, {"loading", "error"})
        if message:
            parent_item.appendRow(self._state_item(str(message), "error"))
        parent_item.setData(False, TREE_LOADING_ROLE)
        return True

    def set_connection_status(self, connection_id: str, message: str, *, state: str = "notice") -> bool:
        item = self._connection_items.get(str(connection_id or ""))
        if item is None:
            return False
        self._remove_state_rows(item, {"loading", "error", "notice"})
        if message:
            item.appendRow(self._state_item(str(message), state))
        return True

    def set_directory_closing(self, connection_id: str, closing: bool) -> None:
        item = self._connection_items.get(str(connection_id or ""))
        if item is None:
            return
        item.setData(bool(closing), TREE_CLOSING_ROLE)
        for row in range(item.rowCount()):
            self._set_subtree_enabled(item.child(row), not bool(closing))

    def clear_directory(self, connection_id: str) -> None:
        item = self._connection_items.get(str(connection_id or ""))
        if item is None:
            return
        self._clear_children(item)
        for role in (
            TREE_TARGET_ROLE,
            TREE_NODE_ROLE,
            TREE_GENERATION_ROLE,
            TREE_LOADING_ROLE,
            TREE_LOADED_ROLE,
            TREE_CLOSING_ROLE,
            TREE_REQUESTED_DATABASE_ROLE,
        ):
            item.setData(None, role)
        item.appendRow(self._state_item(
            "展开以浏览对象目录" if self.language == "zh" else "Expand to browse the object directory",
            "expand",
        ))

    def _on_expanded(self, proxy_index) -> None:
        if self._suppress_directory_expansion or not proxy_index.isValid():
            return
        source_index = self.proxy_model.mapToSource(proxy_index)
        item = self.source_model.itemFromIndex(source_index)
        if item is None:
            return
        connection_id = self._connection_id_for_item(item)
        if not connection_id:
            return
        connection_item = self._connection_items.get(connection_id)
        if connection_item is None or bool(connection_item.data(TREE_CLOSING_ROLE)):
            return
        kind = str(item.data(NODE_KIND_ROLE) or "")
        state = str(item.data(TREE_STATE_ROLE) or "")
        if kind == "connection":
            self.directory_connection_expanded.emit(connection_id)
            return
        if kind == "tree-state" and state == "more":
            parent = item.data(TREE_NODE_ROLE)
            target = item.data(TREE_TARGET_ROLE)
            cursor = item.data(TREE_CURSOR_ROLE)
            generation = int(item.data(TREE_GENERATION_ROLE) or 0)
            parent_item = self._find_directory_parent(connection_id, target, parent)
            if (
                parent_item is not None
                and target is not None
                and cursor
                and not bool(parent_item.data(TREE_LOADING_ROLE))
            ):
                self.directory_more_requested.emit(connection_id, target, parent, str(cursor), generation)
            return
        target = item.data(TREE_TARGET_ROLE)
        node = item.data(TREE_NODE_ROLE)
        generation = int(item.data(TREE_GENERATION_ROLE) or 0)
        if (
            target is not None
            and node is not None
            and bool(getattr(node, "actionable", False))
            and self._is_expandable_kind(str(getattr(node, "kind", "")))
            and not bool(item.data(TREE_LOADED_ROLE))
            and not bool(item.data(TREE_LOADING_ROLE))
        ):
            item.setData(True, TREE_LOADING_ROLE)
            self.directory_node_expanded.emit(connection_id, target, node, generation)

    def _on_double_clicked(self, index) -> None:
        source = self.proxy_model.mapToSource(index) if index.isValid() else index
        item = self.source_model.itemFromIndex(source) if source.isValid() else None
        connection_id = self._connection_id_for_item(item) if item is not None else ""
        if not connection_id:
            return
        if str(item.data(NODE_KIND_ROLE) or "") == "connection":
            self.connection_activated.emit(connection_id)
            return
        target = item.data(TREE_TARGET_ROLE)
        node = item.data(TREE_NODE_ROLE)
        if (
            target is not None
            and node is not None
            and str(item.data(TREE_STATE_ROLE) or "") != "more"
            and bool(getattr(node, "actionable", False))
            and not bool(self._connection_items.get(connection_id).data(TREE_CLOSING_ROLE))
        ):
            self.directory_node_activated.emit(
                connection_id,
                target,
                node,
                int(item.data(TREE_GENERATION_ROLE) or 0),
            )

    def _find_directory_parent(self, connection_id: str, target, node) -> QStandardItem | None:
        if node is None:
            return None
        item = self._find_directory_item(connection_id, target, str(getattr(node, "node_ref", "") or ""))
        if item is None or item.data(TREE_NODE_ROLE) != node:
            return None
        return item

    def _find_directory_parent_by_ref(self, connection_id: str, target, parent_ref: str) -> QStandardItem | None:
        if not parent_ref:
            return None
        return self._find_directory_item(connection_id, target, parent_ref)

    def _find_directory_item(self, connection_id: str, target, node_ref: str) -> QStandardItem | None:
        root = self._connection_items.get(str(connection_id or ""))
        if root is None:
            return None
        if root.data(TREE_TARGET_ROLE) == target and getattr(root.data(TREE_NODE_ROLE), "node_ref", None) == node_ref:
            return root
        return self._find_descendant(root, target, node_ref)

    def _find_directory_item_by_ref(self, connection_id: str, node_ref: str) -> QStandardItem | None:
        root = self._connection_items.get(str(connection_id or ""))
        if root is None:
            return None
        if getattr(root.data(TREE_NODE_ROLE), "node_ref", None) == node_ref:
            return root
        return self._find_descendant_by_ref(root, node_ref)

    def _find_descendant_by_ref(self, parent: QStandardItem, node_ref: str) -> QStandardItem | None:
        for row in range(parent.rowCount()):
            child = parent.child(row)
            if getattr(child.data(TREE_NODE_ROLE), "node_ref", None) == node_ref:
                return child
            nested = self._find_descendant_by_ref(child, node_ref)
            if nested is not None:
                return nested
        return None

    def _collect_expanded_nodes(
        self, parent: QStandardItem, connection_id: str, expanded: set[tuple[str, str]]
    ) -> None:
        for row in range(parent.rowCount()):
            child = parent.child(row)
            target = child.data(TREE_TARGET_ROLE)
            node = child.data(TREE_NODE_ROLE)
            proxy_index = self.proxy_model.mapFromSource(child.index())
            if (
                target is not None
                and node is not None
                and str(child.data(NODE_KIND_ROLE) or "") != "more"
                and bool(child.data(TREE_LOADED_ROLE))
                and proxy_index.isValid()
                and self.isExpanded(proxy_index)
            ):
                expanded.add((connection_id, str(getattr(node, "node_ref", "") or "")))
            self._collect_expanded_nodes(child, connection_id, expanded)

    def _expand_static_folders(self) -> None:
        stack = [self.source_model.invisibleRootItem()]
        while stack:
            parent = stack.pop()
            for row in range(parent.rowCount()):
                item = parent.child(row)
                if str(item.data(NODE_KIND_ROLE) or "") in {"group", "environment", "engine", "warnings"}:
                    index = self.proxy_model.mapFromSource(item.index())
                    if index.isValid():
                        self.expand(index)
                stack.append(item)

    def _expand_visible_paths(self) -> None:
        previous_suppression = self._suppress_directory_expansion
        self._suppress_directory_expansion = True
        try:
            if not self.proxy_model.filterRegularExpression().pattern():
                self._expand_static_folders()
                return
            visible: list[QStandardItem] = []
            stack = [self.source_model.invisibleRootItem()]
            while stack:
                parent = stack.pop()
                for row in range(parent.rowCount()):
                    item = parent.child(row)
                    proxy_index = self.proxy_model.mapFromSource(item.index())
                    if proxy_index.isValid():
                        visible.append(item)
                        stack.append(item)
            for item in visible:
                source = item
                while source is not None:
                    index = self.proxy_model.mapFromSource(source.index())
                    if index.isValid():
                        kind = str(source.data(NODE_KIND_ROLE) or "")
                        if kind in {"group", "environment", "engine", "warnings"}:
                            self.expand(index)
                        elif kind == "connection":
                            if source.data(TREE_TARGET_ROLE) is not None and bool(source.data(TREE_LOADED_ROLE)):
                                self.expand(index)
                        elif source.data(TREE_NODE_ROLE) is not None and bool(source.data(TREE_LOADED_ROLE)):
                            self.expand(index)
                    source = source.parent()
        finally:
            self._suppress_directory_expansion = previous_suppression

    def _find_descendant(self, parent: QStandardItem, target, node_ref: str) -> QStandardItem | None:
        for row in range(parent.rowCount()):
            child = parent.child(row)
            if child.data(TREE_TARGET_ROLE) == target and getattr(child.data(TREE_NODE_ROLE), "node_ref", None) == node_ref:
                return child
            nested = self._find_descendant(child, target, node_ref)
            if nested is not None:
                return nested
        return None

    def _update_generation(self, parent: QStandardItem, target, generation: int) -> None:
        for row in range(parent.rowCount()):
            child = parent.child(row)
            if child.data(TREE_TARGET_ROLE) == target and child.data(TREE_NODE_ROLE) is not None:
                child.setData(int(generation), TREE_GENERATION_ROLE)
            self._update_generation(child, target, generation)

    def _connection_id_for_item(self, item: QStandardItem | None) -> str:
        current = item
        while current is not None:
            connection_id = str(current.data(CONNECTION_ID_ROLE) or "")
            if connection_id:
                return connection_id
            current = current.parent()
        return ""

    def _state_item(self, label: str, state: str) -> QStandardItem:
        item = QStandardItem(str(label))
        item.setEditable(False)
        item.setData("tree-state", NODE_KIND_ROLE)
        item.setData(str(state), TREE_STATE_ROLE)
        item.setForeground(QColor(ThemeManager.instance().token("TEXT_MUTED")))
        return item

    @staticmethod
    def _remove_state_rows(parent: QStandardItem, states: set[str] | None = None) -> None:
        wanted = states
        for row in range(parent.rowCount() - 1, -1, -1):
            child = parent.child(row)
            if str(child.data(NODE_KIND_ROLE) or "") != "tree-state":
                continue
            if wanted is None or str(child.data(TREE_STATE_ROLE) or "") in wanted:
                parent.removeRow(row)

    @staticmethod
    def _clear_children(parent: QStandardItem) -> None:
        while parent.rowCount():
            parent.removeRow(parent.rowCount() - 1)

    @classmethod
    def _count_signed_nodes(cls, parent: QStandardItem | None) -> int:
        if parent is None:
            return 0
        count = 1 if parent.data(TREE_NODE_ROLE) is not None else 0
        return count + cls._count_signed_descendants(parent)

    @classmethod
    def _count_signed_descendants(cls, parent: QStandardItem) -> int:
        count = 0
        for row in range(parent.rowCount()):
            child = parent.child(row)
            kind = str(child.data(NODE_KIND_ROLE) or "")
            if child.data(TREE_NODE_ROLE) is not None and kind not in {"more", "tree-state"}:
                count += 1
            count += cls._count_signed_descendants(child)
        return count

    @classmethod
    def _set_subtree_enabled(cls, item: QStandardItem, enabled: bool) -> None:
        item.setEnabled(bool(enabled))
        for row in range(item.rowCount()):
            cls._set_subtree_enabled(item.child(row), enabled)

    @staticmethod
    def _is_expandable_kind(kind: str) -> bool:
        return kind in {"root", "database", "schema", "mongo_database"}

    def _node_label(self, node) -> str:
        name = str(getattr(node, "name", "") or "")
        labels = {
            "database": ("数据库", "Database"),
            "schema": ("模式", "Schema"),
            "table": ("表", "Table"),
            "view": ("视图", "View"),
            "redis_key": ("键", "Key"),
            "mongo_database": ("数据库", "Database"),
            "mongo_collection": ("集合", "Collection"),
        }
        pair = labels.get(str(getattr(node, "kind", "")))
        if pair is None:
            return name
        prefix = pair[0 if self.language == "zh" else 1]
        return f"{prefix} · {name}"

    def _node_tooltip(self, node, *, extra_lines: tuple[str, ...] = ()) -> str:
        """Keep the full signed name available when the display label is elided."""
        name = str(getattr(node, "name", "") or "")
        lines = [name] if name else []
        lines.extend(str(line) for line in extra_lines if str(line))
        notice = self._notice_text(str(getattr(node, "notice", "") or ""))
        if notice:
            lines.append(notice)
        return "\n".join(lines)

    def _capability_notice(self, page) -> str:
        codes = set(str(value) for value in tuple(getattr(page, "notices", ()) or ()))
        notices = {
            "dameng_directory_unavailable_readonly_connector_not_wired": (
                "此达梦连接暂不支持对象目录浏览。",
                "Object-directory browsing is unavailable for this Dameng connection.",
            ),
            "provider_directory_unavailable": (
                "此连接类型暂不支持对象目录浏览。",
                "Object-directory browsing is unavailable for this connection type.",
            ),
            "tree_node_registry_budget_exhausted": (
                "目录项过多，当前页面已保留；请缩小范围或刷新后重试。",
                "There are too many directory items. The current page was kept; narrow the scope or refresh and try again.",
            ),
            "tree_cursor_registry_budget_exhausted": (
                "无法继续分页，请刷新目录后重试。",
                "Further pages are unavailable. Refresh the directory and try again.",
            ),
            "directory_identity_unavailable": (
                "此目录项没有可用的读取信息。",
                "This directory item has no readable identity.",
            ),
            "directory_identity_over_budget": (
                "此目录项超出可读取范围。",
                "This directory item exceeds the supported read scope.",
            ),
            "directory_identity_unrepresentable": (
                "此目录项无法安全读取。",
                "This directory item cannot be read safely.",
            ),
        }
        for code in notices:
            if code in codes:
                return notices[code][0 if self.language == "zh" else 1]
        return ""

    def _notice_text(self, code: str) -> str:
        """Translate known backend capability codes without exposing internals."""
        if not code:
            return ""
        notices = {
            "directory_identity_unavailable": (
                "此目录项没有可用的读取信息。",
                "This directory item has no readable identity.",
            ),
            "directory_identity_over_budget": (
                "此目录项超出可读取范围。",
                "This directory item exceeds the supported read scope.",
            ),
            "directory_identity_unrepresentable": (
                "此目录项无法安全读取。",
                "This directory item cannot be read safely.",
            ),
            "directory_identity_missing": (
                "此目录项没有可用的读取信息。",
                "This directory item has no readable identity.",
            ),
            "directory_name_unavailable": (
                "此目录项名称不可用。",
                "This directory item has no available name.",
            ),
        }
        translated = notices.get(str(code))
        if translated:
            return translated[0 if self.language == "zh" else 1]
        return "此目录项暂不可读取。" if self.language == "zh" else "This directory item is unavailable."

    def select_first_engine(self, dialect: str) -> bool:
        wanted = str(dialect or "").strip().lower()
        for connection_id, source_index in self._connection_indices.items():
            if str(source_index.data(ENGINE_ROLE) or "").lower() == wanted:
                return self.select_connection(connection_id)
        return False

    def _folder(self, title: str, kind: str) -> QStandardItem:
        item = QStandardItem(str(title))
        item.setEditable(False)
        item.setData(kind, NODE_KIND_ROLE)
        item.setForeground(QColor(ThemeManager.instance().token("TEXT_MUTED")))
        return item

    @staticmethod
    def environment_token(environment_key: str) -> str:
        return {
            "production": "DANGER",
            "staging": "WARNING",
            "test": "CYAN",
            "development": "PRIMARY",
            "local": "SUCCESS",
        }.get(str(environment_key or ""), "BORDER_STRONG")

    @staticmethod
    def _environment_color(environment_key: str) -> QColor:
        token = WorkspaceTree.environment_token(environment_key)
        return QColor(ThemeManager.instance().token("TEXT_MUTED" if token == "BORDER_STRONG" else token))

    def _connection_tooltip(self, summary: ConnectionSummary) -> str:
        env_label = self._environment_label(summary.environment_key)
        if summary.environment_key == "unknown" and summary.environment_raw:
            env_label = f"{env_label}（{summary.environment_raw}）" if self.language == "zh" else (
                f"{env_label} ({summary.environment_raw})"
            )
        fields = [summary.engine_label, env_label]
        if summary.host:
            fields.append(summary.host)
        if summary.database:
            fields.append(summary.database)
        return " · ".join(fields)

    def _environment_label(self, key: str) -> str:
        values = {
            "local": ("本地", "Local"),
            "development": ("开发", "Development"),
            "test": ("测试", "Test"),
            "staging": ("预发布", "Staging"),
            "production": ("生产", "Production"),
            "unknown": ("未知环境", "Unknown environment"),
        }
        return values.get(str(key or "unknown"), values["unknown"])[0 if self.language == "zh" else 1]

    def _on_current_changed(self, current, _previous) -> None:
        kind = str(current.data(NODE_KIND_ROLE) or "")
        if kind == "ambiguous":
            self.connection_selected.emit("")
            self.connection_ambiguous.emit(str(current.data(AMBIGUOUS_ID_ROLE) or ""))
        else:
            source_index = self.proxy_model.mapToSource(current) if current.isValid() else current
            item = self.source_model.itemFromIndex(source_index) if source_index.isValid() else None
            self.connection_selected.emit(self._connection_id_for_item(item))

__all__ = [
    "WorkspaceTree", "NODE_KIND_ROLE", "CONNECTION_ID_ROLE", "ENGINE_ROLE", "ENVIRONMENT_ROLE",
    "AMBIGUOUS_ID_ROLE", "TREE_TARGET_ROLE", "TREE_NODE_ROLE", "TREE_CURSOR_ROLE",
    "TREE_GENERATION_ROLE", "TREE_LOADING_ROLE", "TREE_LOADED_ROLE", "TREE_STATE_ROLE",
    "TREE_CLOSING_ROLE", "TREE_REQUESTED_DATABASE_ROLE", "CONNECTION_SUBTITLE_ROLE",
    "ENVIRONMENT_LABEL_ROLE", "CONNECTION_OPEN_ROLE", "SEARCH_TEXT_ROLE", "SECTION_COUNT_ROLE",
]
