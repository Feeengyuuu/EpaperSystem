param(
  [string]$ZipName = "chinese-literature-clock-xinkai-near-migrate-20260525.zip",
  [string]$HostName = "ColoredEpaperFrame.local",
  [string]$UserName = "feeengyuuu",
  [string]$ServerUrl = "http://192.168.1.90:8766",
  [string]$PackageDir = "~/inkypi-weather-pi-package-20260524-3"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$KeyPath = Join-Path $Root ".ssh\epaperpod_codex_20260525"
$KnownHosts = Join-Path $Root ".tmp\epaperpod_known_hosts"
$Ssh = "C:\Windows\System32\OpenSSH\ssh.exe"

if (!(Test-Path -LiteralPath $KeyPath)) {
  throw "Missing SSH key: $KeyPath"
}

$Remote = "cd $PackageDir && wget -O $ZipName $ServerUrl/$ZipName && unzip -o $ZipName && sudo -n systemctl restart inkypi && systemctl is-active inkypi"
& $Ssh -i $KeyPath -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$KnownHosts "$UserName@$HostName" $Remote
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
