# Double-click pack.cmd at the repo root. This wrapper does not add a second
# PyInstaller recipe; scripts/build_release.ps1 remains the only release build.
param([switch]$CheckOnly)

$ErrorActionPreference = 'Continue'

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$ProjectDir = (Resolve-Path (Join-Path $ScriptDir '..')).Path
Set-Location -LiteralPath $ProjectDir

$logDir = Join-Path $ProjectDir 'build'
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$logPath = Join-Path $logDir 'pack-last.log'

try {
    Start-Transcript -LiteralPath $logPath -Force | Out-Null
} catch {
    Write-Host "无法写日志 $logPath : $($_.Exception.Message)"
}

Write-Host ''
Write-Host 'PengToolsHub 一键打包'
Write-Host "仓库: $ProjectDir"
Write-Host '若 dist\PengToolsHub 或 Installer\PengToolsHub 里的 EXE 正被占用，打包会停下来。本脚本不会结束这些进程。'
Write-Host 'PyInstaller 通常要十几分钟。完成前不要关闭窗口。'
Write-Host ''

if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir 'run.py'))) {
    Write-Host '未找到 run.py，当前目录不是仓库根。'
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}

$buildScript = Join-Path $ScriptDir 'build_release.ps1'
if (-not (Test-Path -LiteralPath $buildScript)) {
    Write-Host "未找到构建脚本: $buildScript"
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}

# 构建环境解析：显式变量 > 隔离环境 .venv-build > 自动补建。
# 具体的版本/依赖校验由 build_release.ps1 负责，这里只保证解释器存在。
$explicitPython = $env:PENGTOOLS_BUILD_PYTHON
if ($explicitPython) {
    if (-not (Test-Path -LiteralPath $explicitPython)) {
        Write-Host "PENGTOOLS_BUILD_PYTHON 指向的 Python 不存在: $explicitPython"
        Write-Host '请修正该环境变量，或清除它以便使用 .venv-build。'
        try { Stop-Transcript | Out-Null } catch {}
        exit 1
    }
    $buildPython = $explicitPython
    Write-Host "构建环境（来自 PENGTOOLS_BUILD_PYTHON）: $buildPython"
} else {
    $buildPython = Join-Path $ProjectDir '.venv-build\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $buildPython)) {
        $setupScript = Join-Path $ScriptDir 'setup_build_env.ps1'
        Write-Host "未找到构建环境: $buildPython"
        if (-not (Test-Path -LiteralPath $setupScript)) {
            Write-Host "未找到环境安装脚本: $setupScript"
            try { Stop-Transcript | Out-Null } catch {}
            exit 1
        }
        Write-Host '首次打包需要先创建独立构建环境，会下载 PyInstaller 与依赖，通常 3-10 分钟。'
        Write-Host '现在自动创建（需要联网）……'
        Write-Host ''
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setupScript
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $buildPython)) {
            Write-Host ''
            Write-Host "自动创建构建环境失败（退出码 $LASTEXITCODE）。请手动执行后重试："
            Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_build_env.ps1'
            Write-Host '若已有合规环境，可用 PENGTOOLS_BUILD_PYTHON 指向其 python.exe。'
            try { Stop-Transcript | Out-Null } catch {}
            exit 1
        }
        Write-Host ''
        Write-Host "构建环境就绪: $buildPython"
    }
}

if ($CheckOnly) {
    Write-Host "环境检查通过，可以开始打包: $buildPython"
    Write-Host '去掉 -Check 即可执行完整打包。'
    try { Stop-Transcript | Out-Null } catch {}
    exit 0
}

$started = Get-Date
# Separate powershell process so this wrapper does not turn PyInstaller stderr into a failed command.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $buildScript
$buildExit = $LASTEXITCODE
if ($null -eq $buildExit) { $buildExit = 0 }

$zip = Join-Path $ProjectDir 'PengToolsHub_Offline_Setup.zip'
$distExe = Join-Path $ProjectDir 'dist\PengToolsHub\PengToolsHub.exe'
$installerExe = Join-Path $ProjectDir 'Installer\PengToolsHub\PengToolsHub.exe'

function Test-FreshFile([string]$Path, [datetime]$Since) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path
    return ($item.LastWriteTime -ge $Since.AddSeconds(-2))
}

$zipOk = Test-FreshFile $zip $started
$distOk = Test-FreshFile $distExe $started
$instOk = Test-FreshFile $installerExe $started
$fresh = $zipOk -and $distOk -and $instOk

Write-Host ''
Write-Host '---------- 结果 ----------'
if ($buildExit -ne 0 -and $fresh) {
    Write-Host "构建脚本退出码 $buildExit。ZIP、dist EXE、Installer EXE 的修改时间都在本次启动之后，按成功计。"
    Write-Host 'PyInstaller 把进度写到 stderr 时，外层有时会把这次运行看成失败。以这三个文件为准。'
}
if (-not $fresh) {
    Write-Host "打包没有完成（构建退出码 $buildExit）。"
    if (-not $zipOk) { Write-Host "缺少或不是本次生成的 ZIP: $zip" }
    if (-not $distOk) { Write-Host "缺少或不是本次生成的 EXE: $distExe" }
    if (-not $instOk) { Write-Host "缺少或不是本次生成的安装副本: $installerExe" }
    Write-Host "日志: $logPath"
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}

$zipMb = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)
Write-Host "$zip  ($zipMb MB)"
Write-Host $distExe
Write-Host $installerExe

$infoPath = Join-Path $ProjectDir 'resources\build_info.json'
if (Test-Path -LiteralPath $infoPath) {
    try {
        $info = Get-Content -LiteralPath $infoPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host ("build_info: version {0} / {1} / {2}" -f $info.version, $info.edition, $info.build_time)
    } catch {
        Write-Host "build_info 读失败: $($_.Exception.Message)"
    }
}
Write-Host '升级时只替换程序文件，保留程序旁边的 data 目录。'
Write-Host "日志: $logPath"

try { Stop-Transcript | Out-Null } catch {}
exit 0
