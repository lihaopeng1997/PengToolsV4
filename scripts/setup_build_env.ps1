param(
    [switch]$Development
)

$ErrorActionPreference = 'Stop'

$ScriptDir = $PSScriptRoot
$ProjectDir = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$EnvName = if ($Development) { '.venv-dev' } else { '.venv-build' }
$RequirementsName = if ($Development) { 'requirements-dev.txt' } else { 'requirements-build.txt' }
$EnvDir = Join-Path $ProjectDir $EnvName
$EnvPython = Join-Path $EnvDir 'Scripts\python.exe'
$Requirements = Join-Path $ProjectDir $RequirementsName

if (-not (Test-Path -LiteralPath $Requirements)) {
    throw "Environment requirements not found: $Requirements"
}

if (-not (Test-Path -LiteralPath $EnvPython)) {
    $BasePython = $env:PENGTOOLS_BASE_PYTHON
    if ($BasePython) {
        if (-not (Test-Path -LiteralPath $BasePython)) {
            throw "PENGTOOLS_BASE_PYTHON does not exist: $BasePython"
        }
        & $BasePython -m venv $EnvDir
    } else {
        & py -3.12 -m venv $EnvDir
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to create the isolated Python 3.12 environment.'
    }
}

& $EnvPython -X utf8 -m pip install --disable-pip-version-check -r $Requirements
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install the isolated environment requirements.'
}

& $EnvPython -X utf8 -m pip check
if ($LASTEXITCODE -ne 0) {
    throw 'The isolated environment has dependency conflicts.'
}

$Purpose = if ($Development) { 'Development' } else { 'Build' }
Write-Host "$Purpose environment ready: $EnvPython"
