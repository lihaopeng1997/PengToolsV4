# 根目录便捷入口 → scripts/build_private_release.ps1
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'scripts\build_private_release.ps1') @args
