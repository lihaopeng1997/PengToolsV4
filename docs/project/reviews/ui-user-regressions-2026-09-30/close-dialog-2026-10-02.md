# 关闭弹窗再次收紧（2026-10-02）

用户再次反馈关闭弹出框太大；此次反馈不视为 UI7 视觉签收。接手时进程与 Sky 窗口清单均无 PengToolsHub，尚未确定用户实际入口。当前分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`，保留全部已有未提交修改。

## 本次差量

仅调整 `ui/confirm_dialog.py` 中的 CloseActionDialog：普通宽度400→360，边距18→14、垂直间距10→8；大字号按真实最小内容宽度适配，高度按完整布局的折行计算。随机文案、复选框、同排取消/隐藏/退出、安全默认和返回值保持。测试仅更新相关尺寸/折行行为，不改变其他弹窗或共享样式。

workbench worker 保存了真实编辑前两文件字节，随后自验并冻结。TEMP证据根目录：`C:/Users/Lenovo/AppData/Local/Temp/pengtools-close-action-20261002-093420-3f24b6d5191541e1af05574d3d222ab2/`，包含 baseline.json、scoped.patch、verified 前后图、实际字体/几何和 root-acceptance.json。

## 主代理验收

Windows QPA下固定同文案中文尺寸400×179→360×165；字号设置16为360×166。英文窄宽折为两行，两个字号设置分别360×165、360×166；消息label高34等于HFW，三个按钮自然宽且同排。字体为Microsoft YaHei UI，DPR1.5。13/16是ThemeManager设置值，实际QApplication QFont为9pt，dialog为13/16px，消息为13px，标题18px；证据已纠正初始font_size_pt字段，不能将设置值写成实际pt。

主代理目检中文普通/英文大字号after图，文案、复选框、按钮完整。主代理独立执行下列4项，exit0、4/4通过；两个文件hash前后稳定：

```powershell
$env:QT_QPA_PLATFORM='windows'
& '.venv-build/Scripts/python.exe' -B -m unittest tests.test_prism_dialogs.PrismDialogContractTests.test_close_action_dialog_safety_defaults tests.test_prism_dialogs.PrismDialogContractTests.test_close_action_buttons_keep_natural_width_and_action_results tests.test_prism_dialogs.PrismDialogContractTests.test_close_action_dialog_compact_with_all_messages_and_large_font tests.test_dialog_paths.DialogSafeDefaultsAndFramelessContractTests.test_v1_12_close_dialog_safe_defaults_unchanged -v
```

其中覆盖30个中英文/字号/随机提示组合、完整折行、一排按钮、点击结果、“不再提示”值与安全默认。非全量测试、非实际EXE弹窗几何测量；用户视觉签收仍待确认。全局 `git diff --check` exit0，原13保护文件当前13项均匹配。

- 弹窗源SHA256：`08e0e90ea825c45b851b1a55ae6bce3bb8e0bec8056a0f7e3af1858f6fd4ce28`。
- 测试SHA256：`32b02d58ca2349ee1cf60edc8d618b7ce3b60ed3c41d23e9300a0747e78c421f`。

## 软件落地

UI8在新目录 `D:/PengTools/build_new_20261002_UI8` 单次官方隔离构建exit0。runtime输入为UI7的258路径加此次弹窗；对UI7 prebuild快照只有弹窗与UI7隔离build_info时间戳不同，UI8构建后只有自身build_info时间戳变化。没有打入尚未完成的Agent/数据中心重构，所有旧包/data保留，未提交或推送。

包装worker完成258路径计划后，最新live句柄变成pending_init；root中断该句柄并接管最小包装fallback，没有因为观察超时重启任务。root只在TEMP新建 `root_package_ui8.ps1`，复用原计划及未经修改的官方builder。preflight验证新target不存在、绝对清理/输出路径位于指定新source、旧cmd删除分支的旧输出不存在、源哈希/原13保护文件/分支SHA；从精确白名单复制，不使用live Main/native hosts等Agent/DC修改。证据为 `C:/Users/Lenovo/AppData/Local/Temp/pengtools-ui8-prep-20261002/ui8-root-build.log`、UI8 source_snapshot.json/package_result.json。

构建器依赖检查通过，resources/packaging秘密扫描HIGH RISK0/WARN0/PASS（非归档全范围秘密扫描）。EXE与Installer哈希一致；ZIP2,068项，data/cache/bytecode项0；13保护文件0变化。root另独立核对258路径集合/输入差量/构建后差量/ZIP禁带路径，均符合预期。所有旧运行数据仍保留，没有迁移或覆盖。

- [UI8程序](D:/PengTools/build_new_20261002_UI8/source/dist/PengToolsHub/PengToolsHub.exe)，SHA256 `ca0749fd34ab6906ba8b76f8b5504e6d308cdebd1f95e48ddace5ce52df2d7bd`。
- [UI8完整包](D:/PengTools/build_new_20261002_UI8/source/PengToolsHub_UI8_20261002.zip)，SHA256 `d6ca8dec75e168f37826f29490f39d072bf55960196870e45f331274c6f0691d`。

## 实际软件弹窗

构建后最新Sky窗口清单无PengToolsHub。root用computer-use的Sky启动精确UI8 EXE，唯一返回主窗口id68304、app为该新路径；首页正常。按刚获取截图定位点击右上角关闭，出现当前EXE的关闭弹窗，标题、单行随机提示、复选框与同排三按钮完整。Sky返回弹窗截图区域363×179（含Windows装饰），不将其等同于Qt client测量；360×165是上方固定双行文案fixture。root已查看真实截图，弹窗留给用户，没有点击退出/隐藏/不再提示。用户最终视觉签收仍待确认，真实事务退出不由此弹窗观察证明。

### 21:52 实窗复核

本次续接先用进程与Sky窗口清单分别确认没有运行中的PengToolsHub；弹窗源码、UI8隔离源码以及EXE哈希仍匹配上方冻结值。重新启动精确UI8，唯一返回窗口id1640916，主窗1440×900；使用新截图点击关闭后，实际弹窗区域再次为363×179，标题、提示、复选框与同排按钮完整，已目检并留在屏幕上。没有选择任何关闭动作或改动用户数据；本次没有重新修改弹窗、打包或重复已通过的四项测试。用户此前所用EXE入口仍未确定，不推断反馈来自旧包，也不把本次显示视为视觉签收。旧窗口id和坐标不可复用。
