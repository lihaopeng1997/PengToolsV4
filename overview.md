# 逻辑审计与修复：实施状态

## 已完成的代码修复
- **自动推断不再覆盖用户选择**：`apply_auto_inference(only_empty=True)` 对布尔标记只在「键缺失」时补全；显式 `False` 与分类「其他」不再被正文关键词勾回。编辑对话框勾选框改为 base 优先。
- **台账原子写入**：`save_requirements` / `save_release_board` 改为临时文件 + `os.replace`，降低崩溃半截 JSON 导致需求全丢的风险。
- **工作台联动补齐**：需求保存 → `requirement_saved`（定位上线月份）；删除/扫描/置顶/SVN 更新等 → `requirements_changed` 触发工作台刷新。
- **Fernet 密钥 fail-closed**：本机密钥文件写失败时拒绝加密，避免用不可持久密钥加密后下次解密为空。
- 工作台双卡高度对齐、本月优先展示、保存后定位月份（上一轮）保留。

## 验证结果
- 定向回归：dashboard / requirement flags / lazy workflow / secure store / core 等（见本轮测试输出）。
- 已知不在本轮改动范围：SSH `AutoAddPolicy`、发版 Excel 追加去重、请求库敏感头落盘、网关私钥外置——记入后续加固。

## 交付
- 源码修复后提交 Git；按项目节奏重建离线包。
