# 根目录便捷入口 → scripts/build_release.ps1（唯一发布构建）
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'scripts\build_release.ps1') @args
