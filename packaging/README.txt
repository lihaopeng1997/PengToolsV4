PengToolsHub 离线安装包（程序目录版）

目录结构：
  setup.cmd            安装前检查 + 创建数据目录
  README.txt           本说明
  PengToolsHub\        程序目录（PengToolsHub.exe 与 _internal 运行时）

使用方法：
1. 解压本目录全部文件到你要放置的文件夹（不要解压到 Program Files 的只读位置）。
2. 双击 setup.cmd：检查程序文件完整，并在 PengToolsHub\ 下创建 data 数据目录。
3. 日常使用请双击 PengToolsHub\PengToolsHub.exe。

升级方法：
1. 用新版本包内的 PengToolsHub 文件夹中的程序文件（EXE 与 _internal 等），
   覆盖替换旧版的同名程序文件。
2. 必须保留 PengToolsHub\data 文件夹（里面是你的需求、日报、设置）。
   升级只替换程序文件，不删除、不覆盖 data。

其它说明：
1. 本工具为离线桌面程序。接口排查仅允许本机 127.0.0.1；证件/VIN 数据仅供测试。
2. SVN 相关功能需在客户内网验证。
