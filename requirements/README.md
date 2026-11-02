# Python 依赖

| 文件 | 用途 |
|---|---|
| 根目录 `requirements.txt` | Python 3.12 运行依赖 |
| `build.txt` | 引入运行依赖并固定 PyInstaller 版本 |
| `development.txt` | 引入构建依赖并提供依赖审计工具 |

使用仓库根目录的 `scripts/setup_build_env.ps1` 创建构建环境，或加 `-Development` 创建开发环境。当前清单不安装测试框架；本机保留的测试环境不随仓库分发。
