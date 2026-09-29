<#
.SYNOPSIS
Builds the CheapTrip Windows installer and smoke-tests it (used by CI and the release workflow).

  1. Installs the app's requirements and PyInstaller.
  2. Freezes the app into dist\cheaptrip\ (packaging\cheaptrip.spec).
  3. Builds dist\CheapTrip-Setup-<version>.exe with Inno Setup (packaging\installer.iss).
  4. Installs it silently, as a first-time user gets it, and checks the installed app:
     the settings the wizard writes, the sign-in shortcut, --version, doctor,
     one real dry-run cycle, a running engine, and the uninstaller.

Usage (PowerShell 7, from anywhere):  pwsh packaging/build_windows.ps1 [-Version 0.6.0]
#>
param([string]$Version = "")

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Native([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

function Confirm-Step([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "Smoke test failed: $Message" }
    Write-Host "ok  $Message"
}

# Runs a native command and returns its combined output; its exit code stays in $LASTEXITCODE.
# Output on stderr (the app's logs) is not an error here.
function Invoke-Capture([scriptblock]$Command) {
    $ErrorActionPreference = "Continue"
    return (& $Command 2>&1 | Out-String)
}

function Wait-Until([scriptblock]$Condition, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { return $true }
        Start-Sleep -Seconds 2
    }
    return [bool](& $Condition)
}

# ── Version ───────────────────────────────────────────────────────────────────
$CodeVersion = (python -c "from utils.version import __version__; print(__version__)").Trim()
if (-not $Version) { $Version = $CodeVersion }
if ($Version -ne $CodeVersion) { throw "Version $Version doesn't match utils/version.py ($CodeVersion)" }
Write-Host "Building CheapTrip $Version"

# ── Freeze the app ────────────────────────────────────────────────────────────
Invoke-Native "pip install" {
    python -m pip install --disable-pip-version-check -r requirements.txt "pyinstaller>=6.10,<7"
}
Invoke-Native "PyInstaller" { python -m PyInstaller --noconfirm --clean packaging/cheaptrip.spec }

# ── Build the installer ───────────────────────────────────────────────────────
$Iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) {
    Invoke-Native "Inno Setup install" { choco install innosetup --yes --no-progress }
}
Invoke-Native "Inno Setup" { & $Iscc /Qp "/DAppVersion=$Version" packaging\installer.iss }
$Setup = Join-Path $Root "dist\CheapTrip-Setup-$Version.exe"
Confirm-Step (Test-Path $Setup) "built $Setup ($([math]::Round((Get-Item $Setup).Length / 1MB)) MB)"

# ── Smoke test: install silently, as a first-time user ────────────────────────
$DataDir = Join-Path $env:APPDATA "CheapTrip"
$AppDir = Join-Path $env:LOCALAPPDATA "Programs\CheapTrip"
$Exe = Join-Path $AppDir "cheaptrip.exe"
$Log = Join-Path $DataDir "logs\engine.log"
$Startup = Join-Path ([Environment]::GetFolderPath("Startup")) "CheapTrip.lnk"
$Logs = New-Item -ItemType Directory -Force (Join-Path $Root "dist\smoke-logs")
if (Test-Path $DataDir) { Remove-Item $DataDir -Recurse -Force }

try {
    $install = Start-Process $Setup -Wait -PassThru -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$Logs\install.log`"",
        "/TELEGRAMCHATID=4242", "/HOMEAIRPORTS=`"lgw stn`""
    )
    Confirm-Step ($install.ExitCode -eq 0) "silent install (exit code $($install.ExitCode))"
    Confirm-Step (Wait-Until { Test-Path $Exe } 60) "installed $Exe"
    Confirm-Step (Test-Path $Startup) "'Start CheapTrip when I sign in' is on by default"

    $settings = Get-Content (Join-Path $DataDir ".env") -Raw
    Confirm-Step ($settings -match "(?m)^TELEGRAM_CHAT_ID=4242\s*$") "the wizard's answers are in .env"
    $prefs = Get-Content (Join-Path $DataDir "config\user_preferences.yaml") -Raw
    Confirm-Step ($prefs -match "(?m)^home_airports: \[LGW, STN\]\s*$") "the home airports are in the preferences"

    # ── Run the installed app ─────────────────────────────────────────────────
    $out = (Invoke-Capture { & $Exe --version }).Trim()
    Confirm-Step ($LASTEXITCODE -eq 0 -and $out -eq "CheapTrip, version $Version") "--version prints '$out'"

    $doctor = Invoke-Capture { & $Exe doctor }
    $doctorExit = $LASTEXITCODE
    Write-Host $doctor
    Confirm-Step ($doctorExit -eq 1) "doctor exits 1 without a Telegram token"
    Confirm-Step ($doctor -match "Preferences: .*home LGW, STN") "doctor reads the installed preferences"
    Confirm-Step ($doctor -notmatch "Traceback") "doctor runs without errors"

    Invoke-Capture { & $Exe cycle } | Out-File (Join-Path $Logs "cycle.txt")
    Confirm-Step ($LASTEXITCODE -eq 0) "the dry-run cycle exits 0"
    $cycles = @(Select-String -Path $Log -Pattern "pipeline_cycle_complete").Count
    Confirm-Step ($cycles -ge 1) "a real dry-run cycle completes"

    $engine = Start-Process $Exe -ArgumentList "run" -PassThru `
        -RedirectStandardOutput (Join-Path $Logs "run-out.txt") -RedirectStandardError (Join-Path $Logs "run.txt")
    $ranCycle = Wait-Until { @(Select-String -Path $Log -Pattern "pipeline_cycle_complete").Count -gt $cycles } 240
    $stillRunning = -not $engine.HasExited
    Stop-Process -Id $engine.Id -Force -ErrorAction SilentlyContinue
    Confirm-Step $ranCycle "the running engine completes a cycle"
    Confirm-Step $stillRunning "the engine keeps running after its first cycle (scheduler started)"

    # ── Uninstall: the program goes, the settings stay ────────────────────────
    $uninstall = Start-Process (Join-Path $AppDir "unins000.exe") -Wait -PassThru `
        -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
    Confirm-Step ($uninstall.ExitCode -eq 0) "silent uninstall (exit code $($uninstall.ExitCode))"
    Confirm-Step (Wait-Until { -not (Test-Path $Exe) } 60) "the program is removed"
    Confirm-Step (-not (Test-Path $Startup)) "the sign-in shortcut is removed"
    Confirm-Step (Test-Path (Join-Path $DataDir ".env")) "the settings are kept"
}
finally {
    if (Test-Path $Log) { Copy-Item $Log $Logs -Force }
}

Write-Host "`nCheapTrip $Version installer: $Setup"
