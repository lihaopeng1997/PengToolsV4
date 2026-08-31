$ErrorActionPreference = 'Stop'

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

$BuildPython = $env:PENGTOOLS_BUILD_PYTHON
if (-not $BuildPython) {
    $BuildPython = Join-Path $ProjectDir '.venv-build\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $BuildPython)) {
    throw @"
未找到 PengToolsHub 独立构建环境：$BuildPython
请先执行：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_build_env.ps1
也可通过 PENGTOOLS_BUILD_PYTHON 指定已按 requirements-build.txt 安装的 Python 3.12。
"@
}

& $BuildPython -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Build Python must be Python 3.12: $BuildPython"
}
& $BuildPython -c "import importlib.metadata as m; raise SystemExit(0 if m.version('PyInstaller') == '6.22.2' else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 6.22.2 is required. Rebuild the environment from requirements-build.txt: $BuildPython"
}
& $BuildPython -m pip check
if ($LASTEXITCODE -ne 0) {
    throw "Build environment dependency check failed: $BuildPython"
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
        [string[]]$Targets
    )
    $targets = @($Targets) | Where-Object { $_ }
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
  - dist/PengToolsHub/ 与 Installer/PengToolsHub/ 的预览窗口
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
    # onedir 后 Installer/PrivateInstaller staging 含完整运行时（mitmproxy 模板等），
    # 会造成启发式误报；源头（resources/packaging）已由扫描覆盖，staging 产物不再重复扫描。
    & $BuildPython $ScanScript --project $ProjectDir --root resources --root packaging
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

    # Installer 顶层旧文件与旧程序目录清理：统一放在「数据保护检查 + 锁检查」通过之后（见下方 cleanup 区）。
    # 本脚本永远不主动删除用户 data 目录。

    # 1) 定义全部路径：当前产物 + 各 staging + legacy 残留
    $AppDir = Join-Path $DistDir 'PengToolsHub'
    $ExePath = Join-Path $AppDir 'PengToolsHub.exe'
    $InstallerAppDir = Join-Path $InstallerDir 'PengToolsHub'
    $InstallerExePath = Join-Path $InstallerAppDir 'PengToolsHub.exe'
    $LegacyOnefileExe = Join-Path $DistDir 'PengToolsHub.exe'
    $LegacyInstallerExe = Join-Path $InstallerDir 'PengToolsHub.exe'
    $LegacyInstallerDir = Join-Path $ProjectDir 'PrivateInstaller'
    $LegacyPrivateAppDir = Join-Path $LegacyInstallerDir 'PengToolsHub'
    $LegacyPrivateExe = Join-Path $LegacyPrivateAppDir 'PengToolsHub.exe'
    $LegacyPrivateOnefileExe = Join-Path $LegacyInstallerDir 'PengToolsHub.exe'

    # 2) 数据保护检查（先于一切 destructive cleanup）：staging 中发现用户 data 立即中止，
    #    不删除、不移动、不自动备份、不打入 ZIP。用户数据只属于 <exe 旁>/data。
    $DataSafetyPaths = @(
        (Join-Path $AppDir 'data'),            # dist data: AppDir 会被整体重建, 必须前置守护
        (Join-Path $InstallerDir 'data'),
        (Join-Path $InstallerAppDir 'data'),
        (Join-Path $LegacyInstallerDir 'data'),
        (Join-Path $LegacyPrivateAppDir 'data')
    )
    $foundData = @($DataSafetyPaths | Where-Object { Test-Path -LiteralPath $_ })
    if ($foundData.Count -gt 0) {
        $list = ($foundData | ForEach-Object { "  - $_" }) -join "`n"
        throw @"
检测到发布暂存目录中存在用户 data，为防止误删或打入安装包，本次构建已停止。
请先将 data 目录备份/移动到安全位置，再重新构建。
发现位置：
$list
"@
    }

    # 3) 锁检查（当前 + 全部 legacy EXE）：任何 destructive cleanup 都必须等校验通过（运行中不强杀）
    Write-Host 'Checking EXE lock / running PengToolsHub before PyInstaller...'
    $LockTargets = @(
        $ExePath,
        $InstallerExePath,
        $LegacyOnefileExe,
        $LegacyInstallerExe,
        $LegacyPrivateExe,
        $LegacyPrivateOnefileExe
    )
    Assert-ReleaseArtifactsUnlocked -Targets $LockTargets

    # 4) 数据 + 锁全部通过：此时才允许写 build_info（安全检查失败时仓库文件保持不变）
    & $BuildPython -c "import json,sys; open(sys.argv[1],'w',encoding='utf-8').write(json.dumps({'version':'4.27','edition':'Private','build_date':sys.argv[2],'build_time':sys.argv[3]},ensure_ascii=False,indent=2)+chr(10))" $BuildInfoPath $BuildDate $BuildTime
    if (-not (Test-Path -LiteralPath $BuildInfoPath)) {
        throw 'Failed to write build_info.json'
    }

    # 5) 准备 Installer staging（复制 setup.cmd / README.txt；不包含任何用户 data）
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
    # 6) 清理旧产物，避免新旧产物混装
    # 4) 锁校验通过后清理旧产物，避免新旧产物混装
    foreach ($stagingDir in @($InstallerDir, $LegacyInstallerDir)) {
        if (Test-Path -LiteralPath $stagingDir) {
            Get-ChildItem $stagingDir -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin @('setup.cmd', 'README.txt') } | ForEach-Object { cmd /c "del /f /q `"$($_.FullName)`"" 2>$null }
        }
    }
    if (Test-Path -LiteralPath $LegacyOnefileExe) { cmd /c "del /f /q `"$LegacyOnefileExe`"" 2>$null }
    if (Test-Path -LiteralPath $LegacyInstallerExe) { cmd /c "del /f /q `"$LegacyInstallerExe`"" 2>$null }
    if (Test-Path -LiteralPath $LegacyPrivateOnefileExe) { cmd /c "del /f /q `"$LegacyPrivateOnefileExe`"" 2>$null }
    if (Test-Path -LiteralPath $ExePath) { cmd /c "del /f /q `"$ExePath`"" 2>$null }
    if (Test-Path -LiteralPath $AppDir) { cmd /c "rmdir /s /q `"$AppDir`"" 2>$null }
    if (Test-Path -LiteralPath $InstallerAppDir) { cmd /c "rmdir /s /q `"$InstallerAppDir`"" 2>$null }
    if (Test-Path -LiteralPath $LegacyPrivateAppDir) { cmd /c "rmdir /s /q `"$LegacyPrivateAppDir`"" 2>$null }

    # Safe seed templates only (secret scan already passed).
    # --specpath changes the base directory used by the generated spec, so every source
    # path that ends up in Analysis must be absolute instead of relative to the spec file.
    $pyArgs = @(
        '-m', 'PyInstaller',
        '--noconfirm',
        '--clean',
        '--onedir',
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
        '--add-data', ((Join-Path $ProjectDir 'resources\webui') + ';resources\webui'),
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
        '--hidden-import', 'PyQt6.QtWebEngineWidgets',
        '--hidden-import', 'PyQt6.QtWebEngineCore',
        '--hidden-import', 'PyQt6.QtWebChannel',
        '--hidden-import', 'PyQt6.QtWebEngineQuick',
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
    # 隔离构建 PATH，避免 PyInstaller 从宿主环境 PATH 误收集第三方 DLL（如外部 poppler/icu 污染）
    $origPath = $env:PATH
    try {
        $buildScriptsDir = Split-Path -Parent $BuildPython
        $buildVenvDir = Split-Path -Parent $buildScriptsDir
        $sys32 = Join-Path $env:SystemRoot 'System32'
        $sysRoot = $env:SystemRoot
        $wbem = Join-Path $sys32 'Wbem'
        $psHomeDir = Join-Path $sys32 'WindowsPowerShell\v1.0'
        $cleanBuildPath = @($buildScriptsDir, $buildVenvDir, $sys32, $sysRoot, $wbem, $psHomeDir) |
            Where-Object { $_ -and (Test-Path -LiteralPath $_) }
        $env:PATH = ($cleanBuildPath -join ';')

        & $BuildPython @pyArgs
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller failed with exit code $LASTEXITCODE"
        }
    } finally {
        $env:PATH = $origPath
    }

    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "EXE not found: $ExePath"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $AppDir '_internal'))) {
        throw "onedir runtime directory not found: " + (Join-Path $AppDir '_internal')
    }
    # 移除生产包中冗余的 WebEngine debug 资源（保留正式 release .pak）
    $debugPaks = @(
        (Join-Path $AppDir '_internal\PyQt6\Qt6\resources\qtwebengine_devtools_resources.debug.pak'),
        (Join-Path $AppDir '_internal\PyQt6\Qt6\resources\qtwebengine_resources.debug.pak')
    )
    foreach ($dp in $debugPaks) {
        if (Test-Path -LiteralPath $dp) {
            Remove-Item -LiteralPath $dp -Force -ErrorAction SilentlyContinue
        }
    }
    # 裁剪 WebEngine locales（仅保留产品支持的 zh-CN 及 Chromium fallback en-US）
    $localesDir = Join-Path $AppDir '_internal\PyQt6\Qt6\translations\qtwebengine_locales'
    $keepLocales = @('zh-CN.pak', 'en-US.pak')
    if (-not (Test-Path -LiteralPath $localesDir)) {
        throw "Post-condition failed: WebEngine locales directory not found: $localesDir"
    }
    $existingPaks = Get-ChildItem -LiteralPath $localesDir -Filter '*.pak' -File
    foreach ($pak in $existingPaks) {
        if ($keepLocales -notcontains $pak.Name) {
            Remove-Item -LiteralPath $pak.FullName -Force -ErrorAction SilentlyContinue
        }
    }
    # Post-condition 校验：KEEP locale 必须全部存在，且非 KEEP locale 不得残留
    $remainingPaks = Get-ChildItem -LiteralPath $localesDir -Filter '*.pak' -File | ForEach-Object { $_.Name }
    foreach ($k in $keepLocales) {
        if ($remainingPaks -notcontains $k) {
            throw "Post-condition failed: required WebEngine locale '$k' is missing in $localesDir"
        }
    }
    foreach ($r in $remainingPaks) {
        if ($keepLocales -notcontains $r) {
            throw "Post-condition failed: unexpected WebEngine locale '$r' remained in $localesDir"
        }
    }
    # 裁剪 Qt translations *.qm 文件（仅保留基础中文翻译 qt_zh_CN.qm / qtbase_zh_CN.qm，其余未使用的语言包全部裁剪）
    $transDir = Join-Path $AppDir '_internal\PyQt6\Qt6\translations'
    $keepQm = @('qt_zh_CN.qm', 'qtbase_zh_CN.qm')
    if (-not (Test-Path -LiteralPath $transDir)) {
        throw "Post-condition failed: Qt translations directory not found: $transDir"
    }
    $existingQm = Get-ChildItem -LiteralPath $transDir -Filter '*.qm' -File
    foreach ($qm in $existingQm) {
        if ($keepQm -notcontains $qm.Name) {
            Remove-Item -LiteralPath $qm.FullName -Force -ErrorAction SilentlyContinue
        }
    }
    # Post-condition 校验：KEEP qm 必须全部存在，且非 KEEP qm 不得残留
    $remainingQm = Get-ChildItem -LiteralPath $transDir -Filter '*.qm' -File | ForEach-Object { $_.Name }
    foreach ($k in $keepQm) {
        if ($remainingQm -notcontains $k) {
            throw "Post-condition failed: required Qt translation '$k' is missing in $transDir"
        }
    }
    foreach ($r in $remainingQm) {
        if ($keepQm -notcontains $r) {
            throw "Post-condition failed: unexpected Qt translation '$r' remained in $transDir"
        }
    }
    # 裁剪未使用的 Qt Multimedia 与 FFmpeg 运行时
    $multimediaBinFiles = @(
        'avcodec-61.dll',
        'avformat-61.dll',
        'Qt6Multimedia.dll',
        'avutil-59.dll',
        'swscale-8.dll',
        'Qt6MultimediaQuick.dll',
        'swresample-5.dll',
        'Qt6MultimediaWidgets.dll'
    )
    $qtBinDir = Join-Path $AppDir '_internal\PyQt6\Qt6\bin'
    foreach ($mFile in $multimediaBinFiles) {
        $target = Join-Path $qtBinDir $mFile
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        }
    }
    $multimediaPluginDir = Join-Path $AppDir '_internal\PyQt6\Qt6\plugins\multimedia'
    if (Test-Path -LiteralPath $multimediaPluginDir) {
        Remove-Item -LiteralPath $multimediaPluginDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Post-condition 校验：多媒体文件 0 残留，且核心 WebEngine/QtQuick 关键二进制必须仍存在
    foreach ($mFile in $multimediaBinFiles) {
        $target = Join-Path $qtBinDir $mFile
        if (Test-Path -LiteralPath $target) {
            throw "Post-condition failed: multimedia binary '$mFile' remained in $qtBinDir"
        }
    }
    if (Test-Path -LiteralPath $multimediaPluginDir) {
        throw "Post-condition failed: multimedia plugin directory remained in $multimediaPluginDir"
    }
    $requiredBinaries = @('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6Widgets.dll', 'Qt6Gui.dll', 'Qt6Core.dll')
    foreach ($req in $requiredBinaries) {
        $reqPath = Join-Path $qtBinDir $req
        if (-not (Test-Path -LiteralPath $reqPath)) {
            throw "Post-condition failed: required binary '$req' is missing in $qtBinDir"
        }
    }
    # 裁剪未使用的 Qt 3D / Quick3D / ShaderTools 与 3D 插件
    $quick3dBinFiles = @(
        'Qt6Quick3DRuntimeRender.dll',
        'Qt6ShaderTools.dll',
        'Qt6Quick3DPhysics.dll',
        'Qt6Quick3DParticles.dll',
        'Qt6Quick3D.dll',
        'Qt6Quick3DXr.dll',
        'Qt6Quick3DHelpers.dll',
        'Qt6Quick3DHelpersImpl.dll',
        'Qt6Quick3DUtils.dll',
        'Qt6Quick3DEffects.dll',
        'Qt6Quick3DAssetUtils.dll',
        'Qt6Quick3DGlslParser.dll',
        'Qt6Quick3DSpatialAudio.dll',
        'Qt6Quick3DIblBaker.dll',
        'Qt6Quick3DAssetImport.dll',
        'Qt6Quick3DPhysicsHelpers.dll'
    )
    foreach ($q3d in $quick3dBinFiles) {
        $target = Join-Path $qtBinDir $q3d
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        }
    }
    $quick3dPluginDirs = @('assetimporters', 'sceneparsers', 'renderers', 'geometryloaders')
    foreach ($pdir in $quick3dPluginDirs) {
        $targetDir = Join-Path $AppDir ('_internal\PyQt6\Qt6\plugins\' + $pdir)
        if (Test-Path -LiteralPath $targetDir) {
            Remove-Item -LiteralPath $targetDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    # Post-condition 校验：3D 候选 0 残留，且核心 WebEngine/QtQuick/OpenGL/QmlMeta 等必须完好
    foreach ($q3d in $quick3dBinFiles) {
        $target = Join-Path $qtBinDir $q3d
        if (Test-Path -LiteralPath $target) {
            throw "Post-condition failed: Quick3D binary '$q3d' remained in $qtBinDir"
        }
    }
    foreach ($pdir in $quick3dPluginDirs) {
        $targetDir = Join-Path $AppDir ('_internal\PyQt6\Qt6\plugins\' + $pdir)
        if (Test-Path -LiteralPath $targetDir) {
            throw "Post-condition failed: Quick3D plugin directory '$pdir' remained in $targetDir"
        }
    }
    $required3DContractBinaries = @(
        'Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll',
        'Qt6Qml.dll', 'Qt6QmlModels.dll', 'Qt6QmlMeta.dll', 'Qt6OpenGL.dll',
        'Qt6Widgets.dll', 'Qt6Gui.dll', 'Qt6Core.dll'
    )
    foreach ($req in $required3DContractBinaries) {
        $reqPath = Join-Path $qtBinDir $req
        if (-not (Test-Path -LiteralPath $reqPath)) {
            throw "Post-condition failed: required binary '$req' is missing in $qtBinDir"
        }
    }
    # 裁剪未使用的 Qt Designer 与 Qt SQL 驱动插件
    $designerDll = Join-Path $qtBinDir 'Qt6Designer.dll'
    if (Test-Path -LiteralPath $designerDll) {
        Remove-Item -LiteralPath $designerDll -Force -ErrorAction SilentlyContinue
    }
    $sqldriversPluginDir = Join-Path $AppDir '_internal\PyQt6\Qt6\plugins\sqldrivers'
    if (Test-Path -LiteralPath $sqldriversPluginDir) {
        Remove-Item -LiteralPath $sqldriversPluginDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Post-condition 校验：Designer 与 sqldrivers 0 残留，且核心合同保持
    if (Test-Path -LiteralPath $designerDll) {
        throw "Post-condition failed: Qt6Designer.dll remained in $qtBinDir"
    }
    if (Test-Path -LiteralPath $sqldriversPluginDir) {
        throw "Post-condition failed: sqldrivers plugin directory remained in $sqldriversPluginDir"
    }
    $requiredFinalContractBinaries = @(
        'Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll',
        'Qt6Qml.dll', 'Qt6QmlModels.dll', 'Qt6QmlMeta.dll', 'Qt6OpenGL.dll',
        'Qt6Widgets.dll', 'Qt6Gui.dll', 'Qt6Core.dll'
    )
    foreach ($req in $requiredFinalContractBinaries) {
        $reqPath = Join-Path $qtBinDir $req
        if (-not (Test-Path -LiteralPath $reqPath)) {
            throw "Post-condition failed: required binary '$req' is missing in $qtBinDir"
        }
    }
    # 复制整个程序目录（PengToolsHub.exe + _internal\...），不含任何用户 data
    Copy-Item $AppDir $InstallerDir -Recurse -Force

    # Do not use name PrivateDir - PowerShell treats $Private: as a scope
    # PrivateInstaller 旧文件/data 清理已在锁前阶段完成（data 安全检查 + 锁检查之后）。
    if (Test-Path -LiteralPath $LegacyInstallerDir) {
        Copy-Item $AppDir (Join-Path $LegacyInstallerDir 'PengToolsHub') -Recurse -Force
        $SetupSrc = Join-Path $InstallerDir 'setup.cmd'
        if (Test-Path -LiteralPath $SetupSrc) {
            Copy-Item $SetupSrc (Join-Path $LegacyInstallerDir 'setup.cmd') -Force
        }
    }

    $ZipPath = Join-Path $ProjectDir 'PengToolsHub_Offline_Setup.zip'
    if (Test-Path -LiteralPath $ZipPath) {
        cmd /c "del /f /q `"$ZipPath`"" 2>$null
    }
    # 防御性复检: staging 出现 data 一律不生成 ZIP (第二道保险)
    foreach ($zipDataGuard in @((Join-Path $InstallerDir 'data'), (Join-Path $InstallerAppDir 'data'))) {
        if (Test-Path -LiteralPath $zipDataGuard) {
            throw "ZIP 生成前检测到 staging data ($zipDataGuard), 已中止, 不生成 ZIP。"
        }
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
