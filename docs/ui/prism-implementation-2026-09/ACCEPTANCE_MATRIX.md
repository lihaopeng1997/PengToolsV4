# 晴空棱镜验收缺口清单

采集基准：`59a120530a2faf13c09a036a09a2cc5f13ad4c9d`。

本表盘点当前文件中的证据，不代表最终验收。源码扫描包含本地未提交内容；截图可能来自旧提交，具体以各JSON中的源码记录为准。没有源码记录的旧图不能视为当前版本证据。

## 22个导航入口的窗口样本

下表只列网页主壳的已录制初始状态；“—”表示该窗口组合没有对应记录。链接存在不证明布局符合规格，也不证明错误、长内容、执行、取消或业务契约已通过。

|入口|1440展开|1440收起|1280|1100|960|
|---|---|---|---|---|---|
|0 首页|[录制1](shell/nav-0-1440-900.json)|—|—|—|[录制5](shell/nav-0-960-640-dpr-100-font-16.json)|
|1 证件|—|—|—|—|[录制1](shell/nav-1-960-640.json)|
|2 发版联动|—|—|—|—|[录制1](shell/nav-2-960-640.json)|
|3 接口文档|—|—|—|—|[录制1](shell/nav-3-960-640.json)|
|4 VIN|—|—|—|—|[录制1](shell/nav-4-960-640.json)|
|5 加解密|—|—|—|—|[录制1](shell/nav-5-960-640.json)|
|6 命令库|—|—|—|—|[录制1](shell/nav-6-960-640.json)|
|7 设置|[录制1](shell/nav-7-1440-900.json)|—|—|—|[录制8](shell/nav-7-960-640-tab-1.json)|
|8 学习|—|—|—|—|[录制1](shell/nav-8-960-640.json)|
|9 日报|—|—|—|—|[录制1](shell/nav-9-960-640.json)|
|10 需求|[录制1](shell/nav-10-1440-900.json)|—|—|—|[录制1](shell/nav-10-960-640.json)|
|11 格式工具|—|—|—|—|[录制2](shell/nav-11-960-640-tab-1.json)|
|12 接口排查|—|—|—|—|[录制1](shell/nav-12-960-640.json)|
|13 日志|—|—|—|—|[录制1](shell/nav-13-960-640.json)|
|16 聊天|—|—|—|—|[录制1](shell/nav-16-960-640.json)|
|17 工作|[录制1](shell/nav-17-1440-900.json)|—|—|—|[录制1](shell/nav-17-960-640.json)|
|18 Oracle|—|—|—|—|[录制5](shell/nav-18-960-640-dpr-100-font-16.json)|
|19 MySQL|—|—|—|—|[录制1](shell/nav-19-960-640.json)|
|20 OceanBase|—|—|—|—|[录制1](shell/nav-20-960-640.json)|
|21 达梦|—|—|—|—|[录制1](shell/nav-21-960-640.json)|
|22 Redis|[录制1](shell/nav-22-1440-900.json)|—|[录制1](shell/nav-22-1280-800-collapsed.json)|—|[录制8](shell/nav-22-960-640-dpr-100-font-16.json)|
|23 MongoDB|[录制1](shell/nav-23-1440-900.json)|—|[录制1](shell/nav-23-1280-800-collapsed.json)|—|[录制6](shell/nav-23-960-640-dpr-100-font-16.json)|

原生首页/侧栏已有四种组合记录，见[实施记录](STATUS.md)。其模拟的是启动时网页不可用，不替代运行中崩溃、实际多屏或全状态验收。

## QDialog子类的初始样本

这里只扫描panels和ui内直接继承QDialog的类；不包含平台文件选择器、QMessageBox、间接继承或其他动态工厂。13/16表示字体设置，均不是物理DPI测试。

|源码类|13字号|16字号|
|---|---|---|
|[ObjectPickDialog](../../../panels/ai_token_edit.py)|[初始录制](dialogs/objects-13.json)|[初始录制](dialogs/objects-16.json)|
|[_SkillManagerDialog](../../../panels/model_chat_panel.py)|[初始录制](dialogs/skills-13.json)|[初始录制](dialogs/skills-16.json)|
|[CategoryManageDialog](../../../panels/ops_log_panel.py)|[初始录制](dialogs/category-13.json)|[初始录制](dialogs/category-16.json)|
|[ServerEditorDialog](../../../panels/ops_log_panel.py)|[初始录制](dialogs/server-13.json)|[初始录制](dialogs/server-16.json)|
|[LogSettingsDialog](../../../panels/ops_log_panel.py)|[初始录制](dialogs/log-settings-13.json)|[初始录制](dialogs/log-settings-16.json)|
|[ServerManageDialog](../../../panels/ops_log_panel.py)|[初始录制](dialogs/servers-13.json)|[初始录制](dialogs/servers-16.json)|
|[CommandHistoryDialog](../../../panels/ops_log_panel.py)|[初始录制](dialogs/history-13.json)|[初始录制](dialogs/history-16.json)|
|[CustomCommandDialog](../../../panels/ops_panel.py)|[初始录制](dialogs/command-13.json)|[初始录制](dialogs/command-16.json)|
|[PasteKnowledgeDialog](../../../panels/personal_panel.py)|[初始录制](dialogs/paste-13.json)|[初始录制](dialogs/paste-16.json)|
|[KnowledgeEditDialog](../../../panels/personal_panel.py)|[初始录制](dialogs/knowledge-13.json)|[初始录制](dialogs/knowledge-16.json)|
|[MonthPickerDialog](../../../panels/requirement_panel.py)|[初始录制](dialogs/month-13.json)|[初始录制](dialogs/month-16.json)|
|[RequirementAttachmentDialog](../../../panels/requirement_panel.py)|[初始录制](dialogs/attachment-13.json)|[初始录制](dialogs/attachment-16.json)|
|[SvnCheckoutDialog](../../../panels/requirement_panel.py)|[初始录制](dialogs/svn-13.json)|[初始录制](dialogs/svn-16.json)|
|[RequirementDialog](../../../panels/requirement_panel.py)|[初始录制](dialogs/requirement-13.json)|[初始录制](dialogs/requirement-16.json)|
|[TestPointsDialog](../../../panels/test_points_editor.py)|[初始录制](dialogs/test-points-13.json)|[初始录制](dialogs/test-points-16.json)|
|[TicketSubmitConfigDialog](../../../panels/ticket_submit_dialog.py)|[初始录制](dialogs/ticket-config-13.json)|[初始录制](dialogs/ticket-config-16.json)|
|[TicketSubmitDialog](../../../panels/ticket_submit_dialog.py)|[初始录制](dialogs/ticket-13.json)|[初始录制](dialogs/ticket-16.json)|
|[ConfirmActionDialog](../../../ui/confirm_dialog.py)|[初始录制](dialogs/confirm-13.json)|[初始录制](dialogs/confirm-16.json)|
|[CloseActionDialog](../../../ui/confirm_dialog.py)|[初始录制](dialogs/close-13.json)|[初始录制](dialogs/close-16.json)|
|[AppNoticeDialog](../../../ui/confirm_dialog.py)|[初始录制](dialogs/notice-13.json)|[初始录制](dialogs/notice-16.json)|
|[NextStepDialog](../../../ui/confirm_dialog.py)|[初始录制](dialogs/next-13.json)|[初始录制](dialogs/next-16.json)|
|[HttpsCertConsentDialog](../../../ui/confirm_dialog.py)|[初始录制](dialogs/https-13.json)|[初始录制](dialogs/https-16.json)|
|[ConnectionDialog](../../../ui/connection_dialog.py)|[初始录制](dialogs/connection-13.json)|[初始录制](dialogs/connection-16.json)|
|[ImagePreviewDialog](../../../ui/daily_rich_edit.py)|[初始录制](dialogs/image-preview-13.json)|[初始录制](dialogs/image-preview-16.json)|
|[FloatingShortcutsEditor](../../../ui/floating_shortcuts_editor.py)|[初始录制](dialogs/floating-shortcuts-13.json)|[初始录制](dialogs/floating-shortcuts-16.json)|
|[UserGuideDialog](../../../ui/help_dialog.py)|[初始录制](dialogs/help-13.json)|[初始录制](dialogs/help-16.json)|

当前扫描到26个直接子类，初始截图覆盖不等于保存、取消、拖动、权限和错误状态均已验收。

## 临时创建的QDialog

|源码|创建位置|状态|
|---|---|---|
|[panels/ai_token_edit.py](../../../panels/ai_token_edit.py)|_view_tokens / 行164|待逐项运行核验|
|[panels/ai_workbench_panel.py](../../../panels/ai_workbench_panel.py)|_view_snapshot / 行1156|待逐项运行核验|
|[panels/interface_debug_panel.py](../../../panels/interface_debug_panel.py)|_show_history_cleanup_dialog / 行4121|待逐项运行核验|
|[panels/interface_debug_panel.py](../../../panels/interface_debug_panel.py)|_show_environment_config_dialog / 行4243|待逐项运行核验|
|[panels/interface_debug_panel.py](../../../panels/interface_debug_panel.py)|_show_url_filter_config_dialog / 行4359|待逐项运行核验|
|[panels/requirement_panel.py](../../../panels/requirement_panel.py)|_choose_date / 行422|待逐项运行核验|
|[panels/sql_panel.py](../../../panels/sql_panel.py)|_offer_apply_draft / 行1471|待逐项运行核验|

## 仍需逐项收尾的验收门槛

- 按规格第14.2节补齐窗口组合，以及长内容、执行/失败/取消、极端分栏偏好与数据保持证据。
- Loading按LD-01至LD-08逐调用点审计；组件运行测试不代表所有业务入口接入正确。LD-03/LD-04尚无完整接入清单。
- Redis/MongoDB同步AI等待仍需先确认[线程范围草案](../../project/NOSQL_AI_WAITING_HANDOFF.md)，不能将普通继续消息当成范围批准。
- SQL工作台原_run_sql(reset=True)在启动请求前清空结果；与LD-04保留旧结果的目标存在边界冲突，需核对原行为与批准范围，不能仅为动画擅自修改model逻辑。
- 真实多屏、负坐标跨屏、运行中DPI切换与Windows透明合成尚无完整证据。现有模拟矩形、DPR缩放不替代物理屏幕验证。
- 第14.3节启动基线、10分钟CPU、200次切页内存和帧率仍未完成；发布构建按另行发版范围执行。
- 人工视觉接受与本地图集浏览器交互尚待完成；禁止以静态生成图集作为交互通过证据。

后续AI协作提示词已发布：[需求AI接手](../../project/AI_REQUIREMENTS_PROMPT.md)。本表用于防止漏项，不把本轮UI缺陷转交为后续新增需求。
