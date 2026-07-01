param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]] $PytestArgs
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$projectRoot = Join-Path $repoRoot "inkypi-weather\package\InkyPi"
$pcPackages = Join-Path $projectRoot ".pc-packages"
$tmpRoot = Join-Path $projectRoot ".tmp\pytest"
$runId = "{0}-{1}" -f $PID, (Get-Date -Format "yyyyMMddHHmmssfff")
$processTemp = Join-Path $tmpRoot "tmp-$runId"

New-Item -ItemType Directory -Force -Path $tmpRoot | Out-Null
New-Item -ItemType Directory -Force -Path $processTemp | Out-Null

$pythonCandidates = @(
  (Join-Path $projectRoot ".venv\Scripts\python.exe"),
  (Join-Path $projectRoot ".venv-test\Scripts\python.exe"),
  (Join-Path $projectRoot ".venv-codex\Scripts\python.exe"),
  (Join-Path $projectRoot ".venv-local\Scripts\python.exe"),
  "python"
)

function Test-PythonHasPytest($candidate) {
  if ($candidate -ne "python" -and -not (Test-Path -LiteralPath $candidate)) {
    return $false
  }
  & $candidate -m pytest --version *> $null
  return $LASTEXITCODE -eq 0
}

$python = $null
foreach ($candidate in $pythonCandidates) {
  if (Test-PythonHasPytest $candidate) {
    $python = $candidate
    break
  }
}

if (-not $python) {
  throw "No Python executable with pytest found. From inkypi-weather/package/InkyPi, create .venv and run: .\.venv\Scripts\python.exe -m pip install -r install\requirements-dev.txt"
}

$previousPythonPath = $env:PYTHONPATH
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$previousTmpDir = $env:TMPDIR
$pythonPathEntries = @(
  (Join-Path $projectRoot "src"),
  $projectRoot
)
if ($python -eq "python" -and (Test-Path -LiteralPath $pcPackages)) {
  $pythonPathEntries += $pcPackages
}
if ($previousPythonPath) {
  $pythonPathEntries += $previousPythonPath
}
$env:PYTHONPATH = ($pythonPathEntries -join ";")
$env:TEMP = $processTemp
$env:TMP = $processTemp
$env:TMPDIR = $processTemp

try {
  Push-Location $projectRoot
  & $python -m pytest -p "no:cacheprovider" @PytestArgs
  exit $LASTEXITCODE
} finally {
  Pop-Location
  $env:PYTHONPATH = $previousPythonPath
  $env:TEMP = $previousTemp
  $env:TMP = $previousTmp
  $env:TMPDIR = $previousTmpDir
}
