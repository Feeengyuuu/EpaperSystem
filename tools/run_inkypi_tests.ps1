param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]] $PytestArgs
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$projectRoot = Join-Path $repoRoot "inkypi-weather\package\InkyPi"
$pcPackages = Join-Path $projectRoot ".pc-packages"

$pythonCandidates = @()
if ($env:INKYPI_PYTHON311) {
  $pythonCandidates += $env:INKYPI_PYTHON311
}
$pythonCandidates += @(
  (Join-Path $projectRoot ".venv\Scripts\python.exe"),
  (Join-Path $projectRoot ".venv-test\Scripts\python.exe"),
  (Join-Path $projectRoot ".venv-codex\Scripts\python.exe"),
  (Join-Path $projectRoot ".venv-local\Scripts\python.exe"),
  "python"
)

function Test-Python311HasPytest($candidate) {
  if ($candidate -ne "python" -and -not (Test-Path -LiteralPath $candidate)) {
    return $false
  }
  $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
  if ($LASTEXITCODE -ne 0 -or $version -ne "3.11") {
    return $false
  }
  & $candidate -m pytest --version *> $null
  return $LASTEXITCODE -eq 0
}

$python = $null
foreach ($candidate in $pythonCandidates) {
  if (Test-Python311HasPytest $candidate) {
    $python = $candidate
    break
  }
}

if (-not $python) {
  throw "No Python 3.11 interpreter with pytest found. Create .venv-test with Python 3.11 and install install\requirements-dev.txt with --require-hashes."
}

$tmpRoot = Join-Path $projectRoot ".tmp\pytest"
$runId = "{0}-{1}" -f $PID, ([guid]::NewGuid().ToString("N"))
$processTemp = Join-Path $tmpRoot "tmp-$runId"
New-Item -ItemType Directory -Force -Path $processTemp | Out-Null

$previousPythonPath = $env:PYTHONPATH
$previousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
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
$env:PYTHONPATH = ($pythonPathEntries -join [IO.Path]::PathSeparator)
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:TEMP = $processTemp
$env:TMP = $processTemp
$env:TMPDIR = $processTemp

$testExitCode = 1
try {
  Push-Location $projectRoot
  try {
    & $python -m pytest -p "no:cacheprovider" @PytestArgs
    $testExitCode = $LASTEXITCODE
  } finally {
    Pop-Location
  }
} finally {
  $env:PYTHONPATH = $previousPythonPath
  $env:PYTHONDONTWRITEBYTECODE = $previousDontWriteBytecode
  $env:TEMP = $previousTemp
  $env:TMP = $previousTmp
  $env:TMPDIR = $previousTmpDir
  if (Test-Path -LiteralPath $processTemp) {
    Remove-Item -LiteralPath $processTemp -Recurse -Force
  }
}

exit $testExitCode
