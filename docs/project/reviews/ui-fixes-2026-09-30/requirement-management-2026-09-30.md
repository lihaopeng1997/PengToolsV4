# 需求管理修复证据（2026-09-30）

- 工作区：`D:\PengTools`；分支 `dev/2026-09-23`；HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。本记录只汇总现有定向日志和限定差量，不代表独立复跑；本次未接触真实需求数据、真实 SVN 或用户应用。
- 变更聚焦需求面板保存与导航：保存候选值成功后再更新内存状态；写盘异常由真实按钮路径捕获；首页动作通过精确 section 定位既有需求编辑器。差量未删除现有需求面板方法。

## 验收证据

- `%TEMP%\pengtools_requirement_20260930_focused_tests_final.log`：`test_requirement_scan_folders`、`test_requirement_save_failures`、`test_requirement_focus_section`、`test_requirement_worker_lifecycle` 共 50 项，exit 0。覆盖月份/需求文件夹扫描、保存失败状态保护、section 精确定位和 worker 生命周期。
- `%TEMP%\pengtools_requirement_20260930_status_dialog_probe_final.log`：真实 `RequirementDialog.exec()` 合成 fixture 输出 `REAL_DIALOG status_combo_focus=True cancel_save=0`，exit 0。确认「编辑状态」聚焦原有状态选择框；测试取消，未保存。
- `%TEMP%\pengtools_requirement_20260930_flag_abort_after.log`：合成写盘异常下调用真实完成按钮 `.click()` 后输出 `SURVIVED_CLICK state_unchanged changed=0 error=1`。失败没有提交内存变更，错误已处理。
- `%TEMP%\pengtools_requirement_20260930_flag_abort_before.log`：修复前隔离子进程退出码为 `-1073740791`。这证明该错误路径曾导致进程异常退出，不能据此认定它与用户报告的闪退完全同因。
- 精确定位测试涵盖有效 ID 被旧搜索/过滤隐藏时仍能定位，以及未知 ID 不改变当前选择与过滤条件。首页的完成状态读取既有 `status` 值（`已完成` / `已上线`）；SQL flag 是另一项业务状态，不能把它当作首页完成标记。

## 验收边界

定向测试使用合成数据。状态对话框 fixture 证明原有 modal 能打开并聚焦，但因测试取消且 `cancel_save=0`，不证明真实编辑保存后首页自然刷新。首页 WebEngine 全链路没有通过运行时断言；本报告也不代表用户应用中的视觉验收或实际闪退根因已确定。
