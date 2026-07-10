# Job Agent CLI installer for Windows (PowerShell).
#
# Usage (recommended one-liner; open PowerShell as a normal user):
#
#   irm https://raw.githubusercontent.com/jiyangnan/job-agent/main/scripts/install.ps1 | iex
#
# Or, if you've already cloned the repo:
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# What it does:
# 1. Verifies prerequisites (Python ≥ 3.11, git, Chrome)
# 2. Clones the repo to %USERPROFILE%\.job-agent (if running via irm)
# 3. Creates an isolated venv at %USERPROFILE%\.job-agent\.venv
# 4. Installs the CLI in editable mode and marks it as an official managed install
# 5. Adds a `jobagent.cmd` shim at %USERPROFILE%\.job-agent\bin (PATH-friendly)
#
# Pre-PyPI; this is the official Windows install path during M1.

$ErrorActionPreference = "Stop"

$RepoUrl     = $env:JOBAGENT_REPO_URL    ; if (-not $RepoUrl)     { $RepoUrl     = "https://github.com/jiyangnan/job-agent.git" }
$InstallDir  = $env:JOBAGENT_INSTALL_DIR ; if (-not $InstallDir)  { $InstallDir  = Join-Path $env:USERPROFILE ".job-agent" }
$BinDir      = Join-Path $InstallDir "bin"

function Info($msg) { Write-Host "▶ $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "✓ $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "⚠ $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "✗ $msg" -ForegroundColor Red; exit 1 }

# 1. Prerequisites
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git not found. Install Git for Windows from https://git-scm.com/download/win first."
}

$pythonCmd = $null
foreach ($cand in @("python", "python3", "py")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $pythonCmd = $cand; break }
}
if (-not $pythonCmd) {
    Die "Python not found. Install Python 3.11+ from https://python.org or run: winget install Python.Python.3.12"
}

$pyVer = & $pythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$pyParts = $pyVer.Split('.')
if ([int]$pyParts[0] -lt 3 -or ([int]$pyParts[0] -eq 3 -and [int]$pyParts[1] -lt 11)) {
    Die "Python 3.11+ required (you have $pyVer). Install a newer Python, then re-run."
}
Ok "Python $pyVer"

# Chrome detection (warn-only)
$chromePaths = @(
    "$env:PROGRAMFILES\Google\Chrome\Application\chrome.exe",
    "${env:PROGRAMFILES(X86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chromeFound = $false
foreach ($p in $chromePaths) { if (Test-Path $p) { $chromeFound = $true; break } }
if ($chromeFound) { Ok "Chrome installed" }
else { Warn "Google Chrome not found. Install from https://www.google.com/chrome/ before running 'jobagent login'." }

# 2. Clone or update repo
if (Test-Path (Join-Path $InstallDir ".git")) {
    Info "Updating existing checkout at $InstallDir"
    Push-Location $InstallDir
    if (git status --porcelain) { Pop-Location; Die "Existing install has local changes; automatic bootstrap update refused." }
    git fetch origin main --tags | Out-Null
    git checkout --detach origin/main | Out-Null
    Pop-Location
} else {
    Info "Cloning Job Agent into $InstallDir"
    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir -Parent) | Out-Null
    git clone $RepoUrl $InstallDir
}
Ok "Repo at $InstallDir"

# 3. Venv
$venvDir = Join-Path $InstallDir ".venv"
if (-not (Test-Path $venvDir)) {
    Info "Creating venv"
    & $pythonCmd -m venv $venvDir
}
Ok "venv at $venvDir"

# 4. Install package
$venvPy  = Join-Path $venvDir "Scripts\python.exe"
$venvPip = Join-Path $venvDir "Scripts\pip.exe"
Info "Installing dependencies (this may take a minute)"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPip install -e $InstallDir --quiet
Ok "CLI installed"

$metadata = @{
    managed = $true
    install_type = "official-installer"
    repository = $RepoUrl
    install_dir = $InstallDir
} | ConvertTo-Json
$metadata | Set-Content -Path (Join-Path $InstallDir ".jobagent-install.json") -Encoding UTF8
Ok "Managed install metadata written"

# 5. Shim
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$shimPath = Join-Path $BinDir "jobagent.cmd"
@"
@echo off
"$venvPy" -m jobagent %*
"@ | Set-Content -Path $shimPath -Encoding ASCII
Ok "Shim at $shimPath"

# 6. Persist PATH if not already present
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if (-not $userPath) { $userPath = "" }
if ($userPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable("PATH", "$BinDir;$userPath", "User")
    Warn "Added $BinDir to your User PATH. Close and reopen PowerShell for it to take effect."
}

Write-Host ""
Write-Host "=========================================="
Write-Host "  Job Agent installed successfully"
Write-Host "=========================================="
Write-Host ""
Write-Host "Next steps:"
Write-Host ""
Write-Host "1. Get your license key from the project maintainer."
Write-Host ""
Write-Host "2. Open a NEW PowerShell window (so PATH refreshes), then:"
Write-Host "     jobagent init --key <jba_live_xxx>"
Write-Host ""
Write-Host "3. Verify environment:"
Write-Host "     jobagent doctor env"
Write-Host ""
Write-Host "4. Analyze your resume:"
Write-Host "     jobagent resume analyze --file %USERPROFILE%\Downloads\your-resume.pdf"
Write-Host ""
Write-Host "5. Start with one platform:"
Write-Host "     jobagent boss discover"
Write-Host ""
Write-Host "6. Read the full guide:"
Write-Host "     $InstallDir\README.md"
Write-Host ""
