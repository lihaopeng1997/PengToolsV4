# PengToolsHub 安测基线（Security Test Baseline）

> 版本：1.0 · 日期：2026-07-23  
> 适用：公司内使用 / 离线安装包 / 信息安全测试（安测）对照  
> 非法律意见；以实际测评大纲与公司制度为准。

## 1. 目标

本产品为**桌面本地工具**（无云账号体系、无强制遥测）。安测关注点：

| 域 | 产品对应能力 |
|----|--------------|
| 敏感信息存储 | SSH 主机密码、用户私有笔记、本地配置 |
| 网络通信 | 请求测试 HTTPS、SSH 日志排查、本地抓包代理 |
| 默认安全策略 | 校验证书、远程目标确认、禁止弱编码新写入 |
| 分发安全 | 安装包无生产账密、发布前敏感扫描 |
| 开源合规 | PyQt6 许可、第三方声明（见 SECURITY.md） |

## 2. 默认安全策略（出厂收紧）

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `security_ssl_verify` | `true` | 请求测试 HTTPS **校验证书** |
| `security_confirm_remote_request` | `true` | 非 `localhost`/`127.0.0.1` 发送前**二次确认** |
| `security_prod_host_hints` | prod/生产/… | 命中时确认文案升级为危险样式 |
| SSH `password_token` | `dpapi:` 优先 | Windows DPAPI；失败回退 `enc:` Fernet |
| 新写入 `b64:` | **禁止** | 历史 `b64:` 可读，保存时升级 |

用户可在 **设置 → 安全与安测** 关闭 SSL 校验或远程确认（需自行承担风险）；请求测试页可临时关闭「校验 HTTPS 证书」以适配内网自签。

## 3. 控制对照表（常见安测条目）

| 条目意图 | 实现位置 | 验收要点 |
|----------|----------|----------|
| 凭据不明文落盘 | `tools/secure_store.py`、`ops_ssh.encrypt_secret` | `ops_servers.json` 中无明文密码；前缀 `dpapi:`/`enc:` |
| 禁止可逆弱编码作主方案 | 同上，`encrypt_secret` 不写 `b64:` | 新保存服务器后 token 非 `b64:` |
| TLS 证书校验默认开启 | `iface_request_test.send_http_request(verify_ssl=True)` | 未勾选关闭时使用 `ssl.create_default_context()` |
| 敏感操作二次确认 | `interface_debug_panel._rt_confirm_remote_if_needed` | 向非本机 URL 发送弹出确认 |
| 安装包无敏感种子 | `resources/private_knowledge_seed*`、`scan_release_secrets.py` | 扫描 PASS；种子为空/假数据 |
| 日志/抓包本机边界 | 抓包监听 `127.0.0.1`；报文默认内存 | 不把会话默认上传外网 |
| 本地数据目录 | `config.local_data_dir()` → `data/` | 升级 EXE 不覆盖用户 `data/` |

## 4. 自动化自检

```powershell
cd PengToolsV4
python -m unittest tests.test_secure_store tests.test_ops_ssh tests.test_iface_request_test tests.test_release_secrets -v
python scripts/scan_release_secrets.py
```

打包：

```powershell
.\scripts\build_release.ps1
```

`build_release.ps1` 在 PyInstaller 前执行敏感扫描；失败则中止。

## 5. 手工安测检查清单

- [ ] 新增 SSH 服务器并保存：磁盘 `password_token` 为 `dpapi:` 或 `enc:`，非明文/`b64:`
- [ ] 将历史 `b64:` 服务器重新打开并保存后，token 升级为强加密
- [ ] 请求测试访问 `https://` 公共站点：默认证书校验失败时有明确错误（无效自签）
- [ ] 取消勾选「校验 HTTPS 证书」后，内网自签可通（若环境具备）
- [ ] 向非本机 HTTP 地址发送：弹出远程确认；取消则不发包
- [ ] `localhost` 请求：不弹远程确认
- [ ] 设置页关闭「远程请求确认」后：非本机不再弹窗
- [ ] `python scripts/scan_release_secrets.py` 退出码 0
- [ ] 安装包 `resources` 内无 JWT/VPN/生产密码样本

## 6. 已知残余风险（测评可写「残余风险」）

1. **本机管理员 / 同用户可读 `data/`**：DPAPI 绑定当前 Windows 用户，同用户进程可解密；无法防本机恶意软件。
2. **Fernet 密钥文件**：非 Windows 或 DPAPI 失败时使用 `data/.ops_ssh_key`，与密文同盘，防护弱于 DPAPI。
3. **用户主动关闭 SSL/确认**：功能保留以支持内网联调，会降低防护，需制度约束。
4. **PyQt6 GPL/商业许可**：闭源分发需公司确认许可，见 `docs/SECURITY.md`。
5. **DEFAULT_SYSTEMS 中的地址模板**：为交付路径模板字段，用户应在本机改为真实环境；勿把含生产账密的 `systems.json` 打进安装包。

## 7. 相关文档

- `docs/SECURITY.md` — 分发与敏感信息纪律
- `scripts/scan_release_secrets.py` — 发布前扫描
- `tools/secure_store.py` — 凭据保护实现
