# 关闭弹窗整体尺寸续修

用户反馈 UI6 的关闭弹窗仍然太大。此前只修复按钮被拉伸的问题，弹窗本身仍有 560 的最小宽度，操作分成两行；不能把此前按钮验收当作整个弹窗已缩小。

## 源码与行为

分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`，继续保留未提交工作区。新接手副本在 `C:/Users/Lenovo/AppData/Local/Temp/pengtools-close-compact-20261001-034550a3a7f841e5835621a545adde64/`；`confirm_dialog.before.py` SHA-256 为 `abfa7bd60d150982d7d69d27e0ba2fe7789381e8a94105ffc6046f6cb9ed2ee1`。

仅调整 `ui/confirm_dialog.py` 的 `CloseActionDialog`：目标宽度 400，边距 18、间距 10；取消/隐藏/退出同一行，复选框独立一行；布局完成并应用样式后按实际折行内容计算高度。标题支持换行。原随机提示、隐藏/退出返回值、“不再提示”结果和安全默认焦点保留。没有更改共享样式、其他弹窗或关闭业务处理。

## 验证

Windows QPA 下，同一主题、字号设置 13、同一双行中文提示的源类前后测量：内容几何由 **560×211** 改为 **400×179**；Microsoft YaHei UI 9 pt，DPR 1.5。三个按钮均为 86×32，修改后在同一行。主代理已读取前后 Qt 抓图，文案、复选框与按钮完整。原始 `geometry.json` 和 `before.png`/`after.png` 保存在上述 TEMP 目录；这是合成对话框测量，不是对用户实际包窗口的 Qt 几何读取。

通过的定向命令（Windows QPA，4 项）：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
& '.venv-build/Scripts/python.exe' -B -m unittest tests.test_prism_dialogs.PrismDialogContractTests.test_close_action_dialog_safety_defaults tests.test_prism_dialogs.PrismDialogContractTests.test_close_action_buttons_keep_natural_width_and_action_results tests.test_prism_dialogs.PrismDialogContractTests.test_close_action_dialog_compact_with_all_messages_and_large_font tests.test_dialog_paths.DialogSafeDefaultsAndFramelessContractTests.test_v1_12_close_dialog_safe_defaults_unchanged -v
```

新增尺寸回归覆盖全部 10 条中文/5 条英文提示、字号设置 13/16，共 30 个内容组合：弹窗不超过 440×240，折行说明完整，动作在同一行，控件位于弹窗内，退出不是默认动作。原按钮点击返回值和安全焦点测试通过。此前 offscreen 三个关闭弹窗测试也通过；未运行全量测试或重复首页/导入回归。

尝试观察实际 UI6 旧包时，工具先报告 `coordinate input geometry is unavailable`，重新获取后报告 `window is not a usable app window`；随后查询无 PengToolsHub 进程。没有将这些输入尝试记作实际旧弹窗验收，也没有强制结束进程。

## 交付状态

源码已冻结并复核，`git diff --check` 通过。新 `D:/PengTools/build_new_20261001_UI7/source` 通过已审阅的 fresh-target 包装调用既有官方构建器一次，exit 0。所有旧候选与 data 保留，运行时白名单沿用 258 个路径，没有复制用户数据。相对 UI6 的 258 个运行输入，只有 `ui/confirm_dialog.py` 变化；无 Vue 源码或嵌入资源变化。

- UI7 快照的 258 项 live 输入哈希全部匹配；隔离副本唯一变化为 `resources/build_info.json` 构建日期/时间。13 个原保护文件未变化。
- 依赖检查通过；官方 resources/packaging 范围秘密扫描为 HIGH RISK 0、WARN 0、PASS，不声称归档全范围秘密扫描通过。PyInstaller 既有可选/平台依赖警告没有阻止构建，不由本次弹窗操作推断其他模块运行。
- ZIP 共 2,068 项，data、bytecode/cache 项为零；dist/Installer EXE 哈希一致。
- [UI7 程序](D:/PengTools/build_new_20261001_UI7/source/dist/PengToolsHub/PengToolsHub.exe)，SHA-256 `eb82ee6f2071efb6e6d4fe6b22dc424167c9b36cc836f1fdc85c21ee1daf5354`。
- [UI7 完整包](D:/PengTools/build_new_20261001_UI7/PengToolsHub_UI7_20261001.zip)，SHA-256 `7c8d39d8809b7b0781824fa0c0bc158b645e7ecb9e45b685b282b242677f140b`。

## 实际 UI7 包窗口

启动前查询无 PengToolsHub 进程。使用 Sky 启动精确 UI7 EXE，唯一返回主窗口 ID `169610062`，实际首页正常。随后按新截图定位点击右上角关闭，出现真实关闭确认弹窗，显示完整标题、随机提示、“关闭时不再提示”，取消/隐藏/退出三个紧凑按钮并排。主代理已观察实际窗口截图；这补上了当前包弹窗外观检查，但不是用户最终视觉签收，也不是实际包窗口 Qt 几何读取。

弹窗保持打开供用户查看，没有点击退出、隐藏或复选框，没有强制结束进程。新运行产生的 UI7 配置/日志保留；未迁移、清理或覆盖旧数据。未提交、推送或暂存。
