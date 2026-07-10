param(
  [string]$ZipName = "inkypi-release.zip",
  [string]$ReleaseId = "",
  [string]$HostName = "ColoredEpaperFrame.local",
  [string]$UserName = "feeengyuuu"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$KeyPath = Join-Path $Root ".ssh\epaperpod_codex_20260525"
$KnownHosts = Join-Path $Root ".tmp\epaperpod_known_hosts"
$Ssh = "C:\Windows\System32\OpenSSH\ssh.exe"
$Scp = "C:\Windows\System32\OpenSSH\scp.exe"

if (!(Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
  throw "Missing SSH key: $KeyPath"
}
if (!(Test-Path -LiteralPath $KnownHosts -PathType Leaf)) {
  throw "Pinned SSH known-hosts file is required: $KnownHosts"
}
if (!(Test-Path -LiteralPath $Ssh -PathType Leaf) -or !(Test-Path -LiteralPath $Scp -PathType Leaf)) {
  throw "Windows OpenSSH client is unavailable"
}
if ($HostName -notmatch '^[A-Za-z0-9.-]+$' -or $UserName -notmatch '^[A-Za-z0-9._-]+$') {
  throw "Unsafe SSH user or host name"
}

$Artifact = if ([IO.Path]::IsPathRooted($ZipName)) { $ZipName } else { Join-Path $Root $ZipName }
if (!(Test-Path -LiteralPath $Artifact -PathType Leaf)) {
  throw "Missing release artifact: $Artifact"
}
$Artifact = (Resolve-Path -LiteralPath $Artifact).Path
$Sha256 = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash.ToLowerInvariant()
if (!$ReleaseId) {
  $ReleaseId = "deploy-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))-$($Sha256.Substring(0, 12))"
}
if ($ReleaseId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
  throw "Unsafe release id: $ReleaseId"
}

$RemoteName = "inkypi-$ReleaseId.zip"
$RemotePath = "/var/tmp/$RemoteName"
$SshOptions = @(
  "-i", $KeyPath,
  "-o", "BatchMode=yes",
  "-o", "IdentitiesOnly=yes",
  "-o", "ConnectTimeout=8",
  "-o", "StrictHostKeyChecking=yes",
  "-o", "UserKnownHostsFile=$KnownHosts"
)
$Destination = "${UserName}@${HostName}:$RemotePath"

& $Scp @SshOptions $Artifact $Destination
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

$Remote = "set -Eeuo pipefail; trap 'rm -f $RemotePath' EXIT; sudo -n /usr/local/sbin/inkypi-update --artifact $RemotePath --sha256 $Sha256 --release-id $ReleaseId; systemctl is-active --quiet inkypi.service"
& $Ssh @SshOptions "$UserName@$HostName" $Remote
exit $LASTEXITCODE
