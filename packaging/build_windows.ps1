<#
.SYNOPSIS
Builds the CheapTrip Windows installer and smoke-tests it (used by CI and the release workflow).

  1. Builds the app's screens (ui\, npm) and installs the requirements and PyInstaller.
  2. Freezes the app into dist\cheaptrip\ (packaging\cheaptrip.spec): CheapTrip.exe, the
     desktop app, and cheaptrip-cli.exe, the console commands.
  3. Builds dist\CheapTrip-Setup-<version>.exe with Inno Setup (packaging\installer.iss).
  4. Installs it silently, as a first-time user gets it, and checks the installed app:
     the shortcuts, "start at sign-in" and the cheaptrip:// link, --version, doctor on the
     settings the app creates, one real dry-run cycle; then the desktop app started
     minimized (its API, its first search, Search now, the setup wizard's settings and the
     sign-in switch, a test notification, a second launch handing over, one engine at a
     time, Quit), and the uninstaller.

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

# ── The app's screens ─────────────────────────────────────────────────────────
Push-Location ui
try {
    Invoke-Native "npm ci" { npm ci --no-audit --no-fund }
    Invoke-Native "UI build" { npm run build }
}
finally { Pop-Location }

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
$Exe = Join-Path $AppDir "cheaptrip-cli.exe"
$App = Join-Path $AppDir "CheapTrip.exe"
$AppFile = Join-Path $DataDir "app.json"
$Log = Join-Path $DataDir "logs\engine.log"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
# The "start at sign-in" command, or $null when it's off (a missing value, which strict mode can't read as a property)
function Get-SignIn { (Get-Item $RunKey).GetValue("CheapTrip") }
$Logs = New-Item -ItemType Directory -Force (Join-Path $Root "dist\smoke-logs")
if (Test-Path $DataDir) { Remove-Item $DataDir -Recurse -Force }

try {
    $install = Start-Process $Setup -Wait -PassThru -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$Logs\install.log`""
    )
    Confirm-Step ($install.ExitCode -eq 0) "silent install (exit code $($install.ExitCode))"
    Confirm-Step (Wait-Until { (Test-Path $Exe) -and (Test-Path $App) } 60) "installed $App and $Exe"
    $signIn = Get-SignIn
    Confirm-Step ($signIn -eq "`"$App`" --minimized") "'Start CheapTrip when I sign in' is on by default, in the tray ($signIn)"
    $protocol = (Get-ItemProperty "HKCU:\Software\Classes\cheaptrip\shell\open\command")."(default)"
    Confirm-Step ($protocol -eq "`"$App`" `"%1`"") "cheaptrip:// links open the app"

    # ── Run the installed app ─────────────────────────────────────────────────
    $out = (Invoke-Capture { & $Exe --version }).Trim()
    Confirm-Step ($LASTEXITCODE -eq 0 -and $out -eq "CheapTrip, version $Version") "--version prints '$out'"

    $doctor = Invoke-Capture { & $Exe doctor }
    $doctorExit = $LASTEXITCODE
    Write-Host $doctor
    Confirm-Step ($doctorExit -eq 1) "doctor exits 1 without a Telegram token"
    Confirm-Step ((Test-Path (Join-Path $DataDir ".env")) -and (Test-Path (Join-Path $DataDir "config\user_preferences.yaml"))) `
        "the first start creates the settings and preferences"
    Confirm-Step ($doctor -match "Preferences: .*home MXP, LIN, BGY") "doctor reads the new preferences"
    Confirm-Step ($doctor -notmatch "Traceback") "doctor runs without errors"

    Invoke-Capture { & $Exe cycle } | Out-File (Join-Path $Logs "cycle.txt")
    Confirm-Step ($LASTEXITCODE -eq 0) "the dry-run cycle exits 0"
    $cycles = @(Select-String -Path $Log -Pattern "pipeline_cycle_complete").Count
    Confirm-Step ($cycles -ge 1) "a real dry-run cycle completes"

    # ── The desktop app ───────────────────────────────────────────────────────
    $appProcess = Start-Process $App -ArgumentList "--minimized" -PassThru
    Confirm-Step (Wait-Until { Test-Path $AppFile } 90) "the app starts in the tray and writes app.json"
    $info = Get-Content $AppFile -Raw | ConvertFrom-Json
    $Api = "http://127.0.0.1:$($info.port)/api/v1"
    $Auth = @{ Authorization = "Bearer $($info.token)" }
    function Invoke-Api([string]$Method, [string]$Path, $Body = $null) {
        $params = @{ Method = $Method; Uri = "$Api$Path"; Headers = $Auth; NoProxy = $true
                     SkipHttpErrorCheck = $true; StatusCodeVariable = "code" }
        if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Compress -Depth 6); $params.ContentType = "application/json" }
        $result = Invoke-RestMethod @params
        return [pscustomobject]@{ Status = [int]$code; Body = $result }
    }

    $status = (Invoke-Api GET "/status").Body
    Confirm-Step ($status.version -eq $Version) "the app's API answers (version $($status.version), $($status.state))"
    $page = Invoke-WebRequest "http://127.0.0.1:$($info.port)/" -NoProxy
    Confirm-Step ($page.Content -match 'id="root"') "the app serves its screens"
    # (the dry-run cycle above is in the history too: wait for the app's own search at start)
    Confirm-Step (Wait-Until { $s = (Invoke-Api GET "/status").Body
                               -not $s.searching -and $s.last_cycle.trigger -eq "startup" -and $s.last_cycle.status -eq "ok" } 300) `
        "the app's first search completes"
    $deals = (Invoke-Api GET "/deals").Body
    $found = @($deals.instant).Count + @($deals.digest).Count
    Confirm-Step ($found -gt 0) "the Deals screen lists $found deals"

    $searches = (Invoke-Api GET "/status").Body.searches_today
    $started = (Invoke-Api POST "/engine/search-now").Body.started
    Confirm-Step $started "Search now starts a search"
    Confirm-Step (Wait-Until { $s = (Invoke-Api GET "/status").Body; -not $s.searching -and $s.searches_today -gt $searches } 300) `
        "the search it started completes"

    # The setup wizard's answers, through the app, as a first-time user gives them
    Confirm-Step ((Invoke-Api GET "/status").Body.setup_needed) "a new install opens the setup wizard"
    $saved = Invoke-Api PUT "/settings" @{ preferences = @{ home_airports = @("LGW", "STN") }; app = @{ setup_done = $true; start_at_login = $false } }
    Confirm-Step ($saved.Status -eq 200 -and -not (Get-SignIn)) "the wizard saves the home airports and turns 'start at sign-in' off"
    $prefs = Get-Content (Join-Path $DataDir "config\user_preferences.yaml") -Raw
    Confirm-Step ($prefs -match "(?m)^\s*-\s*LGW\s*$" -and -not (Invoke-Api GET "/status").Body.setup_needed) "the preferences have the new airports and setup is done"
    $saved = Invoke-Api PUT "/settings" @{ app = @{ start_at_login = $true } }
    Confirm-Step ((Get-SignIn) -eq "`"$App`" --minimized") "Settings turns 'start at sign-in' back on"

    $test = Invoke-Api POST "/setup/test-notification" @{ channel = "desktop" }
    Confirm-Step ($test.Status -in 200, 502) "a test notification is sent or refused cleanly (HTTP $($test.Status): $($test.Body | ConvertTo-Json -Compress))"

    $second = Start-Process $App -ArgumentList "cheaptrip://activity" -PassThru -Wait
    Confirm-Step ($second.ExitCode -eq 0 -and -not $appProcess.HasExited) "a second launch hands over to the running app"
    Confirm-Step (@(Select-String -Path $Log -Pattern '"app_show"').Count -ge 1) "the running app shows the screen it was asked for"

    $other = Start-Process $Exe -ArgumentList "run" -PassThru `
        -RedirectStandardOutput (Join-Path $Logs "second-engine-out.txt") -RedirectStandardError (Join-Path $Logs "second-engine.txt")
    $refused = (Wait-Until { $other.HasExited } 60) -and $other.ExitCode -eq 1
    if (-not $other.HasExited) { Stop-Process -Id $other.Id -Force -ErrorAction SilentlyContinue }
    Confirm-Step $refused "the console engine refuses to start while the app runs"

    Confirm-Step ((Invoke-Api POST "/app/quit").Status -eq 200) "Quit is accepted"
    Confirm-Step (Wait-Until { $appProcess.HasExited } 60) "the app quits"
    Confirm-Step (-not (Test-Path $AppFile)) "the app removes app.json when it quits"

    # ── Uninstall: the program goes, the settings stay ────────────────────────
    $uninstall = Start-Process (Join-Path $AppDir "unins000.exe") -Wait -PassThru `
        -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
    Confirm-Step ($uninstall.ExitCode -eq 0) "silent uninstall (exit code $($uninstall.ExitCode))"
    Confirm-Step (Wait-Until { -not (Test-Path $Exe) } 60) "the program is removed"
    Confirm-Step (-not (Get-SignIn)) "'start at sign-in' is removed"
    Confirm-Step (-not (Test-Path "HKCU:\Software\Classes\cheaptrip")) "the cheaptrip:// link is removed"
    Confirm-Step (Test-Path (Join-Path $DataDir ".env")) "the settings are kept"
}
finally {
    if (Test-Path $Log) { Copy-Item $Log $Logs -Force }
}

Write-Host "`nCheapTrip $Version installer: $Setup"
