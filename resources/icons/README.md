# PengTools Pulse Icons

统一规格：24 x 24，`fill="none"`，线宽 1.8，圆端/圆角，使用 `currentColor` 着色。

使用方式：页面用 `QIcon(资源路径)` 加载，按钮/导航决定颜色；SVG 本身不固化蓝、红、绿等语义色。主导航建议 20px，普通工具栏 18px，危险确认/页面标题 24px。

命名与用途：

- `requirements`：需求管理
- `release`：升级准备 / 发版
- `shield-key`：网关加解密
- `settings`：设置
- `search`：搜索
- `add` / `delete` / `edit`：新增、删除、编辑
- `expand` / `collapse`：树操作
- `folder-open`：打开文件夹
- `copy`：复制
- `lock` / `unlock`：SVN 锁定状态与操作
- `xml` / `json`：报文工具模式
- `success` / `warning` / `error` / `info`：反馈和对话框语义

禁止在页面内使用 Emoji 代替这些图标；禁止通过在线图标库或 CDN 加载。
