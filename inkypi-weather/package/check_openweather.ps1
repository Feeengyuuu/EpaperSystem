param(
    [switch]$Live
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$inkypi = Join-Path $root "InkyPi"
$python = "C:\Users\super\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

$packages = Join-Path $inkypi ".pc-packages"
$env:PYTHONPATH = "$packages"
Set-Location $inkypi

$scriptArgs = @()
if ($Live) {
    $scriptArgs += "--live"
}

& $python (Join-Path $root "tools\check_openweather.py") @scriptArgs
