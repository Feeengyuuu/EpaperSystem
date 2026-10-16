param(
    [Parameter(Mandatory = $true)][string]$TaskDir,
    [string]$Python = '',
    [ValidateRange(1, 32)][int]$ShardCount = 6,
    [int]$Only = -1
)
# Fresh-process sharded full suite (ERR-20260830-002: intermittent Windows native
# access violations in unchanged code). Each shard runs in its own Python process;
# a shard that dies with 0xC0000005 or 0xC0000409 is retried, test failures are not.
# Logs go to the round's task folder, which must be a direct child of outputs\.
$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$Outputs = Join-Path $Root 'outputs'
$Task = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($TaskDir)) { $TaskDir } else { Join-Path $Root $TaskDir }))
if ([IO.Path]::GetDirectoryName($Task) -ne $Outputs) { throw "Task folder must be a direct child of $Outputs." }
if ([IO.Path]::GetFileName($Task) -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') { throw 'Unsafe task folder name.' }
New-Item -ItemType Directory -Force -Path $Task | Out-Null
if ((Get-Item -LiteralPath $Task -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing a task folder that is a reparse point.' }
if ($Python) { $env:INKYPI_PYTHON311 = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($Python)) { $Python } else { Join-Path $Root $Python })) }
$Package = Join-Path $Root 'inkypi-weather\package\InkyPi'
$Runner = Join-Path $Root 'tools\run_inkypi_tests.ps1'
$Files = @(Get-ChildItem -LiteralPath (Join-Path $Package 'tests') -Recurse -Filter 'test_*.py' | Sort-Object FullName |
    ForEach-Object { $_.FullName.Substring($Package.Length + 1) })
$Summary = Join-Path $Task 'shards-summary.log'
"files=$($Files.Count) shards=$ShardCount only=$Only started=$([DateTime]::UtcNow.ToString('o'))" | Add-Content -LiteralPath $Summary
$Failed = @()
for ($Shard = 0; $Shard -lt $ShardCount; $Shard++) {
    if ($Only -ge 0 -and $Shard -ne $Only) { continue }
    $Selected = @(for ($i = $Shard; $i -lt $Files.Count; $i += $ShardCount) { $Files[$i] })
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        $Log = Join-Path $Task ("shard-{0}-try{1}.log" -f $Shard, $Attempt)
        pwsh -NoProfile -File $Runner -q @Selected > $Log 2>&1
        $Code = $LASTEXITCODE
        $Tail = (Get-Content -LiteralPath $Log -Tail 3) -join ' '
        "shard=$Shard try=$Attempt exit=$Code $Tail" | Add-Content -LiteralPath $Summary
        if ($Code -ne -1073741819 -and $Code -ne -1073740791) { break }
    }
    if ($Code -ne 0) { $Failed += $Shard }
}
$Result = if ($Failed.Count) { 'failed shards=' + ($Failed -join ',') } else { 'passed' }
"done=$([DateTime]::UtcNow.ToString('o')) result=$Result" | Add-Content -LiteralPath $Summary
$Result
if ($Failed.Count) { exit 1 }
