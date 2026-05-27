$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$inkypi = Join-Path $root "InkyPi"
$python = "C:\Users\super\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

$packages = Join-Path $inkypi ".pc-packages"
if (-not (Test-Path -LiteralPath $packages)) {
    throw "Missing local dependencies at $packages. Install them inside the project folder first."
}

$chromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application",
    "$env:LOCALAPPDATA\Google\Chrome\Application"
)

foreach ($candidate in $chromeCandidates) {
    if ($candidate -and (Test-Path -LiteralPath (Join-Path $candidate "chrome.exe"))) {
        $env:PATH = "$candidate;$env:PATH"
        break
    }
}

$env:PYTHONPATH = "$packages;$inkypi\src"
Set-Location $inkypi
& $python "src\inkypi.py" "--dev"
