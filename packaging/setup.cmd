@echo off
chcp 65001 >nul
setlocal
set "source_dir=%~dp0"
set "app_dir=%~dp0PengToolsHub\"
set "app_exe=%app_dir%PengToolsHub.exe"

echo PengToolsHub 离线安装（程序目录版）
echo 本脚本只做安装前检查并创建数据目录，日常使用直接双击 PengToolsHub\PengToolsHub.exe。
echo.

if not exist "%app_exe%" (
    echo 未找到 %app_exe%
    echo 请确认解压后 setup.cmd 与 PengToolsHub 文件夹在同一目录。
    pause
    exit /b 1
)

if not exist "%app_dir%data" mkdir "%app_dir%data"

echo 程序目录：%app_dir%
echo 数据目录：%app_dir%data
echo 启动方式：双击 PengToolsHub\PengToolsHub.exe
echo.
echo 升级说明：替换 PengToolsHub 文件夹内的程序文件（EXE 与 _internal 等），
echo           必须保留 PengToolsHub\data 文件夹（里面是你的需求、日报、设置）。
echo.
pause
endlocal
