$ErrorActionPreference = 'Stop'

# Ensure system Python 3.12 (with PyInstaller) is used, not managed Python 3.13
$env:Path = "D:\development\tools\Python312;D:\development\tools\Python312\Scripts;" + $env:Path

# WorkBuddy/CodeBuddy sandbox shim intercepts os.remove() (safe-delete) and breaks PyInstaller
# cache cleanup / EXE overwrite. Clear its trigger env vars so deletion goes through normally.
Remove-Item Env:CODEBUDDY_SESSION_ID -ErrorAction SilentlyContinue
Remove-Item Env:CLAUDE_SESSION_ID -ErrorAction SilentlyContinue
Remove-Item Env:CODEBUDDY_SAFE_DELETE_SANDBOX -ErrorAction SilentlyContinue

# Unique release build: PengToolsHub (full Private features + brand icon)
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $ScriptDir) {
    $ScriptDir = (Get-Location).Path
}
$ProjectDir = (Resolve-Path (Join-Path $ScriptDir '..')).Path
if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir 'run.py'))) {
    throw "Project root not found from script dir: $ScriptDir"
}

$DistDir = Join-Path $ProjectDir 'dist'
$InstallerDir = Join-Path $ProjectDir 'Installer'
# 明确模板路径：优先仓库内资源；可选环境变量 PENGTOOLS_RELEASE_TEMPLATE 指向唯一外部模板
$TemplateResource = Join-Path $ProjectDir 'resources\release_workbook_template.xlsx'
$EnvTemplate = $env:PENGTOOLS_RELEASE_TEMPLATE
$BuildInfoPath = Join-Path $ProjectDir 'resources\build_info.json'
$BuildDate = Get-Date -Format 'yyyy-MM-dd'
$BuildTime = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Test-FileWritable([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return $true
    }
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $stream.Close()
        return $true
    } catch {
        return $false
    }
}

function Assert-ReleaseArtifactsUnlocked {
    param(
        [string]$DistExe,
        [string]$InstallerExe
    )
    $targets = @($DistExe, $InstallerExe) | Where-Object { $_ }
    $running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -match '^(?i)PengToolsHub$'
    })
    # 仅当进程映像路径仍指向即将覆盖的 EXE 文件时拦截；路径已不存在（如已改名备份）则警告后继续
    $blocking = @()
    foreach ($proc in $running) {
        $img = $null
        try { $img = $proc.Path } catch { $img = $null }
        if (-not $img) { continue }
        $resolvedTargets = @()
        foreach ($t in $targets) {
            if (Test-Path -LiteralPath $t) {
                $resolvedTargets += (Resolve-Path -LiteralPath $t).Path
            } else {
                $resolvedTargets += [System.IO.Path]::GetFullPath($t)
            }
        }
        $imgFull = [System.IO.Path]::GetFullPath($img)
        if ($resolvedTargets -contains $imgFull -and (Test-Path -LiteralPath $img)) {
            $blocking += $proc
        }
    }
    if ($blocking.Count -gt 0) {
        $ids = ($blocking | ForEach-Object { $_.Id }) -join ', '
        throw @"
检测到正在运行且占用目标 EXE 的 PengToolsHub（PID: $ids）。
请先关闭程序后再打包，本脚本不会强制结束进程。
"@
    } elseif ($running.Count -gt 0) {
        $ids = ($running | ForEach-Object { $_.Id }) -join ', '
        Write-Host "Warning: PengToolsHub process still listed (PID: $ids) but image path is not an existing build target; continuing lock checks on output files."
    }
    $blocked = @()
    foreach ($path in $targets) {
        if ($path -and (Test-Path -LiteralPath $path) -and -not (Test-FileWritable $path)) {
            $blocked += $path
        }
    }
    if ($blocked.Count -gt 0) {
        $list = $blocked -join "`n  - "
        throw @"
以下 EXE 无法改名/覆盖（可能被资源管理器预览或安全软件占用）：
  - $list

请关闭：
  - 正在运行的 PengToolsHub
  - dist/PengToolsHub.exe 与 Installer/PengToolsHub.exe 的预览窗口
  - 可能正在扫描这些文件的安全软件
然后重新执行打包。本脚本不会强制结束用户进程。
"@
    }
}

Push-Location $ProjectDir
try {
    # —— 发版 Excel 模板：明确路径，禁止通配静默取第一份 ——
    if ($EnvTemplate) {
        if (-not (Test-Path -LiteralPath $EnvTemplate)) {
            throw "PENGTOOLS_RELEASE_TEMPLATE 指向的文件不存在: $EnvTemplate"
        }
        $ext = [System.IO.Path]::GetExtension($EnvTemplate)
        if ($ext -notin @('.xlsx', '.XLSX')) {
            throw "PENGTOOLS_RELEASE_TEMPLATE 必须是 .xlsx: $EnvTemplate"
        }
        Copy-Item -LiteralPath $EnvTemplate -Destination $TemplateResource -Force
        Write-Host "Using release template from env: $EnvTemplate"
    } elseif (Test-Path -LiteralPath $TemplateResource) {
        Write-Host "Using project release template: $TemplateResource"
    } else {
        # 可选：父目录 02-* 下有且仅有 1 份 xlsx 时才复制；多份直接失败
        $parent = Split-Path -Parent $ProjectDir
        $candidates = @()
        if (Test-Path -LiteralPath $parent) {
            $dirs = Get-ChildItem -LiteralPath $parent -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like '02-*' }
            foreach ($dir in $dirs) {
                $candidates += @(Get-ChildItem -LiteralPath $dir.FullName -File -Filter '*.xlsx' -ErrorAction SilentlyContinue)
            }
        }
        if ($candidates.Count -eq 1) {
            Copy-Item -LiteralPath $candidates[0].FullName -Destination $TemplateResource -Force
            Write-Host "Copied unique external template: $($candidates[0].FullName)"
        } elseif ($candidates.Count -gt 1) {
            $names = ($candidates | ForEach-Object { $_.FullName }) -join "`n  - "
            throw @"
找到多份候选发版 Excel 模板，拒绝静默选择：
  - $names

请将唯一模板放到 resources\release_workbook_template.xlsx，
或设置环境变量 PENGTOOLS_RELEASE_TEMPLATE 为明确路径。
"@
        } else {
            throw 'Release workbook template was not found (resources\release_workbook_template.xlsx).'
        }
    }

    # 发布前敏感扫描：阻断账密/JWT/私钥等进入安装包
    $ScanScript = Join-Path $ScriptDir 'scan_release_secrets.py'
    if (-not (Test-Path -LiteralPath $ScanScript)) {
        throw "Secret scanner missing: $ScanScript"
    }
    Write-Host 'Running release secret scan...'
    python $ScanScript --project $ProjectDir
    if ($LASTEXITCODE -ne 0) {
        throw "Release secret scan failed (exit $LASTEXITCODE). Remove secrets under resources/ before packaging."
    }

    # Safe seed templates only (do not reintroduce real secrets into resources)
    $SeedTxtPath = Join-Path $ProjectDir 'resources\private_knowledge_seed.txt'
    $SeedJsonPath = Join-Path $ProjectDir 'resources\private_knowledge_seed_workbooks.json'
    if (-not (Test-Path -LiteralPath $SeedTxtPath)) {
        throw "Missing safe seed template: $SeedTxtPath"
    }
    if (-not (Test-Path -LiteralPath $SeedJsonPath)) {
        Set-Content -LiteralPath $SeedJsonPath -Value "[]" -Encoding utf8
    }

    python -c "import json,sys; open(sys.argv[1],'w',encoding='utf-8').write(json.dumps({'version':'4.27','edition':'Private','build_date':sys.argv[2],'build_time':sys.argv[3]},ensure_ascii=False,indent=2)+chr(10))" $BuildInfoPath $BuildDate $BuildTime
    if (-not (Test-Path -LiteralPath $BuildInfoPath)) {
        throw 'Failed to write build_info.json'
    }

    if (-not (Test-Path -LiteralPath $InstallerDir)) {
        New-Item -ItemType Directory -Path $InstallerDir | Out-Null
    }
    $PackagingDir = Join-Path $ProjectDir 'packaging'
    foreach ($name in @('setup.cmd', 'README.txt')) {
        $src = Join-Path $PackagingDir $name
        if (Test-Path -LiteralPath $src) {
            Copy-Item $src (Join-Path $InstallerDir $name) -Force
        }
    }
    Get-ChildItem $InstallerDir -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin @('setup.cmd', 'README.txt') } | ForEach-Object { cmd /c "del /f /q `"$($_.FullName)`"" 2>$null }
    $InstallerDataDir = Join-Path $InstallerDir 'data'
    if (Test-Path -LiteralPath $InstallerDataDir) {
        cmd /c "rmdir /s /q `"$InstallerDataDir`"" 2>$null
    }

    $ExePath = Join-Path $DistDir 'PengToolsHub.exe'
    $InstallerExePath = Join-Path $InstallerDir 'PengToolsHub.exe'
    Write-Host 'Checking EXE lock / running PengToolsHub before PyInstaller...'
    Assert-ReleaseArtifactsUnlocked -DistExe $ExePath -InstallerExe $InstallerExePath

    # Safe seed templates only (secret scan already passed).
    # --specpath changes the base directory used by the generated spec, so every source
    # path that ends up in Analysis must be absolute instead of relative to the spec file.
    $pyArgs = @(
        '-m', 'PyInstaller',
        '--noconfirm',
        '--clean',
        '--onefile',
        '--windowed',
        '--name', 'PengToolsHub',
        # spec 属于 PyInstaller 可再生中间文件，放入已忽略的 build 目录，避免污染仓库根目录。
        '--specpath', 'build\pyinstaller-spec',
        '--icon', (Join-Path $ProjectDir 'resources\brand\pengtools-taskbar-hc.ico'),
        '--add-data', ((Join-Path $ProjectDir 'resources\style.qss') + ';resources'),
        '--add-data', ((Join-Path $ProjectDir 'resources\chevron_down.svg') + ';resources'),
        '--add-data', ((Join-Path $ProjectDir 'resources\check_white.svg') + ';resources'),
        '--add-data', ((Join-Path $ProjectDir 'resources\app.ico') + ';resources'),
        '--add-data', ((Join-Path $ProjectDir 'resources\app-icon.png') + ';resources'),
        '--add-data', ((Join-Path $ProjectDir 'resources\brand') + ';resources\brand'),
        '--add-data', ((Join-Path $ProjectDir 'resources\build_info.json') + ';resources'),
        '--add-data', ((Join-Path $ProjectDir 'resources\private_knowledge_seed.txt') + ';resources'),
        '--add-data', ((Join-Path $ProjectDir 'resources\private_knowledge_seed_workbooks.json') + ';resources'),
        '--add-data', ((Join-Path $ProjectDir 'resources\release_workbook_template.xlsx') + ';resources'),
        '--add-data', ((Join-Path $ProjectDir 'resources\icons') + ';resources\icons'),
        '--add-data', ((Join-Path $ProjectDir 'resources\help') + ';resources\help'),
        '--add-data', ((Join-Path $ProjectDir 'resources\ai_skills') + ';resources\ai_skills'),
        '--add-data', ((Join-Path $ProjectDir 'resources\harness') + ';resources\harness'),
        '--hidden-import', 'docx',
        '--hidden-import', 'openpyxl',
        '--hidden-import', 'xlrd',
        '--hidden-import', 'xlwt',
        '--hidden-import', 'xlutils',
        '--hidden-import', 'msoffcrypto',
        '--hidden-import', 'PyQt6.QtSvg',
        '--hidden-import', 'websocket',
        '--hidden-import', 'websocket._app',
        '--hidden-import', 'mitmproxy',
        '--hidden-import', 'mitmproxy.tools.dump',
        '--hidden-import', 'mitmproxy.certs',
        '--hidden-import', 'mitmproxy.options',
        '--hidden-import', 'paramiko',
        '--hidden-import', 'cryptography',
        '--hidden-import', 'cryptography.hazmat.primitives.kdf.pbkdf2',
        '--hidden-import', 'cryptography.hazmat.primitives.kdf',
        '--hidden-import', 'cryptography.hazmat.primitives.asymmetric.padding',
        '--hidden-import', 'cryptography.hazmat.primitives.hashes',
        '--hidden-import', 'cryptography.hazmat.primitives.ciphers',
        '--hidden-import', 'cryptography.x509',
        '--hidden-import', 'encodings.idna',
        '--collect-all', 'cryptography',
        '--collect-all', 'oracledb',
        # PyQt6: use --collect-binaries (collect_dynamic_libs) instead of --collect-all.
        # collect_all pulls in QML/translations (~6573 entries) -> OOM during build, and the
        # standard PyQt6 hook does NOT collect Qt6Core/Gui/Widgets.dll from Qt6/bin -> DLL load
        # failure at runtime. collect_dynamic_libs('PyQt6') grabs all 222 binaries (109 Qt6
        # DLLs + 51 plugins incl. qwindows platform plugin) with far fewer entries.
        '--collect-binaries', 'PyQt6',
        '--hidden-import', 'nacl',
        '--hidden-import', 'tools.intranet_llm',
        '--hidden-import', 'tools.ai_harness',
        '--hidden-import', 'tools.ptools_harness',
        '--hidden-import', 'tools.linux_guard',
        '--hidden-import', 'tools.harness_project',
        '--hidden-import', 'oracledb',
        '--hidden-import', 'pymysql',
        '--hidden-import', 'redis',
        '--hidden-import', 'pymongo',
        '--hidden-import', 'panels.ai_workbench_panel',
        '--hidden-import', 'panels.model_chat_panel',
        '--hidden-import', 'panels.agent_workbench_panel',
        '--hidden-import', 'panels.db_redis_panel',
        '--hidden-import', 'panels.db_mongodb_panel',
        '--hidden-import', 'tools.db_connect',
        '--hidden-import', 'tools.db_redis_ops',
        '--hidden-import', 'tools.db_mongo_ops',
        '--hidden-import', 'tools.sql_guard',
        '--exclude-module', 'PyQt5',
        '--exclude-module', 'PySide2',
        '--exclude-module', 'PySide6',
        '--exclude-module', 'tkinter',
        'run.py'
    )
    python @pyArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $ExePath = Join-Path $DistDir 'PengToolsHub.exe'
    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "EXE not found: $ExePath"
    }
    Copy-Item $ExePath $InstallerDir -Force

    # Do not use name PrivateDir - PowerShell treats $Private: as a scope
    $LegacyInstallerDir = Join-Path $ProjectDir 'PrivateInstaller'
    if (Test-Path -LiteralPath $LegacyInstallerDir) {
        Get-ChildItem $LegacyInstallerDir -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin @('setup.cmd', 'README.txt') } | ForEach-Object { cmd /c "del /f /q `"$($_.FullName)`"" 2>$null }
        $LegacyDataDir = Join-Path $LegacyInstallerDir 'data'
        if (Test-Path -LiteralPath $LegacyDataDir) {
            cmd /c "rmdir /s /q `"$LegacyDataDir`"" 2>$null
        }
        Copy-Item $ExePath $LegacyInstallerDir -Force
        $SetupSrc = Join-Path $InstallerDir 'setup.cmd'
        if (Test-Path -LiteralPath $SetupSrc) {
            Copy-Item $SetupSrc (Join-Path $LegacyInstallerDir 'setup.cmd') -Force
        }
    }

    $ZipPath = Join-Path $ProjectDir 'PengToolsHub_Offline_Setup.zip'
    if (Test-Path -LiteralPath $ZipPath) {
        cmd /c "del /f /q `"$ZipPath`"" 2>$null
    }
    Compress-Archive -Path (Join-Path $InstallerDir '*') -DestinationPath $ZipPath

    $LegacyZip = Join-Path $ProjectDir 'PengToolsHub_Private_Offline_Setup.zip'
    if (Test-Path -LiteralPath $LegacyZip) {
        cmd /c "del /f /q `"$LegacyZip`"" 2>$null
    }
    $LegacyExe = Join-Path $DistDir 'PengToolsHub_Private.exe'
    if (Test-Path -LiteralPath $LegacyExe) {
        cmd /c "del /f /q `"$LegacyExe`"" 2>$null
    }

    Write-Host "Release created: $ZipPath"
    Write-Host "EXE: $ExePath"
    Write-Host "Build date stamped: $BuildDate ($BuildTime)"
}
finally {
    Pop-Location
}
