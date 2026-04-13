param(
  [string]$Version = "1.27.12"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$vendorDir = Join-Path $repoRoot "electron\vendor\syncthing\windows-amd64"
$tmpDir = Join-Path $env:TEMP ("nono-syncthing-" + [guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

$zipName = "syncthing-windows-amd64-v$Version.zip"
$downloadUrl = "https://github.com/syncthing/syncthing/releases/download/v$Version/$zipName"
$zipPath = Join-Path $tmpDir $zipName
$extractDir = Join-Path $tmpDir "extract"

Write-Host "[Syncthing] Downloading $downloadUrl"
Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath

Write-Host "[Syncthing] Extracting package"
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

$exe = Get-ChildItem -Path $extractDir -Recurse -Filter "syncthing.exe" | Select-Object -First 1
if (-not $exe) {
  throw "syncthing.exe not found in downloaded archive."
}

$license = Get-ChildItem -Path $extractDir -Recurse | Where-Object {
  $_.Name -eq "LICENSE" -or $_.Name -eq "LICENSE.txt"
} | Select-Object -First 1

Copy-Item -Path $exe.FullName -Destination (Join-Path $vendorDir "syncthing.exe") -Force
if ($license) {
  Copy-Item -Path $license.FullName -Destination (Join-Path $vendorDir "LICENSE") -Force
}

Write-Host "[Syncthing] Prepared embedded runtime in: $vendorDir"

Remove-Item -Path $tmpDir -Recurse -Force
