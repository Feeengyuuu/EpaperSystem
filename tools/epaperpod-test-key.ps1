param(
  [string]$HostName = "ColoredEpaperFrame.local",
  [string]$UserName = "feeengyuuu"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$KeyPath = Join-Path $Root ".ssh\epaperpod_codex_20260525"
$KnownHosts = Join-Path $Root ".tmp\epaperpod_known_hosts"
$Ssh = "C:\Windows\System32\OpenSSH\ssh.exe"

if (!(Test-Path -LiteralPath $KeyPath)) {
  throw "Missing SSH key: $KeyPath"
}

& $Ssh -i $KeyPath -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$KnownHosts "$UserName@$HostName" "echo ssh-key-ok; hostname; systemctl is-active inkypi"
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
