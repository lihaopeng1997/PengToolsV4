# PenngTools V4 · P0-2 样式核查交接（2026-08-27）

> **范围**：需求管理 → 文件库壳层 → 升级准备 → 接口文档更新 → 日报  
> **边界**：只做样式（间距 / 字号 / Token / 布局骨架 / 响应式）。未加功能、未加入口、未改按钮主次、未加 chip。  
> **流程**：本文件供你开软件核对；**通过后你再开下一批**，本文不预设下一步。

---

## 1. 本轮改动文件（样式相关）

| 文件 | 样式类改动摘要 |
|------|----------------|
| `ui/responsive.py` | `editor_min_height` `180→240`；`apply_splitter_orientation` 默认 `min_editor=240` |
| `panels/requirement_panel.py` | L3 `page-filter-bar`；左右 min=`REQ_LEFT_MIN/REQ_RIGHT_MIN`；页间距 Token；文件库行高 36；action-card 间距；`install_splitter_prefs`；窄屏 min 回退 |
| `panels/sql_panel.py` | 页/Tab 间距 Token；SQL 输入与预览编辑区 min 高 240；`apply_layout_mode` 同步间距与编辑区 |
| `panels/docx_panel.py` | 页间距 Token；主/编辑 splitter 挂名 + `install_splitter_prefs`；浏览器≥240 / 工作区≥520；SQL 编辑区≥240；窄屏堆叠 |
| `panels/personal_panel.py`（日报） | 页间距 Token；历史/编辑 splitter prefs；左≥240 / 右≥520；「今日完成」≥240；补 `apply_layout_mode` |
| `resources/style.qss` | 文件库树 `::item` min-height `22→36`、padding 对齐行高 |
| `tests/test_release_ui.py` | 文件库行高断言 `24→36`（对齐样式规格） |

**未改（刻意）**：九按钮文案/顺序/启用逻辑；需求/升级/文档/日报主次按钮角色；任何 chip 文案；业务保存/SVN/SQL 执行路径。

---

## 2. 按页样式 Diff（只列 QSS / Token / 间距 / 字号 / 布局 / 断点）

### 2.1 需求管理

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | 固定 `12` | `SPACING_PAGE=16`，`apply_layout_mode` → `page_spacing_for_mode`（wide/std 16 · compact 12 · narrow/low 10） |
| 筛选条 objectName | `req-filter-card` | `page-filter-bar`（L3 Token；旧选择器仍留兼容） |
| 左树 minWidth | `200` | `REQ_LEFT_MIN=260`（narrow 时临时 `240`） |
| 右详情 minWidth | `360` | `REQ_RIGHT_MIN=520`（narrow 时临时 `360`） |
| 左树卡片 padding | `10,10,10,10` | `12,10,12,12` |
| 详情 Tab minHeight | `200` | `240` |
| 分隔条 | 仅自存 `requirement_ui` | + `install_splitter_prefs`（page=`requirement`，min `[260,520]`，双击/方向键/夹紧） |

### 2.2 文件库壳层（同页 Tab，九按钮不动）

| 项 | 前 | 后 |
|----|----|----|
| action-card margins | `6,4,6,4` / spacing `4` | `8,6,8,6` / spacing `6` |
| 按钮行 spacing | `4` | `6` |
| 文件树行高 delegate | `24` | `TABLE_ROW_H=36` |
| QSS `requirement-file-tree::item` | `min-height:22px; padding:1px 6px` | `min-height:36px; padding:4px 8px` |
| 九按钮 | 顺序/文案/行为 | **未动** |

### 2.3 升级准备

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | `12` | `SPACING_PAGE` + `page_spacing_for_mode` |
| 升级/发版 Tab spacing | `10` | `SPACING_CARD=12` |
| `input_sql` minHeight | `120` | `editor_min_height()=240` |
| 预览三编辑器 minHeight | 无 | `240` |
| `apply_layout_mode` 编辑器列表 | 旧名未命中输入区 | 含 `input_sql` / `upgrade_preview` / `rollback_preview` / `validation_preview` |

### 2.4 接口文档更新

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | `10` | `SPACING_PAGE` + mode 间距 |
| 主/编辑 splitter | 局部变量，无 prefs | `self.main_splitter` / `self.editor_splitter` + `install_splitter_prefs`；handle `8` |
| 浏览器 / 工作区 min | 无 | `240` / `520`（compact/narrow 堆叠时放宽为高度约束） |
| `sql_editor` minHeight | 默认 | `240` |

### 2.5 日报

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | 默认 | `SPACING_PAGE` + mode 间距 |
| 历史栏 padding | 默认 | `12,10,12,12` spacing `8` |
| 表单 spacing / margins | spacing `6` | spacing `8`，margins `12,8,12,12` |
| 左历史 minWidth | 无 | `240` |
| 右编辑区 minWidth | 无 | `520` |
| 「今日完成」minHeight | `200` | `240` |
| splitter | handle `6`，无 prefs | handle `8` + `install_splitter_prefs`；补 `apply_layout_mode`（窄屏上下堆叠） |

### 2.6 共享度量

| 项 | 前 | 后 |
|----|----|----|
| `editor_min_height()` | `180` | `240` |
| `apply_splitter_orientation(..., min_editor=)` 默认 | `180` | `240` |

---

## 3. 请你开软件重点看（P0-2）

1. **需求管理**：筛选条是否像统一 L3；左右拖到极限停在约 260 / 520；窄窗左右 min 略松。  
2. **文件库**：九按钮仍是原九个、原顺序；树行更高更易点；横向滚动仍在。  
3. **升级准备**：SQL 输入区明显更高（≥240）；Tab 内卡片间距更匀。  
4. **接口文档**：左右分隔可持久化/双击复位；SQL 区高度够用；窄屏浏览器与编辑上下堆。  
5. **日报**：历史与编辑分隔同套交互；「今日完成」更高；保存按钮仍在日期行（未搬页头）。

---

## 4. 回归修补（文件库九按钮高度，仍为 28）

**现象**：`ReleaseUiTests` 整套跑时，`test_requirement_file_library_keeps_selected_file_actions_and_prioritizes_tree_space` 失败：`height() == 28` 不成立（单测单独跑可能过）。

**根因（不是「故意改高」）**：
- `QScrollArea.setWidget` + 全局 QSS（`QPushButton` / `#primary-btn` padding）会把子按钮 min/max 抬到约 36 / 34–42。
- 后续 `_setup_ui` 后半段布局与 `_refresh` 再次 polish 后，仅在 `setWidget` 后立刻 `size_compact_button` 不够。
- action-card margins `8/6`、spacing `6` **不是**抬高根因（旧 margins `6/4` 在同套污染下同样会抬高）。

**修复**：
- 新增 `_clamp_file_library_action_heights()`：九按钮回夹 `BTN_COMPACT_H=28`，host/scroll 固定 28。
- 在 `_setup_ui` 末尾与 `__init__`（`_refresh` 之后）各调用一次。
- QSS：`#primary-btn[compactAction="true"]` 等与紧凑按钮同样 `padding:3px 10px; min/max-height:28px`。

**明确规格**：**文件库九按钮高度保持 28，未改为更高。**

---

## 5. 定向验证（完整 pass/fail 清单）

命令：

```text
python -m unittest tests.test_release_ui.ReleaseUiTests tests.test_requirement_splitter tests.test_daily_report_upgrade tests.test_page_skeleton -v
```

结果：**Ran 50 tests · OK · EXIT 0**（原始输出 `outputs/_p0_2_retest.txt`）

### tests.test_release_ui.ReleaseUiTests（28）

| 结果 | 用例 |
|------|------|
| PASS | test_delete_confirmation_is_cancel_first_and_cancel_default |
| PASS | test_failed_waiting_task_restores_controls |
| PASS | test_global_combo_and_date_styles_have_visible_drop_down_affordance |
| PASS | test_multiple_systems_generate_separate_sql_packages |
| PASS | test_one_click_generates_workbook_and_sql |
| PASS | test_online_month_label_formats_complete_chinese_title |
| PASS | test_only_learning_module_is_hidden |
| PASS | test_pasted_bug_prompts_for_development_svn |
| PASS | test_private_setup_does_not_touch_local_data |
| PASS | test_private_unlock_persists_across_reopen |
| PASS | test_release_page_is_first_and_date_auto_loads_candidates |
| PASS | test_requirement_allows_empty_development_svn |
| PASS | test_requirement_dates_allow_manual_input_and_local_svn_binding |
| PASS | test_requirement_detail_splitter_is_resizable_and_persistent |
| PASS | test_requirement_dev_local_path_saved_and_opens_from_list |
| PASS | test_requirement_dialog_supports_multiple_systems_and_bindings |
| PASS | test_requirement_file_library_keeps_selected_file_actions_and_prioritizes_tree_space |
| PASS | test_requirement_file_tree_elides_long_names_and_keeps_compact_rows |
| PASS | test_requirement_file_tree_refresh_is_silent_and_tree_has_selection_controls |
| PASS | test_requirement_file_tree_shows_all_files_and_lock_icon_without_svn_status_column |
| PASS | test_requirement_file_tree_uses_svn_status_colors |
| PASS | test_requirement_sql_lands_on_organize_tab_and_empty_focuses_row |
| PASS | test_requirement_status_flow_is_available_in_editor_and_filter |
| PASS | test_requirement_system_configuration_is_shared |
| PASS | test_requirement_tree_supports_multi_delete_badges_and_drag_reclassification |
| PASS | test_requirements_are_grouped_by_month_and_sorted_by_modified_time |
| PASS | test_scan_result_stays_browsable_and_loading_restores_controls |
| PASS | test_upgrade_reuses_data_directory_and_accepts_legacy_requirement |

### tests.test_requirement_splitter（4）

| 结果 | 用例 |
|------|------|
| PASS | test_content_sized_top |
| PASS | test_detail_card_is_content_sized |
| PASS | test_file_tabs_take_remaining_space |
| PASS | test_flags_single_row |

### tests.test_daily_report_upgrade（12）

| 结果 | 用例 |
|------|------|
| PASS | test_group_dates_by_month |
| PASS | test_image_size_change_is_dirty |
| PASS | test_normalize_legacy_plain |
| PASS | test_qt_html_wrapper_is_not_dirty |
| PASS | test_save_image_and_cleanup |
| PASS | test_action_buttons_share_date_row |
| PASS | test_clicking_saved_date_is_not_marked_unsaved |
| PASS | test_completed_editor_takes_most_vertical_space |
| PASS | test_image_context_menu_uses_chinese_not_qt_english |
| PASS | test_import_yesterday_plan_fills_today_completed |
| PASS | test_inserted_image_can_be_resized_and_persists_in_html |
| PASS | test_tree_groups_and_rich_export |

### tests.test_page_skeleton（6）

| 结果 | 用例 |
|------|------|
| PASS | test_frame_object_name_and_title |
| PASS | test_optional_parts |
| PASS | test_qss_has_skeleton_sections |
| PASS | test_filter_bar_button_count_within_baseline |
| PASS | test_complete_pages_have_header |
| PASS | test_primary_count_at_most_one |

**FAIL：无。ERROR：无。**

---

## 6. 交付产物

| 项 | 路径 / 值 |
|----|-----------|
| 样式提交 | `d69240c` |
| 高度回归修补 | 见本次后续 commit |
| EXE | `dist\PengToolsHub.exe` |
| 安装包 | `PengToolsHub_Offline_Setup.zip` |
| 构建时间 | 以 `resources/build_info.json` 为准 |
