# 关闭弹窗 UI9 再次收紧

10月4日续核：EXE SHA256仍为下方冻结值，生产Qt预览再次目检完整。最新进程清单已无PengToolsHub，取代本文旧PID10900观察。Sky分别使用反斜线及正斜线精确UI9路径启动，两次launch_app均15秒超时；随后窗口和进程清单仍无目标，未成功自动打开。没有再次修改尺寸、重新构建、改变真实关闭设置或用户数据。UI9实际EXE弹窗仍未验；用户可直接打开下方程序链接。

用户仍反馈关闭弹出框太大。本次仅修改 `CloseActionDialog` 普通目标宽度360→320、边距14→12、垂直间距8→6；保持实际最小内容宽度适配、自然按钮宽度、完整文案、复选框、返回值和安全默认。其他弹窗及共享主题不变。

分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。原有 dirty 修改保留，无暂存、提交或推送。

## 定向验证

Windows QPA，Microsoft YaHei UI，DPR1.5；同一固定双行文案，中文/英文：主题字号设置13时360×165→320×155，设置16时360×166→320×156。设置13/16为CSS像素而非pt。消息实际高度34等于height-for-width；已目检中文普通和英文大字号图，三个按钮同排，提示及复选框完整。

![中文关闭弹窗](close-dialog-ui9-zh.png)

四项定向测试exit0（2.268s），包括30个中英文/字号/随机文案组合的完整折行与控件边界、按钮自然宽度、实际点击结果、复选框返回值和安全默认：

```powershell
$env:QT_QPA_PLATFORM='windows'
& '.venv-build/Scripts/python.exe' -B -m unittest tests.test_prism_dialogs.PrismDialogContractTests.test_close_action_dialog_safety_defaults tests.test_prism_dialogs.PrismDialogContractTests.test_close_action_buttons_keep_natural_width_and_action_results tests.test_prism_dialogs.PrismDialogContractTests.test_close_action_dialog_compact_with_all_messages_and_large_font tests.test_dialog_paths.DialogSafeDefaultsAndFramelessContractTests.test_v1_12_close_dialog_safe_defaults_unchanged -v
```

真实编辑前两文件副本、before/after八张图、geometry.json、scoped.patch及包装日志位于 `C:/Users/Lenovo/AppData/Local/Temp/pengtools-close-action-ui9-20d3699bf12641f49fd7b42aaf349d44/`。源码SHA256 `fc6501a90b4d873009ee9527709ff11be9cb485d78387a37a6374d92846d4189`；测试 `54f2199ce2e6ebd963ef363837b04a543ee04a9fd5e95c50b1c6eb67736e76c7`。

## 隔离发布包及运行限制

新 `build_new_20261002_UI9/source` 使用UI8已冻结的258项白名单，仅覆盖此次弹窗；官方构建exit0。主代理另外核对258项源码：无不预期hash差异，相比UI8仅弹窗和隔离build_info时间戳不同。包内EXE与Installer一致，ZIP2,068项，data/cache/bytecode0；原13保护文件0变化。未复制或覆盖旧包的用户数据，未打入尚未完成的Agent/数据中心代码。

- [UI9程序](D:/PengTools/build_new_20261002_UI9/source/dist/PengToolsHub/PengToolsHub.exe)，SHA256 `ea1d42c4b482b00f69a205041669010d46f79351ef8bcc59b0435e9cbc3363eb`。
- [UI9完整包](D:/PengTools/build_new_20261002_UI9/source/PengToolsHub_UI9_20261002.zip)，SHA256 `aa8a39b3d3fde1b80ab322653340293ae0120d6a425785ad96df7334e933feab`。

当前已有UI8进程PID10900。Sky最初无目标窗口，重新启动精确UI8激活原进程；实际显示设置页及悬浮工具栏。使用新截图点击主窗关闭后窗口隐藏，进程仍在，没有出现关闭确认框。这与已有“关闭时不询问且隐藏”的源码路径相符，但未读取或修改真实设置以确认原因。没有强制退出、修改“不再提示”或启动UI9绕过单实例。UI9实际EXE弹窗及用户视觉签收仍待确认；上图和尺寸是生产弹窗类的Windows Qt fixture，不能写成实际UI9 EXE观察。
