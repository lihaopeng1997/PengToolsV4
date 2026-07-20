$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DistDir = Join-Path $ProjectDir 'dist'
$InstallerDir = Join-Path $ProjectDir 'Installer'

Push-Location $ProjectDir
try {
    Get-ChildItem $InstallerDir -File | Where-Object {
        $_.Name -notin @('setup.cmd', 'README.txt')
    } | Remove-Item -Force
    $InstallerDataDir = Join-Path $InstallerDir 'data'
    if (Test-Path -LiteralPath $InstallerDataDir) {
        Remove-Item -LiteralPath $InstallerDataDir -Recurse -Force
    }

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name PengToolsHub `
        --icon resources\app.ico `
        --add-data 'resources\style.qss;resources' `
        --add-data 'resources\chevron_down.svg;resources' `
        --add-data 'resources\app.ico;resources' `
        --add-data 'resources\app-icon.png;resources' `
        --hidden-import docx `
        run.py

    Copy-Item (Join-Path $DistDir 'PengToolsHub.exe') $InstallerDir -Force
    $ZipPath = Join-Path $ProjectDir 'PengToolsHub_Offline_Setup.zip'
    if (Test-Path $ZipPath) {
        Remove-Item $ZipPath -Force
    }
    Compress-Archive -Path (Join-Path $InstallerDir '*') -DestinationPath $ZipPath
    Write-Host "Release created: $ZipPath"
}
finally {
    Pop-Location
}
