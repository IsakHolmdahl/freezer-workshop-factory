<#
.SYNOPSIS
  Windows entry point for the AI Software Factory setup.

.DESCRIPTION
  Scion's Hub and Runtime Broker do not run on native Windows, so the factory
  runs inside WSL2 with Docker Desktop (or Docker Engine in the distribution).
  This script checks those prerequisites and then runs `make <target>` inside WSL.

    .\setup.ps1                 # guided setup (make setup)
    .\setup.ps1 doctor          # any other make target
    .\setup.ps1 -Distro Ubuntu  # choose the WSL distribution
#>
param(
    [Parameter(Position = 0)][string]$Target = "setup",
    [string]$Distro = ""
)
$ErrorActionPreference = "Stop"

function Fail([string]$Message, [string[]]$Fix) {
    Write-Host "x $Message" -ForegroundColor Red
    foreach ($line in $Fix) { Write-Host "  try: $line" }
    exit 1
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Fail "WSL is not installed" @("wsl --install   (in an elevated PowerShell, then restart Windows)")
}

# `wsl -l -q` prints UTF-16; strip the NULs PowerShell leaves in.
$distros = @((wsl.exe -l -q) -replace "`0", "" | Where-Object { $_.Trim() -and $_ -notmatch "^docker-desktop" })
if (-not $distros) {
    Fail "No WSL distribution is installed" @("wsl --install -d Ubuntu")
}
if (-not $Distro) { $Distro = $distros[0].Trim() }
$wsl = @("-d", $Distro)

$kernel = (wsl.exe @wsl -- uname -r) -replace "`0", ""
if ($kernel -notmatch "WSL2|microsoft-standard") {
    Fail "The distribution $Distro runs under WSL 1" @("wsl --set-version $Distro 2")
}

wsl.exe @wsl -- sh -c "docker info >/dev/null 2>&1"
if ($LASTEXITCODE -ne 0) {
    Fail "Docker is not reachable from $Distro" @(
        "start Docker Desktop and enable Settings > Resources > WSL integration for $Distro",
        "or install Docker Engine inside the distribution: https://docs.docker.com/engine/install/")
}

# The checkout must live in the WSL filesystem: Windows drives (/mnt/c) break
# file modes, symlinks and Docker bind mounts.
$here = (Get-Location).Path
if ($here -match '^\\\\wsl(\.localhost|\$)\\') {
    $linuxPath = (wsl.exe @wsl -- wslpath -a "$here") -replace "`0", ""
} else {
    Write-Host "This checkout is on a Windows drive. Clone it inside WSL instead:" -ForegroundColor Yellow
    Write-Host "  wsl -d $Distro"
    Write-Host "  git clone --recurse-submodules <repository-url> ~/ai-software-factory-opencode"
    Write-Host "  cd ~/ai-software-factory-opencode && make setup"
    exit 1
}

wsl.exe @wsl -- sh -c "command -v make >/dev/null 2>&1 && command -v git >/dev/null 2>&1"
if ($LASTEXITCODE -ne 0) {
    Fail "make and git are missing in $Distro" @("wsl -d $Distro -- sudo apt update", "wsl -d $Distro -- sudo apt install -y make git")
}

Write-Host "Running 'make $Target' in $Distro ($linuxPath)" -ForegroundColor Cyan
wsl.exe @wsl --cd "$linuxPath" -- make $Target
exit $LASTEXITCODE
