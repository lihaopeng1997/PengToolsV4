@echo off
chcp 65001 >nul
setlocal
set "source_dir=%~dp0"
set "install_dir=%~dp0"

echo PengToolsHub 离线安装
echo 本程序放在当前目录运行。升级时只替换 EXE，不要删除 data 文件夹。
echo.

if not exist "%install_dir%\data" mkdir "%install_dir%\data"
if not exist "%source_dir%PengToolsHub.exe" (
    echo 未找到 PengToolsHub.exe，请把本脚本和 EXE 放在同一目录。
    pause
    exit /b 1
)

echo 数据目录：%install_dir%data
echo 启动：双击 PengToolsHub.exe
echo.
pause
endlocal
