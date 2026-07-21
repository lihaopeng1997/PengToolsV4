# 根目录便捷入口 → scripts/build_release.ps1（标准包，常规维护禁止）
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'scripts\build_release.ps1') @args
