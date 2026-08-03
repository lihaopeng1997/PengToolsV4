# 兼容旧入口：原 Private 构建已合并为唯一产品 PengToolsHub
$ErrorActionPreference = 'Stop'
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
& (Join-Path $ScriptDir 'build_release.ps1') @args
