$ErrorActionPreference = 'Stop'

# 脚本位于 scripts/，工程根为上一级（兼容 PSScriptRoot 为空的调用方式）
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
$InstallerDir = Join-Path $ProjectDir 'PrivateInstaller'
$OriginalRelease = Join-Path $ProjectDir 'PengToolsHub_Offline_Setup.zip'
$OriginalHash = if (Test-Path -LiteralPath $OriginalRelease) { (Get-FileHash $OriginalRelease -Algorithm SHA256).Hash } else { '' }
$TemplateSource = Get-ChildItem (Split-Path -Parent $ProjectDir) -Directory |
    Where-Object { $_.Name -like '02-*' } |
    Get-ChildItem -File -Filter '*.xlsx' |
    Select-Object -First 1 -ExpandProperty FullName
$TemplateResource = Join-Path $ProjectDir 'resources\release_workbook_template.xlsx'
$BuildInfoPath = Join-Path $ProjectDir 'resources\build_info.json'
$BuildDate = Get-Date -Format 'yyyy-MM-dd'
$BuildTime = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

Push-Location $ProjectDir
try {
    if (-not $TemplateSource) { throw 'Release workbook template was not found.' }
    Copy-Item -LiteralPath $TemplateSource -Destination $TemplateResource -Force

    # Stamp build date for sidebar display (resources/build_info.json)
    python -c "import json,sys; p=sys.argv[1]; d={'version':'4.27','edition':'Private','build_date':sys.argv[2],'build_time':sys.argv[3]}; open(p,'w',encoding='utf-8').write(json.dumps(d,ensure_ascii=False,indent=2)+chr(10))" $BuildInfoPath $BuildDate $BuildTime
    if ($LASTEXITCODE -ne 0) { throw 'Failed to write build_info.json' }

    Get-ChildItem $InstallerDir -File | Where-Object { $_.Name -notin @('setup.cmd', 'README.txt') } | Remove-Item -Force
    $InstallerDataDir = Join-Path $InstallerDir 'data'
    if (Test-Path -LiteralPath $InstallerDataDir) { Remove-Item -LiteralPath $InstallerDataDir -Recurse -Force }

    # Keep README first lines: product name + build date (ASCII-safe stamp)
    $ReadmePath = Join-Path $InstallerDir 'README.txt'
    if (Test-Path -LiteralPath $ReadmePath) {
        python -c "import pathlib,re,sys; p=pathlib.Path(sys.argv[1]); d=sys.argv[2]; t=p.read_text(encoding='utf-8'); t=re.sub(r'V4\\.\\d+(?:\\.\\d+)?', 'V4.27', t, count=1); key='\\u66f4\\u65b0\\u65e5\\u671f'; label=key.encode().decode('unicode_escape'); t=re.sub(label+r'[\\uff1a:]\\s*\\S+', label+'\\uff1a'+d, t, count=1); p.write_text(t if label in t else ('PengTools Hub V4.27 Private\\n'+label+'\\uff1a%s\\n\\n'%d)+t, encoding='utf-8')" $ReadmePath $BuildDate
    }

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name PengToolsHub_Private `
        --icon resources\brand\pengtools-app-v2.ico `
        --add-data 'resources\style.qss;resources' `
        --add-data 'resources\chevron_down.svg;resources' `
        --add-data 'resources\check_white.svg;resources' `
        --add-data 'resources\app.ico;resources' `
        --add-data 'resources\app-icon.png;resources' `
        --add-data 'resources\brand;resources\brand' `
        --add-data 'resources\build_info.json;resources' `
        --add-data 'resources\private_knowledge_seed.txt;resources' `
        --add-data 'resources\private_knowledge_seed_workbooks.json;resources' `
        --add-data 'resources\release_workbook_template.xlsx;resources' `
        --add-data 'resources\icons;resources\icons' `
        --add-data 'resources\help;resources\help' `
        --hidden-import docx `
        --hidden-import openpyxl `
        --hidden-import msoffcrypto `
        --hidden-import PyQt6.QtSvg `
        --hidden-import websocket `
        --hidden-import websocket._app `
        --hidden-import mitmproxy `
        --hidden-import mitmproxy.tools.dump `
        --hidden-import mitmproxy.certs `
        --hidden-import mitmproxy.options `
        --exclude-module PyQt5 `
        --exclude-module PySide2 `
        --exclude-module PySide6 `
        --exclude-module tkinter `
        run.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

    Copy-Item (Join-Path $DistDir 'PengToolsHub_Private.exe') $InstallerDir -Force
    $ZipPath = Join-Path $ProjectDir 'PengToolsHub_Private_Offline_Setup.zip'
    if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
    Compress-Archive -Path (Join-Path $InstallerDir '*') -DestinationPath $ZipPath

    if ($OriginalHash -and (Get-FileHash $OriginalRelease -Algorithm SHA256).Hash -ne $OriginalHash) {
        throw 'Original PengToolsHub_Offline_Setup.zip was modified.'
    }
    Write-Host "Private release created: $ZipPath"
    Write-Host "Build date stamped: $BuildDate ($BuildTime)"
}
finally {
    Pop-Location
}
