# SSH 会话回调回归（离线）

这三个脚本验证「日志排查 → 人工排查」里 SSH 会话按钮与回调的身份隔离。
全部使用假的 SSH 传输与离屏 Qt，不连接真实服务器、不读取真实配置或凭据。

## 运行

```bash
python tests/ssh_session/verify_ssh_buttons.py
python tests/ssh_session/verify_ssh_identity.py
python tests/ssh_session/verify_ssh_callbacks2.py
```

每个脚本以 `RESULT: N passed, M failed` 结尾，失败时退出码为 1。

## 覆盖范围

| 脚本 | 覆盖 |
|---|---|
| `verify_ssh_buttons.py` | 按钮状态跟随当前终端：已连接/未连接切换、连接中与断开中禁用、无终端提示、A 连接中切到 B 不污染、断开按钮位于左工具条、新会话初始未连接 |
| `verify_ssh_identity.py` | 会话身份：A/B/C 关闭 A 后 B 掉线不影响 C、连接中关闭旧代次失效、最后标签重建新旧 id、未知/重复掉线、`detach()` 不误报、旧 shell 关闭不发信号、主动清理只影响目标、延迟信号 |
| `verify_ssh_callbacks2.py` | 补丁回归：重建后掉线命中新会话、worker 结束自动恢复按钮与文案区分（连接中 / 后台任务）、断开立即结束查询等待且不误报远端断开、无关断开不终止查询 |

## 约定

- 需要 `QT_QPA_PLATFORM=offscreen`（脚本内已设置）。
- 只读：不写仓库文件，不产生网络请求。
- 新增用例请保持同样的 `check(name, cond)` 结构，便于统一统计。
