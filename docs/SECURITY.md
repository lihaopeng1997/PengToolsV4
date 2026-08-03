# PengToolsHub 安全与分发基线

> 面向公司内使用与离线安装包发布。非法律意见。  
> **安测对照清单与控制点**见：`docs/SECURITY_TEST_BASELINE.md`。

## 必须遵守

1. **禁止**把生产/模拟环境账号、密码、VPN、堡垒机、JWT、私钥写入：
   - `resources/`（会打进 EXE）
   - 可随安装包分发的任何路径
2. 用户私有笔记、主机密码只允许落在本机 `data/`（`local_data_dir()`）。
3. 发布前必须执行敏感扫描：
   ```powershell
   python scripts/scan_release_secrets.py
   ```
   `scripts/build_release.ps1` 已在打包前自动调用；扫描失败则中止打包。
4. 历史若曾打包过含密种子：按**已泄露**处理，相关凭据应轮换。

## 内置学习种子

| 文件 | 要求 |
|------|------|
| `resources/private_knowledge_seed.txt` | 仅安全说明/假数据 |
| `resources/private_knowledge_seed_workbooks.json` | 默认 `[]` |

运行时：`load_seed_entries()` 会跳过带明显敏感模式的内置表。

## 安测相关默认策略

| 项 | 默认 | 实现 |
|----|------|------|
| HTTPS 证书校验 | 开启 | `security_ssl_verify` + `send_http_request(verify_ssl=…)` |
| 远程请求确认 | 开启 | `security_confirm_remote_request`；本机回环免确认 |
| SSH 密码存储 | DPAPI 优先 | `tools/secure_store.py` → `dpapi:` / `enc:`；**禁止新写 `b64:`** |
| 设置入口 | 设置 → 安全与安测 | 可按联调需要放宽（需制度约束） |

## 能力边界（摘要）

| 能力 | 边界 |
|------|------|
| 接口抓包 | 仅 `127.0.0.1` MITM；报文默认内存 |
| 请求测试 | 可访问用户填写的环境 URL；默认校验证书 + 非本机二次确认 |
| SSH 日志排查 | 密码 DPAPI/Fernet 存 `data/`；本机同用户仍可解密 |
| 无云同步/遥测 | 产品不上传账号体系数据 |

## 开源许可提示

- **PyQt6** 为 GPL 或商业双许可；闭源大规模分发前请确认公司是否持有商业许可，或评估迁移 **PySide6（LGPL）**。
- 建议维护 `THIRD_PARTY_NOTICES.md`（依赖清单）。

## 发布检查清单

- [ ] `python scripts/scan_release_secrets.py` 通过
- [ ] 种子文件无真实账密
- [ ] `Installer/data` 未打进用户数据
- [ ] 已知历史敏感包已停止传播
- [ ] 安测基线文档与当前版本策略一致（`SECURITY_TEST_BASELINE.md`）
