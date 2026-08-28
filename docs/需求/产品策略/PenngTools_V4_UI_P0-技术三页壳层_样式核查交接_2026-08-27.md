# PenngTools V4 · P0 技术三页壳层样式核查交接（2026-08-27）

> **范围**：接口排查 → 日志排查 → SQL 控制台（壳层纯样式）  
> **边界**：只做间距 / 字号 / Token / 骨架 / 响应式。抓包启停、真实发送、SSH 执行、SQL 执行、主次按钮、chip 集合与业务路径 **一律未改**。  
> **流程**：改完交本文件；**你核过再走下一批**，本文不预设下一步。

---

## 1. 本轮改动文件

| 文件 | 样式类改动 |
|------|------------|
| `panels/interface_debug_panel.py` | 页间距 Token；详情编辑区 min→240；响应预览 min→220；筛选条 margins；搜索框 36；详情区宽屏 minWidth 520；`page_spacing_for_mode` |
| `panels/ops_log_panel.py` | 页间距 Token；`_card` padding；终端 min→220；宽屏右栏 min→480；`page_spacing_for_mode` |
| `panels/ai_workbench_panel.py` | 页间距 Token；SqlEditor≥240；列/上下 splitter 初始 mins；compact 左栏 240；窄屏 chrome 改名 `page-narrow-chrome`；结果/字段表行高 36 |
| `resources/style.qss` | 会话搜索框 36；远端/导出树 item 36；`page-narrow-chrome` |
| `tests/test_iface_request_test.py` | 对齐 P0 文案（请求验证 / 发送三态） |
| `tests/test_interface_fiddler_workbench.py` | 对齐 P0 抓包在页头、编辑区 240/220、宽窗下 splitter 回放 |

**未改**：九按钮无关；抓包/发送/SSH/SQL 执行语义；主次按钮角色；filter chip 集合与文案。

---

## 2. 按页样式 Diff

### 2.1 接口排查

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | `10` | `SPACING_PAGE` + `page_spacing_for_mode` |
| 筛选条 margins / 行 spacing | `8,8,8,10` / `6` | `12,8,12,8` / `8`（objectName 仍为 `iface-session-toolbar`，避免 chip 计入 `page-filter-bar` 基线） |
| 搜索框高 | 28–32 | 36–40 |
| 概览/请求/响应编辑 minHeight | `180` | `editor_min_height()=240` |
| 请求验证响应预览 minHeight | `120` | `220` |
| 水平分栏 prefs mins | `[240,420]` | `[240,480]`；wide/standard 详情区 `setMinimumWidth(520)` |

### 2.2 日志排查

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | `8` | `SPACING_PAGE` + mode 间距 |
| `_card` margins / spacing | `10` / `8` | `12,10,12,12` / `SPACING_CARD` |
| 终端 tab minHeight | `180` | `220` |
| wide/std 右栏 minWidth / prefs | `420` / `[260,360]` | `480` / `[260,480]`（compact `[240,360]`） |
| QSS 远端树 / 导出树 item | 30 / 28 | **36** |

### 2.3 SQL 控制台

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | `8` | `SPACING_PAGE` + mode 间距 |
| SqlEditor minHeight | 无 | `240`（新建 Tab + `apply_layout_mode` 同步） |
| columns 初始 mins | `[160,320,200]` | `[240,520,240]` |
| body 上下 mins | `[280,160]` | `[240,220]` |
| compact 左栏 min | `200` | `240` |
| 窄屏开关条 objectName | `page-toolbar`（误用 L2） | `page-narrow-chrome` |
| 结果/字段表默认行高 | apply_table 默认 32 | `TABLE_ROW_H=36` |

---

## 3. 请你开软件重点看

1. **接口排查**：筛选条更贴 L3；详情编辑区更高；宽屏拖分隔详情侧停在约 520；窄屏堆叠仍可用。  
2. **日志排查**：终端区更高；宽屏右侧更宽下限；远端树行更好点。  
3. **SQL 控制台**：新标签编辑区 ≥240；上下拖结果区停在约 220；窄屏「目录/助手」开关条样式独立，不像第二块工具栏。

---

## 4. 完整测试清单（必须贴清）

```text
python -m unittest tests.test_page_skeleton \
  tests.test_iface_request_test \
  tests.test_interface_fiddler_workbench \
  tests.test_splitter_prefs -v
```

**汇总：Ran 57 · FAIL 0 · ERROR 0 · OK**

| 结果 | 用例 |
|------|------|
| PASS | tests.test_page_skeleton.EmptyStateFactoryTests.test_frame_object_name_and_title |
| PASS | tests.test_page_skeleton.EmptyStateFactoryTests.test_optional_parts |
| PASS | tests.test_page_skeleton.EmptyStateFactoryTests.test_qss_has_skeleton_sections |
| PASS | tests.test_page_skeleton.FilterBarPurityTests.test_filter_bar_button_count_within_baseline |
| PASS | tests.test_page_skeleton.PageHeaderTests.test_complete_pages_have_header |
| PASS | tests.test_page_skeleton.PrimaryActionTests.test_primary_count_at_most_one |
| PASS | tests.test_iface_request_test.IfacePanelRequestTestSmoke.test_fill_and_export_from_panel |
| PASS | tests.test_iface_request_test.IfacePanelRequestTestSmoke.test_key_value_editors_roundtrip_headers_and_params |
| PASS | tests.test_iface_request_test.IfacePanelRequestTestSmoke.test_panel_has_request_test_and_export |
| PASS | tests.test_iface_request_test.IfacePanelRequestTestSmoke.test_response_view_keeps_full_body_and_format_buttons |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_export_import_roundtrip |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_extract_sm4_key_from_header |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_fill_form_uses_base_and_params |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_headers_and_params_roundtrip |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_import_rejects_wrong_kind |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_is_loopback_host |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_normalize_base_host_defaults_http |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_plaintext_bodies_fallback_raw |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_rewrite_url_keeps_path_query |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_send_env_host_ok |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_send_https_can_disable_verify |
| PASS | tests.test_iface_request_test.IfaceRequestTestHelpers.test_send_rejects_bad_scheme |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_all_action_rows_and_session_columns_adapt_to_available_workspace_width |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_capture_action_switches_without_clearing_session |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_capture_control_is_one_stateful_action_and_keeps_proxy_tools |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_compact_overflow_labels_refresh_after_language_switch |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_compact_session_view_column_menu_matches_visible_columns |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_detail_environment_context_uses_name_without_base_url |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_detail_summary_includes_capture_time_and_current_environment |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_detail_workspace_keeps_summary_and_readable_response |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_four_detail_tabs_and_columns |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_history_copy_curl_keeps_saved_full_url |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_history_fill_url_preserves_request_editor_content |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_library_activation_fills_form_without_sending |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_library_history_actions_move_to_context_menu |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_narrow_layout_can_hide_left_session_pane_without_hiding_capture |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_reapplying_same_layout_preserves_dragged_splitter_sizes |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_request_test_has_environment_and_filter_config_entries |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_request_test_secondary_actions_move_into_overflow_without_hiding_send |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_request_test_splitter_persists_only_visual_sizes |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_request_test_uses_resizable_editor_response_splitter |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_result_columns_recalculate_after_deferred_splitter_resize |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_session_list_uses_compact_two_line_diagnostics_view |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_session_toolbar_moves_optional_actions_into_overflow_when_left_pane_is_narrow |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_shutdown_clears_memory |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_strip_url_prefixes |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_wide_layout_defaults_prioritize_detail_pane |
| PASS | tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_workspace_places_capture_and_session_tools_inside_left_pane |
| PASS | tests.test_interface_fiddler_workbench.SessionViewLogicTests.test_content_kind_and_size |
| PASS | tests.test_interface_fiddler_workbench.SessionViewLogicTests.test_filters_combinable |
| PASS | tests.test_interface_fiddler_workbench.SessionViewLogicTests.test_pretty_body |
| PASS | tests.test_interface_fiddler_workbench.SessionViewLogicTests.test_search_and_sort |
| PASS | tests.test_interface_fiddler_workbench.SessionViewLogicTests.test_ui_prefs_no_payload |
| PASS | tests.test_splitter_prefs.SplitterPrefsTests.test_accessible_name_and_clamp |
| PASS | tests.test_splitter_prefs.SplitterPrefsTests.test_arrow_key_nudges_sizes |
| PASS | tests.test_splitter_prefs.SplitterPrefsTests.test_children_not_collapsible_after_install |
| PASS | tests.test_splitter_prefs.SplitterPrefsTests.test_double_click_handle_restores_defaults |

另：`scripts/diagnostics/_p0_2_style_smoke.py` 同类 tech-trio smoke（三页间距/min 高）本地通过。

---

## 5. 交付产物

打包后填写：`dist\PengToolsHub.exe`、`PengToolsHub_Offline_Setup.zip`、`resources/build_info.json` 构建时间与提交 hash。
