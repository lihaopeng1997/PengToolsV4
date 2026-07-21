$ErrorActionPreference = 'Stop'

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
    if ($TemplateSource) {
        Copy-Item -LiteralPath $TemplateSource -Destination $TemplateResource -Force
    } elseif (-not (Test-Path -LiteralPath $TemplateResource)) {
        throw 'Release workbook template was not found.'
    }

    python -c "import json,sys; open(sys.argv[1],'w',encoding='utf-8').write(json.dumps({'version':'4.27','edition':'Private','build_date':sys.argv[2],'build_time':sys.argv[3]},ensure_ascii=False,indent=2)+chr(10))" $BuildInfoPath $BuildDate $BuildTime
    if (-not (Test-Path -LiteralPath $BuildInfoPath)) {
        throw 'Failed to write build_info.json'
    }

    if (-not (Test-Path -LiteralPath $InstallerDir)) {
        New-Item -ItemType Directory -Path $InstallerDir | Out-Null
    }
    Get-ChildItem $InstallerDir -File | Where-Object { $_.Name -notin @('setup.cmd', 'README.txt') } | Remove-Item -Force
    $InstallerDataDir = Join-Path $InstallerDir 'data'
    if (Test-Path -LiteralPath $InstallerDataDir) {
        Remove-Item -LiteralPath $InstallerDataDir -Recurse -Force
    }

    # Brand icon from former Private package
    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name PengToolsHub `
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
        Get-ChildItem $LegacyInstallerDir -File | Where-Object { $_.Name -notin @('setup.cmd', 'README.txt') } | Remove-Item -Force
        $LegacyDataDir = Join-Path $LegacyInstallerDir 'data'
        if (Test-Path -LiteralPath $LegacyDataDir) {
            Remove-Item -LiteralPath $LegacyDataDir -Recurse -Force
        }
        Copy-Item $ExePath $LegacyInstallerDir -Force
        $SetupSrc = Join-Path $InstallerDir 'setup.cmd'
        if (Test-Path -LiteralPath $SetupSrc) {
            Copy-Item $SetupSrc (Join-Path $LegacyInstallerDir 'setup.cmd') -Force
        }
    }

    $ZipPath = Join-Path $ProjectDir 'PengToolsHub_Offline_Setup.zip'
    if (Test-Path -LiteralPath $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }
    Compress-Archive -Path (Join-Path $InstallerDir '*') -DestinationPath $ZipPath

    $LegacyZip = Join-Path $ProjectDir 'PengToolsHub_Private_Offline_Setup.zip'
    if (Test-Path -LiteralPath $LegacyZip) {
        Remove-Item -LiteralPath $LegacyZip -Force
    }
    $LegacyExe = Join-Path $DistDir 'PengToolsHub_Private.exe'
    if (Test-Path -LiteralPath $LegacyExe) {
        Remove-Item -LiteralPath $LegacyExe -Force
    }

    Write-Host "Release created: $ZipPath"
    Write-Host "EXE: $ExePath"
    Write-Host "Build date stamped: $BuildDate ($BuildTime)"
}
finally {
    Pop-Location
}
