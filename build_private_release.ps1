# 兼容旧入口 → 已合并为 scripts/build_release.ps1
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'scripts\build_release.ps1') @args
